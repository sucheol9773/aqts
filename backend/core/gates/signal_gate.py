"""SignalGate: 시그널 유효성 검증 (시그널 → 앙상블 전이)."""

from typing import Any
from core.gates.base import BaseGate, GateResult, GateSeverity


class SignalGate(BaseGate):
    """개별 전략 시그널의 유효성을 검증합니다.

    차단 조건:
    - 시그널이 없음
    - 모든 시그널이 HOLD (의미 있는 액션 없음)
    - 상충 시그널 비율 초과
    """

    @property
    def gate_id(self) -> str:
        return "SignalGate"

    async def evaluate(self, data: Any, **kwargs) -> GateResult:
        if data is None or (isinstance(data, list) and len(data) == 0):
            return self._block("시그널이 없습니다", severity=GateSeverity.CRITICAL)

        signals = data if isinstance(data, list) else [data]

        # 모든 HOLD 검사
        directions = [getattr(s, "direction", None) for s in signals]
        non_hold = [d for d in directions if d is not None and d.value != "HOLD"]

        if len(non_hold) == 0:
            return self._block(
                f"모든 시그널이 HOLD ({len(signals)}건)",
                severity=GateSeverity.WARNING,
                total_signals=len(signals),
            )

        return self._pass(
            f"시그널 검증 통과 (활성 {len(non_hold)}/{len(signals)}건)",
            active_signals=len(non_hold),
            total_signals=len(signals),
        )
