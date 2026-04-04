"""
Contract Converters 테스트

내부 dataclass → Pydantic 계약 변환 정확성 및 validation 강제 검증.
"""

import unittest

import pytest
from pydantic import ValidationError

from config.constants import Market, OrderSide, OrderType, SignalDirection, StrategyType
from contracts.converters import (
    internal_signal_to_contract,
    order_request_to_contract,
    signal_value_to_direction,
)
from contracts.order import OrderIntent
from contracts.signal import Signal as SignalContract
from core.quant_engine.signal_generator import Signal as InternalSignal


@pytest.mark.smoke
class TestSignalValueToDirection(unittest.TestCase):
    """signal_value_to_direction 변환 검증"""

    def test_positive_value_is_buy(self):
        assert signal_value_to_direction(0.5) == SignalDirection.BUY

    def test_negative_value_is_sell(self):
        assert signal_value_to_direction(-0.5) == SignalDirection.SELL

    def test_zero_value_is_hold(self):
        assert signal_value_to_direction(0.0) == SignalDirection.HOLD

    def test_threshold_boundary_hold(self):
        """abs(value) == threshold → HOLD"""
        assert signal_value_to_direction(0.1) == SignalDirection.HOLD
        assert signal_value_to_direction(-0.1) == SignalDirection.HOLD

    def test_just_above_threshold_buy(self):
        assert signal_value_to_direction(0.11) == SignalDirection.BUY

    def test_just_below_negative_threshold_sell(self):
        assert signal_value_to_direction(-0.11) == SignalDirection.SELL

    def test_custom_threshold(self):
        assert signal_value_to_direction(0.3, threshold=0.5) == SignalDirection.HOLD
        assert signal_value_to_direction(0.6, threshold=0.5) == SignalDirection.BUY

    def test_extreme_values(self):
        assert signal_value_to_direction(1.0) == SignalDirection.BUY
        assert signal_value_to_direction(-1.0) == SignalDirection.SELL


@pytest.mark.smoke
class TestInternalSignalToContract(unittest.TestCase):
    """internal_signal_to_contract 변환 검증"""

    def test_buy_signal_converts(self):
        """매수 시그널 변환"""
        sig = InternalSignal(
            ticker="005930",
            strategy=StrategyType.TREND_FOLLOWING,
            value=0.7,
            confidence=0.8,
            reason="Golden cross",
        )
        contract = internal_signal_to_contract(sig)
        assert isinstance(contract, SignalContract)
        assert contract.ticker == "005930"
        assert contract.direction == SignalDirection.BUY
        assert contract.confidence == 0.8
        assert contract.strategy_id == StrategyType.TREND_FOLLOWING
        assert contract.reason == "Golden cross"
        assert contract.market == Market.KRX

    def test_sell_signal_converts(self):
        """매도 시그널 변환"""
        sig = InternalSignal(
            ticker="AAPL",
            strategy=StrategyType.MEAN_REVERSION,
            value=-0.6,
            confidence=0.9,
            reason="RSI overbought",
        )
        contract = internal_signal_to_contract(sig, market=Market.NASDAQ)
        assert contract.direction == SignalDirection.SELL
        assert contract.market == Market.NASDAQ

    def test_hold_signal_converts(self):
        """보유 시그널 변환 (confidence=0 허용)"""
        sig = InternalSignal(
            ticker="005930",
            strategy=StrategyType.FACTOR,
            value=0.05,
            confidence=0.0,
            reason="Neutral",
        )
        contract = internal_signal_to_contract(sig)
        assert contract.direction == SignalDirection.HOLD
        assert contract.confidence == 0.0

    def test_buy_signal_with_zero_confidence_raises(self):
        """매수 시그널인데 confidence=0이면 계약 위반"""
        sig = InternalSignal(
            ticker="005930",
            strategy=StrategyType.TREND_FOLLOWING,
            value=0.8,
            confidence=0.0,
            reason="Should fail",
        )
        with pytest.raises(ValidationError, match="confidence"):
            internal_signal_to_contract(sig)

    def test_empty_ticker_raises(self):
        """빈 ticker는 계약 위반"""
        sig = InternalSignal(
            ticker="",
            strategy=StrategyType.FACTOR,
            value=0.5,
            confidence=0.5,
        )
        with pytest.raises(ValidationError):
            internal_signal_to_contract(sig)

    def test_frozen_result(self):
        """변환 결과는 immutable"""
        sig = InternalSignal(
            ticker="005930",
            strategy=StrategyType.FACTOR,
            value=0.5,
            confidence=0.7,
        )
        contract = internal_signal_to_contract(sig)
        with pytest.raises(ValidationError):
            contract.confidence = 0.9


@pytest.mark.smoke
class TestOrderRequestToContract(unittest.TestCase):
    """order_request_to_contract 변환 검증"""

    def _make_order_request(self, **overrides):
        """OrderRequest 팩토리"""
        from core.order_executor.executor import OrderRequest

        defaults = dict(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=10,
            order_type=OrderType.MARKET,
            limit_price=None,
            reason="rebalancing",
        )
        defaults.update(overrides)
        return OrderRequest(**defaults)

    def test_market_order_converts(self):
        req = self._make_order_request()
        contract = order_request_to_contract(req)
        assert isinstance(contract, OrderIntent)
        assert contract.ticker == "005930"
        assert contract.side == OrderSide.BUY
        assert contract.quantity == 10
        assert contract.order_type == OrderType.MARKET

    def test_limit_order_with_price(self):
        req = self._make_order_request(
            order_type=OrderType.LIMIT,
            limit_price=70000.0,
        )
        contract = order_request_to_contract(req)
        assert contract.limit_price == 70000.0

    def test_limit_order_without_price_raises(self):
        """LIMIT 주문인데 limit_price 없으면 계약 위반"""
        req = self._make_order_request(
            order_type=OrderType.LIMIT,
            limit_price=None,
        )
        with pytest.raises(ValidationError, match="limit_price"):
            order_request_to_contract(req)

    def test_zero_quantity_raises(self):
        """수량 0은 계약 위반"""
        req = self._make_order_request(quantity=0)
        with pytest.raises(ValidationError):
            order_request_to_contract(req)

    def test_strategy_id_and_decision_id(self):
        req = self._make_order_request()
        contract = order_request_to_contract(
            req,
            strategy_id="TF",
            decision_id="dec-001",
        )
        assert contract.strategy_id == "TF"
        assert contract.decision_id == "dec-001"

    def test_frozen_result(self):
        req = self._make_order_request()
        contract = order_request_to_contract(req)
        with pytest.raises(ValidationError):
            contract.quantity = 99


if __name__ == "__main__":
    unittest.main()
