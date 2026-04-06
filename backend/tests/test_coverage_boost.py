"""
커버리지 부스트 테스트

대상 모듈 (커버리지 목표):
  - core/strategy_ensemble/engine.py   67% → 85%+
  - core/pipeline.py                   65% → 85%+
  - core/rl/data_loader.py             68% → 85%+
  - core/scheduler_handlers.py         80% → 85%+

모든 외부 의존성 (DB, Redis, 외부 API)은 Mock으로 대체합니다.
"""

import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from config.constants import RiskProfile, StrategyType
from core.strategy_ensemble.engine import (
    EnsembleSignal,
    StrategyEnsembleEngine,
    StrategySignalInput,
)

# ══════════════════════════════════════════════════════════════
# 1. StrategyEnsembleEngine 커버리지 부스트
# ══════════════════════════════════════════════════════════════


class TestEnsembleSignalDetailed:
    """EnsembleSignal 데이터 구조 — 미커버 메서드 테스트"""

    def test_to_detailed_dict(self):
        """to_detailed_dict()가 regime, threshold, action 포함하는지 검증"""
        sig = EnsembleSignal(
            ticker="005930",
            final_signal=0.5,
            final_confidence=0.85,
            component_signals={"FACTOR": 0.7},
            weights_used={"FACTOR": 0.25},
            risk_profile="BALANCED",
            regime="BULL",
            buy_threshold=0.25,
            sell_threshold=0.25,
            raw_confidence=0.80,
        )
        d = sig.to_detailed_dict()

        assert d["regime"] == "BULL"
        assert d["buy_threshold"] == 0.25
        assert d["sell_threshold"] == 0.25
        assert d["raw_confidence"] == 0.80
        assert d["action"] == "BUY"
        assert d["ticker"] == "005930"
        assert d["final_signal"] == 0.5

    def test_to_detailed_dict_sell_action(self):
        """매도 시그널의 to_detailed_dict 검증"""
        sig = EnsembleSignal(
            ticker="000660",
            final_signal=-0.6,
            final_confidence=0.75,
            sell_threshold=0.3,
        )
        d = sig.to_detailed_dict()
        assert d["action"] == "SELL"

    def test_to_detailed_dict_hold_action(self):
        """홀드 시그널의 to_detailed_dict 검증"""
        sig = EnsembleSignal(
            ticker="035420",
            final_signal=0.1,
            final_confidence=0.5,
            buy_threshold=0.3,
            sell_threshold=0.3,
        )
        d = sig.to_detailed_dict()
        assert d["action"] == "HOLD"


class TestEnsembleEngineWeightPaths:
    """StrategyEnsembleEngine — get_weights() 경로별 커버리지"""

    @pytest.mark.asyncio
    async def test_get_weights_from_redis_cache(self):
        """Redis 캐시에서 가중치 로드하는 경로"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)
        cached = {"FACTOR": 0.3, "SENTIMENT": 0.7}

        with patch.object(engine, "_get_cached_weights", return_value=cached):
            weights = await engine.get_weights()

        assert weights == cached

    @pytest.mark.asyncio
    async def test_get_weights_from_db(self):
        """Redis 미스 → DB에서 가중치 로드하는 경로"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)
        db_weights = {"FACTOR": 0.4, "SENTIMENT": 0.6}

        with (
            patch.object(engine, "_get_cached_weights", return_value=None),
            patch.object(engine, "_load_weights_from_db", return_value=db_weights),
            patch.object(engine, "_cache_weights", new_callable=AsyncMock),
        ):
            weights = await engine.get_weights()

        assert weights == db_weights

    @pytest.mark.asyncio
    async def test_risk_profile_property(self):
        """risk_profile 프로퍼티 접근 검증"""
        engine = StrategyEnsembleEngine(RiskProfile.AGGRESSIVE)
        assert engine.risk_profile == RiskProfile.AGGRESSIVE


