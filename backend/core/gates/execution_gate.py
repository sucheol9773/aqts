"""ExecutionGate: 주문 실행 성공 검증 (주문 → 체결 전이)."""

from typing import Any

from core.gates.base import BaseGate, GateResult, GateSeverity


class ExecutionGate(BaseGate):
    """주문 실행 성공 여부를 검증합니다.

    차단 조건:
    - 주문 전송 실패
    - 브로커 연결 오류
    - 주문 파라미터 거부
    """

    @property
    def gate_id(self) -> str:
        return "ExecutionGate"

    async def evaluate(self, data: Any, **kwargs) -> GateResult:
        if data is None:
            return self._block("실행 결과가 없습니다", severity=GateSeverity.CRITICAL)

        if isinstance(data, dict):
            submitted = data.get("submitted", False)
            error = data.get("error")
            broker_order_id = data.get("broker_order_id")
        else:
            submitted = getattr(data, "submitted", False)
            error = getattr(data, "error", None)
            broker_order_id = getattr(data, "broker_order_id", None)

        if error:
            return self._block(
                f"주문 실행 오류: {error}",
                severity=GateSeverity.ERROR,
                error=str(error),
            )

        if not submitted:
            return self._block(
                "주문이 브로커에 전송되지 않았습니다",
                severity=GateSeverity.ERROR,
            )

        return self._pass(
            f"주문 실행 성공 (ID: {broker_order_id})",
            broker_order_id=broker_order_id,
        )
