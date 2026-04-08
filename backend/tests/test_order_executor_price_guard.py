"""P1-정합성 §7.3 OrderExecutor ↔ price_guard wiring 통합 테스트.

핵심 불변식:
    1. live 경로에서 quote_provider 가 주입되지 않으면 QuoteFetchError
       로 fail-closed.
    2. live 경로 + stale quote → StaleQuoteError, KIS 호출 없음.
    3. live 경로 + LIMIT 주문이 pre-trade 밴드를 벗어나면
       PriceDeviationError, KIS 호출 없음.
    4. live 경로 + 정상 시세 + 밴드 내 LIMIT → 실행 경로 통과.
    5. 체결 후 slippage 가 임계를 초과하면 post-trade counter 증가
       (주문은 성공).
    6. dry_run / backtest 경로에서는 guard 가 전혀 활성화되지 않음.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from config.constants import Market, OrderSide, OrderStatus, OrderType
from core.monitoring.metrics import POST_TRADE_SLIPPAGE_ALERTS_TOTAL
from core.order_executor.executor import OrderExecutor, OrderRequest, OrderResult
from core.order_executor.price_guard import (
    PriceDeviationError,
    PriceGuardConfig,
    Quote,
    QuoteFetchError,
    StaleQuoteError,
    StaticQuoteProvider,
)
from core.trading_guard import TradingGuard, reset_trading_guard


@pytest.fixture(autouse=True)
def _reset_guard():
    reset_trading_guard()
    yield
    reset_trading_guard()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _limit_request(price: float) -> OrderRequest:
    return OrderRequest(
        ticker="005930",
        market=Market.KRX,
        side=OrderSide.BUY,
        quantity=10,
        order_type=OrderType.LIMIT,
        limit_price=price,
        reason="unit-test",
    )


def _market_request() -> OrderRequest:
    return OrderRequest(
        ticker="005930",
        market=Market.KRX,
        side=OrderSide.BUY,
        quantity=10,
        order_type=OrderType.MARKET,
        reason="unit-test",
    )


def _build_live_executor(provider=None, config=None) -> OrderExecutor:
    """live 경로를 흉내내기 위해 is_backtest 를 False 로 만든 executor."""
    executor = OrderExecutor(
        dry_run=False,
        trading_guard=TradingGuard(),
        quote_provider=provider,
        price_guard_config=config,
    )
    # KISClient.is_backtest 프로퍼티를 직접 강제로 False 로 패치
    type(executor._kis_client).is_backtest = property(lambda self: False)  # type: ignore[assignment]
    return executor


def _restore_is_backtest(executor: OrderExecutor) -> None:
    """fixture 정리를 위해 프로퍼티를 원복."""
    try:
        del type(executor._kis_client).is_backtest
    except AttributeError:
        pass


@pytest.fixture
def live_executor_factory():
    created: list[OrderExecutor] = []

    def _factory(provider=None, config=None):
        ex = _build_live_executor(provider=provider, config=config)
        created.append(ex)
        return ex

    yield _factory

    for ex in created:
        _restore_is_backtest(ex)


def _fake_fill(avg_price: float) -> OrderResult:
    return OrderResult(
        order_id="TEST-1",
        ticker="005930",
        market=Market.KRX,
        side=OrderSide.BUY,
        quantity=10,
        filled_quantity=10,
        avg_price=avg_price,
        status=OrderStatus.FILLED,
        executed_at=_now(),
    )


@pytest.mark.asyncio
async def test_live_without_provider_fails_closed(live_executor_factory):
    executor = live_executor_factory(provider=None)

    with patch.object(executor, "_execute_market_order", new=AsyncMock()) as mock_market:
        with pytest.raises(QuoteFetchError, match="quote_provider"):
            await executor.execute_order(_market_request())
        mock_market.assert_not_called()


@pytest.mark.asyncio
async def test_live_stale_quote_rejects_order(live_executor_factory):
    stale = Quote(
        "005930",
        Market.KRX,
        70000.0,
        _now() - timedelta(seconds=30),
    )
    provider = StaticQuoteProvider({("005930", Market.KRX): stale})
    executor = live_executor_factory(provider=provider)

    with patch.object(executor, "_execute_market_order", new=AsyncMock()) as mock_market:
        with pytest.raises(StaleQuoteError):
            await executor.execute_order(_market_request())
        mock_market.assert_not_called()


@pytest.mark.asyncio
async def test_live_limit_out_of_band_rejected(live_executor_factory):
    fresh = Quote("005930", Market.KRX, 70000.0, _now())
    provider = StaticQuoteProvider({("005930", Market.KRX): fresh})
    executor = live_executor_factory(provider=provider)

    with patch.object(executor, "_execute_limit_order", new=AsyncMock()) as mock_limit:
        with pytest.raises(PriceDeviationError):
            # +10% — default band 2% 를 크게 초과
            await executor.execute_order(_limit_request(price=77000.0))
        mock_limit.assert_not_called()


@pytest.mark.asyncio
async def test_live_limit_within_band_passes(live_executor_factory):
    fresh = Quote("005930", Market.KRX, 70000.0, _now())
    provider = StaticQuoteProvider({("005930", Market.KRX): fresh})
    executor = live_executor_factory(provider=provider)

    fake = _fake_fill(avg_price=70100.0)

    with (
        patch.object(
            executor,
            "_execute_limit_order",
            new=AsyncMock(return_value=fake),
        ) as mock_limit,
        patch.object(executor, "_store_order", new=AsyncMock()),
        patch("core.order_executor.executor.async_session_factory") as mock_factory,
    ):
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = mock_ctx
        # +1% — band 내
        result = await executor.execute_order(_limit_request(price=70700.0))
        assert result.order_id == "TEST-1"
        mock_limit.assert_awaited_once()


@pytest.mark.asyncio
async def test_live_post_trade_slippage_observed(live_executor_factory):
    fresh = Quote("005930", Market.KRX, 70000.0, _now())
    provider = StaticQuoteProvider({("005930", Market.KRX): fresh})
    executor = live_executor_factory(
        provider=provider,
        config=PriceGuardConfig(max_post_trade_slippage_pct=0.01),
    )

    # BUY 가 reference 대비 +1.5% 비싸게 체결 → warn 카운터 증가
    fake = _fake_fill(avg_price=71050.0)

    before = POST_TRADE_SLIPPAGE_ALERTS_TOTAL.labels(severity="warn", market="KRX")._value.get()

    with (
        patch.object(
            executor,
            "_execute_market_order",
            new=AsyncMock(return_value=fake),
        ) as mock_market,
        patch.object(executor, "_store_order", new=AsyncMock()),
        patch("core.order_executor.executor.async_session_factory") as mock_factory,
    ):
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = mock_ctx
        result = await executor.execute_order(_market_request())
        assert result.order_id == "TEST-1"
        mock_market.assert_awaited_once()

    after = POST_TRADE_SLIPPAGE_ALERTS_TOTAL.labels(severity="warn", market="KRX")._value.get()
    assert after == before + 1


@pytest.mark.asyncio
async def test_dry_run_skips_price_guard_entirely():
    """dry_run 은 provider 없이도 guard 우회."""
    executor = OrderExecutor(
        dry_run=True,
        trading_guard=TradingGuard(),
        quote_provider=None,
    )

    fake = _fake_fill(avg_price=70000.0)

    with (
        patch.object(
            executor,
            "_execute_market_order",
            new=AsyncMock(return_value=fake),
        ) as mock_market,
        patch.object(executor, "_store_order", new=AsyncMock()),
        patch("core.order_executor.executor.async_session_factory") as mock_factory,
    ):
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = mock_ctx
        result = await executor.execute_order(_market_request())
        assert result.order_id == "TEST-1"
        mock_market.assert_awaited_once()
