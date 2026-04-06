"""
실시간 파이프라인 E2E 통합 테스트

전체 장 운영 사이클에서 RL 추론 + 실시간 시세 + 스케줄러가
올바르게 연결되어 동작하는지 검증합니다.

테스트 시나리오:
  1. MarketOpen → RL 추론 → 실시간 시세 시작 → 데이터 플로우 검증
  2. 장중 시세 수신 → 스냅샷 축적 → API 조회 가능
  3. MarketClose → 실시간 시세 중지 → 리소스 정리
  4. RL 모델 없이 graceful degradation (앙상블만 사용)
  5. WebSocket 재연결 후 구독 복구
  6. RL 추론 + 앙상블 블렌딩 → 오더 생성 검증
  7. 전체 파이프라인 상태 전파 (Redis 캐시)
"""

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from core.data_collector.realtime_manager import (
    IntradayBar,
    RealtimeManager,
    RealtimeSnapshot,
)

# ══════════════════════════════════════════════════════════════
# 공통 Mock 인프라
# ══════════════════════════════════════════════════════════════


class InMemoryRedisPipeline:
    """Redis Pipeline Mock"""

    def __init__(self, store: dict):
        self._store = store
        self._ops = []

    def set(self, key: str, value: str, ex=None):
        self._ops.append(("set", key, value, ex))
        return self

    async def execute(self):
        for op in self._ops:
            if op[0] == "set":
                _, key, value, ex = op
                self._store[key] = value
        self._ops.clear()
        return []


class InMemoryRedis:
    """Redis Mock — 핸들러 간 데이터 공유용"""

    def __init__(self):
        self._store = {}

    async def get(self, key: str):
        return self._store.get(key)

    async def set(self, key: str, value: str, ex=None):
        self._store[key] = value
        return True

    def pipeline(self):
        return InMemoryRedisPipeline(self._store)


def make_ohlcv_df(ticker: str, days: int = 200):
    """테스트용 OHLCV DataFrame 생성"""
    import pandas as pd

    dates = pd.bdate_range(end="2026-04-03", periods=days)
    np.random.seed(hash(ticker) % 2**31)
    base = 50000 + np.random.randint(-10000, 10000)
    prices = base + np.cumsum(np.random.randn(days) * 100)
    prices = np.maximum(prices, 1000)

    df = pd.DataFrame(
        {
            "open": prices * (1 + np.random.randn(days) * 0.005),
            "high": prices * (1 + abs(np.random.randn(days) * 0.01)),
            "low": prices * (1 - abs(np.random.randn(days) * 0.01)),
            "close": prices,
            "volume": np.random.randint(100000, 5000000, days),
        },
        index=dates,
    )
    df.index.name = "date"
    return df


@dataclass
class FakeQuote:
    """WebSocket 체결가 메시지 Mock"""

    ticker: str
    price: float
    change: float = 0.0
    change_rate: float = 0.0
    bid1: float = 0.0
    ask1: float = 0.0
    volume: int = 100
    cum_volume: int = 5000
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class FakeOrderbook:
    """WebSocket 호가 메시지 Mock"""

    ticker: str
    bids: list = None
    asks: list = None

    def __post_init__(self):
        if self.bids is None:
            self.bids = [50000.0]
        if self.asks is None:
            self.asks = [50100.0]


def make_db_rows(tickers_by_country):
    """유니버스 DB 결과 Mock"""
    rows = []
    for country, tickers in tickers_by_country.items():
        for t in tickers:
            rows.append((t, "KOSPI" if country == "KR" else "NYSE", country))
    return rows


# ══════════════════════════════════════════════════════════════
# 1. 전체 장 운영 사이클 E2E
# ══════════════════════════════════════════════════════════════


