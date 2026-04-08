"""KISQuoteProvider 유닛테스트.

근거: docs/security/security-integrity-roadmap.md §7.3
대상: core/order_executor/quote_provider_kis.py
"""

from __future__ import annotations

import asyncio
import time

import pytest

from config.constants import Market
from core.monitoring.metrics import (
    QUOTE_CACHE_HITS_TOTAL,
    QUOTE_CACHE_MISSES_TOTAL,
)
from core.order_executor.price_guard import Quote, QuoteFetchError
from core.order_executor.quote_provider_kis import (
    KISQuoteProvider,
    KISQuoteProviderConfig,
    get_kis_quote_provider,
    reset_kis_quote_provider,
)


def _hits(market: str) -> float:
    return QUOTE_CACHE_HITS_TOTAL.labels(market=market)._value.get()


def _misses(market: str) -> float:
    return QUOTE_CACHE_MISSES_TOTAL.labels(market=market)._value.get()


# ══════════════════════════════════════════════════════════════════════════════
# Fake KISClient
# ══════════════════════════════════════════════════════════════════════════════
class FakeKISClient:
    def __init__(
        self,
        kr_response=None,
        us_response=None,
        kr_exc: Exception | None = None,
        us_exc: Exception | None = None,
    ):
        self.kr_response = kr_response if kr_response is not None else {"output": {"stck_prpr": "70000"}}
        self.us_response = us_response if us_response is not None else {"output": {"last": "150.5"}}
        self.kr_exc = kr_exc
        self.us_exc = us_exc
        self.kr_calls: list[str] = []
        self.us_calls: list[tuple[str, str]] = []

    async def get_kr_stock_price(self, ticker: str) -> dict:
        self.kr_calls.append(ticker)
        if self.kr_exc is not None:
            raise self.kr_exc
        return self.kr_response

    async def get_us_stock_price(self, ticker: str, exchange: str = "NAS") -> dict:
        self.us_calls.append((ticker, exchange))
        if self.us_exc is not None:
            raise self.us_exc
        return self.us_response


# ══════════════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════════════
class TestConfig:
    def test_defaults(self):
        c = KISQuoteProviderConfig()
        assert c.cache_ttl_seconds == 1.5
        assert c.max_cache_entries == 4096

    @pytest.mark.parametrize(
        "field,value",
        [
            ("cache_ttl_seconds", 0.0),
            ("cache_ttl_seconds", -0.1),
            ("max_cache_entries", 0),
            ("max_cache_entries", -1),
        ],
    )
    def test_invalid(self, field, value):
        with pytest.raises(ValueError):
            KISQuoteProviderConfig(**{field: value})


# ══════════════════════════════════════════════════════════════════════════════
# 기본 fetch 경로
# ══════════════════════════════════════════════════════════════════════════════
class TestFetchKR:
    @pytest.mark.asyncio
    async def test_kr_basic_fetch(self):
        fake = FakeKISClient(kr_response={"output": {"stck_prpr": "70500"}})
        provider = KISQuoteProvider(kis_client=fake)
        q = await provider.get_quote("005930", Market.KRX)
        assert isinstance(q, Quote)
        assert q.ticker == "005930"
        assert q.market == Market.KRX
        assert q.price == 70500.0
        assert q.fetched_at.tzinfo is not None
        assert fake.kr_calls == ["005930"]

    @pytest.mark.asyncio
    async def test_kr_missing_output(self):
        fake = FakeKISClient(kr_response={})
        provider = KISQuoteProvider(kis_client=fake)
        with pytest.raises(QuoteFetchError, match="missing 'output'"):
            await provider.get_quote("005930", Market.KRX)

    @pytest.mark.asyncio
    async def test_kr_missing_field(self):
        fake = FakeKISClient(kr_response={"output": {}})
        provider = KISQuoteProvider(kis_client=fake)
        with pytest.raises(QuoteFetchError, match="missing 'stck_prpr'"):
            await provider.get_quote("005930", Market.KRX)

    @pytest.mark.asyncio
    async def test_kr_non_numeric(self):
        fake = FakeKISClient(kr_response={"output": {"stck_prpr": "abc"}})
        provider = KISQuoteProvider(kis_client=fake)
        with pytest.raises(QuoteFetchError, match="not numeric"):
            await provider.get_quote("005930", Market.KRX)

    @pytest.mark.asyncio
    async def test_kr_zero_price(self):
        fake = FakeKISClient(kr_response={"output": {"stck_prpr": "0"}})
        provider = KISQuoteProvider(kis_client=fake)
        with pytest.raises(QuoteFetchError, match="non-positive"):
            await provider.get_quote("005930", Market.KRX)

    @pytest.mark.asyncio
    async def test_kr_upstream_exception_wrapped(self):
        fake = FakeKISClient(kr_exc=RuntimeError("KIS network down"))
        provider = KISQuoteProvider(kis_client=fake)
        with pytest.raises(QuoteFetchError, match="KIS upstream error"):
            await provider.get_quote("005930", Market.KRX)


