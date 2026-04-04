"""
Contract 1: PriceData — OHLCV 시세 데이터 계약
"""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from config.constants import Market


class PriceData(BaseModel):
    """일봉 OHLCV 시세 데이터 계약.

    모든 가격 필드는 양수, volume은 0 이상이어야 하며,
    high >= max(open, close), low <= min(open, close) 관계를 강제합니다.
    """

    ticker: str = Field(..., min_length=1, max_length=20, description="종목 코드")
    market: Market = Field(..., description="거래소")
    trade_date: date = Field(..., description="거래일")
    open: float = Field(..., gt=0, description="시가")
    high: float = Field(..., gt=0, description="고가")
    low: float = Field(..., gt=0, description="저가")
    close: float = Field(..., gt=0, description="종가")
    volume: int = Field(..., ge=0, description="거래량")
    adjusted_close: Optional[float] = Field(None, gt=0, description="수정 종가")
    collected_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="수집 시각 (UTC)",
    )

    @field_validator("ticker")
    @classmethod
    def validate_ticker_format(cls, v: str) -> str:
        """종목 코드는 영숫자와 점(.)만 허용."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("ticker는 비어 있을 수 없습니다")
        if not all(c.isalnum() or c in (".", "-") for c in stripped):
            raise ValueError(f"ticker에 허용되지 않는 문자: {stripped}")
        return stripped

    @model_validator(mode="after")
    def validate_ohlc_consistency(self) -> "PriceData":
        """OHLC 관계: high >= low, high >= max(open, close), low <= min(open, close)."""
        if self.high < self.low:
            raise ValueError(f"high({self.high}) < low({self.low}): OHLC 관계 위반")
        if self.high < max(self.open, self.close):
            raise ValueError(f"high({self.high}) < max(open={self.open}, close={self.close})")
        if self.low > min(self.open, self.close):
            raise ValueError(f"low({self.low}) > min(open={self.open}, close={self.close})")
        return self

    model_config = {"frozen": True, "extra": "forbid"}
