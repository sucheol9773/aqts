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

from api.errors import ErrorCode, raise_api_error
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
from core.order_executor.order_state_machine import (
    InvalidOrderTransition,
    assert_can_cancel,
    parse_order_status,
)
from core.order_executor.quote_provider_kis import get_kis_quote_provider
from core.utils.timezone import to_kst
from db.database import get_db_session
from db.repositories.audit_log import AuditLogger, AuditWriteFailure

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


def _raise_audit_unavailable() -> None:
    """P0-4: 감사 DB 장애 → 503 AUDIT_UNAVAILABLE + Retry-After."""
    raise HTTPException(
        status_code=503,
        headers={"Retry-After": "30"},
        detail={
            "success": False,
            "error_code": "AUDIT_UNAVAILABLE",
            "message": "감사 시스템 일시 장애로 주문이 차단되었습니다",
            "retry_after_seconds": 30,
        },
    )


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
            headers={"Retry-After": "30"},
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
        filled_at=to_kst(result.executed_at) if result.status == OrderStatus.FILLED else None,
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

    # 3) P0-4 pre-flight audit: 감사 DB 가 살아있음을 주문 체결 전에 증명.
    #    여기서 실패하면 executor 는 아예 호출되지 않아 "주문 미체결 + 503".
    audit = AuditLogger(db)
    try:
        await audit.log_strict(
            action_type="ORDER_REQUESTED",
            module="order_executor",
            description=(
                f"Order REQUESTED: {order_body.side} {order_body.ticker} "
                f"x{order_body.quantity} ({order_body.order_type}) idem={key}"
            ),
            metadata={
                "idempotency_key": key,
                "route": route,
                "user_id": user_id,
                "request": payload,
            },
        )
    except AuditWriteFailure:
        try:
            store.release_claim(user_id, route, key)
        except IdempotencyStoreUnavailable:
            pass
        _raise_audit_unavailable()

    # 4) 실행
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

        # P1-정합성 §7.3: live 경로는 quote_provider 가 반드시 주입되어야
        # OrderExecutor 가 시세 가드를 활성화한다 (fail-closed).
        executor = OrderExecutor(quote_provider=get_kis_quote_provider())
        result = await executor.execute_order(order_req)

        # 5) P0-4 post-audit: 실행 결과를 strict 로 기록.
        #    여기서 실패하면 주문은 이미 브로커에 나갔으므로 수동 reconcile
        #    필요 — logger.critical + 503 + release_claim.
        await audit.log_strict(
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
    except AuditWriteFailure:
        logger.critical(
            "Post-exec audit write failed — order may be executed without audit trail. "
            f"Manual reconciliation required. idem={key}"
        )
        try:
            store.release_claim(user_id, route, key)
        except IdempotencyStoreUnavailable:
            pass
        _raise_audit_unavailable()
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
        logger.error(f"Order idempotency store_result failed (order already executed) key={key}")

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

    # P0-4 pre-flight audit — 배치 실행 전 감사 DB 헬스 증명.
    audit = AuditLogger(db)
    try:
        await audit.log_strict(
            action_type="BATCH_ORDER_REQUESTED",
            module="order_executor",
            description=(f"Batch order REQUESTED: {len(batch_body.orders)}건 idem={key}"),
            metadata={
                "idempotency_key": key,
                "route": route,
                "user_id": user_id,
                "request": payload,
            },
        )
    except AuditWriteFailure:
        try:
            store.release_claim(user_id, route, key)
        except IdempotencyStoreUnavailable:
            pass
        _raise_audit_unavailable()

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
        # P1-정합성 §7.3: live 경로는 quote_provider 가 반드시 주입되어야
        # OrderExecutor 가 시세 가드를 활성화한다 (fail-closed).
        executor = OrderExecutor(quote_provider=get_kis_quote_provider())
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

        # P0-4 post-audit (strict). 실패 시 브로커 이미 실행됨 → critical+503.
        await audit.log_strict(
            action_type="BATCH_ORDER_CREATED",
            module="order_executor",
            description=(f"Batch order: {len(results)}건 " f"(성공: {success_count}, 실패: {fail_count})"),
            metadata={
                "total": len(results),
                "success_count": success_count,
                "fail_count": fail_count,
                "idempotency_key": key,
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
    except AuditWriteFailure:
        logger.critical(
            "Post-exec batch audit write failed — orders may be executed without audit trail. "
            f"Manual reconciliation required. idem={key}"
        )
        try:
            store.release_claim(user_id, route, key)
        except IdempotencyStoreUnavailable:
            pass
        _raise_audit_unavailable()
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
        logger.error(f"Batch order idempotency store_result failed (orders executed) key={key}")

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
                           filled_quantity, filled_price, status, created_at, error_message
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
                           filled_quantity, filled_price, status, created_at, error_message
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
                        filled_at=to_kst(row[8]) if row[7] == "FILLED" else None,
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
                   filled_quantity, filled_price, status, created_at, error_message
            FROM orders
            WHERE order_id = :order_id
        """
        )
        result = await db.execute(query, {"order_id": order_id})
        row = result.fetchone()

        if not row:
            raise_api_error(
                404,
                ErrorCode.ORDER_NOT_FOUND,
                "주문을 찾을 수 없습니다.",
                order_id=order_id,
            )

        order = OrderResponse(
            order_id=row[0],
            ticker=row[1],
            market=row[2],
            side=row[3],
            quantity=row[4],
            order_type="MARKET",
            status=row[7],
            filled_price=float(row[6]) if row[6] and float(row[6]) > 0 else None,
            filled_at=to_kst(row[8]) if row[7] == "FILLED" else None,
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
            raise_api_error(
                404,
                ErrorCode.ORDER_NOT_FOUND,
                "주문을 찾을 수 없습니다.",
                order_id=order_id,
            )

        current_status_raw = row[0]
        # DB 무결성 가정: status 컬럼은 OrderStatus enum 범위여야 한다. 알 수
        # 없는 값이면 fail-closed 503 (토큰 저장소/감사 저장소와 동일 정책).
        try:
            current_status = parse_order_status(current_status_raw)
        except ValueError:
            logger.error(f"Order {order_id} has unknown status value in DB: {current_status_raw!r}")
            raise_api_error(
                503,
                ErrorCode.ORDER_STORE_UNAVAILABLE,
                "주문 저장소에 알 수 없는 상태값이 기록되어 있습니다.",
                headers={"Retry-After": "30"},
                order_id=order_id,
            )

        # P1-정합성: 상태 전이 유효성 검증 (OrderStateMachine 단일 진실원천).
        # 종결 상태(FILLED/CANCELLED/FAILED) 또는 PENDING/SUBMITTED/PARTIAL 외
        # 의 상태에서 취소 시도 → 409 + INVALID_ORDER_TRANSITION + Prometheus
        # counter 증가 (알람 임계 0).
        try:
            assert_can_cancel(current_status, order_id=order_id)
        except InvalidOrderTransition as exc:
            raise_api_error(
                409,
                ErrorCode.INVALID_ORDER_TRANSITION,
                "현재 상태에서는 주문을 취소할 수 없습니다.",
                order_id=order_id,
                current_status=exc.from_state.value if exc.from_state else None,
                target_status=exc.to_state.value,
            )

        # 상태 변경
        update_query = text(
            """
            UPDATE orders SET status = 'CANCELLED' WHERE order_id = :order_id
        """
        )
        await db.execute(update_query, {"order_id": order_id})
        await db.commit()

        # P0-4: 취소도 금전적 쓰기 경로이므로 strict.
        audit = AuditLogger(db)
        try:
            await audit.log_strict(
                action_type="ORDER_CANCELLED",
                module="order_executor",
                description=f"Order {order_id} cancelled by user {current_user.username}",
                metadata={"order_id": order_id, "previous_status": current_status.value},
            )
        except AuditWriteFailure:
            _raise_audit_unavailable()

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