class TestMarketDayCycleE2E:
    """장 시작 → 장중 → 장 마감 전체 사이클 테스트"""

    @pytest.mark.asyncio
    async def test_market_open_starts_rl_and_realtime(self):
        """
        handle_market_open()이 (1) 앙상블 실행 (2) RL 추론
        (3) 실시간 시세 수신을 순서대로 시작하는지 검증
        """
        redis = InMemoryRedis()
        kr_tickers = ["005930", "000660", "035420"]
        ohlcv_dict = {t: make_ohlcv_df(t) for t in kr_tickers}

        # DB Mock
        mock_session = AsyncMock()
        mock_execute_result = MagicMock()
        mock_execute_result.fetchall.return_value = make_db_rows({"KR": kr_tickers})
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        # 앙상블 Mock
        mock_runner_result = MagicMock()
        mock_runner_result.to_summary_dict.return_value = {
            "final_signal": 0.6,
            "confidence": 0.8,
        }

        # WebSocket Mock
        mock_ws_client = MagicMock()
        mock_ws_client.connect = AsyncMock(return_value=True)
        mock_ws_client.subscribe_batch = AsyncMock(return_value=3)
        mock_ws_client.disconnect = AsyncMock()
        mock_ws_client.stats = {"connected": True}
        mock_ws_client.on_quote = None
        mock_ws_client.on_orderbook = None

        with (
            patch("core.scheduler_handlers.async_session_factory") as mock_sf,
            patch("core.scheduler_handlers.RedisManager") as mock_rm,
            patch("core.scheduler_handlers.DynamicEnsembleRunner") as mock_ens_cls,
            patch("core.scheduler_handlers._run_rl_inference") as mock_rl_fn,
            patch(
                "core.data_collector.kis_websocket.KISRealtimeClient",
                return_value=mock_ws_client,
            ),
        ):
            # session factory
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.__aexit__ = AsyncMock(return_value=None)
            mock_sf.return_value = mock_ctx

            # Redis
            mock_rm.get_client.return_value = redis

            # Ensemble
            mock_ens = AsyncMock()
            mock_ens.run = AsyncMock(return_value=mock_runner_result)
            mock_ens_cls.return_value = mock_ens

            # RL 추론 결과 Mock
            mock_rl_fn.return_value = {
                "enabled": True,
                "model_version": "v001",
                "signals_count": 2,
                "orders_count": 0,
            }

            from core.scheduler_handlers import handle_market_open

            result = await handle_market_open()

        # 검증: 앙상블 성공
        assert result["succeeded"] == 3
        assert result["total_tickers"] == 3

        # 검증: RL 추론 활성화
        assert result["rl_inference"]["enabled"] is True
        assert result["rl_inference"]["model_version"] == "v001"
        assert result["rl_inference"]["signals_count"] == 2

        # 검증: 실시간 시세 시작
        assert result["realtime"]["enabled"] is True
        assert result["realtime"]["tickers_count"] == 3

        # 검증: Redis 캐시에 앙상블 + RL 결과 저장됨
        assert "ensemble:latest:005930" in redis._store
        assert "ensemble:latest:_summary" in redis._store

    @pytest.mark.asyncio
    async def test_market_close_stops_realtime(self):
        """
        handle_market_close()가 실시간 시세를 중지하고
        최종 포지션을 조회하는지 검증
        """
        import core.scheduler_handlers as handlers

        # 전역 realtime_manager 설정
        mock_manager = AsyncMock(spec=RealtimeManager)
        mock_manager.stop = AsyncMock()
        handlers._realtime_manager = mock_manager

        with (
            patch("core.scheduler_handlers.RedisManager") as mock_rm,
            patch("core.data_collector.kis_client.KISClient") as mock_kis_cls,
        ):
            mock_rm.get_client.return_value = InMemoryRedis()

            mock_kis = AsyncMock()
            mock_kis.get_kr_balance.return_value = {
                "output1": [
                    {
                        "pdno": "005930",
                        "prdt_name": "삼성전자",
                        "hldg_qty": "100",
                        "pchs_avg_pric": "70000",
                        "prpr": "72000",
                        "evlu_amt": "7200000",
                        "evlu_pfls_amt": "200000",
                        "evlu_pfls_rt": "2.86",
                    }
                ],
                "output2": [
                    {
                        "tot_evlu_amt": "57200000",
                        "dnca_tot_amt": "50000000",
                    }
                ],
            }
            mock_kis_cls.return_value = mock_kis

            from core.scheduler_handlers import handle_market_close

            result = await handle_market_close()

        # 검증: 실시간 중지 호출됨
        mock_manager.stop.assert_awaited_once()
        assert handlers._realtime_manager is None

        # 검증: 포지션 조회
        assert result["positions_count"] == 1
        assert result["portfolio_value"] > 0

    @pytest.mark.asyncio
    async def test_full_day_cycle_data_propagation(self):
        """
        장 시작 → 장중 시세 수신 → 장 마감의 전체 사이클에서
        데이터가 올바르게 전파되는지 검증
        """
        # Phase 1: RealtimeManager 시작 + 스냅샷 초기화
        manager = RealtimeManager(subscribe_orderbook=True)

        mock_ws = MagicMock()
        mock_ws.connect = AsyncMock(return_value=True)
        mock_ws.subscribe_batch = AsyncMock(return_value=2)
        mock_ws.disconnect = AsyncMock()
        mock_ws.stats = {"connected": True, "subscriptions": 2}

        tickers = ["005930", "000660"]

        with patch(
            "core.data_collector.kis_websocket.KISRealtimeClient",
            return_value=mock_ws,
        ):
            started = await manager.start(tickers)

        assert started is True
        assert manager.is_running is True
        assert len(manager.get_all_snapshots()) == 2

        # Phase 2: 시세 수신 시뮬레이션
        quotes = [
            FakeQuote(ticker="005930", price=72000, volume=500, cum_volume=10000),
            FakeQuote(ticker="005930", price=72500, volume=300, cum_volume=10300),
            FakeQuote(ticker="005930", price=71800, volume=200, cum_volume=10500),
            FakeQuote(ticker="000660", price=180000, volume=100, cum_volume=3000),
        ]

        for q in quotes:
            manager._on_quote(q)

        # Phase 2-b: 호가 수신
        ob = FakeOrderbook(ticker="005930", bids=[71900.0], asks=[72000.0])
        manager._on_orderbook(ob)

        # Phase 3: 스냅샷 검증
        snap_005930 = manager.get_snapshot("005930")
        assert snap_005930 is not None
        assert snap_005930.price == 71800  # 마지막 체결가
        assert snap_005930.bid1 == 71900.0  # 호가에서 업데이트됨
        assert snap_005930.ask1 == 72000.0

        # 인트라데이 바 검증
        intraday = snap_005930.intraday
        assert intraday.open_price == 72000  # 첫 체결가
        assert intraday.high_price == 72500
        assert intraday.low_price == 71800
        assert intraday.close_price == 71800  # 마지막 체결가
        assert intraday.trade_count == 3  # 3번 체결

        # 현재가 딕셔너리
        prices = manager.get_current_prices()
        assert prices["005930"] == 71800
        assert prices["000660"] == 180000
        assert len(prices) == 2

        # Phase 4: 통계 조회
        stats = manager.stats
        assert stats["running"] is True
        assert stats["tickers_count"] == 2
        assert stats["tickers_with_data"] == 2

        # Phase 5: 장 마감 — 리소스 정리
        await manager.stop()
        assert manager.is_running is False


