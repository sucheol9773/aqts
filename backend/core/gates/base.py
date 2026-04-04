"""
Gate 기반 클래스: GateResult 스키마 + BaseGate 추상 클래스
"""

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class GateDecision(str, Enum):
    """Gate 판정 결과."""

    PASS = "PASS"
    BLOCK = "BLOCK"


class GateSeverity(str, Enum):
    """Gate 차단 심각도."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class GateResult(BaseModel):
    """Gate 판정 결과 레코드.

    모든 Gate는 이 스키마를 반환합니다.
    Stage 4 감사 체인에서 외부 참조로 사용됩니다.
    """

    gate_id: str = Field(..., description="Gate 식별자 (e.g., 'DataGate')")
    decision: GateDecision = Field(..., description="PASS 또는 BLOCK")
    reason: str = Field("", max_length=500, description="판정 사유")
    severity: GateSeverity = Field(GateSeverity.INFO, description="심각도")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    context: dict = Field(default_factory=dict, description="추가 컨텍스트")
    decision_id: Optional[str] = Field(None, description="감사 체인 연결 ID")

    model_config = {"frozen": True, "extra": "forbid"}


class BaseGate(ABC):
    """Gate 추상 기반 클래스.

    모든 Gate는 이 클래스를 상속하고 evaluate()를 구현합니다.
    """

    @property
    @abstractmethod
    def gate_id(self) -> str:
        """Gate 고유 식별자."""
        ...

    @abstractmethod
    async def evaluate(self, data: Any, **kwargs) -> GateResult:
        """Gate 판정을 수행합니다.

        Args:
            data: 판정 대상 데이터
            **kwargs: 추가 컨텍스트

        Returns:
            GateResult: PASS 또는 BLOCK 판정
        """
        ...

    def _pass(self, reason: str = "", **ctx) -> GateResult:
        """PASS 결과 헬퍼."""
        return GateResult(
            gate_id=self.gate_id,
            decision=GateDecision.PASS,
            reason=reason,
            context=ctx,
        )

    def _block(self, reason: str, severity: GateSeverity = GateSeverity.ERROR, **ctx) -> GateResult:
        """BLOCK 결과 헬퍼."""
        return GateResult(
            gate_id=self.gate_id,
            decision=GateDecision.BLOCK,
            reason=reason,
            severity=severity,
            context=ctx,
        )
