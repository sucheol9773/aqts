"""
Contract 4: FeatureVector — 팩터 스코어 + 기술적 지표 + 감성 점수 계약
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class FeatureVector(BaseModel):
    """특성 벡터 계약.

    팩터 스코어와 기술적 지표를 정규화된 [-1.0, +1.0] 범위로 강제합니다.
    최소 1개 이상의 팩터 또는 지표가 존재해야 합니다.
    """

    ticker: str = Field(..., min_length=1, max_length=20, description="종목 코드")
    as_of: datetime = Field(..., description="특성 계산 기준 시점")

    # 팩터 스코어 (정규화: -1.0 ~ +1.0)
    factor_value: Optional[float] = Field(None, ge=-1.0, le=1.0, description="가치 팩터")
    factor_momentum: Optional[float] = Field(None, ge=-1.0, le=1.0, description="모멘텀 팩터")
    factor_quality: Optional[float] = Field(None, ge=-1.0, le=1.0, description="퀄리티 팩터")
    factor_low_vol: Optional[float] = Field(None, ge=-1.0, le=1.0, description="저변동성 팩터")
    factor_size: Optional[float] = Field(None, ge=-1.0, le=1.0, description="규모 팩터")

    # 기술적 지표 (정규화)
    tech_rsi: Optional[float] = Field(None, ge=0.0, le=100.0, description="RSI (0-100)")
    tech_macd_signal: Optional[float] = Field(None, description="MACD 시그널 차이")
    tech_bollinger_pctb: Optional[float] = Field(None, description="볼린저 %B")

    # 감성 점수
    sentiment: Optional[float] = Field(None, ge=-1.0, le=1.0, description="감성 점수")

    calculated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="계산 완료 시각",
    )

    @field_validator("ticker")
    @classmethod
    def validate_ticker(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("ticker는 비어 있을 수 없습니다")
        return stripped

    @model_validator(mode="after")
    def validate_at_least_one_feature(self) -> "FeatureVector":
        """최소 1개 팩터/기술적 지표/감성 점수가 존재해야 합니다."""
        feature_fields = [
            self.factor_value, self.factor_momentum, self.factor_quality,
            self.factor_low_vol, self.factor_size,
            self.tech_rsi, self.tech_macd_signal, self.tech_bollinger_pctb,
            self.sentiment,
        ]
        if all(f is None for f in feature_fields):
            raise ValueError("최소 1개 이상의 특성(팩터/기술지표/감성)이 필요합니다")
        return self

    model_config = {"frozen": True, "extra": "forbid"}
