"""TradingGuardGate: 7-layer TradingGuard 어댑터 (포트폴리오 → 주문 전이)."""

from typing import Any

from core.gates.base import BaseGate, GateResult, GateSeverity


class TradingGuardGate(BaseGate):
    """TradingGuard 7-layer 규칙 엔진을 래핑하는 Gate 어댑터.

    TradingGuard의 내부 sub-check(Risk/Liquidity/Volatility/Correlation/Capital)
    결과를 Gate 프로토콜로 변환합니다.

    차단 조건:
    - TradingGuard가 거래를 거부
    - 환경 검증 실패
    - 자본 한도 초과
    """

    def __init__(self, trading_guard=None):
        """TradingGuard 인스턴스를 주입받습니다."""
        self._guard = trading_guard

    @property
    def gate_id(self) -> str:
        return "TradingGuardGate"

    async def evaluate(self, data: Any, **kwargs) -> GateResult:
        if self._guard is None:
            # TradingGuard 미설정 시 pass-through (테스트 환경)
            guard_result = kwargs.get("guard_result")
            if guard_result is None:
                return self._block(
                    "TradingGuard가 설정되지 않았습니다",
                    severity=GateSeverity.CRITICAL,
                )
        else:
            # 실제 TradingGuard 호출
            guard_result = (
                await self._guard.validate(data) if hasattr(self._guard, "validate") else kwargs.get("guard_result")
            )

        if guard_result is None:
            return self._block(
                "TradingGuard 결과를 얻을 수 없습니다",
                severity=GateSeverity.CRITICAL,
            )

        # guard_result는 dict 또는 객체
        is_approved = False
        reason = ""

        if isinstance(guard_result, dict):
            is_approved = guard_result.get("approved", False)
            reason = guard_result.get("reason", "")
            blocked_layers = guard_result.get("blocked_layers", [])
        elif hasattr(guard_result, "approved"):
            is_approved = guard_result.approved
            reason = getattr(guard_result, "reason", "")
            blocked_layers = getattr(guard_result, "blocked_layers", [])
        else:
            is_approved = bool(guard_result)
            reason = str(guard_result)
            blocked_layers = []

        if not is_approved:
            return self._block(
                f"TradingGuard 거부: {reason}",
                severity=GateSeverity.ERROR,
                blocked_layers=blocked_layers,
            )

        return self._pass("TradingGuard 승인")
