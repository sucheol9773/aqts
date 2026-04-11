"""
스케줄러 핸들러 확장 유닛테스트

handle_midday_check, handle_market_close, handle_post_market 핸들러 검증.

각 핸들러는 KIS API, Redis, DB, TradingGuard, DailyReporter 등
외부 의존성을 사용하므로 전부 mock 처리합니다.

NOTE: 핸들러 내부에서 lazy import하는 모듈은 원본 모듈 경로로 패치합니다.
  - KISClient → core.data_collector.kis_client.KISClient
  - TradingGuard → core.trading_guard.TradingGuard
  - DailyReporter → core.daily_reporter.DailyReporter
  - AuditLogger → db.repositories.audit_log.AuditLogger
  - get_settings → config.settings.get_settings
  - HealthChecker → core.health_checker.HealthChecker

  반면 top-level import인 RedisManager, async_session_factory는
  core.scheduler_handlers 경로로 패치합니다.
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.scheduler_handlers import (
    handle_market_close,
    handle_midday_check,
    handle_post_market,
)

# 패치 경로 상수
_KIS = "core.data_collector.kis_client.KISClient"
_GUARD = "core.trading_guard.TradingGuard"
_REPORTER = "core.daily_reporter.DailyReporter"
_AUDIT = "db.repositories.audit_log.AuditLogger"
_SETTINGS = "config.settings.get_settings"
_REDIS = "core.scheduler_handlers.RedisManager.get_client"
_SESSION = "core.scheduler_handlers.async_session_factory"


# ══════════════════════════════════════
# 공통 fixture
# ══════════════════════════════════════


def _make_kis_balance(positions=None, total_eval=50_000_000.0, cash=10_000_000.0):
    """KIS API get_kr_balance 응답 mock 생성"""
    if positions is None:
        positions = [
            {
                "pdno": "005930",
                "prdt_name": "삼성전자",
                "hldg_qty": "100",
                "pchs_avg_pric": "65000",
                "prpr": "68000",
                "evlu_amt": "6800000",
                "evlu_pfls_amt": "300000",
                "evlu_pfls_rt": "4.62",
            },
            {
                "pdno": "035720",
                "prdt_name": "카카오",
                "hldg_qty": "50",
                "pchs_avg_pric": "50000",
                "prpr": "45000",
                "evlu_amt": "2250000",
                "evlu_pfls_amt": "-250000",
                "evlu_pfls_rt": "-10.00",
            },
        ]

    return {
        "output1": positions,
        "output2": [
            {
                "tot_evlu_amt": str(total_eval),
                "dnca_tot_amt": str(cash),
            }
        ],
    }


def _mock_session_ctx(mock_session):
    """async_session_factory context manager mock 생성"""
    return AsyncMock(
        __aenter__=AsyncMock(return_value=mock_session),
        __aexit__=AsyncMock(return_value=False),
    )


# ══════════════════════════════════════
# handle_midday_check 테스트
# ══════════════════════════════════════


class TestHandleMiddayCheck:
    """중간 점검 핸들러 테스트"""

    @pytest.mark.asyncio
    async def test_returns_positions_and_eval(self):
        """KIS 잔고 조회 결과가 반환되는지"""
        mock_kis = AsyncMock()
        mock_kis.get_kr_balance.return_value = _make_kis_balance()

        mock_guard = MagicMock()
        mock_guard.state = MagicMock(
            current_drawdown=0.05,
            peak_portfolio_value=52_000_000.0,
            current_portfolio_value=50_000_000.0,
        )
        mock_guard.check_max_drawdown.return_value = MagicMock(allowed=True)

        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=None)

        with (
            patch(_KIS, return_value=mock_kis),
            patch(_GUARD, return_value=mock_guard),
            patch(_REDIS, return_value=mock_redis),
        ):
            result = await handle_midday_check()

        assert result["positions_count"] == 2
        assert result["total_eval"] == 50_000_000.0
        assert result["cash"] == 10_000_000.0

    @pytest.mark.asyncio
    async def test_loss_alert_triggered(self):
        """5% 이상 손실 종목이 loss_alert에 포함되는지"""
        mock_kis = AsyncMock()
        mock_kis.get_kr_balance.return_value = _make_kis_balance()

        mock_guard = MagicMock()
        mock_guard.state = MagicMock(
            current_drawdown=0.02,
            peak_portfolio_value=52_000_000.0,
            current_portfolio_value=50_000_000.0,
        )
        mock_guard.check_max_drawdown.return_value = MagicMock(allowed=True)

        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=None)

        with (
            patch(_KIS, return_value=mock_kis),
            patch(_GUARD, return_value=mock_guard),
            patch(_REDIS, return_value=mock_redis),
        ):
            result = await handle_midday_check()

        assert "loss_alert" in result
        assert len(result["loss_alert"]) == 1
        assert result["loss_alert"][0]["ticker"] == "035720"
        assert result["loss_alert"][0]["pnl_pct"] == -10.00

    @pytest.mark.asyncio
    async def test_no_loss_alert_when_all_positive(self):
        """모든 종목 이익이면 loss_alert 없음"""
        positions = [
            {
                "pdno": "005930",
                "prdt_name": "삼성전자",
                "hldg_qty": "100",
                "evlu_pfls_amt": "300000",
                "evlu_pfls_rt": "4.62",
            },
        ]
        mock_kis = AsyncMock()
        mock_kis.get_kr_balance.return_value = _make_kis_balance(positions=positions)

        mock_guard = MagicMock()
        mock_guard.state = MagicMock(
            current_drawdown=0.0,
            peak_portfolio_value=50_000_000.0,
            current_portfolio_value=50_000_000.0,
        )
        mock_guard.check_max_drawdown.return_value = MagicMock(allowed=True)

        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=None)

        with (
            patch(_KIS, return_value=mock_kis),
            patch(_GUARD, return_value=mock_guard),
            patch(_REDIS, return_value=mock_redis),
        ):
            result = await handle_midday_check()

        assert "loss_alert" not in result

    @pytest.mark.asyncio
    async def test_dd_warning_when_high_drawdown(self):
        """드로다운 15% 초과 시 dd_warning 발생"""
        mock_kis = AsyncMock()
        mock_kis.get_kr_balance.return_value = _make_kis_balance()

        mock_guard = MagicMock()
        mock_guard.state = MagicMock(
            current_drawdown=0.18,
            peak_portfolio_value=60_000_000.0,
            current_portfolio_value=50_000_000.0,
        )
        mock_guard.check_max_drawdown.return_value = MagicMock(allowed=True)

        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=None)

        with (
            patch(_KIS, return_value=mock_kis),
            patch(_GUARD, return_value=mock_guard),
            patch(_REDIS, return_value=mock_redis),
        ):
            result = await handle_midday_check()

        assert "dd_warning" in result

    @pytest.mark.asyncio
    async def test_kis_failure_graceful(self):
        """KIS API 실패 시에도 정상 반환"""
        mock_guard = MagicMock()
        mock_guard.state = MagicMock(current_drawdown=0.0)

        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=None)

        with (
            patch(_KIS, side_effect=RuntimeError("KIS 연결 실패")),
            patch(_GUARD, return_value=mock_guard),
            patch(_REDIS, return_value=mock_redis),
        ):
            result = await handle_midday_check()

        assert "kis_error" in result
        assert "check_time" in result

    @pytest.mark.asyncio
    async def test_ensemble_cache_info_included(self):
        """캐시된 앙상블 정보가 결과에 포함되는지"""
        mock_kis = AsyncMock()
        mock_kis.get_kr_balance.return_value = _make_kis_balance()

        mock_guard = MagicMock()
        mock_guard.state = MagicMock(
            current_drawdown=0.02,
            peak_portfolio_value=52_000_000.0,
            current_portfolio_value=50_000_000.0,
        )
        mock_guard.check_max_drawdown.return_value = MagicMock(allowed=True)

        summary = json.dumps(
            {
                "total_tickers": 15,
                "updated_at": "2026-04-06T09:00:00+00:00",
            }
        )
        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=summary)

        with (
            patch(_KIS, return_value=mock_kis),
            patch(_GUARD, return_value=mock_guard),
            patch(_REDIS, return_value=mock_redis),
        ):
            result = await handle_midday_check()

        assert result["ensemble_cached_tickers"] == 15


# ══════════════════════════════════════
# handle_market_close 테스트
# ══════════════════════════════════════


class TestHandleMarketClose:
    """장 마감 핸들러 테스트"""

    @pytest.mark.asyncio
    async def test_returns_portfolio_summary(self):
        """포트폴리오 가치, 현금, 포지션 수 반환"""
        mock_kis = AsyncMock()
        mock_kis.get_kr_balance.return_value = _make_kis_balance()

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(fetchall=lambda: [("BUY", 3, 5000000.0), ("SELL", 1, 2000000.0)])
        mock_session.commit = AsyncMock()

        mock_audit = AsyncMock()
        mock_audit.log = AsyncMock()

        mock_redis = MagicMock()
        mock_redis.set = AsyncMock()

        with (
            patch(_KIS, return_value=mock_kis),
            patch(_SESSION, return_value=_mock_session_ctx(mock_session)),
            patch(_REDIS, return_value=mock_redis),
            patch(_AUDIT, return_value=mock_audit),
        ):
            result = await handle_market_close()

        assert result["portfolio_value"] == 50_000_000.0
        assert result["cash_balance"] == 10_000_000.0
        assert result["positions_count"] == 2

    @pytest.mark.asyncio
    async def test_trade_stats_included(self):
        """거래 통계가 결과에 포함되는지"""
        mock_kis = AsyncMock()
        mock_kis.get_kr_balance.return_value = _make_kis_balance()

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(fetchall=lambda: [("BUY", 5, 10000000.0)])
        mock_session.commit = AsyncMock()

        mock_audit = AsyncMock()
        mock_audit.log = AsyncMock()

        mock_redis = MagicMock()
        mock_redis.set = AsyncMock()

        with (
            patch(_KIS, return_value=mock_kis),
            patch(_SESSION, return_value=_mock_session_ctx(mock_session)),
            patch(_REDIS, return_value=mock_redis),
            patch(_AUDIT, return_value=mock_audit),
        ):
            result = await handle_market_close()

        assert "trade_stats" in result
        assert result["trade_stats"]["BUY"]["count"] == 5
        assert result["trade_stats"]["BUY"]["amount"] == 10000000.0

    @pytest.mark.asyncio
    async def test_snapshot_saved_to_redis(self):
        """포트폴리오 스냅샷이 Redis에 저장되는지"""
        mock_kis = AsyncMock()
        mock_kis.get_kr_balance.return_value = _make_kis_balance()

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(fetchall=lambda: [])
        mock_session.commit = AsyncMock()

        mock_audit = AsyncMock()
        mock_audit.log = AsyncMock()

        mock_redis = MagicMock()
        mock_redis.set = AsyncMock()

        with (
            patch(_KIS, return_value=mock_kis),
            patch(_SESSION, return_value=_mock_session_ctx(mock_session)),
            patch(_REDIS, return_value=mock_redis),
            patch(_AUDIT, return_value=mock_audit),
        ):
            result = await handle_market_close()

        assert result.get("snapshot_saved") is True
        mock_redis.set.assert_awaited()

        # Redis에 저장된 데이터 검증
        call_args = mock_redis.set.call_args_list[0]
        key = call_args.args[0]
        assert key.startswith("portfolio:snapshot:")

        data = json.loads(call_args.args[1])
        assert data["portfolio_value"] == 50_000_000.0

    @pytest.mark.asyncio
    async def test_audit_log_recorded(self):
        """감사 로그가 기록되는지"""
        mock_kis = AsyncMock()
        mock_kis.get_kr_balance.return_value = _make_kis_balance()

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(fetchall=lambda: [])
        mock_session.commit = AsyncMock()

        mock_audit = AsyncMock()
        mock_audit.log = AsyncMock()

        mock_redis = MagicMock()
        mock_redis.set = AsyncMock()

        with (
            patch(_KIS, return_value=mock_kis),
            patch(_SESSION, return_value=_mock_session_ctx(mock_session)),
            patch(_REDIS, return_value=mock_redis),
            patch(_AUDIT, return_value=mock_audit),
        ):
            await handle_market_close()

        mock_audit.log.assert_awaited_once()
        call_kwargs = mock_audit.log.call_args.kwargs
        assert call_kwargs["action_type"] == "MARKET_CLOSE"
        assert call_kwargs["module"] == "scheduler_handler"

    @pytest.mark.asyncio
    async def test_kis_failure_graceful(self):
        """KIS 실패 시에도 정상 반환"""
        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(fetchall=lambda: [])
        mock_session.commit = AsyncMock()

        mock_audit = AsyncMock()
        mock_audit.log = AsyncMock()

        mock_redis = MagicMock()
        mock_redis.set = AsyncMock()

        with (
            patch(_KIS, side_effect=RuntimeError("KIS 연결 실패")),
            patch(_SESSION, return_value=_mock_session_ctx(mock_session)),
            patch(_REDIS, return_value=mock_redis),
            patch(_AUDIT, return_value=mock_audit),
        ):
            result = await handle_market_close()

        assert "kis_error" in result
        # KIS 실패 시 portfolio_value는 설정되지 않음
        assert "portfolio_value" not in result

    @pytest.mark.asyncio
    async def test_empty_positions_handled(self):
        """포지션 없는 경우 정상 처리"""
        mock_kis = AsyncMock()
        mock_kis.get_kr_balance.return_value = _make_kis_balance(
            positions=[], total_eval=10_000_000.0, cash=10_000_000.0
        )

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(fetchall=lambda: [])
        mock_session.commit = AsyncMock()

        mock_audit = AsyncMock()
        mock_audit.log = AsyncMock()

        mock_redis = MagicMock()
        mock_redis.set = AsyncMock()

        with (
            patch(_KIS, return_value=mock_kis),
            patch(_SESSION, return_value=_mock_session_ctx(mock_session)),
            patch(_REDIS, return_value=mock_redis),
            patch(_AUDIT, return_value=mock_audit),
        ):
            result = await handle_market_close()

        assert result["positions_count"] == 0
        assert result["portfolio_value"] == 10_000_000.0


# ══════════════════════════════════════
# handle_post_market 테스트
# ══════════════════════════════════════


class TestHandlePostMarket:
    """마감 후 핸들러 테스트"""

    @pytest.mark.asyncio
    async def test_returns_daily_report_metrics(self):
        """일일 리포트 메트릭이 결과에 포함되는지"""
        snapshot = json.dumps(
            {
                "portfolio_value": 50_000_000.0,
                "cash_balance": 10_000_000.0,
                "positions": [
                    {
                        "ticker": "005930",
                        "name": "삼성전자",
                        "quantity": 100,
                        "avg_price": 65000,
                        "current_price": 68000,
                        "eval_amount": 6800000,
                        "pnl_amount": 300000,
                        "pnl_percent": 4.62,
                    }
                ],
            }
        )
        prev_snapshot = json.dumps({"portfolio_value": 49_000_000.0})

        mock_redis = MagicMock()

        async def _redis_get(key):
            if "snapshot" in key:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if today in key:
                    return snapshot
                return prev_snapshot
            return None

        mock_redis.get = AsyncMock(side_effect=_redis_get)
        mock_redis.set = AsyncMock()

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(fetchall=lambda: [])

        mock_report = MagicMock()
        mock_report.daily_pnl = 1_000_000.0
        mock_report.daily_return_pct = 2.04
        mock_report.total_trades = 0
        mock_report.total_positions = 1
        mock_report.to_dict.return_value = {"daily_pnl": 1_000_000.0}

        mock_reporter = AsyncMock()
        mock_reporter.generate_report.return_value = mock_report
        mock_reporter.send_telegram_report.return_value = True

        mock_guard = MagicMock()
        mock_guard.state = MagicMock(current_drawdown=0.02, consecutive_losses=0)

        with (
            patch(_REDIS, return_value=mock_redis),
            patch(_SESSION, return_value=_mock_session_ctx(mock_session)),
            patch(_REPORTER, return_value=mock_reporter),
            patch(_GUARD, return_value=mock_guard),
        ):
            result = await handle_post_market()

        assert result["daily_pnl"] == 1_000_000.0
        assert result["daily_return_pct"] == 2.04
        assert result["total_positions"] == 1

    @pytest.mark.asyncio
    async def test_telegram_sent(self):
        """Telegram 발송 성공 확인"""
        # 안전망(post_market 의 zero-snapshot skip) 을 우회하기 위해 실제와 유사한
        # snapshot 을 제공한다. 이 fixture 는 텔레그램/리포트 저장 경로를 검증하는
        # 것이 목적이며, snapshot 부재 시나리오는 별도 테스트에서 검증한다.
        _today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _snapshot_json = json.dumps(
            {
                "date": _today,
                "portfolio_value": 50_000_000.0,
                "cash_balance": 10_000_000.0,
                "positions_count": 1,
                "positions": [
                    {
                        "ticker": "005930",
                        "name": "삼성전자",
                        "quantity": 100,
                        "avg_price": 65000,
                        "current_price": 68000,
                        "eval_amount": 6800000,
                        "pnl_amount": 300000,
                        "pnl_percent": 4.62,
                    }
                ],
            }
        )

        async def _redis_get(key):
            if "snapshot" in key and _today in key:
                return _snapshot_json
            return None

        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(side_effect=_redis_get)
        mock_redis.set = AsyncMock()

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(fetchall=lambda: [])

        mock_report = MagicMock()
        mock_report.daily_pnl = 0.0
        mock_report.daily_return_pct = 0.0
        mock_report.total_trades = 0
        mock_report.total_positions = 0
        mock_report.to_dict.return_value = {}

        mock_reporter = AsyncMock()
        mock_reporter.generate_report.return_value = mock_report
        mock_reporter.send_telegram_report.return_value = True

        mock_guard = MagicMock()
        mock_guard.state = MagicMock(current_drawdown=0.0, consecutive_losses=0)

        mock_settings = MagicMock()
        mock_settings.risk.initial_capital_krw = 50_000_000.0

        with (
            patch(_REDIS, return_value=mock_redis),
            patch(_SESSION, return_value=_mock_session_ctx(mock_session)),
            patch(_REPORTER, return_value=mock_reporter),
            patch(_GUARD, return_value=mock_guard),
            patch(_SETTINGS, return_value=mock_settings),
        ):
            result = await handle_post_market()

        assert result["telegram_sent"] is True
        mock_reporter.send_telegram_report.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_report_saved_to_redis(self):
        """리포트가 Redis에 저장되는지"""
        # 안전망(post_market 의 zero-snapshot skip) 을 우회하기 위해 실제와 유사한
        # snapshot 을 제공한다. 이 fixture 는 텔레그램/리포트 저장 경로를 검증하는
        # 것이 목적이며, snapshot 부재 시나리오는 별도 테스트에서 검증한다.
        _today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _snapshot_json = json.dumps(
            {
                "date": _today,
                "portfolio_value": 50_000_000.0,
                "cash_balance": 10_000_000.0,
                "positions_count": 1,
                "positions": [
                    {
                        "ticker": "005930",
                        "name": "삼성전자",
                        "quantity": 100,
                        "avg_price": 65000,
                        "current_price": 68000,
                        "eval_amount": 6800000,
                        "pnl_amount": 300000,
                        "pnl_percent": 4.62,
                    }
                ],
            }
        )

        async def _redis_get(key):
            if "snapshot" in key and _today in key:
                return _snapshot_json
            return None

        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(side_effect=_redis_get)
        mock_redis.set = AsyncMock()

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(fetchall=lambda: [])

        mock_report = MagicMock()
        mock_report.daily_pnl = 0.0
        mock_report.daily_return_pct = 0.0
        mock_report.total_trades = 0
        mock_report.total_positions = 0
        mock_report.to_dict.return_value = {"daily_pnl": 0.0}

        mock_reporter = AsyncMock()
        mock_reporter.generate_report.return_value = mock_report
        mock_reporter.send_telegram_report.return_value = True

        mock_guard = MagicMock()
        mock_guard.state = MagicMock(current_drawdown=0.0, consecutive_losses=0)

        mock_settings = MagicMock()
        mock_settings.risk.initial_capital_krw = 50_000_000.0

        with (
            patch(_REDIS, return_value=mock_redis),
            patch(_SESSION, return_value=_mock_session_ctx(mock_session)),
            patch(_REPORTER, return_value=mock_reporter),
            patch(_GUARD, return_value=mock_guard),
            patch(_SETTINGS, return_value=mock_settings),
        ):
            result = await handle_post_market()

        assert result.get("report_saved") is True

        # Redis set 호출에 report:daily: 키 사용 확인
        redis_calls = mock_redis.set.call_args_list
        report_call = [c for c in redis_calls if "report:daily:" in str(c.args[0])]
        assert len(report_call) == 1

    @pytest.mark.asyncio
    async def test_telegram_failure_graceful(self):
        """Telegram 발송 실패 시에도 정상 반환"""
        # 안전망(post_market 의 zero-snapshot skip) 을 우회하기 위해 실제와 유사한
        # snapshot 을 제공한다. 이 fixture 는 텔레그램/리포트 저장 경로를 검증하는
        # 것이 목적이며, snapshot 부재 시나리오는 별도 테스트에서 검증한다.
        _today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _snapshot_json = json.dumps(
            {
                "date": _today,
                "portfolio_value": 50_000_000.0,
                "cash_balance": 10_000_000.0,
                "positions_count": 1,
                "positions": [
                    {
                        "ticker": "005930",
                        "name": "삼성전자",
                        "quantity": 100,
                        "avg_price": 65000,
                        "current_price": 68000,
                        "eval_amount": 6800000,
                        "pnl_amount": 300000,
                        "pnl_percent": 4.62,
                    }
                ],
            }
        )

        async def _redis_get(key):
            if "snapshot" in key and _today in key:
                return _snapshot_json
            return None

        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(side_effect=_redis_get)
        mock_redis.set = AsyncMock()

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(fetchall=lambda: [])

        mock_report = MagicMock()
        mock_report.daily_pnl = 0.0
        mock_report.daily_return_pct = 0.0
        mock_report.total_trades = 0
        mock_report.total_positions = 0
        mock_report.to_dict.return_value = {}

        mock_reporter = AsyncMock()
        mock_reporter.generate_report.return_value = mock_report
        mock_reporter.send_telegram_report.side_effect = RuntimeError("Telegram 연결 실패")

        mock_guard = MagicMock()
        mock_guard.state = MagicMock(current_drawdown=0.0, consecutive_losses=0)

        mock_settings = MagicMock()
        mock_settings.risk.initial_capital_krw = 50_000_000.0

        with (
            patch(_REDIS, return_value=mock_redis),
            patch(_SESSION, return_value=_mock_session_ctx(mock_session)),
            patch(_REPORTER, return_value=mock_reporter),
            patch(_GUARD, return_value=mock_guard),
            patch(_SETTINGS, return_value=mock_settings),
        ):
            result = await handle_post_market()

        assert "telegram_error" in result

    @pytest.mark.asyncio
    async def test_uses_initial_capital_when_no_prev_snapshot(self):
        """전일 스냅샷 없으면 초기 자본 사용"""
        # 안전망(post_market 의 zero-snapshot skip) 을 우회하기 위해 실제와 유사한
        # snapshot 을 제공한다. 이 fixture 는 텔레그램/리포트 저장 경로를 검증하는
        # 것이 목적이며, snapshot 부재 시나리오는 별도 테스트에서 검증한다.
        _today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _snapshot_json = json.dumps(
            {
                "date": _today,
                "portfolio_value": 50_000_000.0,
                "cash_balance": 10_000_000.0,
                "positions_count": 1,
                "positions": [
                    {
                        "ticker": "005930",
                        "name": "삼성전자",
                        "quantity": 100,
                        "avg_price": 65000,
                        "current_price": 68000,
                        "eval_amount": 6800000,
                        "pnl_amount": 300000,
                        "pnl_percent": 4.62,
                    }
                ],
            }
        )

        async def _redis_get(key):
            if "snapshot" in key and _today in key:
                return _snapshot_json
            return None

        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(side_effect=_redis_get)
        mock_redis.set = AsyncMock()

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(fetchall=lambda: [])

        mock_report = MagicMock()
        mock_report.daily_pnl = 0.0
        mock_report.daily_return_pct = 0.0
        mock_report.total_trades = 0
        mock_report.total_positions = 0
        mock_report.to_dict.return_value = {}

        mock_reporter = AsyncMock()
        mock_reporter.generate_report.return_value = mock_report
        mock_reporter.send_telegram_report.return_value = True

        mock_guard = MagicMock()
        mock_guard.state = MagicMock(current_drawdown=0.0, consecutive_losses=0)

        mock_settings = MagicMock()
        mock_settings.risk.initial_capital_krw = 50_000_000.0

        with (
            patch(_REDIS, return_value=mock_redis),
            patch(_SESSION, return_value=_mock_session_ctx(mock_session)),
            patch(_REPORTER, return_value=mock_reporter),
            patch(_GUARD, return_value=mock_guard),
            patch(_SETTINGS, return_value=mock_settings),
        ):
            await handle_post_market()

        # generate_report이 initial_capital_krw로 호출되었는지 확인
        call_kwargs = mock_reporter.generate_report.call_args.kwargs
        assert call_kwargs["portfolio_value_start"] == 50_000_000.0

    @pytest.mark.asyncio
    async def test_trades_passed_to_reporter(self):
        """금일 체결 내역이 리포터에 전달되는지"""
        # 안전망(post_market 의 zero-snapshot skip) 을 우회하기 위해 실제와 유사한
        # snapshot 을 제공한다. 이 fixture 는 텔레그램/리포트 저장 경로를 검증하는
        # 것이 목적이며, snapshot 부재 시나리오는 별도 테스트에서 검증한다.
        _today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _snapshot_json = json.dumps(
            {
                "date": _today,
                "portfolio_value": 50_000_000.0,
                "cash_balance": 10_000_000.0,
                "positions_count": 1,
                "positions": [
                    {
                        "ticker": "005930",
                        "name": "삼성전자",
                        "quantity": 100,
                        "avg_price": 65000,
                        "current_price": 68000,
                        "eval_amount": 6800000,
                        "pnl_amount": 300000,
                        "pnl_percent": 4.62,
                    }
                ],
            }
        )

        async def _redis_get(key):
            if "snapshot" in key and _today in key:
                return _snapshot_json
            return None

        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(side_effect=_redis_get)
        mock_redis.set = AsyncMock()

        mock_session = AsyncMock()
        trade_time = datetime(2026, 4, 6, 6, 30, 0, tzinfo=timezone.utc)
        mock_session.execute.return_value = MagicMock(
            fetchall=lambda: [
                ("005930", "BUY", 100, 68000.0, "FILLED", trade_time),
                ("035720", "SELL", 50, 45000.0, "FILLED", trade_time),
            ]
        )

        mock_report = MagicMock()
        mock_report.daily_pnl = 500_000.0
        mock_report.daily_return_pct = 1.0
        mock_report.total_trades = 2
        mock_report.total_positions = 0
        mock_report.to_dict.return_value = {}

        mock_reporter = AsyncMock()
        mock_reporter.generate_report.return_value = mock_report
        mock_reporter.send_telegram_report.return_value = True

        mock_guard = MagicMock()
        mock_guard.state = MagicMock(current_drawdown=0.0, consecutive_losses=0)

        mock_settings = MagicMock()
        mock_settings.risk.initial_capital_krw = 50_000_000.0

        with (
            patch(_REDIS, return_value=mock_redis),
            patch(_SESSION, return_value=_mock_session_ctx(mock_session)),
            patch(_REPORTER, return_value=mock_reporter),
            patch(_GUARD, return_value=mock_guard),
            patch(_SETTINGS, return_value=mock_settings),
        ):
            await handle_post_market()

        call_kwargs = mock_reporter.generate_report.call_args.kwargs
        trades = call_kwargs["trades"]
        assert len(trades) == 2
        assert trades[0].ticker == "005930"
        assert trades[0].side == "BUY"
        assert trades[1].ticker == "035720"
        assert trades[1].side == "SELL"


# ══════════════════════════════════════
# handle_market_close 보강 테스트
# ══════════════════════════════════════


class TestHandleMarketCloseEdgeCases:
    """handle_market_close 미커버 경로 보강"""

    @pytest.mark.asyncio
    async def test_position_with_zero_qty_filtered(self):
        """보유 수량 0인 포지션은 필터링돼야 한다"""
        positions = [
            {
                "pdno": "005930",
                "prdt_name": "삼성전자",
                "hldg_qty": "100",
                "pchs_avg_pric": "65000",
                "prpr": "68000",
                "evlu_amt": "6800000",
                "evlu_pfls_amt": "300000",
                "evlu_pfls_rt": "4.62",
            },
            {
                "pdno": "035720",
                "prdt_name": "카카오",
                "hldg_qty": "0",
                "pchs_avg_pric": "50000",
                "prpr": "45000",
                "evlu_amt": "0",
                "evlu_pfls_amt": "0",
                "evlu_pfls_rt": "0.00",
            },
        ]
        mock_kis = AsyncMock()
        mock_kis.get_kr_balance.return_value = _make_kis_balance(positions=positions)

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(fetchall=lambda: [])
        mock_session.commit = AsyncMock()

        mock_audit = AsyncMock()
        mock_audit.log = AsyncMock()

        mock_redis = MagicMock()
        mock_redis.set = AsyncMock()

        with (
            patch(_KIS, return_value=mock_kis),
            patch(_SESSION, return_value=_mock_session_ctx(mock_session)),
            patch(_REDIS, return_value=mock_redis),
            patch(_AUDIT, return_value=mock_audit),
        ):
            result = await handle_market_close()

        # qty=0인 카카오는 필터링, 삼성전자만 포함
        assert result["positions_count"] == 1

    @pytest.mark.asyncio
    async def test_trade_stats_db_exception(self):
        """거래 통계 DB 조회 실패 시 trade_stats_error 설정"""
        mock_kis = AsyncMock()
        mock_kis.get_kr_balance.return_value = _make_kis_balance()

        # 첫 번째 session 호출(trade stats)은 예외, 두 번째(audit)는 정상
        call_count = 0

        def _session_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # trade stats query — 예외 발생
                raise RuntimeError("DB connection lost")
            # audit — 정상
            mock_s = AsyncMock()
            mock_s.commit = AsyncMock()
            mock_audit_inner = AsyncMock()
            mock_audit_inner.log = AsyncMock()
            return _mock_session_ctx(mock_s)

        mock_redis = MagicMock()
        mock_redis.set = AsyncMock()

        mock_audit = AsyncMock()
        mock_audit.log = AsyncMock()

        with (
            patch(_KIS, return_value=mock_kis),
            patch(_SESSION, side_effect=_session_side_effect),
            patch(_REDIS, return_value=mock_redis),
            patch(_AUDIT, return_value=mock_audit),
        ):
            result = await handle_market_close()

        assert "trade_stats_error" in result
        # KIS는 성공했으므로 portfolio_value는 존재
        assert result["portfolio_value"] == 50_000_000.0

    @pytest.mark.asyncio
    async def test_redis_snapshot_save_exception(self):
        """Redis 스냅샷 저장 실패 시 snapshot_error 설정"""
        mock_kis = AsyncMock()
        mock_kis.get_kr_balance.return_value = _make_kis_balance()

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(fetchall=lambda: [])
        mock_session.commit = AsyncMock()

        mock_audit = AsyncMock()
        mock_audit.log = AsyncMock()

        mock_redis = MagicMock()
        mock_redis.set = AsyncMock(side_effect=RuntimeError("Redis down"))

        with (
            patch(_KIS, return_value=mock_kis),
            patch(_SESSION, return_value=_mock_session_ctx(mock_session)),
            patch(_REDIS, return_value=mock_redis),
            patch(_AUDIT, return_value=mock_audit),
        ):
            result = await handle_market_close()

        assert "snapshot_error" in result
        assert "Redis down" in result["snapshot_error"]

    @pytest.mark.asyncio
    async def test_audit_log_exception(self):
        """감사 로그 기록 실패 시 audit_error 설정"""
        mock_kis = AsyncMock()
        mock_kis.get_kr_balance.return_value = _make_kis_balance()

        mock_session_trade = AsyncMock()
        mock_session_trade.execute.return_value = MagicMock(fetchall=lambda: [])
        mock_session_trade.commit = AsyncMock()

        call_count = 0

        def _session_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _mock_session_ctx(mock_session_trade)
            # 감사 로그 세션 — 예외
            raise RuntimeError("Audit DB unreachable")

        mock_redis = MagicMock()
        mock_redis.set = AsyncMock()

        with (
            patch(_KIS, return_value=mock_kis),
            patch(_SESSION, side_effect=_session_side_effect),
            patch(_REDIS, return_value=mock_redis),
        ):
            result = await handle_market_close()

        assert "audit_error" in result
        # 감사 로그 실패와 무관하게 스냅샷은 저장돼야 함
        assert result.get("snapshot_saved") is True

    @pytest.mark.asyncio
    async def test_audit_log_metadata_structure(self):
        """감사 로그에 전달되는 metadata 구조 검증"""
        mock_kis = AsyncMock()
        mock_kis.get_kr_balance.return_value = _make_kis_balance()

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(fetchall=lambda: [("BUY", 3, 5_000_000.0)])
        mock_session.commit = AsyncMock()

        mock_audit = AsyncMock()
        mock_audit.log = AsyncMock()

        mock_redis = MagicMock()
        mock_redis.set = AsyncMock()

        with (
            patch(_KIS, return_value=mock_kis),
            patch(_SESSION, return_value=_mock_session_ctx(mock_session)),
            patch(_REDIS, return_value=mock_redis),
            patch(_AUDIT, return_value=mock_audit),
        ):
            await handle_market_close()

        call_kwargs = mock_audit.log.call_args.kwargs
        metadata = call_kwargs["metadata"]
        assert metadata["portfolio_value"] == 50_000_000.0
        assert metadata["cash_balance"] == 10_000_000.0
        assert metadata["positions_count"] == 2
        assert "BUY" in metadata["trade_stats"]
        assert metadata["trade_stats"]["BUY"]["count"] == 3

    @pytest.mark.asyncio
    async def test_empty_trade_stats(self):
        """금일 주문 없을 때 trade_stats가 빈 dict"""
        mock_kis = AsyncMock()
        mock_kis.get_kr_balance.return_value = _make_kis_balance()

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(fetchall=lambda: [])
        mock_session.commit = AsyncMock()

        mock_audit = AsyncMock()
        mock_audit.log = AsyncMock()

        mock_redis = MagicMock()
        mock_redis.set = AsyncMock()

        with (
            patch(_KIS, return_value=mock_kis),
            patch(_SESSION, return_value=_mock_session_ctx(mock_session)),
            patch(_REDIS, return_value=mock_redis),
            patch(_AUDIT, return_value=mock_audit),
        ):
            result = await handle_market_close()

        assert result["trade_stats"] == {}

    @pytest.mark.asyncio
    async def test_snapshot_ttl_30_days(self):
        """Redis 스냅샷 TTL이 30일(86400*30)인지 검증"""
        mock_kis = AsyncMock()
        mock_kis.get_kr_balance.return_value = _make_kis_balance()

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(fetchall=lambda: [])
        mock_session.commit = AsyncMock()

        mock_audit = AsyncMock()
        mock_audit.log = AsyncMock()

        mock_redis = MagicMock()
        mock_redis.set = AsyncMock()

        with (
            patch(_KIS, return_value=mock_kis),
            patch(_SESSION, return_value=_mock_session_ctx(mock_session)),
            patch(_REDIS, return_value=mock_redis),
            patch(_AUDIT, return_value=mock_audit),
        ):
            await handle_market_close()

        call_kwargs = mock_redis.set.call_args
        assert call_kwargs.kwargs.get("ex") == 86400 * 30


# ══════════════════════════════════════
# handle_post_market 보강 테스트
# ══════════════════════════════════════


def _make_post_market_mocks(
    snapshot_value=50_000_000.0,
    snapshot_cash=10_000_000.0,
    prev_value=49_000_000.0,
    trade_rows=None,
):
    """handle_post_market 테스트를 위한 공통 mock 세트 생성"""
    _today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snapshot = json.dumps(
        {
            "date": _today,
            "portfolio_value": snapshot_value,
            "cash_balance": snapshot_cash,
            "positions_count": 1,
            "positions": [
                {
                    "ticker": "005930",
                    "name": "삼성전자",
                    "quantity": 100,
                    "avg_price": 65000,
                    "current_price": 68000,
                    "eval_amount": 6800000,
                    "pnl_amount": 300000,
                    "pnl_percent": 4.62,
                }
            ],
        }
    )
    prev_snapshot = json.dumps({"portfolio_value": prev_value})

    async def _redis_get(key):
        if "snapshot" in key:
            if _today in key:
                return snapshot
            return prev_snapshot
        return None

    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(side_effect=_redis_get)
    mock_redis.set = AsyncMock()

    mock_session = AsyncMock()
    if trade_rows is None:
        trade_rows = []
    mock_session.execute.return_value = MagicMock(fetchall=lambda: trade_rows)

    mock_report = MagicMock()
    mock_report.daily_pnl = 1_000_000.0
    mock_report.daily_return_pct = 2.04
    mock_report.total_trades = len(trade_rows)
    mock_report.total_positions = 1
    mock_report.to_dict.return_value = {"daily_pnl": 1_000_000.0}

    mock_reporter = AsyncMock()
    mock_reporter.generate_report.return_value = mock_report
    mock_reporter.send_telegram_report.return_value = True

    mock_guard = MagicMock()
    mock_guard.state = MagicMock(current_drawdown=0.02, consecutive_losses=1)

    return mock_redis, mock_session, mock_reporter, mock_guard, mock_report


class TestHandlePostMarketEdgeCases:
    """handle_post_market 미커버 경로 보강"""

    @pytest.mark.asyncio
    async def test_trade_query_db_exception(self):
        """거래 내역 DB 조회 실패 시 trades_error 설정, 리포트는 정상 생성"""
        mock_redis, _, mock_reporter, mock_guard, _ = _make_post_market_mocks()

        mock_session = AsyncMock()
        mock_session.execute.side_effect = RuntimeError("DB timeout")

        with (
            patch(_REDIS, return_value=mock_redis),
            patch(_SESSION, return_value=_mock_session_ctx(mock_session)),
            patch(_REPORTER, return_value=mock_reporter),
            patch(_GUARD, return_value=mock_guard),
        ):
            result = await handle_post_market()

        assert "trades_error" in result
        # 거래 내역 실패해도 리포트 생성은 진행
        mock_reporter.generate_report.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_trading_guard_exception_uses_defaults(self):
        """TradingGuard 생성자 예외 시 drawdown=0, consecutive_losses=0 사용"""
        mock_redis, mock_session, mock_reporter, _, _ = _make_post_market_mocks()

        with (
            patch(_REDIS, return_value=mock_redis),
            patch(_SESSION, return_value=_mock_session_ctx(mock_session)),
            patch(_REPORTER, return_value=mock_reporter),
            patch(_GUARD, side_effect=RuntimeError("Guard init failed")),
        ):
            result = await handle_post_market()

        # 예외에도 리포트 생성 성공
        call_kwargs = mock_reporter.generate_report.call_args.kwargs
        assert call_kwargs["max_drawdown_today"] == 0.0
        assert call_kwargs["consecutive_losses"] == 0

    @pytest.mark.asyncio
    async def test_report_redis_save_exception(self):
        """리포트 Redis 저장 실패 시 report_save_error 설정"""
        mock_redis, mock_session, mock_reporter, mock_guard, _ = _make_post_market_mocks()

        # get은 정상, set에서 예외
        original_set = mock_redis.set

        async def _set_side_effect(key, value, **kwargs):
            if "report:daily" in key:
                raise RuntimeError("Redis write failed")
            return await original_set(key, value, **kwargs)

        mock_redis.set = AsyncMock(side_effect=_set_side_effect)

        with (
            patch(_REDIS, return_value=mock_redis),
            patch(_SESSION, return_value=_mock_session_ctx(mock_session)),
            patch(_REPORTER, return_value=mock_reporter),
            patch(_GUARD, return_value=mock_guard),
        ):
            result = await handle_post_market()

        assert "report_save_error" in result
        # 텔레그램은 정상 발송됐어야 함
        assert result.get("telegram_sent") is True

    @pytest.mark.asyncio
    async def test_report_generation_exception(self):
        """DailyReporter.generate_report() 예외 시 report_error 설정"""
        mock_redis, mock_session, mock_reporter, mock_guard, _ = _make_post_market_mocks()

        mock_reporter.generate_report.side_effect = RuntimeError("Report generation failed")

        with (
            patch(_REDIS, return_value=mock_redis),
            patch(_SESSION, return_value=_mock_session_ctx(mock_session)),
            patch(_REPORTER, return_value=mock_reporter),
            patch(_GUARD, return_value=mock_guard),
        ):
            result = await handle_post_market()

        assert "report_error" in result
        # 텔레그램 발송은 시도되지 않아야 함
        assert "telegram_sent" not in result

    @pytest.mark.asyncio
    async def test_position_weight_calculation(self):
        """포지션 weight가 eval_amount / portfolio_value_end 로 계산되는지"""
        mock_redis, mock_session, mock_reporter, mock_guard, _ = _make_post_market_mocks()

        with (
            patch(_REDIS, return_value=mock_redis),
            patch(_SESSION, return_value=_mock_session_ctx(mock_session)),
            patch(_REPORTER, return_value=mock_reporter),
            patch(_GUARD, return_value=mock_guard),
        ):
            await handle_post_market()

        call_kwargs = mock_reporter.generate_report.call_args.kwargs
        positions = call_kwargs["positions"]
        assert len(positions) == 1
        # weight = 6800000 / 50000000 = 0.136
        assert positions[0].weight == round(6_800_000 / 50_000_000, 4)

    @pytest.mark.asyncio
    async def test_report_ttl_90_days(self):
        """리포트 Redis 저장 TTL이 90일(86400*90)인지 검증"""
        mock_redis, mock_session, mock_reporter, mock_guard, _ = _make_post_market_mocks()

        with (
            patch(_REDIS, return_value=mock_redis),
            patch(_SESSION, return_value=_mock_session_ctx(mock_session)),
            patch(_REPORTER, return_value=mock_reporter),
            patch(_GUARD, return_value=mock_guard),
        ):
            await handle_post_market()

        # redis.set이 2번 호출됨 (RedisManager.get_client 는 동일 mock)
        # report:daily 키에 대한 set 호출 확인
        for call in mock_redis.set.call_args_list:
            key = call.args[0] if call.args else ""
            if "report:daily" in key:
                assert call.kwargs.get("ex") == 86400 * 90
                return
        pytest.fail("report:daily Redis set 호출을 찾을 수 없음")

    @pytest.mark.asyncio
    async def test_prev_snapshot_malformed_json(self):
        """전일 스냅샷 JSON 파싱 실패 시 initial_capital fallback"""
        _today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_snapshot = json.dumps(
            {
                "portfolio_value": 50_000_000.0,
                "cash_balance": 10_000_000.0,
                "positions": [
                    {
                        "ticker": "005930",
                        "name": "삼성전자",
                        "quantity": 100,
                        "avg_price": 65000,
                        "current_price": 68000,
                        "eval_amount": 6800000,
                        "pnl_amount": 300000,
                        "pnl_percent": 4.62,
                    }
                ],
            }
        )

        async def _redis_get(key):
            if "snapshot" in key:
                if _today in key:
                    return today_snapshot
                return "{INVALID_JSON"
            return None

        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(side_effect=_redis_get)
        mock_redis.set = AsyncMock()

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(fetchall=lambda: [])

        mock_report = MagicMock()
        mock_report.daily_pnl = 0.0
        mock_report.daily_return_pct = 0.0
        mock_report.total_trades = 0
        mock_report.total_positions = 1
        mock_report.to_dict.return_value = {}

        mock_reporter = AsyncMock()
        mock_reporter.generate_report.return_value = mock_report
        mock_reporter.send_telegram_report.return_value = True

        mock_guard = MagicMock()
        mock_guard.state = MagicMock(current_drawdown=0.0, consecutive_losses=0)

        mock_settings = MagicMock()
        mock_settings.risk.initial_capital_krw = 50_000_000.0

        with (
            patch(_REDIS, return_value=mock_redis),
            patch(_SESSION, return_value=_mock_session_ctx(mock_session)),
            patch(_REPORTER, return_value=mock_reporter),
            patch(_GUARD, return_value=mock_guard),
            patch(_SETTINGS, return_value=mock_settings),
        ):
            result = await handle_post_market()

        # JSON 파싱 실패 → prev_portfolio_value=0 → initial_capital fallback
        call_kwargs = mock_reporter.generate_report.call_args.kwargs
        assert call_kwargs["portfolio_value_start"] == 50_000_000.0

    @pytest.mark.asyncio
    async def test_null_trade_fields_handled(self):
        """거래 내역에 NULL 필드가 있어도 정상 처리"""
        trade_rows = [
            ("005930", "BUY", None, None, "FILLED", None),
        ]
        mock_redis, _, mock_reporter, mock_guard, _ = _make_post_market_mocks(trade_rows=trade_rows)

        mock_session = AsyncMock()
        mock_session.execute.return_value = MagicMock(fetchall=lambda: trade_rows)

        with (
            patch(_REDIS, return_value=mock_redis),
            patch(_SESSION, return_value=_mock_session_ctx(mock_session)),
            patch(_REPORTER, return_value=mock_reporter),
            patch(_GUARD, return_value=mock_guard),
        ):
            result = await handle_post_market()

        call_kwargs = mock_reporter.generate_report.call_args.kwargs
        trades = call_kwargs["trades"]
        assert len(trades) == 1
        assert trades[0].quantity == 0
        assert trades[0].price == 0.0
        assert trades[0].amount == 0.0
