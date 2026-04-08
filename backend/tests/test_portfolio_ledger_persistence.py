"""PortfolioLedger DB 영속화 단위 테스트.

본 테스트는 ``LedgerRepository`` Protocol contract 와 ``PortfolioLedger``
의 cache ↔ repository 일관성 불변식을 검증한다. 운영 부트스트랩에서
사용하는 ``SqlPortfolioLedgerRepository`` 의 SQL 발화 시퀀스도 함께
검증한다.
"""

from __future__ import annotations

import asyncio
from typing import Dict, List, Tuple
from unittest.mock import AsyncMock, MagicMock

import pytest

from config.constants import OrderSide
from core.portfolio_ledger import (
    LedgerInvariantError,
    LedgerRepository,
    PortfolioLedger,
    configure_portfolio_ledger,
    get_portfolio_ledger,
    reset_portfolio_ledger,
)

# ─────────────────────────── 인메모리 가짜 리포지토리 ───────────────────────────


class FakeLedgerRepository:
    """원자적 ``apply_delta`` + ``load_all`` 구현. SQL 없이 contract 만 검증."""

    def __init__(self, initial: Dict[str, float] | None = None) -> None:
        self._state: Dict[str, float] = dict(initial or {})
        self.calls: List[Tuple[str, str, float]] = []
        self._lock = asyncio.Lock()

    async def load_all(self) -> Dict[str, float]:
        self.calls.append(("load_all", "", 0.0))
        return dict(self._state)

    async def apply_delta(self, ticker: str, delta: float) -> float:
        async with self._lock:
            self.calls.append(("apply_delta", ticker, delta))
            current = self._state.get(ticker, 0.0)
            new_qty = current + delta
            if new_qty < 0:
                raise LedgerInvariantError(f"refusing short for {ticker}: {current}+{delta}={new_qty}")
            if new_qty == 0:
                self._state.pop(ticker, None)
            else:
                self._state[ticker] = new_qty
            return new_qty


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_portfolio_ledger()
    yield
    reset_portfolio_ledger()


# ─────────────────────────────── Protocol 검증 ───────────────────────────────


def test_fake_repository_satisfies_protocol():
    repo = FakeLedgerRepository()
    assert isinstance(repo, LedgerRepository)


# ─────────────────────────────── hydrate 동작 ───────────────────────────────


@pytest.mark.asyncio
async def test_hydrate_loads_existing_positions():
    repo = FakeLedgerRepository(initial={"005930": 10.0, "AAPL": 4.0})
    ledger = PortfolioLedger(repository=repo)

    assert ledger.is_hydrated is False
    await ledger.hydrate()
    assert ledger.is_hydrated is True
    assert ledger.get_positions() == {"005930": 10.0, "AAPL": 4.0}


@pytest.mark.asyncio
async def test_hydrate_filters_non_positive_rows():
    # repository 가 0 또는 음수를 잘못 반환하더라도 cache 는 양수만 받아들임.
    repo = FakeLedgerRepository(initial={"GOOD": 5.0, "ZERO": 0.0, "NEG": -1.0})
    ledger = PortfolioLedger(repository=repo)
    await ledger.hydrate()
    assert ledger.get_positions() == {"GOOD": 5.0}


@pytest.mark.asyncio
async def test_hydrate_noop_without_repository():
    ledger = PortfolioLedger()
    assert ledger.is_hydrated is False
    await ledger.hydrate()
    assert ledger.is_hydrated is True
    assert ledger.get_positions() == {}


@pytest.mark.asyncio
async def test_hydrate_is_idempotent_overwrites_cache():
    repo = FakeLedgerRepository(initial={"005930": 10.0})
    ledger = PortfolioLedger(repository=repo)
    await ledger.hydrate()
    # repo 상태 변경 후 재 hydrate.
    repo._state.clear()
    repo._state["AAPL"] = 7.0
    await ledger.hydrate()
    assert ledger.get_positions() == {"AAPL": 7.0}


# ─────────────────────────── record_fill ↔ repository ──────────────────────────


@pytest.mark.asyncio
async def test_record_fill_delegates_to_repository_buy():
    repo = FakeLedgerRepository()
    ledger = PortfolioLedger(repository=repo)
    await ledger.hydrate()

    await ledger.record_fill("005930", OrderSide.BUY, 10.0)

    assert any(call[0] == "apply_delta" and call[1] == "005930" for call in repo.calls)
    assert ledger.get_positions() == {"005930": 10.0}
    assert repo._state == {"005930": 10.0}


@pytest.mark.asyncio
async def test_record_fill_sell_decrements_and_removes_zero_row():
    repo = FakeLedgerRepository(initial={"005930": 10.0})
    ledger = PortfolioLedger(repository=repo)
    await ledger.hydrate()

    await ledger.record_fill("005930", OrderSide.SELL, 10.0)
    assert ledger.get_positions() == {}
    assert repo._state == {}


@pytest.mark.asyncio
async def test_record_fill_partial_sell_keeps_residual_in_both_layers():
    repo = FakeLedgerRepository(initial={"005930": 10.0})
    ledger = PortfolioLedger(repository=repo)
    await ledger.hydrate()

    await ledger.record_fill("005930", OrderSide.SELL, 4.0)
    assert ledger.get_positions() == {"005930": 6.0}
    assert repo._state == {"005930": 6.0}


@pytest.mark.asyncio
async def test_short_attempt_raises_and_leaves_cache_unchanged():
    repo = FakeLedgerRepository(initial={"AAPL": 2.0})
    ledger = PortfolioLedger(repository=repo)
    await ledger.hydrate()

    with pytest.raises(LedgerInvariantError):
        await ledger.record_fill("AAPL", OrderSide.SELL, 5.0)

    # 양쪽 모두 변경되지 않아야 한다.
    assert ledger.get_positions() == {"AAPL": 2.0}
    assert repo._state == {"AAPL": 2.0}


