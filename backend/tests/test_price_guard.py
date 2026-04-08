"""주문 경로 가격/시세 가드 유닛테스트.

근거: docs/security/security-integrity-roadmap.md §7.3
대상: core/order_executor/price_guard.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from config.constants import Market, OrderSide
from core.monitoring.metrics import (
    POST_TRADE_SLIPPAGE_ALERTS_TOTAL,
    PRE_TRADE_PRICE_REJECTS_TOTAL,
    QUOTE_FETCH_FAILURES_TOTAL,
    STALE_QUOTE_REJECTS_TOTAL,
)
from core.order_executor.price_guard import (
    PriceDeviationError,
    PriceGuardConfig,
    Quote,
    QuoteFetchError,
    StaleQuoteError,
    StaticQuoteProvider,
    assert_pre_trade_price,
    assert_quote_fresh,
    check_post_trade_slippage,
    compute_deviation_pct,
    fetch_and_validate_quote,
)


def _counter_value(counter, **labels) -> float:
    if labels:
        return counter.labels(**labels)._value.get()
    return counter._value.get()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ══════════════════════════════════════════════════════════════════════════════
# Quote / PriceGuardConfig
# ══════════════════════════════════════════════════════════════════════════════
class TestQuote:
    def test_valid_quote(self):
        q = Quote("005930", Market.KRX, 70000.0, _now())
        assert q.ticker == "005930"
        assert q.price == 70000.0

    def test_non_positive_price_rejected(self):
        with pytest.raises(ValueError, match="price must be > 0"):
            Quote("005930", Market.KRX, 0.0, _now())
        with pytest.raises(ValueError, match="price must be > 0"):
            Quote("005930", Market.KRX, -1.0, _now())

    def test_naive_datetime_rejected(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            Quote("005930", Market.KRX, 100.0, datetime(2026, 1, 1))


class TestPriceGuardConfig:
    def test_defaults(self):
        c = PriceGuardConfig()
        assert c.max_quote_age_seconds == 5.0
        assert c.max_pre_trade_deviation_pct == 0.02
        assert c.max_post_trade_slippage_pct == 0.01

    @pytest.mark.parametrize(
        "field",
        ["max_quote_age_seconds", "max_pre_trade_deviation_pct", "max_post_trade_slippage_pct"],
    )
    def test_non_positive_rejected(self, field):
        kwargs = {field: 0.0}
        with pytest.raises(ValueError):
            PriceGuardConfig(**kwargs)


# ══════════════════════════════════════════════════════════════════════════════
# compute_deviation_pct
# ══════════════════════════════════════════════════════════════════════════════
class TestComputeDeviationPct:
    def test_basic(self):
        assert compute_deviation_pct(100.0, 102.0) == pytest.approx(0.02)
        assert compute_deviation_pct(100.0, 98.0) == pytest.approx(0.02)
        assert compute_deviation_pct(100.0, 100.0) == 0.0

    def test_non_positive_reference_rejected(self):
        with pytest.raises(ValueError):
            compute_deviation_pct(0.0, 100.0)
        with pytest.raises(ValueError):
            compute_deviation_pct(-1.0, 100.0)


# ══════════════════════════════════════════════════════════════════════════════
# assert_quote_fresh
# ══════════════════════════════════════════════════════════════════════════════
class TestAssertQuoteFresh:
    def test_fresh_passes(self):
        now = _now()
        q = Quote("AAPL", Market.NASDAQ, 150.0, now - timedelta(seconds=1))
        assert_quote_fresh(q, max_age_seconds=5.0, now=now)

    def test_stale_rejected(self):
        now = _now()
        before = _counter_value(STALE_QUOTE_REJECTS_TOTAL, market="NASDAQ")
        q = Quote("AAPL", Market.NASDAQ, 150.0, now - timedelta(seconds=10))
        with pytest.raises(StaleQuoteError) as exc:
            assert_quote_fresh(q, max_age_seconds=5.0, now=now)
        assert exc.value.ticker == "AAPL"
        assert exc.value.age_seconds == pytest.approx(10.0, abs=0.01)
        assert exc.value.limit_seconds == 5.0
        after = _counter_value(STALE_QUOTE_REJECTS_TOTAL, market="NASDAQ")
        assert after == before + 1

    def test_future_quote_rejected(self):
        now = _now()
        before = _counter_value(STALE_QUOTE_REJECTS_TOTAL, market="KRX")
        q = Quote("005930", Market.KRX, 70000.0, now + timedelta(seconds=2))
        with pytest.raises(StaleQuoteError):
            assert_quote_fresh(q, max_age_seconds=5.0, now=now)
        after = _counter_value(STALE_QUOTE_REJECTS_TOTAL, market="KRX")
        assert after == before + 1

    def test_non_positive_max_age_rejected(self):
        q = Quote("AAPL", Market.NASDAQ, 150.0, _now())
        with pytest.raises(ValueError):
            assert_quote_fresh(q, max_age_seconds=0.0)


# ══════════════════════════════════════════════════════════════════════════════
# assert_pre_trade_price
# ══════════════════════════════════════════════════════════════════════════════
class TestAssertPreTradePrice:
    def test_buy_within_band_passes(self):
        assert_pre_trade_price(
            ticker="AAPL",
            market=Market.NASDAQ,
            side=OrderSide.BUY,
            reference_price=100.0,
            order_price=101.5,
            max_deviation_pct=0.02,
        )

    def test_buy_below_reference_passes(self):
        # 유리한 방향: BUY 가 싸게 주문 → 통과
        assert_pre_trade_price(
            ticker="AAPL",
            market=Market.NASDAQ,
            side=OrderSide.BUY,
            reference_price=100.0,
            order_price=50.0,
            max_deviation_pct=0.02,
        )

    def test_buy_above_upper_rejected(self):
        before = _counter_value(PRE_TRADE_PRICE_REJECTS_TOTAL, side="BUY", market="NASDAQ")
        with pytest.raises(PriceDeviationError) as exc:
            assert_pre_trade_price(
                ticker="AAPL",
                market=Market.NASDAQ,
                side=OrderSide.BUY,
                reference_price=100.0,
                order_price=105.0,
                max_deviation_pct=0.02,
            )
        assert exc.value.side == OrderSide.BUY
        assert exc.value.deviation_pct == pytest.approx(0.05)
        assert exc.value.limit_pct == 0.02
        after = _counter_value(PRE_TRADE_PRICE_REJECTS_TOTAL, side="BUY", market="NASDAQ")
        assert after == before + 1

    def test_sell_within_band_passes(self):
        assert_pre_trade_price(
            ticker="AAPL",
            market=Market.NASDAQ,
            side=OrderSide.SELL,
            reference_price=100.0,
            order_price=98.5,
            max_deviation_pct=0.02,
        )

    def test_sell_above_reference_passes(self):
        # 유리한 방향: SELL 이 비싸게 주문 → 통과
        assert_pre_trade_price(
            ticker="AAPL",
            market=Market.NASDAQ,
            side=OrderSide.SELL,
            reference_price=100.0,
            order_price=200.0,
            max_deviation_pct=0.02,
        )

    def test_sell_below_lower_rejected(self):
        before = _counter_value(PRE_TRADE_PRICE_REJECTS_TOTAL, side="SELL", market="KRX")
        with pytest.raises(PriceDeviationError) as exc:
            assert_pre_trade_price(
                ticker="005930",
                market=Market.KRX,
                side=OrderSide.SELL,
                reference_price=100.0,
                order_price=95.0,
                max_deviation_pct=0.02,
            )
        assert exc.value.deviation_pct == pytest.approx(0.05)
        after = _counter_value(PRE_TRADE_PRICE_REJECTS_TOTAL, side="SELL", market="KRX")
        assert after == before + 1

    def test_non_positive_params_rejected(self):
        with pytest.raises(ValueError):
            assert_pre_trade_price(
                ticker="X",
                market=Market.KRX,
                side=OrderSide.BUY,
                reference_price=0.0,
                order_price=1.0,
                max_deviation_pct=0.02,
            )
        with pytest.raises(ValueError):
            assert_pre_trade_price(
                ticker="X",
                market=Market.KRX,
                side=OrderSide.BUY,
                reference_price=1.0,
                order_price=0.0,
                max_deviation_pct=0.02,
            )
        with pytest.raises(ValueError):
            assert_pre_trade_price(
                ticker="X",
                market=Market.KRX,
                side=OrderSide.BUY,
                reference_price=1.0,
                order_price=1.0,
                max_deviation_pct=0.0,
            )


# ══════════════════════════════════════════════════════════════════════════════
# check_post_trade_slippage
# ══════════════════════════════════════════════════════════════════════════════
class TestCheckPostTradeSlippage:
    def test_within_band_returns_false(self):
        assert (
            check_post_trade_slippage(
                ticker="AAPL",
                market=Market.NASDAQ,
                side=OrderSide.BUY,
                reference_price=100.0,
                fill_price=100.5,
                max_slippage_pct=0.01,
            )
            is False
        )

    def test_buy_advantageous_returns_false(self):
        # BUY 가 더 싸게 체결됨 → 유리 → 경보 아님
        assert (
            check_post_trade_slippage(
                ticker="AAPL",
                market=Market.NASDAQ,
                side=OrderSide.BUY,
                reference_price=100.0,
                fill_price=50.0,
                max_slippage_pct=0.01,
            )
            is False
        )

    def test_sell_advantageous_returns_false(self):
        assert (
            check_post_trade_slippage(
                ticker="AAPL",
                market=Market.NASDAQ,
                side=OrderSide.SELL,
                reference_price=100.0,
                fill_price=200.0,
                max_slippage_pct=0.01,
            )
            is False
        )

    def test_buy_warn_severity(self):
        before = _counter_value(POST_TRADE_SLIPPAGE_ALERTS_TOTAL, severity="warn", market="NASDAQ")
        result = check_post_trade_slippage(
            ticker="AAPL",
            market=Market.NASDAQ,
            side=OrderSide.BUY,
            reference_price=100.0,
            fill_price=101.5,
            max_slippage_pct=0.01,
        )
        assert result is True
        after = _counter_value(POST_TRADE_SLIPPAGE_ALERTS_TOTAL, severity="warn", market="NASDAQ")
        assert after == before + 1

    def test_buy_critical_severity(self):
        before = _counter_value(POST_TRADE_SLIPPAGE_ALERTS_TOTAL, severity="critical", market="KRX")
        result = check_post_trade_slippage(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            reference_price=100.0,
            fill_price=103.0,  # 3% > 2 * 1%
            max_slippage_pct=0.01,
        )
        assert result is True
        after = _counter_value(POST_TRADE_SLIPPAGE_ALERTS_TOTAL, severity="critical", market="KRX")
        assert after == before + 1

    def test_sell_warn_severity(self):
        before = _counter_value(POST_TRADE_SLIPPAGE_ALERTS_TOTAL, severity="warn", market="NASDAQ")
        result = check_post_trade_slippage(
            ticker="AAPL",
            market=Market.NASDAQ,
            side=OrderSide.SELL,
            reference_price=100.0,
            fill_price=98.5,
            max_slippage_pct=0.01,
        )
        assert result is True
        after = _counter_value(POST_TRADE_SLIPPAGE_ALERTS_TOTAL, severity="warn", market="NASDAQ")
        assert after == before + 1

    def test_non_positive_fill_silent_skip(self):
        assert (
            check_post_trade_slippage(
                ticker="AAPL",
                market=Market.NASDAQ,
                side=OrderSide.BUY,
                reference_price=100.0,
                fill_price=0.0,
                max_slippage_pct=0.01,
            )
            is False
        )

    def test_non_positive_max_rejected(self):
        with pytest.raises(ValueError):
            check_post_trade_slippage(
                ticker="AAPL",
                market=Market.NASDAQ,
                side=OrderSide.BUY,
                reference_price=100.0,
                fill_price=101.0,
                max_slippage_pct=0.0,
            )


# ══════════════════════════════════════════════════════════════════════════════
# StaticQuoteProvider
# ══════════════════════════════════════════════════════════════════════════════
class TestStaticQuoteProvider:
    @pytest.mark.asyncio
    async def test_hit(self):
        q = Quote("AAPL", Market.NASDAQ, 150.0, _now())
        provider = StaticQuoteProvider({("AAPL", Market.NASDAQ): q})
        got = await provider.get_quote("AAPL", Market.NASDAQ)
        assert got is q

    @pytest.mark.asyncio
    async def test_miss_raises_fetch_error(self):
        provider = StaticQuoteProvider({})
        with pytest.raises(QuoteFetchError):
            await provider.get_quote("AAPL", Market.NASDAQ)


# ══════════════════════════════════════════════════════════════════════════════
# fetch_and_validate_quote
# ══════════════════════════════════════════════════════════════════════════════
class _RaisingProvider:
    def __init__(self, exc: Exception):
        self._exc = exc

    async def get_quote(self, ticker, market):
        raise self._exc


class _IdentityMismatchProvider:
    async def get_quote(self, ticker, market):
        return Quote("OTHER", Market.NASDAQ, 150.0, _now())


class TestFetchAndValidateQuote:
    @pytest.mark.asyncio
    async def test_success(self):
        now = _now()
        q = Quote("AAPL", Market.NASDAQ, 150.0, now - timedelta(seconds=1))
        provider = StaticQuoteProvider({("AAPL", Market.NASDAQ): q})
        got = await fetch_and_validate_quote(
            provider,
            ticker="AAPL",
            market=Market.NASDAQ,
            max_age_seconds=5.0,
            now=now,
        )
        assert got is q

    @pytest.mark.asyncio
    async def test_provider_error_normalized(self):
        before = _counter_value(QUOTE_FETCH_FAILURES_TOTAL, market="NASDAQ", reason="provider_error")
        provider = _RaisingProvider(QuoteFetchError("AAPL", "boom"))
        with pytest.raises(QuoteFetchError):
            await fetch_and_validate_quote(
                provider,
                ticker="AAPL",
                market=Market.NASDAQ,
                max_age_seconds=5.0,
            )
        after = _counter_value(QUOTE_FETCH_FAILURES_TOTAL, market="NASDAQ", reason="provider_error")
        assert after == before + 1

    @pytest.mark.asyncio
    async def test_unexpected_error_wrapped(self):
        before = _counter_value(QUOTE_FETCH_FAILURES_TOTAL, market="NASDAQ", reason="unexpected")
        provider = _RaisingProvider(RuntimeError("network down"))
        with pytest.raises(QuoteFetchError):
            await fetch_and_validate_quote(
                provider,
                ticker="AAPL",
                market=Market.NASDAQ,
                max_age_seconds=5.0,
            )
        after = _counter_value(QUOTE_FETCH_FAILURES_TOTAL, market="NASDAQ", reason="unexpected")
        assert after == before + 1

    @pytest.mark.asyncio
    async def test_identity_mismatch(self):
        before = _counter_value(
            QUOTE_FETCH_FAILURES_TOTAL,
            market="NASDAQ",
            reason="identity_mismatch",
        )
        with pytest.raises(QuoteFetchError):
            await fetch_and_validate_quote(
                _IdentityMismatchProvider(),
                ticker="AAPL",
                market=Market.NASDAQ,
                max_age_seconds=5.0,
            )
        after = _counter_value(
            QUOTE_FETCH_FAILURES_TOTAL,
            market="NASDAQ",
            reason="identity_mismatch",
        )
        assert after == before + 1

    @pytest.mark.asyncio
    async def test_stale_after_fetch(self):
        now = _now()
        q = Quote("AAPL", Market.NASDAQ, 150.0, now - timedelta(seconds=30))
        provider = StaticQuoteProvider({("AAPL", Market.NASDAQ): q})
        with pytest.raises(StaleQuoteError):
            await fetch_and_validate_quote(
                provider,
                ticker="AAPL",
                market=Market.NASDAQ,
                max_age_seconds=5.0,
                now=now,
            )
