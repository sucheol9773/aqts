"""Unit tests for core.order_executor.order_state_machine.

근거: docs/security/security-integrity-roadmap.md §7.3.

검증 범위:
    1. VALID_ORDER_TRANSITIONS 매트릭스가 OrderStatus 모든 값을 포함한다.
    2. 허용된 전이(positive) / 거부된 전이(negative) 가 스펙과 일치한다.
    3. 종결 상태(FILLED/CANCELLED/FAILED) 는 어떠한 전이도 허용하지 않는다.
    4. assert_order_transition / assert_can_cancel 은 거부 시 Prometheus
       counter 를 증가시키며 InvalidOrderTransition 을 raise 한다.
    5. parse_order_status 는 유효값을 enum 으로, 무효값을 ValueError 로 처리.
"""

from __future__ import annotations

import pytest

from config.constants import OrderStatus
from core.monitoring.metrics import ORDER_STATE_TRANSITION_REJECTS_TOTAL
from core.order_executor.order_state_machine import (
    CANCELLABLE_ORDER_STATES,
    TERMINAL_ORDER_STATES,
    VALID_ORDER_TRANSITIONS,
    InvalidOrderTransition,
    assert_can_cancel,
    assert_order_transition,
    can_transition_order,
    is_terminal_order_state,
    parse_order_status,
)


def _counter_value(from_state: OrderStatus, to_state: OrderStatus) -> float:
    """Prometheus counter 의 현재값을 읽는다."""
    return ORDER_STATE_TRANSITION_REJECTS_TOTAL.labels(
        from_state=from_state.value,
        to_state=to_state.value,
    )._value.get()


class TestTransitionMatrix:
    def test_matrix_covers_all_statuses(self) -> None:
        assert set(VALID_ORDER_TRANSITIONS.keys()) == set(OrderStatus)

    def test_terminal_states_have_no_outgoing(self) -> None:
        for s in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.FAILED):
            assert VALID_ORDER_TRANSITIONS[s] == set()

    def test_terminal_constant_matches_matrix(self) -> None:
        derived = {s for s, nxt in VALID_ORDER_TRANSITIONS.items() if not nxt}
        assert derived == TERMINAL_ORDER_STATES

    def test_cancellable_constant(self) -> None:
        assert CANCELLABLE_ORDER_STATES == {
            OrderStatus.PENDING,
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIAL,
        }


class TestCanTransitionPositive:
    @pytest.mark.parametrize(
        "from_state,to_state",
        [
            (OrderStatus.PENDING, OrderStatus.SUBMITTED),
            (OrderStatus.PENDING, OrderStatus.CANCELLED),
            (OrderStatus.PENDING, OrderStatus.FAILED),
            (OrderStatus.SUBMITTED, OrderStatus.PARTIAL),
            (OrderStatus.SUBMITTED, OrderStatus.FILLED),
            (OrderStatus.SUBMITTED, OrderStatus.CANCELLED),
            (OrderStatus.SUBMITTED, OrderStatus.FAILED),
            (OrderStatus.PARTIAL, OrderStatus.FILLED),
            (OrderStatus.PARTIAL, OrderStatus.CANCELLED),
            (OrderStatus.PARTIAL, OrderStatus.FAILED),
        ],
    )
    def test_allowed(self, from_state: OrderStatus, to_state: OrderStatus) -> None:
        assert can_transition_order(from_state, to_state) is True


class TestCanTransitionNegative:
    @pytest.mark.parametrize(
        "from_state,to_state",
        [
            # skip transition
            (OrderStatus.PENDING, OrderStatus.PARTIAL),
            (OrderStatus.PENDING, OrderStatus.FILLED),
            # backwards
            (OrderStatus.SUBMITTED, OrderStatus.PENDING),
            (OrderStatus.PARTIAL, OrderStatus.SUBMITTED),
            # terminal outgoing
            (OrderStatus.FILLED, OrderStatus.CANCELLED),
            (OrderStatus.FILLED, OrderStatus.PARTIAL),
            (OrderStatus.CANCELLED, OrderStatus.PENDING),
            (OrderStatus.CANCELLED, OrderStatus.FILLED),
            (OrderStatus.FAILED, OrderStatus.SUBMITTED),
            (OrderStatus.FAILED, OrderStatus.FILLED),
        ],
    )
    def test_rejected(self, from_state: OrderStatus, to_state: OrderStatus) -> None:
        assert can_transition_order(from_state, to_state) is False


class TestAssertOrderTransition:
    def test_allowed_does_not_raise(self) -> None:
        assert_order_transition(OrderStatus.PENDING, OrderStatus.SUBMITTED)

    def test_rejected_raises_and_increments(self) -> None:
        before = _counter_value(OrderStatus.FILLED, OrderStatus.PENDING)
        with pytest.raises(InvalidOrderTransition) as excinfo:
            assert_order_transition(
                OrderStatus.FILLED,
                OrderStatus.PENDING,
                order_id="ord-123",
            )
        after = _counter_value(OrderStatus.FILLED, OrderStatus.PENDING)
        assert after == before + 1
        assert excinfo.value.from_state == OrderStatus.FILLED
        assert excinfo.value.to_state == OrderStatus.PENDING
        assert excinfo.value.order_id == "ord-123"
        assert "ord-123" in str(excinfo.value)


class TestAssertCanCancel:
    @pytest.mark.parametrize(
        "status",
        [OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.PARTIAL],
    )
    def test_cancellable(self, status: OrderStatus) -> None:
        assert_can_cancel(status, order_id="o1")

    @pytest.mark.parametrize(
        "status",
        [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.FAILED],
    )
    def test_rejects_terminal(self, status: OrderStatus) -> None:
        before = _counter_value(status, OrderStatus.CANCELLED)
        with pytest.raises(InvalidOrderTransition) as excinfo:
            assert_can_cancel(status, order_id="o2")
        after = _counter_value(status, OrderStatus.CANCELLED)
        assert after == before + 1
        assert excinfo.value.from_state == status
        assert excinfo.value.to_state == OrderStatus.CANCELLED
        assert excinfo.value.order_id == "o2"


class TestIsTerminalOrderState:
    @pytest.mark.parametrize(
        "status,expected",
        [
            (OrderStatus.PENDING, False),
            (OrderStatus.SUBMITTED, False),
            (OrderStatus.PARTIAL, False),
            (OrderStatus.FILLED, True),
            (OrderStatus.CANCELLED, True),
            (OrderStatus.FAILED, True),
        ],
    )
    def test_values(self, status: OrderStatus, expected: bool) -> None:
        assert is_terminal_order_state(status) is expected


class TestParseOrderStatus:
    @pytest.mark.parametrize("status", list(OrderStatus))
    def test_valid(self, status: OrderStatus) -> None:
        assert parse_order_status(status.value) is status

    @pytest.mark.parametrize("raw", ["", "pending", "UNKNOWN", "FILLED ", "filled"])
    def test_invalid(self, raw: str) -> None:
        with pytest.raises(ValueError):
            parse_order_status(raw)
