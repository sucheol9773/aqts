"""KIS 기반 실시간 시세 Provider (TTL 캐시 + 멀티 마켓 라우팅).

근거: docs/security/security-integrity-roadmap.md §7.3 — Stale quote /
pre-trade / post-trade 가드의 운영 의존성. price_guard.py 는 ``QuoteProvider``
Protocol 만 정의하며 실제 시세 획득은 본 모듈에서 담당한다.

주요 속성:
    1. **TTL 캐시**: ``(ticker, market)`` 키로 ``Quote`` 객체를 지정 시간
       동안 재사용한다. 기본 TTL 은 ``1.5s`` 로 ``PriceGuardConfig.
       max_quote_age_seconds`` (5초) 보다 충분히 작아 캐시된 quote 가
       guard 의 stale 임계를 절대 초과하지 않는다.
    2. **Stampede 방지**: 동일 키에 대해 ``asyncio.Lock`` 으로 직렬화하여
       동시에 다수의 KIS 호출이 발생하지 않게 한다.
    3. **마켓 라우팅**: ``Market.KRX`` 는 KR 시세 API, NYSE/NASDAQ/AMEX 는
       해외 시세 API 로 분기한다. 미지원 마켓은 즉시 ``QuoteFetchError``.
    4. **fail-closed**: KIS 응답이 비어있거나 가격이 0/음수이거나 파싱이
       실패하면 ``QuoteFetchError`` 로 정규화한다. price_guard 의
       ``fetch_and_validate_quote`` 가 이를 받아 카운터를 증가시킨다.
    5. **관측**: 캐시 히트/미스 카운터 + 미스 시 KIS 호출 latency 를
       Histogram 으로 기록.

OrderExecutor 와 분리된 별도 모듈이며, 본 파일은 KISClient 를 *주입* 받는
구조이므로 단위 테스트에서 쉽게 fake client 로 교체할 수 있다.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from config.constants import Market
from config.logging import logger
from core.data_collector.kis_client import KISClient
from core.monitoring.metrics import (
    QUOTE_CACHE_HITS_TOTAL,
    QUOTE_CACHE_MISSES_TOTAL,
    QUOTE_FETCH_LATENCY_SECONDS,
)
from core.order_executor.price_guard import Quote, QuoteFetchError

# ══════════════════════════════════════════════════════════════════════════════
# 마켓 라우팅
# ══════════════════════════════════════════════════════════════════════════════
# Market enum → KIS 해외 거래소 코드 (EXCD).
_US_EXCHANGE_CODE: dict[Market, str] = {
    Market.NASDAQ: "NAS",
    Market.NYSE: "NYS",
    Market.AMEX: "AMS",
}


@dataclass
class _CacheEntry:
    quote: Quote
    expires_monotonic: float


@dataclass
class KISQuoteProviderConfig:
    """KISQuoteProvider 동작 파라미터.

    Attributes:
        cache_ttl_seconds: 시세 캐시 유지 시간. ``PriceGuardConfig.
            max_quote_age_seconds`` 보다 반드시 작아야 한다 (기본 1.5s
            < 5.0s).
        max_cache_entries: 캐시 메모리 상한. 초과 시 LRU 가 아닌 단순
            ``oldest expires_monotonic`` 기준으로 1건 evict.
    """

    cache_ttl_seconds: float = 1.5
    max_cache_entries: int = 4096

    def __post_init__(self) -> None:
        if self.cache_ttl_seconds <= 0:
            raise ValueError("cache_ttl_seconds must be > 0")
        if self.max_cache_entries <= 0:
            raise ValueError("max_cache_entries must be > 0")


class KISQuoteProvider:
    """KIS API + TTL 캐시 기반 ``QuoteProvider`` 구현.

    구조적 의존: ``KISClient`` 의 ``get_kr_stock_price`` /
    ``get_us_stock_price``. 응답 스키마 해석은 본 모듈에 격리되어 있어
    외부 어디서도 raw KIS dict 를 노출하지 않는다.
    """

    def __init__(
        self,
        kis_client: Optional[KISClient] = None,
        config: Optional[KISQuoteProviderConfig] = None,
    ):
        self._kis = kis_client if kis_client is not None else KISClient()
        self._config = config or KISQuoteProviderConfig()
        self._cache: dict[tuple[str, Market], _CacheEntry] = {}
        self._locks: dict[tuple[str, Market], asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    # ── 공용 API ────────────────────────────────────────────────────────
    async def get_quote(self, ticker: str, market: Market) -> Quote:
        """캐시 우선, 없으면 KIS 에 조회 후 캐시에 저장.

        Raises:
            QuoteFetchError: 미지원 마켓 / KIS 호출 실패 / 응답 파싱
                실패 / 가격이 0 이하 / 응답이 비어있는 경우.
        """
        if not ticker:
            raise QuoteFetchError(ticker, "ticker must be non-empty")

        key = (ticker, market)

        # 1차 캐시 확인 (락 없이)
        cached = self._lookup_cache(key)
        if cached is not None:
            QUOTE_CACHE_HITS_TOTAL.labels(market=market.value).inc()
            return cached

        # 캐시 미스 → 키별 lock 획득 후 더블체크 (stampede 방지)
        lock = await self._get_lock(key)
        async with lock:
            cached = self._lookup_cache(key)
            if cached is not None:
                QUOTE_CACHE_HITS_TOTAL.labels(market=market.value).inc()
                return cached

            QUOTE_CACHE_MISSES_TOTAL.labels(market=market.value).inc()
            quote = await self._fetch_upstream(ticker, market)
            self._store_cache(key, quote)
            return quote

    def invalidate(self, ticker: str, market: Market) -> None:
        """특정 (ticker, market) 캐시 항목 제거."""
        self._cache.pop((ticker, market), None)

    def clear(self) -> None:
        """전체 캐시/락 초기화 (테스트 / kill switch 용)."""
        self._cache.clear()
        self._locks.clear()

    # ── 내부 헬퍼 ───────────────────────────────────────────────────────
    def _lookup_cache(self, key: tuple[str, Market]) -> Optional[Quote]:
        entry = self._cache.get(key)
        if entry is None:
            return None
        if entry.expires_monotonic <= time.monotonic():
            # 만료 — 즉시 제거하여 다음 미스 경로로 빠지게 한다.
            self._cache.pop(key, None)
            return None
        return entry.quote

    def _store_cache(self, key: tuple[str, Market], quote: Quote) -> None:
        if len(self._cache) >= self._config.max_cache_entries:
            # 만료 시각이 가장 빠른(=가장 곧 죽을) 항목 1건 제거.
            oldest_key = min(
                self._cache.items(),
                key=lambda kv: kv[1].expires_monotonic,
            )[0]
            self._cache.pop(oldest_key, None)
        self._cache[key] = _CacheEntry(
            quote=quote,
            expires_monotonic=time.monotonic() + self._config.cache_ttl_seconds,
        )

    async def _get_lock(self, key: tuple[str, Market]) -> asyncio.Lock:
        async with self._global_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock

    async def _fetch_upstream(self, ticker: str, market: Market) -> Quote:
        start = time.monotonic()
        try:
            if market == Market.KRX:
                response = await self._kis.get_kr_stock_price(ticker)
                price = self._parse_kr_price(response)
            elif market in _US_EXCHANGE_CODE:
                response = await self._kis.get_us_stock_price(ticker, exchange=_US_EXCHANGE_CODE[market])
                price = self._parse_us_price(response)
            else:
                raise QuoteFetchError(ticker, f"unsupported market for KIS quote provider: {market}")
        except QuoteFetchError:
            raise
        except Exception as exc:
            logger.warning(f"KISQuoteProvider upstream failure: ticker={ticker} " f"market={market.value} err={exc}")
            raise QuoteFetchError(ticker, f"KIS upstream error: {exc}") from exc
        finally:
            QUOTE_FETCH_LATENCY_SECONDS.labels(market=market.value).observe(time.monotonic() - start)

        if price <= 0:
            raise QuoteFetchError(ticker, f"KIS returned non-positive price: {price}")

        return Quote(
            ticker=ticker,
            market=market,
            price=price,
            fetched_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _parse_kr_price(response: dict) -> float:
        """KIS 국내주식 inquire-price 응답에서 ``stck_prpr`` 추출."""
        if not isinstance(response, dict):
            raise QuoteFetchError("", "KR response is not a dict")
        output = response.get("output")
        if not isinstance(output, dict):
            raise QuoteFetchError("", "KR response missing 'output' dict")
        raw = output.get("stck_prpr")
        if raw is None or raw == "":
            raise QuoteFetchError("", "KR response missing 'stck_prpr'")
        try:
            return float(raw)
        except (TypeError, ValueError) as exc:
            raise QuoteFetchError("", f"KR response stck_prpr not numeric: {raw!r}") from exc

    @staticmethod
    def _parse_us_price(response: dict) -> float:
        """KIS 해외주식 price 응답에서 ``last`` 추출."""
        if not isinstance(response, dict):
            raise QuoteFetchError("", "US response is not a dict")
        output = response.get("output")
        if not isinstance(output, dict):
            raise QuoteFetchError("", "US response missing 'output' dict")
        raw = output.get("last")
        if raw is None or raw == "":
            raise QuoteFetchError("", "US response missing 'last'")
        try:
            return float(raw)
        except (TypeError, ValueError) as exc:
            raise QuoteFetchError("", f"US response last not numeric: {raw!r}") from exc


# ══════════════════════════════════════════════════════════════════════════════
# 프로세스 전역 싱글톤
# ══════════════════════════════════════════════════════════════════════════════
_singleton: Optional[KISQuoteProvider] = None


def get_kis_quote_provider() -> KISQuoteProvider:
    """프로세스 전역 KISQuoteProvider 싱글톤.

    OrderExecutor 가 별도 주입 없이 즉시 사용할 수 있도록 단일 인스턴스를
    공유한다. 캐시는 인스턴스 단위이므로 라우트마다 매번 새 executor 를
    만들어도 시세 조회는 공유 캐시를 통해 절약된다.
    """
    global _singleton
    if _singleton is None:
        _singleton = KISQuoteProvider()
    return _singleton


def reset_kis_quote_provider() -> None:
    """테스트 전용 싱글톤 리셋."""
    global _singleton
    _singleton = None


__all__ = [
    "KISQuoteProvider",
    "KISQuoteProviderConfig",
    "get_kis_quote_provider",
    "reset_kis_quote_provider",
]
