"""
Contract 5: Signal — 매매 시그널 계약
"""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from config.constants import Market, SignalDirection, StrategyType


class Signal(BaseModel):
    """매매 시그널 계약.

    개별 전략이 산출한 매수/매도/보유 시그널을 표준화합니다.
    confidence는 [0.0, 1.0] 범위, BUY/SELL 시그널은 confidence > 0이어야 합니다.
    """

    ticker: str = Field(..., min_length=1, max_length=20, description="종목 코드")
    market: Market = Field(..., description="거래소")
    direction: SignalDirection = Field(..., description="시그널 방향")
    confidence: float = Field(..., ge=0.0, le=1.0, description="확신도 (0.0-1.0)")
    strategy_id: StrategyType = Field(..., description="시그널 생성 전략")
    reason: str = Field("", max_length=500, description="시그널 사유")

    generated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="시그널 생성 시각",
    )

    @field_validator("ticker")
    @classmethod
    def validate_ticker(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("ticker는 비어 있을 수 없습니다")
        return stripped

    @model_validator(mode="after")
    def validate_confidence_direction(self) -> "Signal":
        """BUY/SELL 시그널은 confidence > 0이어야 합니다."""
        if self.direction != SignalDirection.HOLD and self.confidence == 0.0:
            raise ValueError(
                f"{self.direction.value} 시그널의 confidence가 0: "
                f"확신이 없으면 HOLD 사용"
            )
        return self

    model_config = {"frozen": True, "extra": "forbid"}