class TestFetchUS:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "market,exchange",
        [
            (Market.NASDAQ, "NAS"),
            (Market.NYSE, "NYS"),
            (Market.AMEX, "AMS"),
        ],
    )
    async def test_us_routing(self, market, exchange):
        fake = FakeKISClient(us_response={"output": {"last": "150.5"}})
        provider = KISQuoteProvider(kis_client=fake)
        q = await provider.get_quote("AAPL", market)
        assert q.price == 150.5
        assert q.market == market
        assert fake.us_calls == [("AAPL", exchange)]
        assert fake.kr_calls == []

    @pytest.mark.asyncio
    async def test_us_missing_field(self):
        fake = FakeKISClient(us_response={"output": {}})
        provider = KISQuoteProvider(kis_client=fake)
        with pytest.raises(QuoteFetchError, match="missing 'last'"):
            await provider.get_quote("AAPL", Market.NASDAQ)

    @pytest.mark.asyncio
    async def test_us_non_dict_response(self):
        fake = FakeKISClient(us_response=["not", "a", "dict"])  # type: ignore[arg-type]
        provider = KISQuoteProvider(kis_client=fake)
        with pytest.raises(QuoteFetchError, match="not a dict"):
            await provider.get_quote("AAPL", Market.NASDAQ)


class TestEmptyTicker:
    @pytest.mark.asyncio
    async def test_empty_ticker_rejected_before_call(self):
        fake = FakeKISClient()
        provider = KISQuoteProvider(kis_client=fake)
        with pytest.raises(QuoteFetchError, match="non-empty"):
            await provider.get_quote("", Market.KRX)
        assert fake.kr_calls == []


