"""``core/portfolio_ledger.py`` 단위 테스트.

본 ledger 는 ReconciliationRunner 가 비교하는 내부 진실원천이므로,
체결 누적/0 잔량 정리/short 거부/snapshot 격리 네 가지 불변식이 모두
보장돼야 한다.
"""

from __future__ import annotations

import asyncio

import pytest

from config.constants import OrderSide
from core.portfolio_ledger import (
    LedgerInvariantError,
    PortfolioLedger,
    get_portfolio_ledger,
    reset_portfolio_ledger,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_portfolio_ledger()
    yield
    reset_portfolio_ledger()


@pytest.mark.asyncio
async def test_buy_then_sell_zeroes_position():
    ledger = PortfolioLedger()
    await ledger.record_fill("005930", OrderSide.BUY, 100)
    await ledger.record_fill("005930", OrderSide.SELL, 100)
    assert ledger.get_positions() == {}


@pytest.mark.asyncio
async def test_partial_sell_keeps_residual():
    ledger = PortfolioLedger()
    await ledger.record_fill("005930", OrderSide.BUY, 100)
    await ledger.record_fill("005930", OrderSide.SELL, 30)
    assert ledger.get_positions() == {"005930": 70}


@pytest.mark.asyncio
async def test_multiple_tickers_independent():
    ledger = PortfolioLedger()
    await ledger.record_fill("005930", OrderSide.BUY, 50)
    await ledger.record_fill("000660", OrderSide.BUY, 20)
    await ledger.record_fill("005930", OrderSide.BUY, 25)
    assert ledger.get_positions() == {"005930": 75, "000660": 20}


@pytest.mark.asyncio
async def test_short_position_rejected():
    ledger = PortfolioLedger()
    await ledger.record_fill("005930", OrderSide.BUY, 10)
    with pytest.raises(LedgerInvariantError, match="short position"):
        await ledger.record_fill("005930", OrderSide.SELL, 20)
    # 거부된 SELL 은 ledger 에 반영되지 않아야 한다.
    assert ledger.get_positions() == {"005930": 10}


@pytest.mark.asyncio
async def test_sell_without_buy_rejected():
    ledger = PortfolioLedger()
    with pytest.raises(LedgerInvariantError):
        await ledger.record_fill("005930", OrderSide.SELL, 1)
    assert ledger.get_positions() == {}


@pytest.mark.asyncio
async def test_snapshot_does_not_leak_internal_state():
    ledger = PortfolioLedger()
    await ledger.record_fill("005930", OrderSide.BUY, 10)
    snap = ledger.get_positions()
    snap["005930"] = 99999
    snap["INJECTED"] = 1
    # ledger 자체는 외부 mutation 의 영향을 받지 않아야 한다.
    assert ledger.get_positions() == {"005930": 10}


@pytest.mark.asyncio
async def test_invalid_ticker_rejected():
    ledger = PortfolioLedger()
    with pytest.raises(ValueError):
        await ledger.record_fill("", OrderSide.BUY, 1)
    with pytest.raises(ValueError):
        await ledger.record_fill("   ", OrderSide.BUY, 1)


@pytest.mark.asyncio
async def test_non_positive_quantity_rejected():
    ledger = PortfolioLedger()
    with pytest.raises(ValueError):
        await ledger.record_fill("005930", OrderSide.BUY, 0)
    with pytest.raises(ValueError):
        await ledger.record_fill("005930", OrderSide.BUY, -1)


@pytest.mark.asyncio
async def test_concurrent_fills_serialize_correctly():
    """동시 체결 50건이 누적 결과 50을 정확히 만들어야 한다."""
    ledger = PortfolioLedger()
    await asyncio.gather(*(ledger.record_fill("005930", OrderSide.BUY, 1) for _ in range(50)))
    assert ledger.get_positions() == {"005930": 50}


def test_singleton_returns_same_instance():
    a = get_portfolio_ledger()
    b = get_portfolio_ledger()
    assert a is b


def test_reset_clears_singleton():
    a = get_portfolio_ledger()
    reset_portfolio_ledger()
    b = get_portfolio_ledger()
    assert a is not b
