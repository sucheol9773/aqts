"""Order 상태 전이 유효성 검증 모듈.

근거: docs/security/security-integrity-roadmap.md §7.3 — "주문 상태 전이
유효성 (PENDING→SUBMITTED→FILLED/CANCELLED 외 차단)".

설계 원칙:
    1. 단일 진실원천(Single source of truth): 허용되는 주문 상태 전이는 오직
       본 모듈의 `VALID_ORDER_TRANSITIONS` 한 곳에만 정의된다. 라우트 핸들러
       또는 OrderExecutor 경로에서 인라인 문자열 비교(`status in ("PENDING",
       "SUBMITTED")`) 로 분기하면 규칙 변경이 즉시 반영되지 않으므로 금지.
    2. 종결 상태(terminal) 불변식: `FILLED`, `CANCELLED`, `FAILED` 에 진입한
       주문은 어떠한 상태로도 전이될 수 없다. 종결 상태에서의 재전이 시도는
       `InvalidOrderTransition` 으로 fail-closed 거부한다.
    3. 관측 가능성: 모든 거부는 `aqts_order_state_transition_rejects_total`
       Counter 를 `{from_state, to_state}` 라벨로 증가시킨다. 알람 임계 0.
       무결성 위반이거나 코드 경로의 버그(예: 동일 주문을 두 번 체결 처리)
       신호이므로 0 이 유지되어야 한다.
    4. Wiring Rule: 정의(transition map)만 만드는 것으로는 부족하다. 본
       모듈은 `api/routes/orders.py::cancel_order` 와 연동되어 있고, 통합
       테스트 `tests/test_order_state_machine_cancel_route.py` 가 라우트에서
       실제로 사용되는지 검증한다.

전이 매트릭스:

    PENDING   → SUBMITTED, CANCELLED, FAILED
    SUBMITTED → PARTIAL, FILLED, CANCELLED, FAILED
    PARTIAL   → FILLED, CANCELLED, FAILED
    FILLED    → ∅  (terminal)
    CANCELLED → ∅  (terminal)
    FAILED    → ∅  (terminal)

근거:
    - PENDING→SUBMITTED: 정상 주문 제출 경로 (execute_order).
    - PENDING→CANCELLED: 제출 직전 사용자 취소 (이론상 가능, 라우트에서 허용).
    - PENDING→FAILED: 사전 검증 실패 (TradingGuard 차단 등).
    - SUBMITTED→PARTIAL/FILLED: 브로커 체결 피드백.
    - SUBMITTED→CANCELLED: 사용자/타임아웃 취소.
    - SUBMITTED→FAILED: 브로커 거부/네트워크 오류.
    - PARTIAL→FILLED: 잔여 수량 체결 완료.
    - PARTIAL→CANCELLED: 잔여 수량 취소.
    - PARTIAL→FAILED: 잔여 수량 체결 실패.
    - FILLED/CANCELLED/FAILED: 종결. 어떠한 전이도 불허.
"""

from __future__ import annotations

from typing import Dict, Optional, Set

from config.constants import OrderStatus
from core.monitoring.metrics import ORDER_STATE_TRANSITION_REJECTS_TOTAL


class InvalidOrderTransition(Exception):
    """허용되지 않은 주문 상태 전이 시도.

    Attributes:
        from_state: 현재 상태 (None 이면 초기 상태 없이 전이 시도).
        to_state: 전이하려는 목표 상태.
        order_id: 관련 주문 ID (있을 때만).
    """

    def __init__(
        self,
        from_state: Optional[OrderStatus],
        to_state: OrderStatus,
        order_id: Optional[str] = None,
    ) -> None:
        self.from_state = from_state
        self.to_state = to_state
        self.order_id = order_id
        from_label = from_state.value if from_state is not None else "<none>"
        super().__init__(
            f"허용되지 않은 주문 상태 전이: {from_label} → {to_state.value}"
            + (f" (order_id={order_id})" if order_id else "")
        )


