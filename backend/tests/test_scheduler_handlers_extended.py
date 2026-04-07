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
