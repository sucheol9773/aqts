"""PortfolioGate: 포트폴리오 구성 유효성 검증 (포트폴리오 → TradingGuard 전이)."""

from typing import Any
from core.gates.base import BaseGate, GateResult, GateSeverity


class PortfolioGate(BaseGate):
    """목표 포트폴리오의 구성 유효성을 검증합니다.

    차단 조건:
    - 포트폴리오가 없음
    - 단일 종목 집중도 초과
    - 비중 합 불일치
    """

    @property
    def gate_id(self) -> str:
        return "PortfolioGate"

    async def evaluate(self, data: Any, **kwargs) -> GateResult:
        max_single_weight = kwargs.get("max_single_weight", 0.20)

        if data is None:
            return self._block("포트폴리오가 없습니다", severity=GateSeverity.CRITICAL)

        positions = getattr(data, "positions", [])
        cash_weight = getattr(data, "cash_weight", 0.0)

        # 비중 합 검사
        pos_sum = sum(getattr(p, "target_weight", 0.0) for p in positions)
        total = pos_sum + cash_weight
        if abs(total - 1.0) > 0.01:
            return self._block(
                f"비중 합 불일치: {total:.4f}",
                severity=GateSeverity.ERROR,
                weight_sum=total,
            )

        # 단일 종목 집중도
        for p in positions:
            w = getattr(p, "target_weight", 0.0)
            if w > max_single_weight:
                return self._block(
                    f"{getattr(p, 'ticker', '?')} 비중 {w:.2%} > "
                    f"한도 {max_single_weight:.2%}",
                    severity=GateSeverity.WARNING,
                    ticker=getattr(p, "ticker", "?"),
                    weight=w,
                )

        return self._pass(
            f"포트폴리오 검증 통과 ({len(positions)}종목)",
            position_count=len(positions),
            cash_weight=cash_weight,
        )