class TestEnsembleEngineRegimeDetection:
    """레짐 감지 통합 경로 커버리지"""

    @pytest.mark.asyncio
    async def test_generate_signal_with_ohlcv(self):
        """OHLCV 데이터가 충분할 때 레짐 감지 경로"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)
        engine._weights = {
            "FACTOR": 0.5,
            "SENTIMENT": 0.5,
        }

        # 60일 이상의 OHLCV 생성
        dates = pd.date_range("2025-01-01", periods=100, freq="B")
        ohlcv = pd.DataFrame(
            {
                "open": np.random.uniform(50000, 60000, 100),
                "high": np.random.uniform(55000, 65000, 100),
                "low": np.random.uniform(45000, 55000, 100),
                "close": np.random.uniform(50000, 60000, 100),
                "volume": np.random.randint(100000, 1000000, 100),
            },
            index=dates,
        )

        signals = [
            StrategySignalInput(strategy="FACTOR", value=0.6, confidence=0.8, reason="test"),
            StrategySignalInput(
                strategy="SENTIMENT",
                value=0.3,
                confidence=0.7,
                reason="test",
            ),
        ]

        with patch.object(engine, "_store_signal", new_callable=AsyncMock):
            result = await engine.generate_ensemble_signal("005930", signals, ohlcv=ohlcv)

        assert result.regime in (
            "BULL",
            "BEAR",
            "SIDEWAYS",
            "HIGH_VOLATILITY",
        )
        assert result.ticker == "005930"

    @pytest.mark.asyncio
    async def test_generate_signal_no_matching_strategies(self):
        """시그널의 전략 키가 가중치에 없을 때 (total_weight == 0)"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)
        engine._weights = {
            "FACTOR": 0.5,
            "SENTIMENT": 0.5,
        }

        # 가중치에 없는 전략만 전달
        signals = [
            StrategySignalInput(
                strategy="UNKNOWN_STRATEGY",
                value=0.9,
                confidence=0.9,
                reason="no match",
            ),
        ]

        with patch.object(engine, "_store_signal", new_callable=AsyncMock):
            result = await engine.generate_ensemble_signal("005930", signals)

        assert result.final_signal == 0.0
        assert result.raw_confidence == 0.0


