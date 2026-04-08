"""주문 경로 가격/시세 가드 모듈.

근거: docs/security/security-integrity-roadmap.md §7.3 — "Stale quote 감지
+ pre-trade price guard + post-trade slippage guard".

주요 불변식:
    1. **Stale quote**: 주문 시점 직전에 조회한 시세의 ``fetched_at`` 이
       현재 시각 기준 ``max_quote_age_seconds`` 를 초과하면 시세가 낡은
       것으로 간주하여 fail-closed 거부한다. 시세 조회 실패도 거부.
    2. **Pre-trade price deviation**: LIMIT 주문의 ``limit_price`` 가
       기준 시세(최근 조회값)로부터 ``max_pre_trade_deviation_pct`` 이상
       이탈한 경우 fail-closed 거부한다. 방향성 적용:
       BUY 는 너무 *비싸게*, SELL 은 너무 *싸게* 제출되는 경로만 차단.
    3. **Post-trade slippage**: 체결된 ``avg_price`` 가 기준 시세로부터
       ``max_post_trade_slippage_pct`` 이상 이탈하면 fail-closed 가
       아니라 **경보 기록**. 브로커 체결은 이미 발생했으므로 롤백이
       불가능하므로 관측·감사 계층으로만 동작한다.

인접 통제:
    - Prometheus Counter 4종을 instrument 한다 (아래 메트릭 import 참고).
    - TradingGuard (P0-5) 가 kill switch / 일일 손실 / BUY 금액 한도를
      담당한다. 본 모듈은 **시세 vs 주문가** 의 정합성에 한정한다.
    - Reconciliation (P1-정합성) 은 사후 원장 대사에 해당한다. 본 모듈은
      "주문 전/직후" 시점의 시세 정합성만 본다.

모듈은 OrderExecutor 에서 사용되지만, 본 파일 자체는 OrderExecutor 에
의존하지 않는다 — 순수 함수 + Protocol + 예외 집합이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol, runtime_checkable

from config.constants import Market, OrderSide
from core.monitoring.metrics import (
    POST_TRADE_SLIPPAGE_ALERTS_TOTAL,
    PRE_TRADE_PRICE_REJECTS_TOTAL,
    QUOTE_FETCH_FAILURES_TOTAL,
    STALE_QUOTE_REJECTS_TOTAL,
)


# ══════════════════════════════════════════════════════════════════════════════
# 데이터 구조
# ══════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Quote:
    """시세 스냅샷.

    ``fetched_at`` 은 **클라이언트 측에서 응답을 수신한 시각**(UTC).
    브로커 서버 시각이 아니므로 네트워크 왕복과 클라이언트 처리 지연이
    포함되지만, 본 가드는 "내 코드가 이 시세를 사용한 시점" 을 기준으로
    신선도를 판단하므로 이 정의가 올바르다.
    """

    ticker: str
    market: Market
    price: float
    fetched_at: datetime

    def __post_init__(self) -> None:
        if self.price <= 0:
            raise ValueError(f"Quote.price must be > 0 (got {self.price!r})")
        if self.fetched_at.tzinfo is None:
            raise ValueError("Quote.fetched_at must be timezone-aware")


@dataclass(frozen=True)
class PriceGuardConfig:
    """가드 임계값.

    운영/테스트에서 주입 가능하도록 dataclass 로 분리한다. 기본값은
    보수적으로 설정: 5초 stale, ±2% pre-trade, ±1% post-trade slippage.
    변경 시 ``docs/security/security-integrity-roadmap.md`` §7.3 에
    이유와 관측 결과를 기록한다.
    """

    max_quote_age_seconds: float = 5.0
    max_pre_trade_deviation_pct: float = 0.02
    max_post_trade_slippage_pct: float = 0.01

    def __post_init__(self) -> None:
        if self.max_quote_age_seconds <= 0:
            raise ValueError("max_quote_age_seconds must be > 0")
        if self.max_pre_trade_deviation_pct <= 0:
            raise ValueError("max_pre_trade_deviation_pct must be > 0")
        if self.max_post_trade_slippage_pct <= 0:
            raise ValueError("max_post_trade_slippage_pct must be > 0")


# ══════════════════════════════════════════════════════════════════════════════
# 예외
# ══════════════════════════════════════════════════════════════════════════════
class PriceGuardError(Exception):
    """price_guard 계층 공통 기반 예외."""


class StaleQuoteError(PriceGuardError):
    """조회한 시세가 허용 age 를 초과했을 때."""

    def __init__(self, ticker: str, age_seconds: float, limit_seconds: float):
        self.ticker = ticker
        self.age_seconds = age_seconds
        self.limit_seconds = limit_seconds
        super().__init__(f"Stale quote for {ticker}: age={age_seconds:.3f}s > limit={limit_seconds:.3f}s")


class PriceDeviationError(PriceGuardError):
    """주문가가 기준 시세 밴드를 벗어났을 때 (pre-trade)."""

    def __init__(
        self,
        ticker: str,
        side: OrderSide,
        reference_price: float,
        order_price: float,
        deviation_pct: float,
        limit_pct: float,
    ):
        self.ticker = ticker
        self.side = side
        self.reference_price = reference_price
        self.order_price = order_price
        self.deviation_pct = deviation_pct
        self.limit_pct = limit_pct
        super().__init__(
            f"Pre-trade price deviation for {ticker} {side.value}: "
            f"order={order_price} vs reference={reference_price} "
            f"(deviation={deviation_pct:.4%} > limit={limit_pct:.4%})"
        )


class QuoteFetchError(PriceGuardError):
    """QuoteProvider 가 시세를 가져오지 못한 경우 (fail-closed)."""

    def __init__(self, ticker: str, reason: str):
        self.ticker = ticker
        self.reason = reason
        super().__init__(f"Quote fetch failed for {ticker}: {reason}")


# ══════════════════════════════════════════════════════════════════════════════
# QuoteProvider Protocol
# ══════════════════════════════════════════════════════════════════════════════
@runtime_checkable
class QuoteProvider(Protocol):
    """주문 경로가 사용하는 시세 조회 인터페이스.

    구현체는 반드시 ``Quote.fetched_at`` 을 호출 시각으로 세팅해야 한다.
    실패 시 ``QuoteFetchError`` 를 raise 한다 — 임의 예외를 흘리면
    OrderExecutor 상위 로직이 fail-closed 를 보장하지 못한다.
    """

    async def get_quote(self, ticker: str, market: Market) -> Quote: ...


class StaticQuoteProvider:
    """테스트/리허설용 고정 시세 provider.

    운영 경로에서는 사용하지 않는다.
    """

    def __init__(self, quotes: dict[tuple[str, Market], Quote]):
        self._quotes = dict(quotes)

    async def get_quote(self, ticker: str, market: Market) -> Quote:
        try:
            return self._quotes[(ticker, market)]
        except KeyError as exc:
            raise QuoteFetchError(ticker, f"no static quote for ({ticker},{market.value})") from exc


# ══════════════════════════════════════════════════════════════════════════════
# 순수 검증 함수
# ══════════════════════════════════════════════════════════════════════════════
def compute_deviation_pct(reference: float, candidate: float) -> float:
    """reference 대비 candidate 의 절대 이탈율을 반환한다."""
    if reference <= 0:
        raise ValueError("reference must be > 0")
    return abs(candidate - reference) / reference


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def assert_quote_fresh(
    quote: Quote,
    *,
    max_age_seconds: float,
    now: Optional[datetime] = None,
) -> None:
    """Quote 가 ``max_age_seconds`` 이내에 조회되었는지 검증한다.

    위반 시 ``aqts_stale_quote_rejects_total{market}`` 증가 후
    ``StaleQuoteError`` raise.
    """
    if max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be > 0")
    current = now or _utcnow()
    if quote.fetched_at.tzinfo is None:
        raise ValueError("quote.fetched_at must be timezone-aware")
    age = (current - quote.fetched_at).total_seconds()
    if age < 0:
        # 미래 시각의 quote → 신뢰할 수 없음
        STALE_QUOTE_REJECTS_TOTAL.labels(market=quote.market.value).inc()
        raise StaleQuoteError(quote.ticker, age, max_age_seconds)
    if age > max_age_seconds:
        STALE_QUOTE_REJECTS_TOTAL.labels(market=quote.market.value).inc()
        raise StaleQuoteError(quote.ticker, age, max_age_seconds)


def assert_pre_trade_price(
    *,
    ticker: str,
    market: Market,
    side: OrderSide,
    reference_price: float,
    order_price: float,
    max_deviation_pct: float,
) -> None:
    """LIMIT 주문가가 기준 시세 밴드 안에 있는지 검증한다.

    - BUY: ``order_price > reference * (1 + max_deviation_pct)`` → 거부
      (기준보다 너무 *비싸게* 매수하려는 경로).
    - SELL: ``order_price < reference * (1 - max_deviation_pct)`` → 거부
      (기준보다 너무 *싸게* 매도하려는 경로).

    허용 방향(BUY 가 저렴한 값, SELL 이 높은 값)은 통과시킨다 — 이는
    일반적으로 체결 가능성이 낮을 뿐 무결성 위반이 아니기 때문이다.
    단, Counter 는 "blocked" 방향만 집계한다.
    """
    if max_deviation_pct <= 0:
        raise ValueError("max_deviation_pct must be > 0")
    if reference_price <= 0 or order_price <= 0:
        raise ValueError("reference_price and order_price must be > 0")

    if side == OrderSide.BUY:
        upper = reference_price * (1.0 + max_deviation_pct)
        if order_price > upper:
            deviation = compute_deviation_pct(reference_price, order_price)
            PRE_TRADE_PRICE_REJECTS_TOTAL.labels(side=side.value, market=market.value).inc()
            raise PriceDeviationError(
                ticker=ticker,
                side=side,
                reference_price=reference_price,
                order_price=order_price,
                deviation_pct=deviation,
                limit_pct=max_deviation_pct,
            )
    elif side == OrderSide.SELL:
        lower = reference_price * (1.0 - max_deviation_pct)
        if order_price < lower:
            deviation = compute_deviation_pct(reference_price, order_price)
            PRE_TRADE_PRICE_REJECTS_TOTAL.labels(side=side.value, market=market.value).inc()
            raise PriceDeviationError(
                ticker=ticker,
                side=side,
                reference_price=reference_price,
                order_price=order_price,
                deviation_pct=deviation,
                limit_pct=max_deviation_pct,
            )
    else:
        raise ValueError(f"Unsupported order side for price guard: {side!r}")


def check_post_trade_slippage(
    *,
    ticker: str,
    market: Market,
    side: OrderSide,
    reference_price: float,
    fill_price: float,
    max_slippage_pct: float,
) -> bool:
    """체결 후 slippage 관측.

    브로커 체결은 이미 발생했으므로 본 함수는 **예외를 raise 하지 않는다**.
    위반 시 ``aqts_post_trade_slippage_alerts_total{severity,market}`` 을
    증가시키고 ``True`` 를 반환 (상위에서 critical log 발행).

    severity 는 ``max_slippage_pct`` 의 2배 초과 여부로 분기한다:
    ``warn`` (초과) / ``critical`` (2x 초과).

    Returns:
        bool: 임계 초과 여부 (상위 로깅 판단용).
    """
    if max_slippage_pct <= 0:
        raise ValueError("max_slippage_pct must be > 0")
    if reference_price <= 0 or fill_price <= 0:
        return False  # 데이터 없음 — 판단 불가, silent skip (counter 증가 없음)

    deviation = compute_deviation_pct(reference_price, fill_price)

    # 방향성 적용: 불리한 방향(BUY 가 더 비싸게 체결, SELL 이 더 싸게 체결)만 경보
    if side == OrderSide.BUY and fill_price <= reference_price:
        return False
    if side == OrderSide.SELL and fill_price >= reference_price:
        return False

    if deviation <= max_slippage_pct:
        return False

    severity = "critical" if deviation > 2 * max_slippage_pct else "warn"
    POST_TRADE_SLIPPAGE_ALERTS_TOTAL.labels(severity=severity, market=market.value).inc()
    return True


async def fetch_and_validate_quote(
    provider: QuoteProvider,
    *,
    ticker: str,
    market: Market,
    max_age_seconds: float,
    now: Optional[datetime] = None,
) -> Quote:
    """QuoteProvider 호출 + 신선도 검증의 합성 헬퍼.

    내부에서 발생하는 임의 예외는 ``QuoteFetchError`` 로 정규화되며,
    Counter ``aqts_quote_fetch_failures_total{market,reason}`` 가 증가한다.
    """
    try:
        quote = await provider.get_quote(ticker, market)
    except QuoteFetchError:
        QUOTE_FETCH_FAILURES_TOTAL.labels(market=market.value, reason="provider_error").inc()
        raise
    except Exception as exc:  # pragma: no cover - defensive
        QUOTE_FETCH_FAILURES_TOTAL.labels(market=market.value, reason="unexpected").inc()
        raise QuoteFetchError(ticker, f"unexpected provider error: {exc}") from exc

    if quote.ticker != ticker or quote.market != market:
        QUOTE_FETCH_FAILURES_TOTAL.labels(market=market.value, reason="identity_mismatch").inc()
        raise QuoteFetchError(
            ticker,
            f"quote identity mismatch: got ({quote.ticker},{quote.market.value})",
        )

    assert_quote_fresh(quote, max_age_seconds=max_age_seconds, now=now)
    return quote
