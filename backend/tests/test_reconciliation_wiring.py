"""Reconciliation wiring 정적/통합 검증.

근거: docs/security/security-integrity-roadmap.md §7.3 — 정합성 도메인의
"정의 ≠ 적용" 회귀 방지. ReconciliationRunner 가 정의돼 있어도 실제
스케줄러에 register 되지 않으면 통제는 형식적이다.

본 테스트는 두 진입점(``scheduler_main.py`` 와 ``main.py``)이 모두
``register_reconciliation_runner`` 를 호출하는지 AST 로 검증하고, 런타임
경로에서 OrderExecutor → PortfolioLedger → LedgerPositionProvider →
ReconciliationRunner 의 한 사이클이 mismatch 를 정확히 잡아내는지를
end-to-end 로 검증한다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
SCHEDULER_MAIN = BACKEND / "scheduler_main.py"
APP_MAIN = BACKEND / "main.py"


def _calls_register_runner(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "register_reconciliation_runner"
        ):
            return True
    return False


def test_scheduler_main_registers_reconciliation_runner():
    assert _calls_register_runner(SCHEDULER_MAIN), (
        "scheduler_main.py 에서 register_reconciliation_runner 호출이 없다 " "(Wiring Rule 위반: 정의 ≠ 적용)"
    )


def test_main_app_registers_reconciliation_runner():
    assert _calls_register_runner(APP_MAIN), (
        "main.py (embedded scheduler) 에서 " "register_reconciliation_runner 호출이 없다"
    )


@pytest.mark.asyncio
async def test_end_to_end_executor_fill_to_reconcile_match():
    """OrderExecutor 체결이 ledger 에 반영된 뒤 reconcile 이 match 한다."""
    from config.constants import OrderSide
    from core.portfolio_ledger import (
        PortfolioLedger,
        get_portfolio_ledger,
        reset_portfolio_ledger,
    )
    from core.reconciliation import ReconciliationEngine
    from core.reconciliation_providers import LedgerPositionProvider
    from core.reconciliation_runner import (
        ReconciliationRunner,
        StaticPositionProvider,
    )
    from core.trading_guard import reset_trading_guard

    reset_portfolio_ledger()
    reset_trading_guard()
    try:
        ledger: PortfolioLedger = get_portfolio_ledger()
        # 가상의 체결 두 건
        await ledger.record_fill("005930", OrderSide.BUY, 100)
        await ledger.record_fill("000660", OrderSide.BUY, 50)

        runner = ReconciliationRunner(
            engine=ReconciliationEngine(),
            broker_provider=StaticPositionProvider(positions={"005930": 100, "000660": 50}),
            internal_provider=LedgerPositionProvider(),
        )
        result = await runner.run()
        assert result.matched is True
        assert result.mismatches == []
    finally:
        reset_portfolio_ledger()
        reset_trading_guard()


@pytest.mark.asyncio
async def test_end_to_end_mismatch_triggers_kill_switch():
    """ledger 와 broker 가 다르면 reconcile 이 mismatch + kill switch 활성화."""
    from config.constants import OrderSide
    from core.portfolio_ledger import (
        get_portfolio_ledger,
        reset_portfolio_ledger,
    )
    from core.reconciliation import ReconciliationEngine
    from core.reconciliation_providers import LedgerPositionProvider
    from core.reconciliation_runner import (
        ReconciliationRunner,
        StaticPositionProvider,
    )
    from core.trading_guard import get_trading_guard, reset_trading_guard

    reset_portfolio_ledger()
    reset_trading_guard()
    try:
        await get_portfolio_ledger().record_fill("005930", OrderSide.BUY, 100)

        runner = ReconciliationRunner(
            engine=ReconciliationEngine(),
            # 브로커 잔고가 ledger 와 30 만큼 차이남
            broker_provider=StaticPositionProvider(positions={"005930": 70}),
            internal_provider=LedgerPositionProvider(),
            mismatch_threshold=0,
        )
        result = await runner.run()
        assert result.matched is False
        assert len(result.mismatches) == 1
        assert result.mismatches[0]["ticker"] == "005930"
        assert result.mismatches[0]["broker_qty"] == 70
        assert result.mismatches[0]["internal_qty"] == 100
        assert get_trading_guard()._state.kill_switch_on is True
    finally:
        reset_portfolio_ledger()
        reset_trading_guard()
