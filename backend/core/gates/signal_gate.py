"""SignalGate: 시그널 유효성 검증 (시그널 → 앙상블 전이)."""

from typing import Any

from core.gates.base import BaseGate, GateResult, GateSeverity


class SignalGate(BaseGate):
    """개별 전략 시그널의 유효성을 검증합니다.

    차단 조건:
    - 시그널이 없음
    - 모든 시그널이 HOLD (의미 있는 액션 없음)
    - 상충 시그널 비율 초과

    시그널 활성 판정 기준:
    - direction 속성이 있으면 direction.value != "HOLD" 로 판정
    - value (float) 속성이 있으면 abs(value) > hold_threshold 로 판정
    """

    @property
    def gate_id(self) -> str:
        return "SignalGate"

    async def evaluate(self, data: Any, **kwargs) -> GateResult:
        hold_threshold = kwargs.get("hold_threshold", 0.05)

        if data is None or (isinstance(data, list) and len(data) == 0):
            return self._block("시그널이 없습니다", severity=GateSeverity.CRITICAL)

        signals = data if isinstance(data, list) else [data]

        active_count = 0
        for s in signals:
            # 방법 1: direction enum (QuantSignal 등)
            direction = getattr(s, "direction", None)
            if direction is not None:
                if direction.value != "HOLD":
                    active_count += 1
                continue

            # 방법 2: value float (StrategySignalInput 등)
            value = getattr(s, "value", None)
            if value is not None and abs(value) > hold_threshold:
                active_count += 1

        if active_count == 0:
            return self._block(
                f"모든 시그널이 HOLD ({len(signals)}건)",
                severity=GateSeverity.WARNING,
                total_signals=len(signals),
            )

        return self._pass(
            f"시그널 검증 통과 (활성 {active_count}/{len(signals)}건)",
            active_signals=active_count,
            total_signals=len(signals),
        )
