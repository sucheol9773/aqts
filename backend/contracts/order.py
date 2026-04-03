"""
Contract 7: Order — 주문 의도 계약
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from config.constants import Market, OrderSide, OrderType


class OrderIntent(BaseModel):
    """주문 의도 계약.

    주문 파라미터의 유효성을 강제합니다:
    - LIMIT 주문 시 limit_price 필수
    - quantity > 0
    - ticker 형식 검증
    """

    ticker: str = Field(..., min_length=1, max_length=20, description="종목 코드")
    market: Market = Field(..., description="거래소")
    side: OrderSide = Field(..., description="주문 방향")
    order_type: OrderType = Field(..., description="주문 유형")
    quantity: int = Field(..., gt=0, description="주문 수량")
    limit_price: Optional[float] = Field(None, gt=0, description="지정가 (LIMIT 시 필수)")
    reason: str = Field("", max_length=500, description="주문 사유")
    strategy_id: Optional[str] = Field(None, max_length=50, description="전략 식별자")
    decision_id: Optional[str] = Field(None, description="감사 체인 연결 ID")

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="주문 생성 시각",
    )

    @field_validator("ticker")
    @classmethod
    def validate_ticker(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("ticker는 비어 있을 수 없습니다")
        return stripped

    @model_validator(mode="after")
    def validate_limit_price_required(self) -> "OrderIntent":
        """LIMIT 주문 시 limit_price가 반드시 존재해야 합니다."""
        if self.order_type == OrderType.LIMIT and self.limit_price is None:
            raise ValueError("LIMIT 주문에는 limit_price가 필수입니다")
        return self

    model_config = {"frozen": True, "extra": "forbid"}