class TestEnsembleEngineRecalibrateEdge:
    """가중치 재계산 엣지 케이스"""

    @pytest.mark.asyncio
    async def test_recalibrate_all_zero_sharpe(self):
        """모든 Sharpe가 0 이하일 때 동일 가중 폴백"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)
        engine._weights = {
            "FACTOR": 0.25,
            "TREND_FOLLOWING": 0.25,
            "SENTIMENT": 0.25,
            "RISK_PARITY": 0.25,
        }

        performances = {
            "FACTOR": -0.5,
            "TREND_FOLLOWING": -1.0,
            "SENTIMENT": 0.0,
            "RISK_PARITY": -0.3,
        }

        with (
            patch.object(engine, "_save_weights_to_db", new_callable=AsyncMock),
            patch.object(engine, "_cache_weights", new_callable=AsyncMock),
            patch.object(engine, "_log_weight_update", new_callable=AsyncMock),
        ):
            new_weights = await engine.recalibrate_weights(performances, method="sharpe")

        # 모두 0 이하 → 동일 가중
        active = [v for v in new_weights.values() if v > 0]
        assert len(active) == 4
        assert abs(max(active) - min(active)) < 0.01


class TestEnsembleInternalMethods:
    """Redis/DB 내부 메서드 — graceful failure 커버리지"""

    @pytest.mark.asyncio
    async def test_get_cached_weights_redis_success(self):
        """Redis에서 가중치 캐시 성공"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=json.dumps({"FACTOR": 0.5, "SENTIMENT": 0.5}))

        with patch("core.strategy_ensemble.engine.RedisManager") as mock_rm:
            mock_rm.get_client.return_value = mock_redis
            result = await engine._get_cached_weights()

        assert result == {"FACTOR": 0.5, "SENTIMENT": 0.5}

    @pytest.mark.asyncio
    async def test_get_cached_weights_redis_failure(self):
        """Redis 연결 실패 시 None 반환"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)

        with patch("core.strategy_ensemble.engine.RedisManager") as mock_rm:
            mock_rm.get_client.side_effect = Exception("Redis down")
            result = await engine._get_cached_weights()

        assert result is None

    @pytest.mark.asyncio
    async def test_get_cached_weights_no_data(self):
        """Redis 캐시 미스 (데이터 없음)"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)

        with patch("core.strategy_ensemble.engine.RedisManager") as mock_rm:
            mock_rm.get_client.return_value = mock_redis
            result = await engine._get_cached_weights()

        assert result is None

    @pytest.mark.asyncio
    async def test_cache_weights_success(self):
        """Redis에 가중치 캐시 저장 성공"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)
        mock_redis = AsyncMock()

        with patch("core.strategy_ensemble.engine.RedisManager") as mock_rm:
            mock_rm.get_client.return_value = mock_redis
            await engine._cache_weights({"FACTOR": 0.5})

        mock_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_weights_failure(self):
        """Redis 캐시 저장 실패 시 조용히 무시"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)

        with patch("core.strategy_ensemble.engine.RedisManager") as mock_rm:
            mock_rm.get_client.side_effect = Exception("Redis down")
            # 예외 없이 통과해야 함
            await engine._cache_weights({"FACTOR": 0.5})

    @pytest.mark.asyncio
    async def test_load_weights_from_db_failure(self):
        """DB 가중치 로드 실패 시 None 반환"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)

        # lazy import이므로 db.database 모듈 자체를 패치
        with patch.dict(
            "sys.modules",
            {"db.database": MagicMock(async_session_factory=MagicMock(side_effect=Exception("DB down")))},
        ):
            result = await engine._load_weights_from_db()

        assert result is None

    @pytest.mark.asyncio
    async def test_save_weights_to_db_failure(self):
        """DB 가중치 저장 실패 시 조용히 무시"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)

        with patch.dict(
            "sys.modules",
            {"db.database": MagicMock(async_session_factory=MagicMock(side_effect=Exception("DB down")))},
        ):
            # 예외 없이 통과해야 함
            await engine._save_weights_to_db({"FACTOR": 0.5})

    @pytest.mark.asyncio
    async def test_store_signal_failure(self):
        """시그널 저장 실패 시 조용히 무시"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)

        sig = EnsembleSignal(
            ticker="005930",
            final_signal=0.5,
            final_confidence=0.8,
        )

        with patch.dict(
            "sys.modules",
            {"db.database": MagicMock(async_session_factory=MagicMock(side_effect=Exception("DB down")))},
        ):
            await engine._store_signal(sig)

    @pytest.mark.asyncio
    async def test_log_weight_update_failure(self):
        """가중치 업데이트 로그 실패 시 조용히 무시"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)

        with patch.dict(
            "sys.modules",
            {"db.database": MagicMock(async_session_factory=MagicMock(side_effect=Exception("DB down")))},
        ):
            await engine._log_weight_update(
                old_weights={"FACTOR": 0.3},
                new_weights={"FACTOR": 0.5},
                method="sharpe",
                performances={"FACTOR": 1.2},
            )

    @pytest.mark.asyncio
    async def test_load_weights_from_db_success(self):
        """DB에서 가중치 로드 성공"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("FACTOR", 0.4),
            ("SENTIMENT", 0.6),
        ]
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_db_mod = MagicMock()
        mock_db_mod.async_session_factory = MagicMock(return_value=mock_ctx)

        with patch.dict("sys.modules", {"db.database": mock_db_mod}):
            result = await engine._load_weights_from_db()

        assert result == {"FACTOR": 0.4, "SENTIMENT": 0.6}

    @pytest.mark.asyncio
    async def test_save_weights_to_db_success(self):
        """DB에 가중치 저장 성공"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)

        mock_session = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_db_mod = MagicMock()
        mock_db_mod.async_session_factory = MagicMock(return_value=mock_ctx)

        with patch.dict("sys.modules", {"db.database": mock_db_mod}):
            await engine._save_weights_to_db({"FACTOR": 0.4, "SENTIMENT": 0.6})

        assert mock_session.execute.call_count == 2
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_signal_success(self):
        """시그널 DB 저장 성공"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)
        sig = EnsembleSignal(
            ticker="005930",
            final_signal=0.5,
            final_confidence=0.8,
            component_signals={"FACTOR": 0.6},
            weights_used={"FACTOR": 0.5},
            risk_profile="BALANCED",
        )

        mock_session = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_db_mod = MagicMock()
        mock_db_mod.async_session_factory = MagicMock(return_value=mock_ctx)

        with patch.dict("sys.modules", {"db.database": mock_db_mod}):
            await engine._store_signal(sig)

        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_log_weight_update_success(self):
        """가중치 업데이트 로그 DB 저장 성공"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)

        mock_session = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_db_mod = MagicMock()
        mock_db_mod.async_session_factory = MagicMock(return_value=mock_ctx)

        with patch.dict("sys.modules", {"db.database": mock_db_mod}):
            await engine._log_weight_update(
                old_weights={"FACTOR": 0.3},
                new_weights={"FACTOR": 0.5},
                method="sharpe",
                performances={"FACTOR": 1.2},
            )

        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()


# ══════════════════════════════════════════════════════════════
# 2. Pipeline 커버리지 부스트
# ══════════════════════════════════════════════════════════════


