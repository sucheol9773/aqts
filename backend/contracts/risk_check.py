"""
Contract 9: RiskCheck — 리스크 점검 결과 계약
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class RiskCheckDecision(str, Enum):
    """리스크 점검 결정."""
    PASS = "PASS"
    BLOCK = "BLOCK"
    WARN = "WARN"


class RiskCheckSeverity(str, Enum):
    """리스크 점검 심각도."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskCheckItem(BaseModel):
    """개별 리스크 점검 항목.

    TradingGuard의 7-layer 각 layer별 결과를 표현합니다.
    """

    layer_name: str = Field(..., min_length=1, max_length=100, description="리스크 레이어 이름")
    decision: RiskCheckDecision = Field(..., description="판정")
    severity: RiskCheckSeverity = Field(RiskCheckSeverity.LOW, description="심각도")
    reason: str = Field("", max_length=500, description="판정 사유")
    metric_value: Optional[float] = Field(None, description="측정값")
    threshold: Optional[float] = Field(None, description="임계값")

    model_config = {"frozen": True, "extra": "forbid"}


class RiskCheckResult(BaseModel):
    """리스크 점검 종합 결과 계약.

    TradingGuard 7-layer 전체 결과를 집계합니다.
    1개라도 BLOCK이면 전체 결정도 BLOCK입니다.
    """

    ticker: str = Field(..., min_length=1, max_length=20, description="종목 코드")
    checks: List[RiskCheckItem] = Field(
        ..., min_length=1, description="개별 리스크 점검 목록"
    )
    overall_decision: RiskCheckDecision = Field(..., description="종합 판정")
    decision_id: Optional[str] = Field(None, description="감사 체인 연결 ID")

    checked_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="점검 시각",
    )

    @field_validator("ticker")
    @classmethod
    def validate_ticker(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("ticker는 비어 있을 수 없습니다")
        return stripped

    @model_validator(mode="after")
    def validate_overall_consistency(self) -> "RiskCheckResult":
        """BLOCK 항목이 있으면 overall도 BLOCK이어야 합니다."""
        has_block = any(c.decision == RiskCheckDecision.BLOCK for c in self.checks)
        if has_block and self.overall_decision != RiskCheckDecision.BLOCK:
            raise ValueError(
                "개별 점검에 BLOCK이 있지만 overall_decision이 BLOCK이 아닙니다"
            )
        return self

    model_config = {"frozen": True, "extra": "forbid"}
