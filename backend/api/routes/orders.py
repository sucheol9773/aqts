"""
주문 API 라우터

주문 생성, 조회, 취소, 배치 실행 엔드포인트를 제공합니다.
OrderExecutor 엔진과 직접 연동하여 실제 주문을 처리합니다.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from api.middleware.auth import get_current_user
from api.middleware.rate_limiter import RATE_ORDER, limiter
from api.schemas.common import APIResponse
from api.schemas.orders import (
    BatchOrderRequest,
    BatchOrderResponse,
    OrderCreateRequest,
    OrderResponse,
)
from config.constants import Market, OrderSide, OrderStatus, OrderType
from config.logging import logger
from core.order_executor.executor import OrderExecutor, OrderRequest, OrderResult
from db.database import get_db_session
from db.repositories.audit_log import AuditLogger

router = APIRouter()


def _order_result_to_response(
    result: OrderResult,
    order_type: str = "MARKET",
    reason: str = "",
) -> OrderResponse:
    """OrderResult → OrderResponse 변환 헬퍼"""
    return OrderResponse(
        order_id=result.order_id,
        ticker=result.ticker,
        market=result.market.value,
        side=result.side.value,
        quantity=result.quantity,
        order_type=order_type,
        status=result.status.value,
        filled_price=result.avg_price if result.avg_price > 0 else None,
        filled_at=result.executed_at if result.status == OrderStatus.FILLED else None,
        reason=reason,
    )


@router.post("/", response_model=APIResponse[OrderResponse])
@limiter.limit(RATE_ORDER)
async def create_order(
    request: Request,
    order_body: OrderCreateRequest,
    current_user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """
    단일 주문 생성

    시장가, 지정가, TWAP, VWAP 주문을 생성하고 OrderExecutor를 통해 실행합니다.
    """
    try:
        logger.info(
            f"Order created: {order_body.side} {order_body.ticker} " f"x{order_body.quantity} ({order_body.order_type})"
        )

        # API 스키마 → OrderRequest 변환
        order_req = OrderRequest(
            ticker=order_body.ticker,
            market=Market(order_body.market),
            side=OrderSide(order_body.side),
            quantity=order_body.quantity,
            order_type=OrderType(order_body.order_type),
            limit_price=order_body.limit_price,
            reason=order_body.reason or "",
        )

        # OrderExecutor 실행
        executor = OrderExecutor()
        result = await executor.execute_order(order_req)

        # 감사 로그 기록
        audit = AuditLogger(db)
        await audit.log(
            action_type="ORDER_CREATED",
            module="order_executor",
            description=(
                f"Order {result.order_id}: {order_body.side} {order_body.ticker} "
                f"x{order_body.quantity} ({order_body.order_type}) → {result.status.value}"
            ),
            metadata=result.to_dict(),
        )

        response = _order_result_to_response(
            result,
            order_type=order_body.order_type,
            reason=order_body.reason or "",
        )
        return APIResponse(success=True, data=response, message="주문이 실행되었습니다.")
    except Exception as e:
        logger.error(f"Order creation error: {e}")
        return APIResponse(success=False, message=f"주문 생성 실패: {str(e)}")


@router.post("/batch", response_model=APIResponse[BatchOrderResponse])
@limiter.limit(RATE_ORDER)
async def create_batch_orders(
    request: Request,
    batch_body: BatchOrderRequest,
    current_user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """
    배치 주문 실행

    복수 주문을 동시에 생성·실행합니다. SELL 주문이 우선 처리됩니다.
    """
    try:
        # API 스키마 → OrderRequest 리스트 변환
        order_requests = [
            OrderRequest(
                ticker=o.ticker,
                market=Market(o.market),
                side=OrderSide(o.side),
                quantity=o.quantity,
                order_type=OrderType(o.order_type),
                limit_price=o.limit_price,
                reason=o.reason or "",
            )
            for o in batch_body.orders
        ]

        # OrderExecutor 배치 실행
        executor = OrderExecutor()
        results = await executor.execute_batch_orders(order_requests)

        # 결과를 OrderResponse로 변환
        responses: list[OrderResponse] = []
        success_count = 0
        fail_count = 0

        for result, orig in zip(results, batch_body.orders):
            responses.append(
                _order_result_to_response(
                    result,
                    order_type=orig.order_type,
                    reason=orig.reason or "",
                )
            )
            if result.status in (OrderStatus.FILLED, OrderStatus.PARTIAL):
                success_count += 1
            elif result.status == OrderStatus.FAILED:
                fail_count += 1

        # 감사 로그
        audit = AuditLogger(db)
        await audit.log(
            action_type="BATCH_ORDER_CREATED",
            module="order_executor",
            description=(f"Batch order: {len(results)}건 " f"(성공: {success_count}, 실패: {fail_count})"),
            metadata={
                "total": len(results),
                "success_count": success_count,
                "fail_count": fail_count,
            },
        )

        batch_response = BatchOrderResponse(
            results=responses,
            total=len(responses),
            success_count=success_count,
            fail_count=fail_count,
        )
        return APIResponse(
            success=True,
            data=batch_response,
            message=f"{len(results)}건 배치 주문이 실행되었습니다.",
        )
    except Exception as e:
        logger.error(f"Batch order error: {e}")
        return APIResponse(success=False, message=f"배치 주문 실패: {str(e)}")


@router.get("/", response_model=APIResponse[list[OrderResponse]])
async def get_orders(
    status: Optional[str] = Query(default=None, description="주문 상태 필터"),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """
    주문 이력 조회

    orders 테이블에서 최근 주문 이력을 조회합니다.
    """
    try:
        orders: list[OrderResponse] = []

        try:
            if status:
                query = text("""
                    SELECT order_id, ticker, market, side, quantity,
                           filled_qty, avg_price, status, created_at, error_message
                    FROM orders
                    WHERE status = :status
                    ORDER BY created_at DESC
                    LIMIT :limit
                """)
                result = await db.execute(query, {"status": status, "limit": limit})
            else:
                query = text("""
                    SELECT order_id, ticker, market, side, quantity,
                           filled_qty, avg_price, status, created_at, error_message
                    FROM orders
                    ORDER BY created_at DESC
                    LIMIT :limit
                """)
                result = await db.execute(query, {"limit": limit})

            rows = result.fetchall()

            for row in rows:
                orders.append(
                    OrderResponse(
                        order_id=row[0],
                        ticker=row[1],
                        market=row[2],
                        side=row[3],
                        quantity=row[4],
                        order_type="MARKET",  # orders 테이블에 order_type 미저장 → 기본값
                        status=row[7],
                        filled_price=float(row[6]) if row[6] and float(row[6]) > 0 else None,
                        filled_at=row[8] if row[7] == "FILLED" else None,
                        reason=row[9] if row[9] else None,
                    )
                )
        except Exception as db_err:
            logger.warning(f"Orders DB query failed (returning empty): {db_err}")

        return APIResponse(success=True, data=orders)
    except Exception as e:
        logger.error(f"Orders query error: {e}")
        return APIResponse(success=False, message=f"조회 실패: {str(e)}")


@router.get("/{order_id}", response_model=APIResponse[OrderResponse])
async def get_order(
    order_id: str,
    current_user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """
    단일 주문 상세 조회
    """
    try:
        query = text("""
            SELECT order_id, ticker, market, side, quantity,
                   filled_qty, avg_price, status, created_at, error_message
            FROM orders
            WHERE order_id = :order_id
        """)
        result = await db.execute(query, {"order_id": order_id})
        row = result.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail=f"주문을 찾을 수 없습니다: {order_id}")

        order = OrderResponse(
            order_id=row[0],
            ticker=row[1],
            market=row[2],
            side=row[3],
            quantity=row[4],
            order_type="MARKET",
            status=row[7],
            filled_price=float(row[6]) if row[6] and float(row[6]) > 0 else None,
            filled_at=row[8] if row[7] == "FILLED" else None,
            reason=row[9] if row[9] else None,
        )
        return APIResponse(success=True, data=order)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Order query error: {e}")
        return APIResponse(success=False, message=f"조회 실패: {str(e)}")


@router.delete("/{order_id}", response_model=APIResponse[dict])
async def cancel_order(
    order_id: str,
    current_user: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """
    주문 취소

    PENDING 또는 SUBMITTED 상태의 주문만 취소 가능합니다.
    orders 테이블에서 상태를 확인하고, CANCELLED로 갱신합니다.
    """
    try:
        # 현재 주문 상태 확인
        check_query = text("""
            SELECT status FROM orders WHERE order_id = :order_id
        """)
        result = await db.execute(check_query, {"order_id": order_id})
        row = result.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail=f"주문을 찾을 수 없습니다: {order_id}")

        current_status = row[0]
        if current_status not in ("PENDING", "SUBMITTED"):
            return APIResponse(
                success=False,
                message=f"취소 불가: 현재 상태가 {current_status}입니다. PENDING 또는 SUBMITTED만 취소 가능합니다.",
            )

        # 상태 변경
        update_query = text("""
            UPDATE orders SET status = 'CANCELLED' WHERE order_id = :order_id
        """)
        await db.execute(update_query, {"order_id": order_id})
        await db.commit()

        # 감사 로그
        audit = AuditLogger(db)
        await audit.log(
            action_type="ORDER_CANCELLED",
            module="order_executor",
            description=f"Order {order_id} cancelled by user {current_user}",
            metadata={"order_id": order_id, "previous_status": current_status},
        )

        logger.info(f"Order cancelled: {order_id}")
        return APIResponse(
            success=True,
            data={"order_id": order_id, "status": "CANCELLED"},
            message="주문이 취소되었습니다.",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Order cancel error: {e}")
        return APIResponse(success=False, message=f"취소 실패: {str(e)}")
