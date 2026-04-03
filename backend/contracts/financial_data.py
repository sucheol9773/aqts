"""
Contract 2: FinancialData — 재무제표 지표 계약
"""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class FinancialData(BaseModel):
    """재무제표 데이터 계약.

    EPS, PER, PBR, ROE 등 핵심 재무 비율을 검증합니다.
    filing_date(공시일)는 period_end(결산일) 이후여야 합니다 (look-ahead bias 방지).
    """

    ticker: str = Field(..., min_length=1, max_length=20, description="종목 코드")
    period_end: date = Field(..., description="결산 기말일 (e.g., 2024-12-31)")
    filing_date: date = Field(..., description="공시 제출일 (point-in-time)")

    eps: Optional[float] = Field(None, description="주당순이익 (원)")
    per: Optional[float] = Field(None, description="주가수익비율")
    pbr: Optional[float] = Field(None, description="주가순자산비율")
    roe: Optional[float] = Field(None, description="자기자본이익률 (%)")
    revenue: Optional[float] = Field(None, ge=0, description="매출액 (백만 원)")
    operating_income: Optional[float] = Field(None, description="영업이익 (백만 원)")
    net_income: Optional[float] = Field(None, description="당기순이익 (백만 원)")
    debt_ratio: Optional[float] = Field(None, ge=0, description="부채비율 (%)")

    collected_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="수집 시각 (UTC)",
    )

    @field_validator("ticker")
    @classmethod
    def validate_ticker(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("ticker는 비어 있을 수 없습니다")
        return stripped

    @model_validator(mode="after")
    def validate_filing_after_period(self) -> "FinancialData":
        """filing_date >= period_end (공시는 결산 후에만 가능)."""
        if self.filing_date < self.period_end:
            raise ValueError(
                f"filing_date({self.filing_date}) < period_end({self.period_end}): "
                f"look-ahead bias 위험"
            )
        return self

    @field_validator("per")
    @classmethod
    def validate_per_range(cls, v: Optional[float]) -> Optional[float]:
        """PER 이상치 경고용 제한 (0 이하 또는 1000 초과 시 거부)."""
        if v is not None and (v <= 0 or v > 1000):
            raise ValueError(f"PER 이상치: {v} (0 < PER ≤ 1000 범위)")
        return v

    model_config = {"frozen": True, "extra": "forbid"}
