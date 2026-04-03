"""DataGate: 원천 데이터 품질 검증 (수집 → 팩터 전이)."""

from typing import Any, List
from core.gates.base import BaseGate, GateResult, GateSeverity


class DataGate(BaseGate):
    """수집된 시세/뉴스/재무 데이터의 품질을 검증합니다.

    차단 조건:
    - 데이터가 비어 있음
    - 연속 결측일 초과
    - 이상치 비율 초과
    """

    @property
    def gate_id(self) -> str:
        return "DataGate"

    async def evaluate(self, data: Any, **kwargs) -> GateResult:
        max_missing = kwargs.get("max_consecutive_missing", 3)
        outlier_threshold = kwargs.get("outlier_ratio_max", 0.05)

        if data is None or (isinstance(data, (list, dict)) and len(data) == 0):
            return self._block(
                "데이터가 비어 있습니다",
                severity=GateSeverity.CRITICAL,
            )

        records = data if isinstance(data, list) else [data]

        # 결측 검사 (consecutive_missing 필드가 있으면 사용)
        for record in records:
            if hasattr(record, "consecutive_missing"):
                if record.consecutive_missing > max_missing:
                    return self._block(
                        f"연속 결측 {record.consecutive_missing}일 > 한도 {max_missing}일",
                        severity=GateSeverity.ERROR,
                        consecutive_missing=record.consecutive_missing,
                    )

        # 이상치 비율 검사
        outlier_ratio = kwargs.get("outlier_ratio", 0.0)
        if outlier_ratio > outlier_threshold:
            return self._block(
                f"이상치 비율 {outlier_ratio:.2%} > 임계값 {outlier_threshold:.2%}",
                severity=GateSeverity.WARNING,
                outlier_ratio=outlier_ratio,
            )

        return self._pass(
            f"데이터 품질 검증 통과 ({len(records)}건)",
            record_count=len(records),
        )
