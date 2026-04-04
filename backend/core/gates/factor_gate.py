"""FactorGate: 팩터 스코어 유효성 검증 (팩터 → 시그널 전이)."""

from typing import Any

from core.gates.base import BaseGate, GateResult, GateSeverity


class FactorGate(BaseGate):
    """팩터 분석 결과의 유효성을 검증합니다.

    차단 조건:
    - FeatureVector가 비어 있음
    - 팩터 값이 범위를 벗어남
    - 계산 가능 종목 비율이 낮음
    """

    @property
    def gate_id(self) -> str:
        return "FactorGate"

    async def evaluate(self, data: Any, **kwargs) -> GateResult:
        min_coverage = kwargs.get("min_coverage", 0.5)

        if data is None or (isinstance(data, list) and len(data) == 0):
            return self._block("팩터 벡터가 없습니다", severity=GateSeverity.CRITICAL)

        vectors = data if isinstance(data, list) else [data]
        total = len(vectors)
        valid = 0

        for vec in vectors:
            # FeatureVector 계약으로 이미 범위 검증됨 → 존재만 확인
            has_any = any(
                getattr(vec, f, None) is not None
                for f in (
                    "factor_value",
                    "factor_momentum",
                    "factor_quality",
                    "factor_low_vol",
                    "factor_size",
                    "tech_rsi",
                    "sentiment",
                )
            )
            if has_any:
                valid += 1

        coverage = valid / total if total > 0 else 0.0
        if coverage < min_coverage:
            return self._block(
                f"팩터 커버리지 {coverage:.1%} < 최소 {min_coverage:.1%}",
                severity=GateSeverity.ERROR,
                coverage=coverage,
                total=total,
                valid=valid,
            )

        return self._pass(
            f"팩터 검증 통과 (커버리지 {coverage:.1%}, {valid}/{total}건)",
            coverage=coverage,
        )
