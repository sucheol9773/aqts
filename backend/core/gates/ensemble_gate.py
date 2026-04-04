"""EnsembleGate: 앙상블 결합 유효성 검증 (앙상블 → 포트폴리오 전이)."""

from typing import Any

from core.gates.base import BaseGate, GateResult, GateSeverity


class EnsembleGate(BaseGate):
    """앙상블 결합 결과의 유효성을 검증합니다.

    차단 조건:
    - 앙상블 결과 없음
    - 가중치 합이 1.0에서 벗어남
    - 단일 전략 가중치가 지나치게 높음
    """

    @property
    def gate_id(self) -> str:
        return "EnsembleGate"

    async def evaluate(self, data: Any, **kwargs) -> GateResult:
        max_single_strategy_weight = kwargs.get("max_single_strategy_weight", 0.6)

        if data is None:
            return self._block("앙상블 결과가 없습니다", severity=GateSeverity.CRITICAL)

        # dict 형태: {strategy_id: weight}
        if isinstance(data, dict):
            weights = data
        elif hasattr(data, "weights"):
            weights = data.weights
        else:
            return self._block(
                "앙상블 데이터 형식 불일치",
                severity=GateSeverity.ERROR,
            )

        if not weights:
            return self._block("앙상블 가중치가 비어 있습니다", severity=GateSeverity.CRITICAL)

        weight_sum = sum(weights.values())
        if abs(weight_sum - 1.0) > 0.01:
            return self._block(
                f"가중치 합 {weight_sum:.4f} ≠ 1.0",
                severity=GateSeverity.ERROR,
                weight_sum=weight_sum,
            )

        max_weight = max(weights.values())
        if max_weight > max_single_strategy_weight:
            max_strategy = max(weights, key=weights.get)
            return self._block(
                f"전략 '{max_strategy}' 가중치 {max_weight:.2%} > " f"한도 {max_single_strategy_weight:.2%}",
                severity=GateSeverity.WARNING,
                max_strategy=str(max_strategy),
                max_weight=max_weight,
            )

        return self._pass(
            f"앙상블 검증 통과 ({len(weights)}개 전략)",
            strategy_count=len(weights),
            weight_sum=weight_sum,
        )
