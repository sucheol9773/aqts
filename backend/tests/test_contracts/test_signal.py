"""Signal 계약 테스트 (Contract 5)."""

import pytest
from pydantic import ValidationError

from config.constants import Market, SignalDirection, StrategyType
from contracts.signal import Signal


def _valid_signal(**overrides):
    defaults = dict(
        ticker="005930",
        market=Market.KRX,
        direction=SignalDirection.BUY,
        confidence=0.8,
        strategy_id=StrategyType.FACTOR,
        reason="가치 팩터 상위 10%",
    )
    defaults.update(overrides)
    return defaults


@pytest.mark.smoke
class TestSignalValid:
    def test_buy_signal(self):
        s = Signal(**_valid_signal())
        assert s.direction == SignalDirection.BUY
        assert s.confidence == 0.8

    def test_sell_signal(self):
        s = Signal(**_valid_signal(direction=SignalDirection.SELL, confidence=0.6))
        assert s.direction == SignalDirection.SELL

    def test_hold_with_zero_confidence(self):
        s = Signal(**_valid_signal(direction=SignalDirection.HOLD, confidence=0.0))
        assert s.confidence == 0.0

    def test_hold_with_confidence(self):
        s = Signal(**_valid_signal(direction=SignalDirection.HOLD, confidence=0.5))
        assert s.confidence == 0.5

    def test_boundary_confidence_1(self):
        s = Signal(**_valid_signal(confidence=1.0))
        assert s.confidence == 1.0

    def test_minimal_confidence(self):
        s = Signal(**_valid_signal(confidence=0.01))
        assert s.confidence == 0.01

    def test_all_strategy_types(self):
        for st in StrategyType:
            s = Signal(**_valid_signal(strategy_id=st))
            assert s.strategy_id == st

    def test_empty_reason(self):
        s = Signal(**_valid_signal(reason=""))
        assert s.reason == ""


@pytest.mark.smoke
class TestSignalInvalid:
    def test_buy_zero_confidence(self):
        with pytest.raises(ValidationError, match="confidence가 0"):
            Signal(**_valid_signal(direction=SignalDirection.BUY, confidence=0.0))

    def test_sell_zero_confidence(self):
        with pytest.raises(ValidationError, match="confidence가 0"):
            Signal(**_valid_signal(direction=SignalDirection.SELL, confidence=0.0))

    def test_confidence_above_1(self):
        with pytest.raises(ValidationError, match="less than or equal to 1"):
            Signal(**_valid_signal(confidence=1.5))

    def test_confidence_negative(self):
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            Signal(**_valid_signal(confidence=-0.1))

    def test_empty_ticker(self):
        with pytest.raises(ValidationError):
            Signal(**_valid_signal(ticker=""))

    def test_extra_field(self):
        with pytest.raises(ValidationError, match="Extra inputs"):
            Signal(**_valid_signal(priority="high"))

    def test_immutable(self):
        s = Signal(**_valid_signal())
        with pytest.raises(ValidationError):
            s.confidence = 0.5

    def test_invalid_direction(self):
        with pytest.raises(ValidationError):
            Signal(**_valid_signal(direction="STRONG_BUY"))

    def test_reason_too_long(self):
        with pytest.raises(ValidationError):
            Signal(**_valid_signal(reason="x" * 501))

    def test_invalid_strategy(self):
        with pytest.raises(ValidationError):
            Signal(**_valid_signal(strategy_id="UNKNOWN"))