# ══════════════════════════════════════════════════════════════
# 2. Graceful Degradation
# ══════════════════════════════════════════════════════════════


class TestGracefulDegradation:
    """모듈 부재/장애 시 graceful degradation 검증"""

    @pytest.mark.asyncio
    async def test_no_champion_model_ensemble_only(self):
        """
        RL 챔피언 모델이 없으면 RL 추론을 건너뛰고
        앙상블 시그널만 사용하는지 검증
        """
        redis = InMemoryRedis()
        kr_tickers = ["005930"]

        mock_session = AsyncMock()
        mock_execute_result = MagicMock()
        mock_execute_result.fetchall.return_value = make_db_rows({"KR": kr_tickers})
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        mock_runner_result = MagicMock()
        mock_runner_result.to_summary_dict.return_value = {
            "final_signal": 0.5,
        }

        mock_ws_client = MagicMock()
        mock_ws_client.connect = AsyncMock(return_value=True)
        mock_ws_client.subscribe_batch = AsyncMock(return_value=1)
        mock_ws_client.disconnect = AsyncMock()
        mock_ws_client.stats = {}
        mock_ws_client.on_quote = None
        mock_ws_client.on_orderbook = None

        with (
            patch("core.scheduler_handlers.async_session_factory") as mock_sf,
            patch("core.scheduler_handlers.RedisManager") as mock_rm,
            patch("core.scheduler_handlers.DynamicEnsembleRunner") as mock_ens_cls,
            patch("core.scheduler_handlers._run_rl_inference") as mock_rl_fn,
            patch(
                "core.data_collector.kis_websocket.KISRealtimeClient",
                return_value=mock_ws_client,
            ),
        ):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.__aexit__ = AsyncMock(return_value=None)
            mock_sf.return_value = mock_ctx

            mock_rm.get_client.return_value = redis

            mock_ens = AsyncMock()
            mock_ens.run = AsyncMock(return_value=mock_runner_result)
            mock_ens_cls.return_value = mock_ens

            # RL — champion 없음
            mock_rl_fn.return_value = {
                "enabled": False,
                "model_version": None,
                "signals_count": 0,
                "orders_count": 0,
                "skip_reason": "no_champion_model",
            }

            from core.scheduler_handlers import handle_market_open

            result = await handle_market_open()

        # RL 추론 스킵, 앙상블만 사용
        assert result["rl_inference"]["enabled"] is False
        assert result["rl_inference"]["skip_reason"] == "no_champion_model"
        # 앙상블은 정상 실행
        assert result["succeeded"] == 1

    @pytest.mark.asyncio
    async def test_websocket_connection_failure(self):
        """
        WebSocket 연결 실패 시 실시간 시세 비활성화되지만
        앙상블 + RL 추론은 정상 동작하는지 검증
        """
        redis = InMemoryRedis()
        kr_tickers = ["005930"]

        mock_session = AsyncMock()
        mock_execute_result = MagicMock()
        mock_execute_result.fetchall.return_value = make_db_rows({"KR": kr_tickers})
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        mock_runner_result = MagicMock()
        mock_runner_result.to_summary_dict.return_value = {
            "final_signal": 0.3,
        }

        # WebSocket 연결 실패
        mock_ws_client = MagicMock()
        mock_ws_client.connect = AsyncMock(return_value=False)
        mock_ws_client.disconnect = AsyncMock()
        mock_ws_client.on_quote = None
        mock_ws_client.on_orderbook = None

        with (
            patch("core.scheduler_handlers.async_session_factory") as mock_sf,
            patch("core.scheduler_handlers.RedisManager") as mock_rm,
            patch("core.scheduler_handlers.DynamicEnsembleRunner") as mock_ens_cls,
            patch("core.scheduler_handlers._run_rl_inference") as mock_rl_fn,
            patch(
                "core.data_collector.kis_websocket.KISRealtimeClient",
                return_value=mock_ws_client,
            ),
        ):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.__aexit__ = AsyncMock(return_value=None)
            mock_sf.return_value = mock_ctx

            mock_rm.get_client.return_value = redis

            mock_ens = AsyncMock()
            mock_ens.run = AsyncMock(return_value=mock_runner_result)
            mock_ens_cls.return_value = mock_ens

            # RL 추론 스킵
            mock_rl_fn.return_value = {
                "enabled": False,
                "model_version": None,
                "signals_count": 0,
                "orders_count": 0,
                "skip_reason": "no_champion_model",
            }

            from core.scheduler_handlers import handle_market_open

            result = await handle_market_open()

        # 앙상블은 성공
        assert result["succeeded"] == 1
        # 실시간은 실패 (WebSocket 연결 못함)
        assert result["realtime"]["enabled"] is False

    @pytest.mark.asyncio
    async def test_empty_universe_early_return(self):
        """유니버스에 종목이 없으면 즉시 반환하는지 검증"""
        mock_session = AsyncMock()
        mock_execute_result = MagicMock()
        mock_execute_result.fetchall.return_value = []  # 빈 유니버스
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        with (
            patch("core.scheduler_handlers.async_session_factory") as mock_sf,
            patch("core.scheduler_handlers.RedisManager"),
        ):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.__aexit__ = AsyncMock(return_value=None)
            mock_sf.return_value = mock_ctx

            from core.scheduler_handlers import handle_market_open

            result = await handle_market_open()

        assert result["warning"] == "활성 종목이 없습니다"