class TestPipelineNewsAndAnalysis:
    """Pipeline — 뉴스 수집, 섹터/매크로 분석, 동적 앙상블 경로"""

    @pytest.mark.asyncio
    async def test_run_news_collection(self):
        """뉴스 수집 위임 호출 검증"""
        from core.pipeline import InvestmentDecisionPipeline

        with (
            patch("core.pipeline.NewsCollectorService") as mock_news_cls,
            patch("core.pipeline.SentimentAnalyzer"),
            patch("core.pipeline.OpinionGenerator"),
            patch("core.pipeline.StrategyEnsembleEngine"),
        ):
            mock_news = AsyncMock()
            mock_news.collect_and_store = AsyncMock(
                return_value={
                    "total_collected": 50,
                    "new_stored": 30,
                    "duplicates_skipped": 20,
                }
            )
            mock_news_cls.return_value = mock_news

            pipeline = InvestmentDecisionPipeline()
            result = await pipeline.run_news_collection()

        assert result["total_collected"] == 50
        assert result["new_stored"] == 30

    @pytest.mark.asyncio
    async def test_run_macro_analysis(self):
        """매크로 분석 경로 커버리지"""
        from core.pipeline import InvestmentDecisionPipeline

        with (
            patch("core.pipeline.NewsCollectorService") as mock_news_cls,
            patch("core.pipeline.SentimentAnalyzer"),
            patch("core.pipeline.OpinionGenerator") as mock_opinion_cls,
            patch("core.pipeline.StrategyEnsembleEngine"),
        ):
            mock_news = AsyncMock()
            mock_news.get_macro_articles = AsyncMock(return_value=[{"title": "Fed holds rates"}])
            mock_news_cls.return_value = mock_news

            mock_opinion = AsyncMock()
            mock_opinion.generate_macro_opinion = AsyncMock(return_value=MagicMock(opinion_type="MACRO"))
            mock_opinion_cls.return_value = mock_opinion

            pipeline = InvestmentDecisionPipeline()
            result = await pipeline.run_macro_analysis()

        assert result.opinion_type == "MACRO"

    @pytest.mark.asyncio
    async def test_run_sector_analysis(self):
        """섹터 분석 경로 커버리지"""
        from core.pipeline import InvestmentDecisionPipeline

        with (
            patch("core.pipeline.NewsCollectorService") as mock_news_cls,
            patch("core.pipeline.SentimentAnalyzer") as mock_sent_cls,
            patch("core.pipeline.OpinionGenerator") as mock_opinion_cls,
            patch("core.pipeline.StrategyEnsembleEngine"),
        ):
            mock_news = AsyncMock()
            mock_news.get_articles_for_ticker = AsyncMock(return_value=[])
            mock_news.get_recent_articles = AsyncMock(return_value=[])
            mock_news.get_macro_articles = AsyncMock(return_value=[{"title": "macro headline"}])
            mock_news_cls.return_value = mock_news

            mock_sent = AsyncMock()
            mock_sent.analyze_ticker = AsyncMock(return_value=MagicMock(score=0.6, summary="Positive"))
            mock_sent_cls.return_value = mock_sent

            mock_opinion = AsyncMock()
            mock_opinion.generate_sector_opinion = AsyncMock(return_value=MagicMock(opinion_type="SECTOR"))
            mock_opinion_cls.return_value = mock_opinion

            pipeline = InvestmentDecisionPipeline()
            result = await pipeline.run_sector_analysis(
                sector_name="반도체",
                tickers=["005930", "000660"],
            )

        assert result.opinion_type == "SECTOR"

    @pytest.mark.asyncio
    async def test_recalibrate_ensemble_weights(self):
        """앙상블 가중치 재계산 위임 호출"""
        from core.pipeline import InvestmentDecisionPipeline

        with (
            patch("core.pipeline.NewsCollectorService"),
            patch("core.pipeline.SentimentAnalyzer"),
            patch("core.pipeline.OpinionGenerator"),
            patch("core.pipeline.StrategyEnsembleEngine") as mock_ens_cls,
        ):
            mock_ens = AsyncMock()
            mock_ens.recalibrate_weights = AsyncMock(return_value={"FACTOR": 0.5, "SENTIMENT": 0.5})
            mock_ens_cls.return_value = mock_ens

            pipeline = InvestmentDecisionPipeline()
            result = await pipeline.recalibrate_ensemble_weights({"FACTOR": 1.5, "SENTIMENT": 1.0})

        assert result["FACTOR"] == 0.5

    @pytest.mark.asyncio
    async def test_run_dynamic_ensemble_success(self):
        """동적 앙상블 성공 경로"""
        from core.pipeline import InvestmentDecisionPipeline
        from core.state_machine import PipelineState, PipelineStateMachine
        from core.strategy_ensemble.regime import MarketRegime

        with (
            patch("core.pipeline.NewsCollectorService"),
            patch("core.pipeline.SentimentAnalyzer"),
            patch("core.pipeline.OpinionGenerator"),
            patch("core.pipeline.StrategyEnsembleEngine"),
            patch("core.pipeline.DynamicEnsembleRunner") as mock_runner_cls,
        ):
            mock_runner = MagicMock()
            mock_result = MagicMock()
            mock_result.regime = MarketRegime.TRENDING_UP
            mock_result.ensemble_signal = 0.65
            mock_result.weights = {"TREND": 0.5, "MEAN_REV": 0.3}
            mock_runner.run_with_ohlcv.return_value = mock_result
            mock_runner_cls.return_value = mock_runner

            # 상태 머신을 mock하여 전이 허용
            mock_sm = MagicMock(spec=PipelineStateMachine)
            mock_sm.state = PipelineState.COMPLETED
            pipeline = InvestmentDecisionPipeline(state_machine=mock_sm)

            # EnsembleGate를 mock하여 PASS 반환
            with patch.object(pipeline, "_evaluate_gate", return_value=False):
                ohlcv = pd.DataFrame(
                    {
                        "open": [100] * 100,
                        "high": [110] * 100,
                        "low": [90] * 100,
                        "close": [105] * 100,
                        "volume": [1000] * 100,
                    },
                    index=pd.date_range("2025-01-01", periods=100, freq="B"),
                )
                result = await pipeline.run_dynamic_ensemble("005930", ohlcv=ohlcv)

        assert not result.blocked
        assert result.dynamic_ensemble is not None

    @pytest.mark.asyncio
    async def test_run_dynamic_ensemble_gate_block(self):
        """동적 앙상블 — EnsembleGate가 블록한 경우"""
        from core.pipeline import InvestmentDecisionPipeline
        from core.state_machine import PipelineState, PipelineStateMachine
        from core.strategy_ensemble.regime import MarketRegime

        with (
            patch("core.pipeline.NewsCollectorService"),
            patch("core.pipeline.SentimentAnalyzer"),
            patch("core.pipeline.OpinionGenerator"),
            patch("core.pipeline.StrategyEnsembleEngine"),
            patch("core.pipeline.DynamicEnsembleRunner") as mock_runner_cls,
        ):
            mock_runner = MagicMock()
            mock_result = MagicMock()
            mock_result.regime = MarketRegime.TRENDING_DOWN
            mock_result.ensemble_signal = -0.3
            mock_result.weights = {"TREND": 0.2}
            mock_runner.run_with_ohlcv.return_value = mock_result
            mock_runner_cls.return_value = mock_runner

            mock_sm = MagicMock(spec=PipelineStateMachine)
            mock_sm.state = PipelineState.ANALYZING
            pipeline = InvestmentDecisionPipeline(state_machine=mock_sm)

            with patch.object(pipeline, "_evaluate_gate", return_value=True):
                ohlcv = pd.DataFrame(
                    {
                        "open": [100],
                        "high": [110],
                        "low": [90],
                        "close": [105],
                        "volume": [1000],
                    }
                )
                result = await pipeline.run_dynamic_ensemble("005930", ohlcv=ohlcv)

        assert result.blocked
        assert result.blocked_by == "EnsembleGate"

    @pytest.mark.asyncio
    async def test_run_dynamic_ensemble_exception(self):
        """동적 앙상블 — 예외 발생 시 에러 상태"""
        from core.pipeline import InvestmentDecisionPipeline
        from core.state_machine import PipelineState, PipelineStateMachine

        with (
            patch("core.pipeline.NewsCollectorService"),
            patch("core.pipeline.SentimentAnalyzer"),
            patch("core.pipeline.OpinionGenerator"),
            patch("core.pipeline.StrategyEnsembleEngine"),
            patch("core.pipeline.DynamicEnsembleRunner") as mock_runner_cls,
        ):
            mock_runner_cls.side_effect = Exception("Runner init failed")

            mock_sm = MagicMock(spec=PipelineStateMachine)
            mock_sm.state = PipelineState.ERROR
            pipeline = InvestmentDecisionPipeline(state_machine=mock_sm)

            result = await pipeline.run_dynamic_ensemble("005930")

        assert result.blocked
        assert "Error" in result.blocked_by

    @pytest.mark.asyncio
    async def test_run_dynamic_ensemble_batch(self):
        """동적 앙상블 배치 실행"""
        from core.pipeline import InvestmentDecisionPipeline, PipelineResult
        from core.state_machine import PipelineStateMachine

        with (
            patch("core.pipeline.NewsCollectorService"),
            patch("core.pipeline.SentimentAnalyzer"),
            patch("core.pipeline.OpinionGenerator"),
            patch("core.pipeline.StrategyEnsembleEngine"),
        ):
            mock_sm = MagicMock(spec=PipelineStateMachine)
            pipeline = InvestmentDecisionPipeline(state_machine=mock_sm)
            mock_result = PipelineResult(blocked=False)
            with patch.object(
                pipeline,
                "run_dynamic_ensemble",
                new_callable=AsyncMock,
                return_value=mock_result,
            ):
                results = await pipeline.run_dynamic_ensemble_batch(["005930", "000660"])

        assert len(results) == 2
        assert "005930" in results
        assert "000660" in results

    @pytest.mark.asyncio
    async def test_run_dynamic_ensemble_batch_with_error(self):
        """배치 실행 중 일부 종목 에러"""
        from core.pipeline import InvestmentDecisionPipeline, PipelineResult
        from core.state_machine import PipelineStateMachine

        with (
            patch("core.pipeline.NewsCollectorService"),
            patch("core.pipeline.SentimentAnalyzer"),
            patch("core.pipeline.OpinionGenerator"),
            patch("core.pipeline.StrategyEnsembleEngine"),
        ):
            mock_sm = MagicMock(spec=PipelineStateMachine)
            pipeline = InvestmentDecisionPipeline(state_machine=mock_sm)
            call_count = 0

            async def side_effect(**kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    raise Exception("Ticker failed")
                return PipelineResult(blocked=False)

            with patch.object(
                pipeline,
                "run_dynamic_ensemble",
                side_effect=side_effect,
            ):
                results = await pipeline.run_dynamic_ensemble_batch(["005930", "000660", "035420"])

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_batch_analysis_with_error(self):
        """배치 분석 중 일부 종목 에러 경로"""
        from core.pipeline import InvestmentDecisionPipeline, PipelineResult
        from core.state_machine import PipelineStateMachine

        with (
            patch("core.pipeline.NewsCollectorService"),
            patch("core.pipeline.SentimentAnalyzer"),
            patch("core.pipeline.OpinionGenerator"),
            patch("core.pipeline.StrategyEnsembleEngine"),
        ):
            mock_sm = MagicMock(spec=PipelineStateMachine)
            pipeline = InvestmentDecisionPipeline(state_machine=mock_sm)
            call_count = 0

            async def side_effect(**kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise Exception("Analysis failed")
                return PipelineResult(blocked=False)

            with patch.object(
                pipeline,
                "run_full_analysis",
                side_effect=side_effect,
            ):
                results = await pipeline.run_batch_analysis(tickers=["005930", "000660"])

        assert len(results) == 1


class TestPipelineHelpers:
    """Pipeline 내부 유틸리티 메서드 커버리지"""

    def test_summarize_quant_signals_with_signals(self):
        """_summarize_quant_signals — 시그널이 있을 때"""
        from core.pipeline import InvestmentDecisionPipeline

        signals = [
            MagicMock(strategy=StrategyType.TREND_FOLLOWING, value=0.6),
            MagicMock(strategy=StrategyType.MEAN_REVERSION, value=-0.3),
            MagicMock(strategy=StrategyType.RISK_PARITY, value=0.2),
        ]

        result = InvestmentDecisionPipeline._summarize_quant_signals(signals, 72.0)

        assert result["composite_score"] == 72.0
        assert result["trend_signal"] == 0.6
        assert result["mean_rev_signal"] == -0.3
        assert result["risk_parity_signal"] == 0.2

    def test_summarize_quant_signals_empty(self):
        """_summarize_quant_signals — 빈 시그널"""
        from core.pipeline import InvestmentDecisionPipeline

        result = InvestmentDecisionPipeline._summarize_quant_signals(None, 50.0)

        assert result["composite_score"] == 50.0
        assert result["trend_signal"] == 0.0

    def test_build_ensemble_inputs(self):
        """_build_ensemble_inputs — 퀀트+AI 시그널 변환"""
        from core.pipeline import InvestmentDecisionPipeline

        quant_signals = [
            MagicMock(
                strategy=StrategyType.FACTOR,
                value=0.7,
                confidence=0.85,
                reason="Factor strong",
            ),
        ]

        # sentiment과 opinion에 실제 숫자 값을 설정
        sentiment = MagicMock()
        sentiment.to_signal_value.return_value = 0.6
        sentiment.confidence = 0.8

        opinion = MagicMock()
        opinion.to_signal_value.return_value = 0.4
        opinion.conviction = 0.7
        opinion.action = MagicMock(value="BUY")

        inputs = InvestmentDecisionPipeline._build_ensemble_inputs(quant_signals, sentiment, opinion)

        assert len(inputs) >= 2  # 퀀트 1개 + 센티먼트 1개
        strategies = [i.strategy for i in inputs]
        assert StrategyType.FACTOR.value in strategies
        assert "SENTIMENT" in strategies

    def test_build_ensemble_inputs_no_quant(self):
        """_build_ensemble_inputs — 퀀트 없이 AI 시그널만"""
        from core.pipeline import InvestmentDecisionPipeline

        sentiment = MagicMock()
        sentiment.to_signal_value.return_value = -0.3
        sentiment.confidence = 0.6

        opinion = MagicMock()
        opinion.to_signal_value.return_value = -0.5
        opinion.conviction = 0.8
        opinion.action = MagicMock(value="SELL")

        inputs = InvestmentDecisionPipeline._build_ensemble_inputs(None, sentiment, opinion)

        assert len(inputs) == 1
        assert inputs[0].strategy == "SENTIMENT"
        assert inputs[0].value < 0  # 매도 시그널


# ══════════════════════════════════════════════════════════════
# 3. RL DataLoader 커버리지 부스트
# ══════════════════════════════════════════════════════════════


class TestRLDataLoaderDB:
    """RLDataLoader — DB 관련 경로 커버리지"""

    def test_build_db_url_with_env(self, monkeypatch):
        """_build_db_url — 환경변수에서 DB URL 구성"""
        from core.rl.data_loader import RLDataLoader

        monkeypatch.setenv("POSTGRES_HOST", "testhost")
        monkeypatch.setenv("POSTGRES_PORT", "5433")
        monkeypatch.setenv("POSTGRES_USER", "testuser")
        monkeypatch.setenv("POSTGRES_PASSWORD", "testpass")
        monkeypatch.setenv("POSTGRES_DB", "testdb")

        url = RLDataLoader._build_db_url()
        assert "testhost" in url
        assert "5433" in url
        assert "testuser" in url
        assert "testdb" in url

    def test_build_db_url_defaults(self, monkeypatch):
        """_build_db_url — 환경변수 없을 때 기본값"""
        from core.rl.data_loader import RLDataLoader

        monkeypatch.delenv("POSTGRES_HOST", raising=False)
        monkeypatch.delenv("POSTGRES_PORT", raising=False)
        monkeypatch.delenv("POSTGRES_USER", raising=False)
        monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
        monkeypatch.delenv("POSTGRES_DB", raising=False)

        url = RLDataLoader._build_db_url()
        assert "localhost" in url

    def test_load_from_db_connection_failure(self):
        """load_from_db — DB 연결 실패 시 예외"""
        from core.rl.data_loader import RLDataLoader

        loader = RLDataLoader()

        mock_engine = MagicMock()
        mock_engine.connect.side_effect = Exception("Connection refused")

        with patch(
            "sqlalchemy.create_engine",
            return_value=mock_engine,
        ):
            with pytest.raises(Exception, match="Connection refused"):
                loader.load_from_db(
                    db_url="postgresql://bad:bad@localhost/bad",
                    tickers=["005930"],
                )

    def test_load_from_db_with_tickers(self):
        """load_from_db — 특정 종목 목록으로 로드"""
        from core.rl.data_loader import RLDataLoader

        loader = RLDataLoader()

        dates = pd.date_range("2024-01-01", periods=400, freq="B")
        df = pd.DataFrame(
            {
                "date": dates,
                "open": np.random.uniform(50000, 60000, 400),
                "high": np.random.uniform(55000, 65000, 400),
                "low": np.random.uniform(45000, 55000, 400),
                "close": np.random.uniform(50000, 60000, 400),
                "volume": np.random.randint(100000, 1000000, 400).astype(float),
            }
        )

        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch(
                "sqlalchemy.create_engine",
                return_value=mock_engine,
            ),
            patch(
                "pandas.read_sql",
                return_value=df,
            ),
        ):
            result = loader.load_from_db(
                db_url="postgresql://test@localhost/test",
                tickers=["005930"],
            )

        assert isinstance(result, dict)
        assert "005930" in result

    def test_load_from_db_short_data_skipped(self):
        """load_from_db — 데이터가 최소 길이 미만이면 스킵"""
        from core.rl.data_loader import RLDataLoader

        loader = RLDataLoader()

        # 100행만 (MIN_DATA_LENGTH=312 미만)
        df = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=100, freq="B"),
                "open": [100.0] * 100,
                "high": [110.0] * 100,
                "low": [90.0] * 100,
                "close": [105.0] * 100,
                "volume": [1000.0] * 100,
            }
        )

        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch(
                "sqlalchemy.create_engine",
                return_value=mock_engine,
            ),
            patch(
                "pandas.read_sql",
                return_value=df,
            ),
        ):
            result = loader.load_from_db(
                db_url="postgresql://test@localhost/test",
                tickers=["005930"],
            )

        assert "005930" not in result

    def test_validate_and_clean_inf_values(self):
        """_validate_and_clean — -inf open은 <= 0 조건으로 제거됨"""
        from core.rl.data_loader import RLDataLoader

        loader = RLDataLoader()

        dates = pd.date_range("2024-01-01", periods=400, freq="B")
        df = pd.DataFrame(
            {
                "open": np.random.uniform(50000, 60000, 400),
                "high": np.random.uniform(55000, 65000, 400),
                "low": np.random.uniform(45000, 55000, 400),
                "close": np.random.uniform(50000, 60000, 400),
                "volume": np.random.randint(100000, 1000000, 400).astype(float),
            },
            index=dates,
        )
        # -inf는 open > 0 조건에서 필터링됨
        df.loc[df.index[10], "open"] = -np.inf

        result = loader._validate_and_clean(df, "005930")
        assert result is not None
        # -inf open 행이 제거됨 (open > 0 조건)
        assert len(result) < 400

    def test_validate_and_clean_negative_prices(self):
        """_validate_and_clean — 음수 가격 제거"""
        from core.rl.data_loader import RLDataLoader

        loader = RLDataLoader()

        dates = pd.date_range("2024-01-01", periods=400, freq="B")
        df = pd.DataFrame(
            {
                "open": np.random.uniform(50000, 60000, 400),
                "high": np.random.uniform(55000, 65000, 400),
                "low": np.random.uniform(45000, 55000, 400),
                "close": np.random.uniform(50000, 60000, 400),
                "volume": np.random.randint(100000, 1000000, 400).astype(float),
            },
            index=dates,
        )
        # 음수 가격 주입
        df.loc[df.index[3], "open"] = -100.0

        result = loader._validate_and_clean(df, "005930")
        assert result is not None
        assert (result["open"] >= 0).all()

    def test_load_from_csv_read_error(self):
        """load_from_csv — CSV 읽기 실패"""
        from core.rl.data_loader import RLDataLoader

        loader = RLDataLoader()

        with tempfile.TemporaryDirectory() as tmpdir:
            # 잘못된 CSV 파일 생성
            bad_csv = os.path.join(tmpdir, "005930.csv")
            with open(bad_csv, "w") as f:
                f.write("not,valid,csv\n\x00\x01\x02")

            result = loader.load_from_csv(tmpdir)
            assert isinstance(result, dict)


