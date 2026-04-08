"""
주문 API 라우터

주문 생성, 조회, 취소, 배치 실행 엔드포인트를 제공합니다.
OrderExecutor 엔진과 직접 연동하여 실제 주문을 처리합니다.
"""

from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from api.middleware.rate_limiter import RATE_ORDER, limiter
from api.middleware.rbac import require_operator, require_viewer
from api.schemas.common import APIResponse
from api.schemas.orders import (
    BatchOrderRequest,
    BatchOrderResponse,
    OrderCreateRequest,
    OrderResponse,
)
from config.constants import Market, OrderSide, OrderStatus, OrderType
from config.logging import logger
from core.idempotency import (
    IdempotencyConflict,
    IdempotencyInProgress,
    IdempotencyStoreUnavailable,
    compute_request_fingerprint,
    get_order_idempotency_store,
)
from core.idempotency.order_idempotency import record_hit
from core.order_executor.executor import OrderExecutor, OrderRequest, OrderResult
from db.database import get_db_session
from db.repositories.audit_log import AuditLogger

router = APIRouter()


# ── Idempotency helpers ──────────────────────────────────────────────
_IDEMPOTENCY_KEY_MAX_LEN = 128


def _validate_idempotency_key(value: Optional[str]) -> str:
    """Idempotency-Key 헤더 검증. 누락/공백/과도 길이 → 400."""
    if not value or not value.strip():
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "IDEMPOTENCY_KEY_REQUIRED",
                "message": "Idempotency-Key 헤더가 필요합니다.",
            },
        )
    key = value.strip()
    if len(key) > _IDEMPOTENCY_KEY_MAX_LEN:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "IDEMPOTENCY_KEY_TOO_LONG",
                "message": f"Idempotency-Key 최대 길이 {_IDEMPOTENCY_KEY_MAX_LEN}자를 초과했습니다.",
            },
        )
    return key


def _raise_idempotency_http(exc: Exception) -> None:
    """idempotency 예외 → HTTPException 매핑 (fail-closed)."""
    if isinstance(exc, IdempotencyConflict):
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "IDEMPOTENCY_CONFLICT",
                "message": "동일한 Idempotency-Key 로 서로 다른 요청이 감지되었습니다.",
            },
        )
    if isinstance(exc, IdempotencyInProgress):
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "IDEMPOTENCY_IN_PROGRESS",
                "message": "동일 Idempotency-Key 의 이전 요청이 아직 처리 중입니다.",
            },
        )
    if isinstance(exc, IdempotencyStoreUnavailable):
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "IDEMPOTENCY_STORE_UNAVAILABLE",
                "message": "주문 멱등성 저장소가 일시적으로 사용 불가합니다.",
            },
        )


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
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    current_user=Depends(require_operator),
    db: AsyncSession = Depends(get_db_session),
):
    """
    단일 주문 생성 (P0-3a: Idempotency-Key 필수).

    시장가, 지정가, TWAP, VWAP 주문을 생성하고 OrderExecutor를 통해 실행합니다.
    `Idempotency-Key` 헤더 미첨부 시 400, 동일 키 재시도(동일 body)는 기존
    응답을 replay 하며, 동일 키 + 다른 body 는 422, 동시 실행 중은 409,
    저장소 장애 시 503 을 반환합니다 (fail-closed).
    """
    key = _validate_idempotency_key(idempotency_key)
    route = "POST /api/orders"
    store = get_order_idempotency_store()
    payload: dict[str, Any] = order_body.model_dump(mode="json")
    fingerprint = compute_request_fingerprint(payload)
    user_id = str(current_user.id)

    # 1) Replay 경로: 이미 저장된 결과가 있으면 그대로 반환.
    try:
        existing = store.lookup(user_id, route, key)
    except IdempotencyStoreUnavailable as e:
        _raise_idempotency_http(e)

    if existing is not None:
        if existing.fingerprint != fingerprint:
            _raise_idempotency_http(IdempotencyConflict(key))
        record_hit()
        return APIResponse(**existing.body)

    # 2) Claim: 동시 요청 직렬화.
    try:
        store.try_claim(user_id, route, key, fingerprint)
    except (IdempotencyConflict, IdempotencyInProgress, IdempotencyStoreUnavailable) as e:
        _raise_idempotency_http(e)

    # 3) 실행
    try:
        logger.info(
            f"Order created: {order_body.side} {order_body.ticker} "
            f"x{order_body.quantity} ({order_body.order_type}) idem={key}"
        )

        order_req = OrderRequest(
            ticker=order_body.ticker,
            market=Market(order_body.market),
            side=OrderSide(order_body.side),
            quantity=order_body.quantity,
            order_type=OrderType(order_body.order_type),
            limit_price=order_body.limit_price,
            reason=order_body.reason or "",
        )

        executor = OrderExecutor()
        result = await executor.execute_order(order_req)

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
        api_body = APIResponse(success=True, data=response, message="주문이 실행되었습니다.")
    except Exception as e:
        # 실패 시 claim 해제 → 클라이언트가 동일 키로 재시도 가능.
        try:
            store.release_claim(user_id, route, key)
        except IdempotencyStoreUnavailable:
            # release 실패는 치명적이지 않음 (claim TTL 30s 로 자동 만료).
            pass
        logger.error(f"Order creation error: {e}")
        return APIResponse(success=False, message=f"주문 생성 실패: {str(e)}")

    # 4) 결과 저장 (성공 응답만 캐시).
    try:
        store.store_result(
            user_id,
            route,
            key,
            fingerprint,
            status_code=200,
            body=api_body.model_dump(mode="json"),
        )
    except IdempotencyStoreUnavailable:
        # 저장 실패해도 주문은 이미 실행됨 — 응답은 반환하되 로그로 경보.
        logger.error("Order idempotency store_result failed (order already executed) key=%s", key)

    return api_body