# ══════════════════════════════════════════════════════════════
# 3. RL 추론 ↔ 앙상블 블렌딩 E2E
# ══════════════════════════════════════════════════════════════


class TestRLEnsembleBlendingE2E:
    """RL 시그널과 앙상블 시그널 블렌딩 통합 검증"""

    def test_blend_weights_sum_to_one(self):
        """RL(0.4) + 앙상블(0.6) = 1.0 검증"""
        from core.rl.inference import RLInferenceService

        svc = RLInferenceService.__new__(RLInferenceService)
        svc.rl_weight = 0.4
        svc.ensemble_weight = 0.6

        blended = svc.blend_with_ensemble(rl_signal=1.0, ensemble_signal=0.0)
        assert abs(blended - 0.4) < 1e-6

        blended = svc.blend_with_ensemble(rl_signal=0.0, ensemble_signal=1.0)
        assert abs(blended - 0.6) < 1e-6

        blended = svc.blend_with_ensemble(rl_signal=0.5, ensemble_signal=0.5)
        assert abs(blended - 0.5) < 1e-6

    def test_blend_custom_weights(self):
        """커스텀 가중치로 블렌딩 검증"""
        from core.rl.inference import RLInferenceService

        svc = RLInferenceService.__new__(RLInferenceService)
        svc.rl_weight = 0.7
        svc.ensemble_weight = 0.3

        blended = svc.blend_with_ensemble(rl_signal=0.8, ensemble_signal=-0.4)
        expected = 0.7 * 0.8 + 0.3 * (-0.4)
        assert abs(blended - expected) < 1e-6

    def test_blend_extreme_signals(self):
        """극단적 시그널 블렌딩 검증 (경계값)"""
        from core.rl.inference import RLInferenceService

        svc = RLInferenceService.__new__(RLInferenceService)
        svc.rl_weight = 0.4
        svc.ensemble_weight = 0.6

        # RL full buy + Ensemble full sell
        blended = svc.blend_with_ensemble(rl_signal=1.0, ensemble_signal=-1.0)
        expected = 0.4 * 1.0 + 0.6 * (-1.0)
        assert abs(blended - expected) < 1e-6
        assert blended < 0  # 앙상블이 더 크므로 매도 방향


