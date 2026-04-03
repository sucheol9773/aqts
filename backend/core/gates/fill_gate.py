"""FillGate: 주문 체결 완전성 검증 (체결 → 완료 전이)."""

from typing import Any
from core.gates.base import BaseGate, GateResult, GateSeverity


class FillGate(BaseGate):
    """주문 체결 완전성을 검증합니다.

    차단 조건:
    - 체결 실패 (FAILED 상태)
    - 부분 체결 비율이 너무 낮음
    """

    @property
    def gate_id(self) -> str:
        return "FillGate"

    async def evaluate(self, data: Any, **kwargs) -> GateResult:
        min_fill_ratio = kwargs.get("min_fill_ratio", 0.0)

        if data is None:
            return self._block("체결 데이터가 없습니다", severity=GateSeverity.CRITICAL)

        if isinstance(data, dict):
            status = data.get("status", "")
            requested = data.get("requested_quantity", 0)
            filled = data.get("filled_quantity", 0)
        else:
            status = getattr(data, "status", "")
            requested = getattr(data, "requested_quantity", 0)
            filled = getattr(data, "filled_quantity", 0)

        # Enum 처리
        status_val = status.value if hasattr(status, "value") else str(status)

        if status_val == "FAILED":
            return self._block(
                "주문 체결 실패 (FAILED)",
                severity=GateSeverity.ERROR,
            )

        if status_val == "CANCELLED" and filled == 0:
            return self._block(
                "주문 전량 취소",
                severity=GateSeverity.WARNING,
            )

        fill_ratio = filled / requested if requested > 0 else 0.0

        if min_fill_ratio > 0 and fill_ratio < min_fill_ratio:
            return self._block(
                f"체결 비율 {fill_ratio:.1%} < 최소 {min_fill_ratio:.1%}",
                severity=GateSeverity.WARNING,
                fill_ratio=fill_ratio,
            )

        return self._pass(
            f"체결 검증 통과 ({filled}/{requested}, {fill_ratio:.1%})",
            fill_ratio=fill_ratio,
            filled=filled,
            requested=requested,
        )