# ══════════════════════════════════════════════════════════════════════════════
# 캐시 동작
# ══════════════════════════════════════════════════════════════════════════════
class TestCache:
    @pytest.mark.asyncio
    async def test_cache_hit_returns_same_quote_without_extra_call(self):
        fake = FakeKISClient(kr_response={"output": {"stck_prpr": "70000"}})
        provider = KISQuoteProvider(kis_client=fake, config=KISQuoteProviderConfig(cache_ttl_seconds=10.0))
        before_hits = _hits("KRX")
        before_misses = _misses("KRX")

        q1 = await provider.get_quote("005930", Market.KRX)
        q2 = await provider.get_quote("005930", Market.KRX)

        assert q1 is q2
        assert len(fake.kr_calls) == 1
        assert _misses("KRX") == before_misses + 1
        assert _hits("KRX") == before_hits + 1

    @pytest.mark.asyncio
    async def test_cache_expiry_triggers_refetch(self):
        fake = FakeKISClient(kr_response={"output": {"stck_prpr": "70000"}})
        provider = KISQuoteProvider(
            kis_client=fake,
            config=KISQuoteProviderConfig(cache_ttl_seconds=0.05),
        )
        await provider.get_quote("005930", Market.KRX)
        await asyncio.sleep(0.1)
        await provider.get_quote("005930", Market.KRX)
        assert len(fake.kr_calls) == 2

    @pytest.mark.asyncio
    async def test_cache_separates_markets(self):
        fake = FakeKISClient()
        provider = KISQuoteProvider(kis_client=fake)
        await provider.get_quote("AAA", Market.KRX)
        await provider.get_quote("AAA", Market.NASDAQ)
        assert len(fake.kr_calls) == 1
        assert len(fake.us_calls) == 1

    @pytest.mark.asyncio
    async def test_invalidate_drops_entry(self):
        fake = FakeKISClient()
        provider = KISQuoteProvider(kis_client=fake)
        await provider.get_quote("005930", Market.KRX)
        provider.invalidate("005930", Market.KRX)
        await provider.get_quote("005930", Market.KRX)
        assert len(fake.kr_calls) == 2

    @pytest.mark.asyncio
    async def test_clear_resets_all(self):
        fake = FakeKISClient()
        provider = KISQuoteProvider(kis_client=fake)
        await provider.get_quote("005930", Market.KRX)
        await provider.get_quote("AAPL", Market.NASDAQ)
        provider.clear()
        await provider.get_quote("005930", Market.KRX)
        await provider.get_quote("AAPL", Market.NASDAQ)
        assert len(fake.kr_calls) == 2
        assert len(fake.us_calls) == 2

    @pytest.mark.asyncio
    async def test_cache_eviction_when_full(self):
        fake = FakeKISClient()
        provider = KISQuoteProvider(
            kis_client=fake,
            config=KISQuoteProviderConfig(cache_ttl_seconds=10.0, max_cache_entries=2),
        )
        await provider.get_quote("A", Market.KRX)
        await provider.get_quote("B", Market.KRX)
        await provider.get_quote("C", Market.KRX)
        assert len(provider._cache) == 2

    @pytest.mark.asyncio
    async def test_concurrent_misses_collapse_to_one_upstream_call(self):
        """동일 키 동시 미스 → upstream 1회만 호출 (stampede 방지)."""

        call_count = {"n": 0}

        class SlowKIS(FakeKISClient):
            async def get_kr_stock_price(self, ticker: str) -> dict:
                call_count["n"] += 1
                await asyncio.sleep(0.05)
                return {"output": {"stck_prpr": "70000"}}

        provider = KISQuoteProvider(
            kis_client=SlowKIS(),
            config=KISQuoteProviderConfig(cache_ttl_seconds=10.0),
        )
        results = await asyncio.gather(*[provider.get_quote("005930", Market.KRX) for _ in range(10)])
        assert call_count["n"] == 1
        assert all(r is results[0] for r in results)


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════
class TestSingleton:
    def test_singleton_returns_same_instance(self):
        reset_kis_quote_provider()
        a = get_kis_quote_provider()
        b = get_kis_quote_provider()
        assert a is b
        reset_kis_quote_provider()

    def test_reset_creates_new_instance(self):
        reset_kis_quote_provider()
        a = get_kis_quote_provider()
        reset_kis_quote_provider()
        b = get_kis_quote_provider()
        assert a is not b
        reset_kis_quote_provider()


# ══════════════════════════════════════════════════════════════════════════════
# Quote 객체 무결성 (시세 시각이 단조증가, max_quote_age_seconds 미만)
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_quote_fetched_at_is_recent():
    fake = FakeKISClient()
    provider = KISQuoteProvider(kis_client=fake)
    before = time.time()
    q = await provider.get_quote("005930", Market.KRX)
    after = time.time()
    ts = q.fetched_at.timestamp()
    assert before - 1 <= ts <= after + 1