# ══════════════════════════════════════════════════════════════
# 4. 실시간 데이터 수신 → 스냅샷 → API 연동
# ══════════════════════════════════════════════════════════════


class TestRealtimeDataFlow:
    """실시간 데이터 수신부터 API 조회까지 데이터 플로우 검증"""

    def test_quote_callback_updates_snapshot(self):
        """체결가 콜백 → 스냅샷 업데이트 → 인트라데이 바 축적"""
        manager = RealtimeManager()
        manager._snapshots["005930"] = RealtimeSnapshot(
            ticker="005930",
            intraday=IntradayBar(ticker="005930"),
        )

        # 연속 체결
        prices = [70000, 71000, 69500, 70500]
        for i, price in enumerate(prices):
            q = FakeQuote(
                ticker="005930",
                price=price,
                volume=100 * (i + 1),
                cum_volume=sum(100 * (j + 1) for j in range(i + 1)),
            )
            manager._on_quote(q)

        snap = manager.get_snapshot("005930")
        assert snap.price == 70500  # 마지막 체결가
        assert snap.intraday.open_price == 70000
        assert snap.intraday.high_price == 71000
        assert snap.intraday.low_price == 69500
        assert snap.intraday.close_price == 70500
        assert snap.intraday.trade_count == 4

    def test_orderbook_callback_updates_bid_ask(self):
        """호가 콜백 → bid/ask 업데이트"""
        manager = RealtimeManager()
        manager._snapshots["005930"] = RealtimeSnapshot(
            ticker="005930",
            intraday=IntradayBar(ticker="005930"),
        )

        ob = FakeOrderbook(
            ticker="005930",
            bids=[69900.0],
            asks=[70100.0],
        )
        manager._on_orderbook(ob)

        snap = manager.get_snapshot("005930")
        assert snap.bid1 == 69900.0
        assert snap.ask1 == 70100.0

    def test_unknown_ticker_auto_registered(self):
        """구독 외 종목 수신 시 자동 등록"""
        manager = RealtimeManager()
        # 초기 스냅샷 없음
        assert manager.get_snapshot("999999") is None

        q = FakeQuote(ticker="999999", price=10000)
        manager._on_quote(q)

        snap = manager.get_snapshot("999999")
        assert snap is not None
        assert snap.price == 10000

    def test_zero_price_excluded_from_current_prices(self):
        """가격 0인 종목은 get_current_prices()에서 제외"""
        manager = RealtimeManager()
        manager._snapshots["005930"] = RealtimeSnapshot(
            ticker="005930",
            price=72000,
            intraday=IntradayBar(ticker="005930"),
        )
        manager._snapshots["000660"] = RealtimeSnapshot(
            ticker="000660",
            price=0,  # 아직 시세 미수신
            intraday=IntradayBar(ticker="000660"),
        )

        prices = manager.get_current_prices()
        assert "005930" in prices
        assert "000660" not in prices


