"""
주문 관련 스키마

주문 생성, 주문 상태, 배치 주문 등 주문 관련 요청/응답 모델을 정의합니다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class OrderCreateRequest(BaseModel):
    """
    주문 생성 요청

    새로운 주문을 생성할 때 사용되는 요청 스키마입니다.
    """

    ticker: str = Field(..., description="종목 코드")
    market: str = Field(..., description="시장 (KRX, NYSE, NASDAQ 등)")
    side: str = Field(..., description="주문 방향 (BUY, SELL)")
    quantity: int = Field(..., gt=0, description="주문 수량")
    order_type: str = Field(..., description="주문 유형 (MARKET, LIMIT, TWAP, VWAP)")
    limit_price: Optional[float] = Field(default=None, gt=0, description="지정가 (LIMIT 주문 시 필수)")
    reason: Optional[str] = Field(default=None, description="주문 사유")


class OrderResponse(BaseModel):
    """
    주문 응답

    주문 생성 또는 조회 시 반환되는 주문 정보입니다.
    """

    model_config = ConfigDict(from_attributes=True)

    order_id: str = Field(..., description="주문 ID")
    ticker: str = Field(..., description="종목 코드")
    market: str = Field(..., description="시장 (KRX, NYSE, NASDAQ 등)")
    side: str = Field(..., description="주문 방향 (BUY, SELL)")
    quantity: int = Field(..., description="주문 수량")
    order_type: str = Field(..., description="주문 유형 (MARKET, LIMIT, TWAP, VWAP)")
    status: str = Field(..., description="주문 상태 (PENDING, SUBMITTED, PARTIAL, FILLED, CANCELLED, FAILED)")
    filled_price: Optional[float] = Field(default=None, description="체결 단가")
    filled_at: Optional[datetime] = Field(default=None, description="체결 시간 (UTC)")
    reason: Optional[str] = Field(default=None, description="주문 사유")


class BatchOrderRequest(BaseModel):
    """
    배치 주문 요청

    여러 개의 주문을 한 번에 생성할 때 사용됩니다.
    """

    orders: list[OrderCreateRequest] = Field(..., min_length=1, description="주문 목록")


class BatchOrderResponse(BaseModel):
    """
    배치 주문 응답

    여러 개의 주문 생성 결과를 반환합니다.
    """

    model_config = ConfigDict(from_attributes=True)

    results: list[OrderResponse] = Field(..., description="주문 응답 목록")
    total: int = Field(..., ge=0, description="전체 주문 수")
    success_count: int = Field(..., ge=0, description="성공한 주문 수")
    fail_count: int = Field(..., ge=0, description="실패한 주문 수")
