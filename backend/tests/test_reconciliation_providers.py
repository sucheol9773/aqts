"""``core/reconciliation_providers.py`` 단위 테스트."""

from __future__ import annotations

import pytest

from config.constants import OrderSide
from core.portfolio_ledger import PortfolioLedger, reset_portfolio_ledger
from core.reconciliation_providers import (
    BrokerPositionParseError,
    KISBrokerPositionProvider,
    LedgerPositionProvider,
)


class FakeKISClient:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.call_count = 0

    async def get_kr_balance(self):
        self.call_count += 1
        if self._exc is not None:
            raise self._exc
        return self._response


# ── KISBrokerPositionProvider ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parses_normal_balance_response():
    response = {
        "output1": [
            {"pdno": "005930", "hldg_qty": "100"},
            {"pdno": "000660", "hldg_qty": "50"},
        ]
    }
    provider = KISBrokerPositionProvider(kis_client=FakeKISClient(response=response))
    result = await provider.get_positions()
    assert result == {"005930": 100.0, "000660": 50.0}


@pytest.mark.asyncio
async def test_zero_quantity_rows_excluded():
    response = {
        "output1": [
            {"pdno": "005930", "hldg_qty": "100"},
            {"pdno": "000660", "hldg_qty": "0"},
        ]
    }
    provider = KISBrokerPositionProvider(kis_client=FakeKISClient(response=response))
    result = await provider.get_positions()
    assert result == {"005930": 100.0}


@pytest.mark.asyncio
async def test_missing_output1_returns_empty():
    provider = KISBrokerPositionProvider(kis_client=FakeKISClient(response={}))
    assert await provider.get_positions() == {}


@pytest.mark.asyncio
async def test_non_dict_response_rejected():
    provider = KISBrokerPositionProvider(kis_client=FakeKISClient(response="oops"))
    with pytest.raises(BrokerPositionParseError, match="unexpected response type"):
        await provider.get_positions()


@pytest.mark.asyncio
async def test_output1_not_list_rejected():
    provider = KISBrokerPositionProvider(kis_client=FakeKISClient(response={"output1": "oops"}))
    with pytest.raises(BrokerPositionParseError, match="must be list"):
        await provider.get_positions()


@pytest.mark.asyncio
async def test_row_missing_field_rejected():
    response = {"output1": [{"pdno": "005930"}]}
    provider = KISBrokerPositionProvider(kis_client=FakeKISClient(response=response))
    with pytest.raises(BrokerPositionParseError, match="missing required field"):
        await provider.get_positions()


@pytest.mark.asyncio
async def test_non_numeric_quantity_rejected():
    response = {"output1": [{"pdno": "005930", "hldg_qty": "many"}]}
    provider = KISBrokerPositionProvider(kis_client=FakeKISClient(response=response))
    with pytest.raises(BrokerPositionParseError, match="non-numeric"):
        await provider.get_positions()


@pytest.mark.asyncio
async def test_negative_quantity_rejected():
    response = {"output1": [{"pdno": "005930", "hldg_qty": "-1"}]}
    provider = KISBrokerPositionProvider(kis_client=FakeKISClient(response=response))
    with pytest.raises(BrokerPositionParseError, match="negative quantity"):
        await provider.get_positions()


@pytest.mark.asyncio
async def test_upstream_exception_wrapped():
    provider = KISBrokerPositionProvider(kis_client=FakeKISClient(exc=RuntimeError("boom")))
    with pytest.raises(BrokerPositionParseError, match="KIS upstream error"):
        await provider.get_positions()


# ── LedgerPositionProvider ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ledger_provider_returns_current_positions():
    reset_portfolio_ledger()
    ledger = PortfolioLedger()
    await ledger.record_fill("005930", OrderSide.BUY, 100)
    await ledger.record_fill("000660", OrderSide.BUY, 25)
    provider = LedgerPositionProvider(ledger=ledger)
    assert await provider.get_positions() == {"005930": 100, "000660": 25}


@pytest.mark.asyncio
async def test_ledger_provider_defaults_to_singleton():
    reset_portfolio_ledger()
    provider = LedgerPositionProvider()
    assert await provider.get_positions() == {}