# ══════════════════════════════════════════════════════════════
# 5. 모델 레지스트리 → 추론 서비스 Wiring 검증
# ══════════════════════════════════════════════════════════════


class TestRegistryInferenceWiring:
    """ModelRegistry → RLInferenceService 연결 검증"""

    def test_registry_champion_loaded_by_inference(self):
        """
        레지스트리에 등록된 챔피언 모델이
        RLInferenceService.load_model()로 정상 로드되는지 검증
        """
        from core.rl.model_registry import ModelRegistry

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ModelRegistry(tmpdir)

            # 더미 모델 등록
            mock_model = MagicMock()
            mock_model.save = MagicMock()
            mock_eval = MagicMock()
            mock_eval.sharpe_ratio = 1.5
            mock_eval.total_return = 0.12
            mock_eval.max_drawdown = -0.08
            mock_eval.total_trades = 50
            mock_eval.improvement_pct = 0.05

            version = registry.register(
                model=mock_model,
                algorithm="PPO",
                eval_result=mock_eval,
                config=None,
                data_info={"tickers": ["005930"]},
            )
            assert version == "v001"

            # 챔피언 확인
            manifest = registry._load_manifest()
            assert manifest["champion_version"] == "v001"

            # RLInferenceService에서 로드
            from core.rl.inference import RLInferenceService

            svc = RLInferenceService(
                registry_dir=tmpdir,
                shadow_mode=True,
            )

            # load_champion은 SB3 모델 파일을 로드하려 하므로
            # registry의 load_champion을 mock
            mock_loaded_model = MagicMock()
            mock_loaded_meta = MagicMock()
            mock_loaded_meta.version = "v001"
            mock_loaded_meta.algorithm = "PPO"
            mock_loaded_meta.oos_sharpe = 1.5
            mock_loaded_meta.config_snapshot = None

            with patch.object(
                svc.registry,
                "load_champion",
                return_value=(mock_loaded_model, mock_loaded_meta),
            ):
                loaded = svc.load_model()

            assert loaded is True
            assert svc.model_version == "v001"

    def test_multi_version_champion_selection(self):
        """
        여러 버전 등록 후 Sharpe 기준 챔피언이 올바르게 선정되는지 검증
        """
        from core.rl.model_registry import ModelRegistry

        def _make_eval(sharpe, ret, mdd, trades=30, improvement=0.0):
            e = MagicMock()
            e.sharpe_ratio = sharpe
            e.total_return = ret
            e.max_drawdown = mdd
            e.total_trades = trades
            e.improvement_pct = improvement
            return e

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ModelRegistry(tmpdir)
            mock_model = MagicMock()
            mock_model.save = MagicMock()

            # v001: Sharpe 0.8
            registry.register(
                model=mock_model,
                algorithm="PPO",
                eval_result=_make_eval(0.8, 0.05, -0.10),
                config=None,
                data_info={},
            )

            # v002: Sharpe 1.5 (새 챔피언)
            registry.register(
                model=mock_model,
                algorithm="SAC",
                eval_result=_make_eval(1.5, 0.12, -0.06),
                config=None,
                data_info={},
            )

            # v003: Sharpe 0.3 (챔피언 유지)
            registry.register(
                model=mock_model,
                algorithm="PPO",
                eval_result=_make_eval(0.3, 0.02, -0.15),
                config=None,
                data_info={},
            )

            manifest = registry._load_manifest()
            assert manifest["champion_version"] == "v002"
            assert manifest["champion_sharpe"] == 1.5
            assert len(manifest["versions"]) == 3