@router.post("/batch", response_model=APIResponse[BatchOrderResponse])
@limiter.limit(RATE_ORDER)
async def create_batch_orders(
    request: Request,
    batch_body: BatchOrderRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    current_user=Depends(require_operator),
    db: AsyncSession = Depends(get_db_session),
):
    """
    배치 주문 실행 (P0-3a: Idempotency-Key 필수).

    복수 주문을 동시에 생성·실행합니다. SELL 주문이 우선 처리됩니다.
    idempotency 프로토콜은 단일 주문과 동일합니다.
    """
    key = _validate_idempotency_key(idempotency_key)
    route = "POST /api/orders/batch"
    store = get_order_idempotency_store()
    payload: dict[str, Any] = batch_body.model_dump(mode="json")
    fingerprint = compute_request_fingerprint(payload)
    user_id = str(current_user.id)

    try:
        existing = store.lookup(user_id, route, key)
    except IdempotencyStoreUnavailable as e:
        _raise_idempotency_http(e)

    if existing is not None:
        if existing.fingerprint != fingerprint:
            _raise_idempotency_http(IdempotencyConflict(key))
        record_hit()
        return APIResponse(**existing.body)

    try:
        store.try_claim(user_id, route, key, fingerprint)
    except (IdempotencyConflict, IdempotencyInProgress, IdempotencyStoreUnavailable) as e:
        _raise_idempotency_http(e)

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
        api_body = APIResponse(
            success=True,
            data=batch_response,
            message=f"{len(results)}건 배치 주문이 실행되었습니다.",
        )
    except Exception as e:
        try:
            store.release_claim(user_id, route, key)
        except IdempotencyStoreUnavailable:
            pass
        logger.error(f"Batch order error: {e}")
        return APIResponse(success=False, message=f"배치 주문 실패: {str(e)}")

    try:
        store.store_result(
            user_id,
            route,
            key,
            fingerprint,
            status_code=200,
            body=api_body.model_dump(mode="json"),
        )
    except IdempotencyStoreUnavailable:
        logger.error("Batch order idempotency store_result failed (orders executed) key=%s", key)

    return api_body


@router.get("/", response_model=APIResponse[list[OrderResponse]])
async def get_orders(
    status: Optional[str] = Query(default=None, description="주문 상태 필터"),
    limit: int = Query(default=50, ge=1, le=200),
    current_user=Depends(require_viewer),
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
                query = text(
                    """
                    SELECT order_id, ticker, market, side, quantity,
                           filled_qty, avg_price, status, created_at, error_message
                    FROM orders
                    WHERE status = :status
                    ORDER BY created_at DESC
                    LIMIT :limit
                """
                )
                result = await db.execute(query, {"status": status, "limit": limit})
            else:
                query = text(
                    """
                    SELECT order_id, ticker, market, side, quantity,
                           filled_qty, avg_price, status, created_at, error_message
                    FROM orders
                    ORDER BY created_at DESC
                    LIMIT :limit
                """
                )
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
    current_user=Depends(require_viewer),
    db: AsyncSession = Depends(get_db_session),
):
    """
    단일 주문 상세 조회
    """
    try:
        query = text(
            """
            SELECT order_id, ticker, market, side, quantity,
                   filled_qty, avg_price, status, created_at, error_message
            FROM orders
            WHERE order_id = :order_id
        """
        )
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
    current_user=Depends(require_operator),
    db: AsyncSession = Depends(get_db_session),
):
    """
    주문 취소

    PENDING 또는 SUBMITTED 상태의 주문만 취소 가능합니다.
    orders 테이블에서 상태를 확인하고, CANCELLED로 갱신합니다.
    """
    try:
        # 현재 주문 상태 확인
        check_query = text(
            """
            SELECT status FROM orders WHERE order_id = :order_id
        """
        )
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
        update_query = text(
            """
            UPDATE orders SET status = 'CANCELLED' WHERE order_id = :order_id
        """
        )
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
