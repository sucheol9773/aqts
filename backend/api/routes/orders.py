"""
주문 API 라우터

주문 생성, 조회, 취소, 배치 실행 엔드포인트를 제공합니다.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.middleware.auth import get_current_user
from api.schemas.common import APIResponse, PaginatedResponse
from api.schemas.orders import (
    BatchOrderRequest,
    BatchOrderResponse,
    OrderCreateRequest,
    OrderResponse,
)
from config.constants import Market, OrderSide, OrderStatus, OrderType
from config.logging import logger

router = APIRouter()


@router.post("/", response_model=APIResponse[OrderResponse])
async def create_order(
    request: OrderCreateRequest,
    current_user: str = Depends(get_current_user),
):
    """
    단일 주문 생성

    시장가, 지정가, TWAP, VWAP 주문을 생성합니다.
    """
    try:
        # TODO: OrderExecutor 연동
        logger.info(
            f"Order created: {request.side} {request.ticker} "
            f"x{request.quantity} ({request.order_type})"
        )

        order = OrderResponse(
            order_id=f"ORD-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            ticker=request.ticker,
            market=request.market,
            side=request.side,
            quantity=request.quantity,
            order_type=request.order_type,
            status=OrderStatus.PENDING,
            limit_price=request.limit_price,
            reason=request.reason or "",
            created_at=datetime.now(timezone.utc),
        )
        return APIResponse(success=True, data=order, message="주문이 생성되었습니다.")
    except Exception as e:
        logger.error(f"Order creation error: {e}")
        return APIResponse(success=False, message=f"주문 생성 실패: {str(e)}")


@router.post("/batch", response_model=APIResponse[BatchOrderResponse])
async def create_batch_orders(
    request: BatchOrderRequest,
    current_user: str = Depends(get_current_user),
):
    """
    배치 주문 실행

    복수 주문을 동시에 생성·실행합니다. SELL 주문이 우선 처리됩니다.
    """
    try:
        # TODO: OrderExecutor.execute_batch_orders 연동
        results = []
        for order_req in request.orders:
            results.append(
                OrderResponse(
                    order_id=f"ORD-BATCH-{len(results)}",
                    ticker=order_req.ticker,
                    market=order_req.market,
                    side=order_req.side,
                    quantity=order_req.quantity,
                    order_type=order_req.order_type,
                    status=OrderStatus.PENDING,
                    limit_price=order_req.limit_price,
                    reason=order_req.reason or "",
                    created_at=datetime.now(timezone.utc),
                )
            )

        batch_response = BatchOrderResponse(
            results=results,
            total=len(results),
            success_count=len(results),
            fail_count=0,
        )
        return APIResponse(
            success=True, data=batch_response,
            message=f"{len(results)}건 배치 주문이 생성되었습니다.",
        )
    except Exception as e:
        logger.error(f"Batch order error: {e}")
        return APIResponse(success=False, message=f"배치 주문 실패: {str(e)}")


@router.get("/", response_model=APIResponse[list[OrderResponse]])
async def get_orders(
    status: Optional[str] = Query(default=None, description="주문 상태 필터"),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: str = Depends(get_current_user),
):
    """
    주문 이력 조회
    """
    try:
        # TODO: 실제 주문 이력 DB 조회
        orders: list[OrderResponse] = []
        return APIResponse(success=True, data=orders)
    except Exception as e:
        logger.error(f"Orders query error: {e}")
        return APIResponse(success=False, message=f"조회 실패: {str(e)}")


@router.get("/{order_id}", response_model=APIResponse[OrderResponse])
async def get_order(
    order_id: str,
    current_user: str = Depends(get_current_user),
):
    """
    단일 주문 상세 조회
    """
    try:
        # TODO: 실제 주문 조회
        raise HTTPException(status_code=404, detail=f"주문을 찾을 수 없습니다: {order_id}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Order query error: {e}")
        return APIResponse(success=False, message=f"조회 실패: {str(e)}")


@router.delete("/{order_id}", response_model=APIResponse[dict])
async def cancel_order(
    order_id: str,
    current_user: str = Depends(get_current_user),
):
    """
    주문 취소

    PENDING 또는 SUBMITTED 상태의 주문만 취소 가능합니다.
    """
    try:
        # TODO: 실제 주문 취소 로직
        logger.info(f"Order cancel requested: {order_id}")
        return APIResponse(
            success=True,
            data={"order_id": order_id, "status": "CANCELLED"},
            message="주문이 취소되었습니다.",
        )
    except Exception as e:
        logger.error(f"Order cancel error: {e}")
        return APIResponse(success=False, message=f"취소 실패: {str(e)}")