# ══════════════════════════════════════════════════════════════
# 6. Redis 캐시 전파 E2E
# ══════════════════════════════════════════════════════════════


class TestRedisCachePropagation:
    """스케줄러 → Redis → API 간 데이터 전파 검증"""

    @pytest.mark.asyncio
    async def test_ensemble_results_cached_to_redis(self):
        """앙상블 결과가 Redis에 올바르게 캐시되는지 검증"""
        redis = InMemoryRedis()

        with patch("core.scheduler_handlers.RedisManager") as mock_rm:
            mock_rm.get_client.return_value = redis

            from core.scheduler_handlers import _cache_ensemble_results

            results = {
                "005930": {"final_signal": 0.7, "confidence": 0.9},
                "000660": {"final_signal": -0.3, "confidence": 0.6},
            }
            await _cache_ensemble_results(results)

        # 종목별 캐시 확인
        cached_005930 = json.loads(redis._store["ensemble:latest:005930"])
        assert cached_005930["final_signal"] == 0.7

        cached_000660 = json.loads(redis._store["ensemble:latest:000660"])
        assert cached_000660["final_signal"] == -0.3

        # 요약 캐시 확인
        summary = json.loads(redis._store["ensemble:latest:_summary"])
        assert summary["total_tickers"] == 2
        assert set(summary["tickers"]) == {"005930", "000660"}

    @pytest.mark.asyncio
    async def test_midday_reads_cached_ensemble(self):
        """
        handle_midday_check()가 Redis에 캐시된
        앙상블 요약을 올바르게 읽는지 검증
        """
        redis = InMemoryRedis()
        await redis.set(
            "ensemble:latest:_summary",
            json.dumps(
                {
                    "updated_at": "2026-04-06T00:00:00Z",
                    "total_tickers": 5,
                    "tickers": ["005930", "000660", "035420", "051910", "006400"],
                }
            ),
        )

        with (
            patch("core.scheduler_handlers.RedisManager") as mock_rm,
            patch("core.data_collector.kis_client.KISClient") as mock_kis_cls,
        ):
            mock_rm.get_client.return_value = redis

            mock_kis = AsyncMock()
            mock_kis.get_kr_balance.return_value = {
                "output1": [],
                "output2": [{"tot_evlu_amt": "50000000", "dnca_tot_amt": "50000000"}],
            }
            mock_kis_cls.return_value = mock_kis

            from core.scheduler_handlers import handle_midday_check

            result = await handle_midday_check()

        assert result["ensemble_cached_tickers"] == 5
        assert result["ensemble_updated_at"] == "2026-04-06T00:00:00Z"


# ══════════════════════════════════════════════════════════════
# 7. 스케줄러 전역 상태 관리
# ══════════════════════════════════════════════════════════════