# ══════════════════════════════════════════════════════════════
# 4. SchedulerHandlers 커버리지 부스트
# ══════════════════════════════════════════════════════════════


class TestSchedulerHandlersExtended:
    """scheduler_handlers.py — 추가 경로 커버리지"""

    @pytest.mark.asyncio
    async def test_stop_realtime_quotes_when_none(self):
        """_stop_realtime_quotes — manager가 None일 때"""
        from core.scheduler_handlers import _stop_realtime_quotes

        with patch("core.scheduler_handlers._realtime_manager", None):
            await _stop_realtime_quotes()

    @pytest.mark.asyncio
    async def test_stop_realtime_quotes_with_manager(self):
        """_stop_realtime_quotes — manager 정리"""
        from core.scheduler_handlers import _stop_realtime_quotes

        mock_manager = AsyncMock()
        mock_manager.stop = AsyncMock()

        with patch("core.scheduler_handlers._realtime_manager", mock_manager):
            await _stop_realtime_quotes()

    @pytest.mark.asyncio
    async def test_run_rl_inference_no_champion(self):
        """_run_rl_inference — 챔피언 모델 없을 때"""
        from core.scheduler_handlers import _run_rl_inference

        mock_svc = MagicMock()
        mock_svc.load_model.return_value = False

        with patch(
            "core.rl.inference.RLInferenceService",
            return_value=mock_svc,
        ):
            result = await _run_rl_inference(
                session=MagicMock(),
                ensemble_results={"005930": {"signal": 0.5}},
            )

        assert result.get("skip_reason") == "no_champion_model"

    @pytest.mark.asyncio
    async def test_run_rl_inference_import_error(self):
        """_run_rl_inference — RL 모듈 미설치 시"""
        from core.scheduler_handlers import _run_rl_inference

        with patch.dict("sys.modules", {"core.rl.inference": None}):
            # ImportError 경로 테스트를 위해 직접 호출
            result = await _run_rl_inference(
                session=MagicMock(),
                ensemble_results={},
            )

        # ImportError 또는 error 경로에 빠짐
        assert result.get("skip_reason") or result.get("error")

    @pytest.mark.asyncio
    async def test_run_rl_inference_exception(self):
        """_run_rl_inference — 예외 발생 시 graceful"""
        from core.scheduler_handlers import _run_rl_inference

        with patch(
            "core.rl.inference.RLInferenceService",
            side_effect=Exception("Init failed"),
        ):
            result = await _run_rl_inference(
                session=MagicMock(),
                ensemble_results={"005930": {}},
            )

        assert "error" in result or "skip_reason" in result
