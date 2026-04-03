"""Order 계약 테스트 (Contract 7)."""

import pytest
from pydantic import ValidationError

from contracts.order import OrderIntent
from config.constants import Market, OrderSide, OrderType


def _valid_order(**overrides):
    defaults = dict(
        ticker="005930", market=Market.KRX, side=OrderSide.BUY,
        order_type=OrderType.MARKET, quantity=100,
    )
    defaults.update(overrides)
    return defaults


class TestOrderValid:
    def test_market_order(self):
        o = OrderIntent(**_valid_order())
        assert o.order_type == OrderType.MARKET
        assert o.limit_price is None

    def test_limit_order(self):
        o = OrderIntent(**_valid_order(
            order_type=OrderType.LIMIT, limit_price=70000.0
        ))
        assert o.limit_price == 70000.0

    def test_sell_order(self):
        o = OrderIntent(**_valid_order(side=OrderSide.SELL))
        assert o.side == OrderSide.SELL

    def test_twap_order(self):
        o = OrderIntent(**_valid_order(order_type=OrderType.TWAP))
        assert o.order_type == OrderType.TWAP

    def test_vwap_order(self):
        o = OrderIntent(**_valid_order(order_type=OrderType.VWAP))
        assert o.order_type == OrderType.VWAP

    def test_with_reason_and_strategy(self):
        o = OrderIntent(**_valid_order(
            reason="팩터 시그널 BUY", strategy_id="FACTOR"
        ))
        assert o.reason == "팩터 시그널 BUY"

    def test_with_decision_id(self):
        o = OrderIntent(**_valid_order(decision_id="dec-123-456"))
        assert o.decision_id == "dec-123-456"

    def test_us_market(self):
        o = OrderIntent(**_valid_order(ticker="AAPL", market=Market.NYSE))
        assert o.market == Market.NYSE

    def test_large_quantity(self):
        o = OrderIntent(**_valid_order(quantity=1_000_000))
        assert o.quantity == 1_000_000


class TestOrderInvalid:
    def test_limit_without_price(self):
        with pytest.raises(ValidationError, match="limit_price가 필수"):
            OrderIntent(**_valid_order(order_type=OrderType.LIMIT))

    def test_zero_quantity(self):
        with pytest.raises(ValidationError, match="greater than 0"):
            OrderIntent(**_valid_order(quantity=0))

    def test_negative_quantity(self):
        with pytest.raises(ValidationError, match="greater than 0"):
            OrderIntent(**_valid_order(quantity=-10))

    def test_negative_limit_price(self):
        with pytest.raises(ValidationError, match="greater than 0"):
            OrderIntent(**_valid_order(
                order_type=OrderType.LIMIT, limit_price=-100.0
            ))

    def test_empty_ticker(self):
        with pytest.raises(ValidationError):
            OrderIntent(**_valid_order(ticker=""))

    def test_extra_field(self):
        with pytest.raises(ValidationError, match="Extra inputs"):
            OrderIntent(**_valid_order(urgency="high"))

    def test_invalid_side(self):
        with pytest.raises(ValidationError):
            OrderIntent(**_valid_order(side="SHORT"))

    def test_invalid_order_type(self):
        with pytest.raises(ValidationError):
            OrderIntent(**_valid_order(order_type="STOP_LOSS"))

    def test_immutable(self):
        o = OrderIntent(**_valid_order())
        with pytest.raises(ValidationError):
            o.quantity = 200

    def test_reason_too_long(self):
        with pytest.raises(ValidationError):
            OrderIntent(**_valid_order(reason="x" * 501))
