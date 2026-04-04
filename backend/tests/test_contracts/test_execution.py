"""Execution 계약 테스트 (Contract 8)."""

import pytest
from pydantic import ValidationError

from config.constants import Market, OrderSide, OrderStatus
from contracts.execution import ExecutionResult


def _valid_exec(**overrides):
    defaults = dict(
        broker_order_id="KIS-2024-001",
        ticker="005930",
        market=Market.KRX,
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        requested_quantity=100,
        filled_quantity=100,
        filled_price=70000.0,
    )
    defaults.update(overrides)
    return defaults


@pytest.mark.smoke
class TestExecutionValid:
    def test_full_fill(self):
        e = ExecutionResult(**_valid_exec())
        assert e.filled_quantity == 100
        assert e.filled_price == 70000.0

    def test_partial_fill(self):
        e = ExecutionResult(**_valid_exec(status=OrderStatus.PARTIAL, filled_quantity=50, filled_price=70000.0))
        assert e.status == OrderStatus.PARTIAL

    def test_cancelled(self):
        e = ExecutionResult(**_valid_exec(status=OrderStatus.CANCELLED, filled_quantity=0, filled_price=None))
        assert e.filled_quantity == 0

    def test_pending_no_fill(self):
        e = ExecutionResult(**_valid_exec(status=OrderStatus.PENDING, filled_quantity=0, filled_price=None))
        assert e.status == OrderStatus.PENDING

    def test_with_commission_and_slippage(self):
        e = ExecutionResult(**_valid_exec(commission=15.0, slippage=0.001))
        assert e.commission == 15.0

    def test_with_decision_id(self):
        e = ExecutionResult(**_valid_exec(decision_id="dec-abc"))
        assert e.decision_id == "dec-abc"

    def test_sell_execution(self):
        e = ExecutionResult(**_valid_exec(side=OrderSide.SELL))
        assert e.side == OrderSide.SELL

    def test_negative_slippage(self):
        # 유리한 슬리피지 (음수 가능)
        e = ExecutionResult(**_valid_exec(slippage=-0.002))
        assert e.slippage == -0.002


@pytest.mark.smoke
class TestExecutionInvalid:
    def test_filled_exceeds_requested(self):
        with pytest.raises(ValidationError, match="filled_quantity.*requested_quantity"):
            ExecutionResult(**_valid_exec(filled_quantity=150))

    def test_filled_status_no_price(self):
        with pytest.raises(ValidationError, match="filled_price가 필수"):
            ExecutionResult(**_valid_exec(status=OrderStatus.FILLED, filled_quantity=100, filled_price=None))

    def test_partial_status_no_price(self):
        with pytest.raises(ValidationError, match="filled_price가 필수"):
            ExecutionResult(**_valid_exec(status=OrderStatus.PARTIAL, filled_quantity=50, filled_price=None))

    def test_negative_requested_quantity(self):
        with pytest.raises(ValidationError, match="greater than 0"):
            ExecutionResult(**_valid_exec(requested_quantity=-1))

    def test_negative_filled_quantity(self):
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            ExecutionResult(**_valid_exec(filled_quantity=-5))

    def test_negative_commission(self):
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            ExecutionResult(**_valid_exec(commission=-10.0))

    def test_empty_broker_order_id(self):
        with pytest.raises(ValidationError):
            ExecutionResult(**_valid_exec(broker_order_id=""))

    def test_extra_field(self):
        with pytest.raises(ValidationError, match="Extra inputs"):
            ExecutionResult(**_valid_exec(exchange_rate=1300.0))

    def test_immutable(self):
        e = ExecutionResult(**_valid_exec())
        with pytest.raises(ValidationError):
            e.filled_quantity = 200

    def test_zero_filled_price(self):
        with pytest.raises(ValidationError, match="greater than 0"):
            ExecutionResult(**_valid_exec(filled_price=0))
