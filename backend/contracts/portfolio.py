"""
Contract 6: Portfolio — 포트폴리오 목표 포지션 계약
"""

from datetime import datetime
from typing import List

from pydantic import BaseModel, Field, field_validator, model_validator

from config.constants import Market


class PositionTarget(BaseModel):
    """개별 종목 목표 비중."""

    ticker: str = Field(..., min_length=1, max_length=20, description="종목 코드")
    market: Market = Field(..., description="거래소")
    target_weight: float = Field(..., ge=0.0, le=1.0, description="목표 비중 (0.0-1.0)")
    current_weight: float = Field(0.0, ge=0.0, le=1.0, description="현재 비중")
    reason: str = Field("", max_length=500, description="포지션 사유")

    @field_validator("ticker")
    @classmethod
    def validate_ticker(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("ticker는 비어 있을 수 없습니다")
        return stripped

    model_config = {"frozen": True, "extra": "forbid"}


class PortfolioTarget(BaseModel):
    """포트폴리오 전체 목표 구성 계약.

    positions의 target_weight 합이 1.0 이하여야 하며 (나머지는 현금),
    중복 ticker가 허용되지 않습니다.
    """

    positions: List[PositionTarget] = Field(..., min_length=0, description="목표 포지션 목록")
    cash_weight: float = Field(..., ge=0.0, le=1.0, description="현금 비중")
    rebalance_reason: str = Field("", max_length=500, description="리밸런싱 사유")
    generated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="포트폴리오 생성 시각",
    )

    @model_validator(mode="after")
    def validate_weight_sum(self) -> "PortfolioTarget":
        """target_weight 합 + cash_weight ≈ 1.0 (±0.01 허용)."""
        pos_sum = sum(p.target_weight for p in self.positions)
        total = pos_sum + self.cash_weight
        if abs(total - 1.0) > 0.01:
            raise ValueError(
                f"비중 합계 불일치: positions({pos_sum:.4f}) + "
                f"cash({self.cash_weight:.4f}) = {total:.4f} (≈1.0 필요)"
            )
        return self

    @model_validator(mode="after")
    def validate_no_duplicate_tickers(self) -> "PortfolioTarget":
        """동일 ticker 중복 불가."""
        tickers = [p.ticker for p in self.positions]
        duplicates = {t for t in tickers if tickers.count(t) > 1}
        if duplicates:
            raise ValueError(f"중복 ticker 발견: {duplicates}")
        return self

    model_config = {"frozen": True, "extra": "forbid"}
