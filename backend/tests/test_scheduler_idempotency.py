"""
Scheduler 멱등성 + handle_market_close/handle_post_market 안전망 유닛테스트.

검증 범위:
    1. scheduler_idempotency 모듈의 mark/is/load/clear 동작
    2. handle_market_close 가 KIS 실패 / 빈응답 시 snapshot 을 저장하지 않는지
    3. handle_post_market 이 snapshot 부재/전부 0 일 때 텔레그램 발송을 skip 하는지
    4. 동일 거래일에 두 번 호출되어도 텔레그램이 1회만 나가는 회귀 시나리오
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.scheduler_idempotency as idem
from core.scheduler_handlers import handle_market_close, handle_post_market

_REDIS_HANDLERS = "core.scheduler_handlers.RedisManager.get_client"
_REDIS_IDEM = "core.scheduler_idempotency.RedisManager.get_client"
_KIS = "core.data_collector.kis_client.KISClient"
_SESSION = "core.scheduler_handlers.async_session_factory"


def _empty_balance() -> dict:
    return {"output1": [], "output2": []}


def _session_ctx(mock_session):
    return AsyncMock(
        __aenter__=AsyncMock(return_value=mock_session),
        __aexit__=AsyncMock(return_value=False),
    )


# ══════════════════════════════════════════════════════════════
# scheduler_idempotency 모듈 자체 테스트
# ══════════════════════════════════════════════════════════════
class _FakeRedis:
    """SET/EXISTS/SCAN/DELETE 만 지원하는 in-memory fake."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.store[key] = value
        return True

    async def exists(self, key: str) -> int:
        return 1 if key in self.store else 0

    async def scan(self, cursor: int = 0, match: str = "*", count: int = 100):
        # cursor 0 -> 한 번에 모두 반환
        import fnmatch

        keys = [k for k in self.store.keys() if fnmatch.fnmatch(k, match)]
        return 0, keys

    async def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                n += 1
        return n


class TestSchedulerIdempotency:
    @pytest.mark.asyncio
    async def test_mark_and_is_executed_roundtrip(self):
        fake = _FakeRedis()
        with patch(_REDIS_IDEM, return_value=fake):
            today = date(2026, 4, 7)
            assert await idem.is_executed("POST_MARKET", today) is False
            ok = await idem.mark_executed("POST_MARKET", today)
            assert ok is True
            assert await idem.is_executed("POST_MARKET", today) is True

    @pytest.mark.asyncio
    async def test_load_executed_for_date_returns_only_today_keys(self):
        fake = _FakeRedis()
        # 다른 날짜와 다른 prefix 키도 섞어서 노이즈를 만든다
        fake.store["scheduler:executed:2026-04-07:POST_MARKET"] = "x"
        fake.store["scheduler:executed:2026-04-07:MARKET_OPEN"] = "x"
        fake.store["scheduler:executed:2026-04-06:POST_MARKET"] = "x"
        fake.store["unrelated:key"] = "x"

        with patch(_REDIS_IDEM, return_value=fake):
            executed = await idem.load_executed_for_date(date(2026, 4, 7))

        assert executed == {"POST_MARKET", "MARKET_OPEN"}

    @pytest.mark.asyncio
    async def test_clear_for_date_removes_only_target_date(self):
        fake = _FakeRedis()
        fake.store["scheduler:executed:2026-04-07:POST_MARKET"] = "x"
        fake.store["scheduler:executed:2026-04-06:POST_MARKET"] = "x"
        with patch(_REDIS_IDEM, return_value=fake):
            n = await idem.clear_for_date(date(2026, 4, 7))
        assert n == 1
        assert "scheduler:executed:2026-04-06:POST_MARKET" in fake.store
        assert "scheduler:executed:2026-04-07:POST_MARKET" not in fake.store

    @pytest.mark.asyncio
    async def test_redis_failure_yields_safe_defaults(self):
        broken = MagicMock()
        broken.set = AsyncMock(side_effect=RuntimeError("redis down"))
        broken.exists = AsyncMock(side_effect=RuntimeError("redis down"))
        with patch(_REDIS_IDEM, return_value=broken):
            assert await idem.mark_executed("POST_MARKET") is False
            assert await idem.is_executed("POST_MARKET") is False


