"""
P1-정합성 ReconciliationRunner + TradingScheduler wiring 통합 테스트.

핵심 불변식 (Wiring Rule):
  1. mismatch 가 임계 초과 시 TradingGuard kill switch 가 활성화된다.
  2. kill switch 활성화는 OrderExecutor 에 즉시 전파된다 (P0-5 의 싱글톤
     공유와 결합되어야 의미가 있다).
  3. TradingScheduler 의 MIDDAY_CHECK / POST_MARKET 기본 핸들러가 등록된
     runner 를 실제로 호출한다.
  4. provider 장애는 metric 으로 관측되고 fail-closed 로 예외 전파된다.
"""

from __future__ import annotations

import pytest

from core.monitoring.metrics import (
    RECONCILIATION_LEDGER_DIFF_ABS,
    RECONCILIATION_MISMATCHES_TOTAL,
    RECONCILIATION_RUNS_TOTAL,
    TRADING_GUARD_KILL_SWITCH_ACTIVE,
)
from core.reconciliation import ReconciliationEngine
from core.reconciliation_runner import (
    CallablePositionProvider,
    ReconciliationRunner,
    StaticPositionProvider,
)
from core.trading_guard import TradingGuard, get_trading_guard, reset_trading_guard
from core.trading_scheduler import TradingScheduler


def _result_counter(label: str) -> float:
    return RECONCILIATION_RUNS_TOTAL.labels(result=label)._value.get()


def _mismatch_counter() -> float:
    return RECONCILIATION_MISMATCHES_TOTAL._value.get()


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_trading_guard()
    yield
    reset_trading_guard()


@pytest.mark.asyncio
async def test_matched_positions_do_not_activate_kill_switch():
    guard = TradingGuard()
    runner = ReconciliationRunner(
        engine=ReconciliationEngine(),
        broker_provider=StaticPositionProvider({"005930": 100.0, "000660": 50.0}),
        internal_provider=StaticPositionProvider({"005930": 100.0, "000660": 50.0}),
        guard=guard,
    )

    before_matched = _result_counter("matched")
    result = await runner.run()

    assert result.matched is True
    assert guard.state.kill_switch_on is False
    assert _result_counter("matched") == before_matched + 1.0
    assert RECONCILIATION_LEDGER_DIFF_ABS._value.get() == 0.0


@pytest.mark.asyncio
async def test_mismatch_above_threshold_activates_kill_switch():
    guard = TradingGuard()
    runner = ReconciliationRunner(
        engine=ReconciliationEngine(),
        broker_provider=StaticPositionProvider({"005930": 100.0}),
        internal_provider=StaticPositionProvider({"005930": 90.0}),
        guard=guard,
        mismatch_threshold=0,
    )

    before_mismatch = _result_counter("mismatch")
    before_count = _mismatch_counter()

    result = await runner.run()

    assert result.matched is False
    assert len(result.mismatches) == 1
    assert guard.state.kill_switch_on is True
    assert "Reconciliation mismatch" in guard.state.kill_switch_reason
    assert _result_counter("mismatch") == before_mismatch + 1.0
    assert _mismatch_counter() == before_count + 1.0
    assert RECONCILIATION_LEDGER_DIFF_ABS._value.get() == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_mismatch_below_threshold_does_not_activate_kill_switch():
    guard = TradingGuard()
    # threshold=2 → 1건 mismatch 는 활성화하지 않음.
    runner = ReconciliationRunner(
        engine=ReconciliationEngine(),
        broker_provider=StaticPositionProvider({"005930": 100.0}),
        internal_provider=StaticPositionProvider({"005930": 90.0}),
        guard=guard,
        mismatch_threshold=2,
    )

    result = await runner.run()
    assert result.matched is False
    assert guard.state.kill_switch_on is False


@pytest.mark.asyncio
async def test_provider_failure_increments_error_counter_and_raises():
    guard = TradingGuard()

    async def failing():
        raise RuntimeError("KIS API down")

    runner = ReconciliationRunner(
        engine=ReconciliationEngine(),
        broker_provider=CallablePositionProvider(failing),
        internal_provider=StaticPositionProvider({"005930": 100.0}),
        guard=guard,
    )

    before_error = _result_counter("error")
    with pytest.raises(RuntimeError, match="KIS API down"):
        await runner.run()
    assert _result_counter("error") == before_error + 1.0
    assert guard.state.kill_switch_on is False


@pytest.mark.asyncio
async def test_singleton_kill_switch_propagates_after_mismatch():
    """runner 가 싱글톤 guard 를 사용하면, 이후 OrderExecutor 도 즉시 차단."""
    shared = get_trading_guard()
    runner = ReconciliationRunner(
        engine=ReconciliationEngine(),
        broker_provider=StaticPositionProvider({"005930": 100.0}),
        internal_provider=StaticPositionProvider({"005930": 50.0}),
        # guard 미지정 → __post_init__ 에서 싱글톤 주입
    )
    assert runner.guard is shared

    await runner.run()
    assert shared.state.kill_switch_on is True
    assert TRADING_GUARD_KILL_SWITCH_ACTIVE._value.get() == 1


@pytest.mark.asyncio
async def test_scheduler_midday_check_invokes_runner():
    """TradingScheduler 의 MIDDAY_CHECK 기본 핸들러가 실제로 runner 를 호출."""
    scheduler = TradingScheduler()
    guard = TradingGuard()
    runner = ReconciliationRunner(
        engine=ReconciliationEngine(),
        broker_provider=StaticPositionProvider({"005930": 100.0}),
        internal_provider=StaticPositionProvider({"005930": 100.0}),
        guard=guard,
    )
    scheduler.register_reconciliation_runner(runner)

    result = await scheduler._default_handle_midday_check()
    assert result["reconciliation"]["wired"] is True
    assert result["reconciliation"]["matched"] is True


@pytest.mark.asyncio
async def test_scheduler_post_market_invokes_runner_and_kill_switch():
    """POST_MARKET 핸들러가 runner 를 호출하고, mismatch 시 kill switch 활성화."""
    scheduler = TradingScheduler()
    guard = TradingGuard()
    runner = ReconciliationRunner(
        engine=ReconciliationEngine(),
        broker_provider=StaticPositionProvider({"005930": 100.0}),
        internal_provider=StaticPositionProvider({"005930": 80.0}),
        guard=guard,
    )
    scheduler.register_reconciliation_runner(runner)

    result = await scheduler._default_handle_post_market()
    assert result["reconciliation"]["wired"] is True
    assert result["reconciliation"]["matched"] is False
    assert result["reconciliation"]["mismatch_count"] == 1
    assert guard.state.kill_switch_on is True


@pytest.mark.asyncio
async def test_scheduler_without_runner_skips_reconciliation():
    """runner 미주입 시 핸들러는 종전과 같이 stub 결과만 반환 (호환)."""
    scheduler = TradingScheduler()
    result = await scheduler._default_handle_midday_check()
    assert result["reconciliation"]["wired"] is False
    assert result["reconciliation"]["skipped"] is True


@pytest.mark.asyncio
async def test_negative_threshold_rejected():
    with pytest.raises(ValueError, match="mismatch_threshold"):
        ReconciliationRunner(
            engine=ReconciliationEngine(),
            broker_provider=StaticPositionProvider({}),
            internal_provider=StaticPositionProvider({}),
            mismatch_threshold=-1,
        )
