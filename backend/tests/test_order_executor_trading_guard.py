"""
P0-5 OrderExecutor ↔ TradingGuard wiring 통합 테스트.

핵심 불변식: kill switch 가 활성화되면 OrderExecutor 는 KIS 클라이언트를
호출하지 않고 `TradingGuardBlocked` 를 전파한다. 관리자 API 에서 싱글톤
guard 에 kill switch 를 걸어도 executor 가 즉시 인지해야 한다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from config.constants import Market, OrderSide, OrderType
from core.monitoring.metrics import (
    TRADING_GUARD_BLOCKS_TOTAL,
    TRADING_GUARD_KILL_SWITCH_ACTIVE,
)
from core.order_executor.executor import OrderExecutor, OrderRequest
from core.trading_guard import (
    TradingGuard,
    TradingGuardBlocked,
    get_trading_guard,
    reset_trading_guard,
)


def _counter(code: str) -> float:
    return TRADING_GUARD_BLOCKS_TOTAL.labels(reason_code=code)._value.get()


@pytest.fixture(autouse=True)
def _reset_guard_singleton():
    reset_trading_guard()
    yield
    reset_trading_guard()


def _sample_request(
    side: OrderSide = OrderSide.BUY,
    order_type: OrderType = OrderType.MARKET,
    limit_price: float | None = None,
) -> OrderRequest:
    return OrderRequest(
        ticker="005930",
        market=Market.KRX,
        side=side,
        quantity=10,
        order_type=order_type,
        limit_price=limit_price,
        reason="unit-test",
    )


@pytest.mark.asyncio
async def test_kill_switch_blocks_order_and_does_not_call_kis():
    guard = TradingGuard()
    guard.activate_kill_switch("manual test activation")
    executor = OrderExecutor(dry_run=True, trading_guard=guard)

    before = _counter("kill_switch")

    with patch.object(executor, "_execute_market_order", new=AsyncMock()) as mock_market:
        with pytest.raises(TradingGuardBlocked) as exc_info:
            await executor.execute_order(_sample_request())
        mock_market.assert_not_called()

    assert exc_info.value.reason_code == "kill_switch"
    assert "Kill Switch" in exc_info.value.reason
    assert _counter("kill_switch") == before + 1.0
    assert TRADING_GUARD_KILL_SWITCH_ACTIVE._value.get() == 1


@pytest.mark.asyncio
async def test_singleton_kill_switch_propagates_to_default_executor():
    """관리자 경로에서 싱글톤 guard 를 조작하면 기본 executor 가 즉시 인지."""
    shared = get_trading_guard()
    shared.activate_kill_switch("admin activated")

    # guard 인자 없이 생성 → 싱글톤 사용
    executor = OrderExecutor(dry_run=True)
    assert executor._trading_guard is shared

    with patch.object(executor, "_execute_market_order", new=AsyncMock()) as mock_market:
        with pytest.raises(TradingGuardBlocked):
            await executor.execute_order(_sample_request())
        mock_market.assert_not_called()


@pytest.mark.asyncio
async def test_buy_limit_over_max_amount_blocked():
    guard = TradingGuard()
    # max_order_amount_krw 는 settings 기반 — 초과를 확실히 만들기 위해 매우 큰 값 사용.
    huge_price = guard._risk.max_order_amount_krw + 1.0
    executor = OrderExecutor(dry_run=True, trading_guard=guard)

    before = _counter("order_amount")

    with patch.object(executor, "_execute_limit_order", new=AsyncMock()) as mock_limit:
        with pytest.raises(TradingGuardBlocked) as exc_info:
            await executor.execute_order(
                OrderRequest(
                    ticker="005930",
                    market=Market.KRX,
                    side=OrderSide.BUY,
                    quantity=1,
                    order_type=OrderType.LIMIT,
                    limit_price=huge_price,
                )
            )
        mock_limit.assert_not_called()

    assert exc_info.value.reason_code == "order_amount"
    assert _counter("order_amount") == before + 1.0


@pytest.mark.asyncio
async def test_reason_code_mapping_daily_loss():
    guard = TradingGuard()
    # 일일 손실 한도 초과 상태 주입
    guard._state.daily_realized_pnl = -(guard._risk.daily_loss_limit_krw + 1.0)
    executor = OrderExecutor(dry_run=True, trading_guard=guard)

    before = _counter("daily_loss")
    with patch.object(executor, "_execute_market_order", new=AsyncMock()) as mock_market:
        with pytest.raises(TradingGuardBlocked) as exc_info:
            await executor.execute_order(_sample_request())
        mock_market.assert_not_called()

    # check_daily_loss_limit 이 kill switch 를 활성화 → 첫 블록은 kill_switch
    # 로 보고되는 것이 실제 동작. reason_code 는 kill switch 가 우선.
    assert exc_info.value.reason_code in ("daily_loss", "kill_switch")
    after_daily = _counter("daily_loss")
    after_kill = _counter("kill_switch")
    assert (after_daily + after_kill) >= before + 1.0


@pytest.mark.asyncio
async def test_allowed_order_reaches_executor_body():
    guard = TradingGuard()
    executor = OrderExecutor(dry_run=True, trading_guard=guard)

    from datetime import datetime, timezone

    from config.constants import OrderStatus
    from core.order_executor.executor import OrderResult

    fake = OrderResult(
        order_id="TEST-1",
        ticker="005930",
        market=Market.KRX,
        side=OrderSide.BUY,
        quantity=10,
        filled_quantity=10,
        avg_price=70000.0,
        status=OrderStatus.FILLED,
        executed_at=datetime.now(timezone.utc),
    )

    with (
        patch.object(executor, "_execute_market_order", new=AsyncMock(return_value=fake)) as mock_market,
        patch.object(executor, "_store_order", new=AsyncMock()),
        patch("core.order_executor.executor.async_session_factory") as mock_factory,
    ):
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = mock_ctx
        # AuditLogger.log 는 fail-open 이라 DB 실패해도 통과.
        result = await executor.execute_order(_sample_request())
        assert result.order_id == "TEST-1"
        mock_market.assert_awaited_once()


def test_reset_singleton_clears_kill_switch_gauge():
    shared = get_trading_guard()
    shared.activate_kill_switch("test")
    assert TRADING_GUARD_KILL_SWITCH_ACTIVE._value.get() == 1

    reset_trading_guard()
    assert TRADING_GUARD_KILL_SWITCH_ACTIVE._value.get() == 0
    assert get_trading_guard() is not shared
