"""ReconGate: 브로커 대사 검증 (체결 → 포지션 반영 전이)."""

from typing import Any

from core.gates.base import BaseGate, GateResult, GateSeverity


class ReconGate(BaseGate):
    """브로커와 내부 DB 간 포지션/잔고 일치를 검증합니다.

    차단 조건:
    - 대사 불일치 (수량 또는 잔고)
    - 브로커 응답 없음
    """

    @property
    def gate_id(self) -> str:
        return "ReconGate"

    async def evaluate(self, data: Any, **kwargs) -> GateResult:
        if data is None:
            return self._block("대사 데이터가 없습니다", severity=GateSeverity.CRITICAL)

        if isinstance(data, dict):
            broker_balance = data.get("broker_balance")
            internal_balance = data.get("internal_balance")
            mismatches = data.get("mismatches", [])
        else:
            broker_balance = getattr(data, "broker_balance", None)
            internal_balance = getattr(data, "internal_balance", None)
            mismatches = getattr(data, "mismatches", [])

        if broker_balance is None:
            return self._block(
                "브로커 잔고 정보를 받지 못했습니다",
                severity=GateSeverity.CRITICAL,
            )

        if mismatches:
            return self._block(
                f"대사 불일치 {len(mismatches)}건 발견",
                severity=GateSeverity.ERROR,
                mismatch_count=len(mismatches),
                mismatches=mismatches[:5],  # 최대 5건만 기록
            )

        return self._pass(
            "브로커 대사 일치 확인",
            broker_balance=broker_balance,
            internal_balance=internal_balance,
        )