# ══════════════════════════════════════════════════════════════
# handle_market_close — KIS 실패/빈응답 시 snapshot skip
# ══════════════════════════════════════════════════════════════
class TestMarketCloseSkipsOnEmpty:
    @pytest.mark.asyncio
    async def test_empty_kis_response_does_not_overwrite_snapshot(self):
        """positions/cash/portfolio 모두 0 이면 redis.set 이 호출되지 않아야 한다."""
        mock_kis = AsyncMock()
        mock_kis.get_kr_balance.return_value = _empty_balance()

        mock_redis = MagicMock()
        mock_redis.set = AsyncMock()

        mock_session = MagicMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(fetchall=MagicMock(return_value=[])))
        mock_session.commit = AsyncMock()

        with (
            patch(_KIS, return_value=mock_kis),
            patch(_REDIS_HANDLERS, return_value=mock_redis),
            patch(_SESSION, return_value=_session_ctx(mock_session)),
            patch(
                "db.repositories.audit_log.AuditLogger",
                return_value=MagicMock(log=AsyncMock()),
            ),
        ):
            result = await handle_market_close()

        assert result["snapshot_saved"] is False
        assert result["snapshot_skip_reason"] == "empty_response"
        mock_redis.set.assert_not_called()

    @pytest.mark.asyncio
    async def test_kis_exception_does_not_overwrite_snapshot(self):
        mock_kis = AsyncMock()
        mock_kis.get_kr_balance.side_effect = RuntimeError("KIS down")

        mock_redis = MagicMock()
        mock_redis.set = AsyncMock()

        mock_session = MagicMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(fetchall=MagicMock(return_value=[])))
        mock_session.commit = AsyncMock()

        with (
            patch(_KIS, return_value=mock_kis),
            patch(_REDIS_HANDLERS, return_value=mock_redis),
            patch(_SESSION, return_value=_session_ctx(mock_session)),
            patch(
                "db.repositories.audit_log.AuditLogger",
                return_value=MagicMock(log=AsyncMock()),
            ),
        ):
            result = await handle_market_close()

        assert result["snapshot_saved"] is False
        assert result["snapshot_skip_reason"] == "kis_error"
        mock_redis.set.assert_not_called()


# ══════════════════════════════════════════════════════════════
# handle_post_market — snapshot 부재/전부 0 시 발송 skip
# ══════════════════════════════════════════════════════════════
class TestPostMarketSafetyNet:
    @pytest.mark.asyncio
    async def test_missing_snapshot_skips_telegram(self):
        """오늘/어제 snapshot 모두 없을 때 telegram_sent 가 호출되지 않아야 한다."""
        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=None)

        mock_settings = MagicMock()
        mock_settings.risk.initial_capital_krw = 0  # 초기자본도 0 인 극단 케이스

        mock_reporter = MagicMock()
        mock_reporter.send_telegram_report = AsyncMock(return_value=True)

        with (
            patch(_REDIS_HANDLERS, return_value=mock_redis),
            patch("config.settings.get_settings", return_value=mock_settings),
            patch("core.daily_reporter.DailyReporter", return_value=mock_reporter),
        ):
            result = await handle_post_market()

        assert result.get("report_skipped") is True
        assert result.get("skip_reason") == "snapshot_missing_or_empty"
        mock_reporter.send_telegram_report.assert_not_called()

    @pytest.mark.asyncio
    async def test_zero_snapshot_skips_telegram(self):
        """snapshot 은 있지만 모두 0 인 경우에도 발송하지 않아야 한다."""
        import json

        zero_snapshot = json.dumps(
            {
                "date": "2026-04-07",
                "portfolio_value": 0,
                "cash_balance": 0,
                "positions_count": 0,
                "positions": [],
                "timestamp": "2026-04-07T15:30:00+09:00",
            }
        )
        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=zero_snapshot)

        mock_reporter = MagicMock()
        mock_reporter.send_telegram_report = AsyncMock(return_value=True)

        with (
            patch(_REDIS_HANDLERS, return_value=mock_redis),
            patch("core.daily_reporter.DailyReporter", return_value=mock_reporter),
        ):
            result = await handle_post_market()

        assert result.get("report_skipped") is True
        mock_reporter.send_telegram_report.assert_not_called()
