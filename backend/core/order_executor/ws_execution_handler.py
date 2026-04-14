"""WebSocket 체결 통보 → DB 갱신 핸들러.

KIS WebSocket 체결 통보(H0STCNI0/H0STCNI9, H0GSCNI0/H0GSCNI9) 수신 시
settlement_poller의 _update_order_status()를 호출하여 DB를 갱신한다.

폴링(poll_after_execution)과 병행하여 이중 안전망(dual safety net) 역할을 한다.
- WebSocket: 실시간 (~1초 이내), 연결 불안정 시 누락 가능
- 폴링: 30초 간격, WebSocket 누락 보완

설계 근거:
    - WebSocket 체결 통보는 KIS 서버 → 클라이언트 push 방식
    - ODER_NO(KIS 주문번호)로 DB의 orders.order_id와 매칭
    - order_id가 UUID 폴백인 경우 ticker + SUBMITTED 상태로 매칭
    - CNTG_YN=2 (체결)일 때만 FILLED/PARTIAL 상태로 갱신
    - CNTG_YN=1 (접수)은 로그만 기록, DB 갱신 없음
"""

from typing import Optional

from sqlalchemy import text

from config.constants import OrderStatus
from config.logging import logger
from core.data_collector.kis_websocket import RealtimeExecutionNotice
from core.order_executor.settlement_poller import _update_order_status
from db.database import async_session_factory


async def handle_execution_notice(notice: RealtimeExecutionNotice) -> None:
    """체결 통보 콜백 — KISRealtimeClient.on_exec_notice에 등록한다.

    Args:
        notice: 파싱된 체결 통보 데이터
    """
    # 접수/정정/취소/거부 통보는 로그만 기록
    if not notice.is_filled:
        logger.debug(f"[WSExecHandler] 접수 통보: " f"ticker={notice.ticker} order_no={notice.order_no}")
        return

    if notice.is_rejected:
        logger.warning(f"[WSExecHandler] 거부 통보: " f"ticker={notice.ticker} order_no={notice.order_no}")
        return

    # DB에서 주문번호로 해당 주문을 조회
    order_row = await _find_order_by_kis_order_no(notice.order_no, notice.ticker)
    if order_row is None:
        logger.warning(f"[WSExecHandler] 매칭 주문 없음: " f"kis_order_no={notice.order_no} ticker={notice.ticker}")
        return

    order_id = order_row["order_id"]
    current_status = order_row["status"]

    # 체결 상태 결정: 체결수량 >= 주문수량이면 FILLED, 아니면 PARTIAL
    if notice.order_qty > 0 and notice.filled_qty >= notice.order_qty:
        new_status = OrderStatus.FILLED
    elif notice.filled_qty > 0:
        new_status = OrderStatus.PARTIAL
    else:
        logger.debug(f"[WSExecHandler] 체결수량 0 — 스킵: order_no={notice.order_no}")
        return

    updated = await _update_order_status(
        order_id=order_id,
        current_status_str=current_status,
        new_status=new_status,
        filled_quantity=notice.filled_qty,
        filled_price=notice.filled_price,
    )

    if updated:
        logger.info(
            f"[WSExecHandler] DB 갱신 완료: "
            f"order_id={order_id} {current_status}→{new_status.value} "
            f"(체결 {notice.filled_qty}주 @ {notice.filled_price})"
        )


async def _find_order_by_kis_order_no(
    kis_order_no: str,
    ticker: str,
) -> Optional[dict]:
    """KIS 주문번호로 orders 테이블에서 주문을 조회한다.

    매칭 전략 (우선순위 순):
    1. order_id = kis_order_no (KIS 주문번호가 order_id로 저장된 경우)
    2. ticker + SUBMITTED 상태 (order_id가 UUID 폴백인 경우, 최신 1건)

    Returns:
        {"order_id": ..., "status": ...} 또는 None
    """
    async with async_session_factory() as session:
        # 1차: order_id로 정확 매칭
        query = text(
            """
            SELECT order_id, status
            FROM orders
            WHERE order_id = :kis_order_no
            LIMIT 1
            """
        )
        result = await session.execute(query, {"kis_order_no": kis_order_no})
        row = result.mappings().first()
        if row:
            return dict(row)

        # 2차: ticker + SUBMITTED 상태 매칭 (최신 주문)
        query_fallback = text(
            """
            SELECT order_id, status
            FROM orders
            WHERE ticker = :ticker
              AND status = :submitted_status
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        result = await session.execute(
            query_fallback,
            {
                "ticker": ticker,
                "submitted_status": OrderStatus.SUBMITTED.value,
            },
        )
        row = result.mappings().first()
        if row:
            logger.info(
                f"[WSExecHandler] ticker 폴백 매칭: " f"kis_order_no={kis_order_no} → order_id={row['order_id']}"
            )
            return dict(row)

        return None