@pytest.mark.asyncio
async def test_repository_failure_does_not_corrupt_cache():
    # repository 가 임의의 RuntimeError 를 raise 해도 cache 는 손대지 않는다.
    repo = FakeLedgerRepository(initial={"005930": 10.0})

    async def failing_apply_delta(ticker: str, delta: float) -> float:
        raise RuntimeError("simulated DB outage")

    repo.apply_delta = failing_apply_delta  # type: ignore[assignment]
    ledger = PortfolioLedger(repository=repo)
    await ledger.hydrate()

    with pytest.raises(RuntimeError, match="simulated DB outage"):
        await ledger.record_fill("005930", OrderSide.BUY, 5.0)

    assert ledger.get_positions() == {"005930": 10.0}


# ───────────────────────── configure_portfolio_ledger ──────────────────────────


@pytest.mark.asyncio
async def test_configure_portfolio_ledger_swaps_singleton():
    first = get_portfolio_ledger()
    assert first.repository is None

    repo = FakeLedgerRepository(initial={"005930": 3.0})
    configured = configure_portfolio_ledger(repo)
    assert configured is get_portfolio_ledger()
    assert configured.repository is repo

    await configured.hydrate()
    assert configured.get_positions() == {"005930": 3.0}


# ───────────────────────── SqlPortfolioLedgerRepository ────────────────────────


def _make_session_factory(execute_side_effects: List[MagicMock]):
    """주입된 결과 시퀀스를 차례로 반환하는 가짜 세션 + factory.

    ``async_sessionmaker[AsyncSession]`` 의 ``async with session_factory()``
    호출 패턴과 ``async with session.begin()`` 트랜잭션 패턴을 모방한다.
    """

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=execute_side_effects)

    txn_cm = AsyncMock()
    txn_cm.__aenter__.return_value = session
    txn_cm.__aexit__.return_value = None
    session.begin = MagicMock(return_value=txn_cm)

    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = session
    session_cm.__aexit__.return_value = None

    factory = MagicMock(return_value=session_cm)
    return factory, session


def _scalar_result(value):
    result = MagicMock()
    if value is None:
        result.first = MagicMock(return_value=None)
    else:
        result.first = MagicMock(return_value=(value,))
    return result


def _empty_result():
    result = MagicMock()
    result.first = MagicMock(return_value=None)
    return result


def _all_result(rows):
    result = MagicMock()
    result.all = MagicMock(return_value=rows)
    return result


@pytest.mark.asyncio
async def test_sql_repository_load_all_executes_filtered_select():
    from db.repositories.portfolio_positions import SqlPortfolioLedgerRepository

    select_result = _all_result([("005930", 10.0), ("AAPL", 4.0)])
    factory, session = _make_session_factory([select_result])
    repo = SqlPortfolioLedgerRepository(factory)

    loaded = await repo.load_all()
    assert loaded == {"005930": 10.0, "AAPL": 4.0}

    sql_text = str(session.execute.await_args_list[0].args[0])
    assert "FROM portfolio_positions" in sql_text
    assert "quantity > 0" in sql_text


@pytest.mark.asyncio
async def test_sql_repository_apply_delta_inserts_when_no_row():
    from db.repositories.portfolio_positions import SqlPortfolioLedgerRepository

    factory, session = _make_session_factory(
        [
            _empty_result(),  # SELECT FOR UPDATE → 없음
            _empty_result(),  # INSERT 반환값 (사용하지 않음)
        ]
    )
    repo = SqlPortfolioLedgerRepository(factory)

    new_qty = await repo.apply_delta("005930", 7.0)
    assert new_qty == 7.0

    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert any("FOR UPDATE" in s for s in statements)
    assert any("INSERT INTO portfolio_positions" in s for s in statements)


@pytest.mark.asyncio
async def test_sql_repository_apply_delta_updates_existing_row():
    from db.repositories.portfolio_positions import SqlPortfolioLedgerRepository

    factory, session = _make_session_factory(
        [
            _scalar_result(10.0),  # 기존 잔량 10
            _empty_result(),  # UPDATE 반환값
        ]
    )
    repo = SqlPortfolioLedgerRepository(factory)

    new_qty = await repo.apply_delta("005930", -3.0)
    assert new_qty == 7.0

    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert any("UPDATE portfolio_positions" in s for s in statements)


@pytest.mark.asyncio
async def test_sql_repository_apply_delta_deletes_when_zero():
    from db.repositories.portfolio_positions import SqlPortfolioLedgerRepository

    factory, session = _make_session_factory(
        [
            _scalar_result(5.0),
            _empty_result(),  # DELETE
        ]
    )
    repo = SqlPortfolioLedgerRepository(factory)

    new_qty = await repo.apply_delta("005930", -5.0)
    assert new_qty == 0.0

    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert any("DELETE FROM portfolio_positions" in s for s in statements)


@pytest.mark.asyncio
async def test_sql_repository_apply_delta_rejects_short():
    from db.repositories.portfolio_positions import SqlPortfolioLedgerRepository

    factory, session = _make_session_factory([_scalar_result(2.0)])
    repo = SqlPortfolioLedgerRepository(factory)

    with pytest.raises(LedgerInvariantError):
        await repo.apply_delta("AAPL", -5.0)

    # SELECT 외의 mutation 은 발생하지 않아야 한다 (FOR UPDATE 는 select 의 일부).
    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert not any("INSERT INTO portfolio_positions" in s for s in statements)
    assert not any("UPDATE portfolio_positions" in s for s in statements)
    assert not any("DELETE FROM portfolio_positions" in s for s in statements)