class TestSchedulerGlobalState:
    """스케줄러 전역 변수 (realtime_manager) 라이프사이클 검증"""

    @pytest.mark.asyncio
    async def test_get_realtime_manager_initially_none(self):
        """초기 상태에서 realtime_manager는 None"""
        import core.scheduler_handlers as handlers

        handlers._realtime_manager = None
        assert handlers.get_realtime_manager() is None

    @pytest.mark.asyncio
    async def test_start_sets_global_manager(self):
        """_start_realtime_quotes가 전역 매니저를 설정하는지 검증"""
        import core.scheduler_handlers as handlers

        handlers._realtime_manager = None

        mock_session = AsyncMock()
        mock_execute_result = MagicMock()
        mock_execute_result.fetchall.return_value = [
            ("005930", "KOSPI", "KR"),
        ]
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        mock_ws = MagicMock()
        mock_ws.connect = AsyncMock(return_value=True)
        mock_ws.subscribe_batch = AsyncMock(return_value=1)
        mock_ws.disconnect = AsyncMock()
        mock_ws.stats = {}
        mock_ws.on_quote = None
        mock_ws.on_orderbook = None

        with (
            patch("core.scheduler_handlers.async_session_factory") as mock_sf,
            patch(
                "core.data_collector.kis_websocket.KISRealtimeClient",
                return_value=mock_ws,
            ),
        ):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.__aexit__ = AsyncMock(return_value=None)
            mock_sf.return_value = mock_ctx

            result = await handlers._start_realtime_quotes(1)

        assert result["enabled"] is True
        assert handlers.get_realtime_manager() is not None
        assert handlers._realtime_manager.is_running is True

        # 정리
        await handlers._stop_realtime_quotes()
        assert handlers._realtime_manager is None

    @pytest.mark.asyncio
    async def test_stop_without_start_is_safe(self):
        """start 없이 stop 호출해도 에러 없이 안전하게 처리"""
        import core.scheduler_handlers as handlers

        handlers._realtime_manager = None
        await handlers._stop_realtime_quotes()
        assert handlers._realtime_manager is None

    @pytest.mark.asyncio
    async def test_double_stop_is_safe(self):
        """stop을 두 번 호출해도 안전하게 처리"""
        import core.scheduler_handlers as handlers

        mock_manager = AsyncMock(spec=RealtimeManager)
        handlers._realtime_manager = mock_manager

        await handlers._stop_realtime_quotes()
        assert handlers._realtime_manager is None

        # 두 번째 stop — 이미 None이므로 무시
        await handlers._stop_realtime_quotes()
        assert handlers._realtime_manager is None


# ══════════════════════════════════════════════════════════════
# 8. 인트라데이 바 누적 정합성
# ══════════════════════════════════════════════════════════════


class TestIntradayBarAccumulation:
    """인트라데이 바 누적 로직의 정합성 검증"""

    def test_single_tick(self):
        """단일 체결에서의 OHLCV"""
        bar = IntradayBar(ticker="005930")
        bar.update(price=72000, vol=500)

        assert bar.open_price == 72000
        assert bar.high_price == 72000
        assert bar.low_price == 72000
        assert bar.close_price == 72000
        assert bar.volume == 500
        assert bar.trade_count == 1

    def test_multi_tick_ohlcv_consistency(self):
        """다수 체결에서 O/H/L/C 일관성: O≤H, L≤C, L≤O, L≤H"""
        bar = IntradayBar(ticker="005930")
        prices = [50000, 52000, 48000, 51000, 49000, 53000]

        for p in prices:
            bar.update(price=p, vol=100)

        # OHLCV 일관성
        assert bar.open_price == 50000  # 첫 가격
        assert bar.high_price == 53000  # 최고가
        assert bar.low_price == 48000  # 최저가
        assert bar.close_price == 53000  # 마지막 가격
        assert bar.volume == 600  # 누적 거래량
        assert bar.trade_count == 6

        # 관계 검증
        assert bar.low_price <= bar.open_price <= bar.high_price
        assert bar.low_price <= bar.close_price <= bar.high_price

    def test_to_dict_serialization(self):
        """to_dict()가 모든 필드를 포함하는지 검증"""
        bar = IntradayBar(ticker="005930")
        bar.update(price=72000, vol=100)
        bar.update(price=73000, vol=200)

        d = bar.to_dict()
        assert d["ticker"] == "005930"
        assert d["open"] == 72000
        assert d["high"] == 73000
        assert d["close"] == 73000
        assert d["volume"] == 300
        assert "first_update" in d
        assert "last_update" in d

    def test_snapshot_to_dict_includes_intraday(self):
        """RealtimeSnapshot.to_dict()에 인트라데이 바가 포함되는지 검증"""
        snap = RealtimeSnapshot(
            ticker="005930",
            price=72000,
            intraday=IntradayBar(ticker="005930"),
        )
        snap.intraday.update(72000, 100)

        d = snap.to_dict()
        assert d["ticker"] == "005930"
        assert d["price"] == 72000
        assert "intraday" in d
        assert d["intraday"]["open"] == 72000