# 허용된 주문 상태 전이. 본 딕셔너리가 단일 진실원천이다.
# 종결 상태(FILLED/CANCELLED/FAILED)는 빈 집합으로 고정.
VALID_ORDER_TRANSITIONS: Dict[OrderStatus, Set[OrderStatus]] = {
    OrderStatus.PENDING: {
        OrderStatus.SUBMITTED,
        OrderStatus.CANCELLED,
        OrderStatus.FAILED,
    },
    OrderStatus.SUBMITTED: {
        OrderStatus.PARTIAL,
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.FAILED,
    },
    OrderStatus.PARTIAL: {
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.FAILED,
    },
    OrderStatus.FILLED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.FAILED: set(),
}


TERMINAL_ORDER_STATES: Set[OrderStatus] = {
    OrderStatus.FILLED,
    OrderStatus.CANCELLED,
    OrderStatus.FAILED,
}


# 취소 가능한 상태 집합. cancel_order 라우트에서 참조.
# 주의: PARTIAL 은 "남은 수량 취소" 의미이므로 허용한다. 현재 라우트는 보수적
# 으로 PENDING/SUBMITTED 만 허용했지만, transition map 상으로는 PARTIAL 도
# 허용 대상이므로 본 모듈이 전이를 판단하고 라우트는 본 모듈을 참조한다.
CANCELLABLE_ORDER_STATES: Set[OrderStatus] = {
    OrderStatus.PENDING,
    OrderStatus.SUBMITTED,
    OrderStatus.PARTIAL,
}


def is_terminal_order_state(status: OrderStatus) -> bool:
    """종결 상태 여부."""
    return status in TERMINAL_ORDER_STATES


def can_transition_order(from_state: OrderStatus, to_state: OrderStatus) -> bool:
    """`from_state` 에서 `to_state` 로 전이가 허용되는지."""
    return to_state in VALID_ORDER_TRANSITIONS.get(from_state, set())


def assert_order_transition(
    from_state: OrderStatus,
    to_state: OrderStatus,
    *,
    order_id: Optional[str] = None,
) -> None:
    """전이가 허용되지 않으면 `InvalidOrderTransition` 을 raise.

    거부 시 Prometheus counter 를 증가시킨다. 알람 임계 0 (어떤 거부든
    무결성 위반 또는 코드 경로 버그의 신호이므로 관측되자마자 조사 대상).
    """
    if not can_transition_order(from_state, to_state):
        ORDER_STATE_TRANSITION_REJECTS_TOTAL.labels(
            from_state=from_state.value,
            to_state=to_state.value,
        ).inc()
        raise InvalidOrderTransition(from_state, to_state, order_id=order_id)


def assert_can_cancel(
    current_status: OrderStatus,
    *,
    order_id: Optional[str] = None,
) -> None:
    """현재 상태가 취소 가능하지 않으면 `InvalidOrderTransition` 을 raise.

    cancel_order 라우트의 전용 헬퍼. 취소 가능 집합은 transition map 에서
    유도되며, 거부 시 `CANCELLED` 로의 전이 거부로 counter 에 기록된다
    (동일한 라벨 포맷을 유지).
    """
    if current_status not in CANCELLABLE_ORDER_STATES:
        ORDER_STATE_TRANSITION_REJECTS_TOTAL.labels(
            from_state=current_status.value,
            to_state=OrderStatus.CANCELLED.value,
        ).inc()
        raise InvalidOrderTransition(
            current_status,
            OrderStatus.CANCELLED,
            order_id=order_id,
        )


def parse_order_status(raw: str) -> OrderStatus:
    """DB 에서 읽어온 문자열을 `OrderStatus` enum 으로 엄격 파싱.

    알 수 없는 상태값이면 `ValueError` 를 raise — 본 함수의 호출부는
    DB 무결성 가정(enum 범위 내)을 위반하는 경우를 fail-closed 로 처리해야
    한다 (예: 503 응답).
    """
    try:
        return OrderStatus(raw)
    except ValueError as exc:
        raise ValueError(f"알 수 없는 주문 상태: {raw!r}") from exc
