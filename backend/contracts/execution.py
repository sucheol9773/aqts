"""
Contract 8: Execution — 체결 결과 계약
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from config.constants import Market, OrderSide, OrderStatus


class ExecutionResult(BaseModel):
    """체결 결과 계약.

    broker_order_id를 통해 주문 의도와 1:N 연결됩니다 (부분 체결 포함).
    filled_quantity <= requested_quantity, 체결가 > 0.
    """

    broker_order_id: str = Field(..., min_length=1, description="브로커 주문 ID")
    ticker: str = Field(..., min_length=1, max_length=20, description="종목 코드")
    market: Market = Field(..., description="거래소")
    side: OrderSide = Field(..., description="주문 방향")
    status: OrderStatus = Field(..., description="체결 상태")

    requested_quantity: int = Field(..., gt=0, description="요청 수량")
    filled_quantity: int = Field(..., ge=0, description="체결 수량")
    filled_price: Optional[float] = Field(None, gt=0, description="평균 체결가")
    commission: float = Field(0.0, ge=0, description="수수료")
    slippage: float = Field(0.0, description="슬리피지 (예상가 대비 차이)")

    decision_id: Optional[str] = Field(None, description="감사 체인 연결 ID")
    executed_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="체결 시각",
    )

    @field_validator("ticker")
    @classmethod
    def validate_ticker(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("ticker는 비어 있을 수 없습니다")
        return stripped

    @model_validator(mode="after")
    def validate_fill_quantity(self) -> "ExecutionResult":
        """filled_quantity <= requested_quantity."""
        if self.filled_quantity > self.requested_quantity:
            raise ValueError(
                f"filled_quantity({self.filled_quantity}) > "
                f"requested_quantity({self.requested_quantity})"
            )
        return self

    @model_validator(mode="after")
    def validate_filled_has_price(self) -> "ExecutionResult":
        """FILLED/PARTIAL 상태에서는 filled_price가 반드시 존재."""
        if self.status in (OrderStatus.FILLED, OrderStatus.PARTIAL):
            if self.filled_quantity > 0 and self.filled_price is None:
                raise ValueError(
                    f"status={self.status.value}이고 filled_quantity > 0이면 "
                    f"filled_price가 필수입니다"
                )
        return self

    model_config = {"frozen": True, "extra": "forbid"}
