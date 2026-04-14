"""주문 체결 상태 폴링 모듈.

주문 제출 후 SUBMITTED 상태에서 멈추는 문제를 해결하기 위해,
KIS 체결 조회 API를 통해 주문 상태를 주기적으로 확인하고
DB를 업데이트한다.

두 가지 실행 모드:
1. poll_after_execution(): 주문 직후 비동기 태스크로 단기 폴링 (30초 간격 × 5회)
2. reconcile_all_submitted(): POST_MARKET 스케줄러에서 SUBMITTED 전량 일괄 조회

설계 근거:
    - KIS API는 주문 제출 시점에 체결 정보를 완전히 반환하지 않음
    - 모의투자(DEMO)에서는 시장가 주문이 즉시 체결되지 않을 수 있음
    - WebSocket 체결 통보와 병행하여 fallback 역할 수행
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text

from config.constants import Market, OrderStatus
from config.logging import logger
from core.data_collector.kis_client import KISClient
from core.order_executor.order_state_machine import (
    assert_order_transition,
    is_terminal_order_state,
    parse_order_status,
)
from db.database import async_session_factory

# 폴링 설정
POLL_INTERVAL_SECONDS = 30
POLL_MAX_RETRIES = 5


async def _fetch_kis_ccld_records(
    kis_client: KISClient,
    today_str: str,
    market: str,
) -> list[dict]:
    """KIS 체결 조회 API를 호출하여 오늘의 체결 내역을 가져온다.

    Args:
        kis_client: KIS API 클라이언트
        today_str: 조회 날짜 (YYYYMMDD)
        market: "KRX" 또는 해외 거래소 코드

    Returns:
        체결 내역 리스트 (KIS API output1 또는 output)
    """
    try:
        if market == Market.KRX.value or market == "KRX":
            response = await kis_client.inquire_kr_daily_ccld(
                start_date=today_str,
                end_date=today_str,
                ccld_dv="00",
            )
            return response.get("output1", [])
        else:
            response = await kis_client.inquire_us_ccld(
                start_date=today_str,
                end_date=today_str,
                ccld_dv="00",
            )
            return response.get("output", [])
    except Exception as e:
        logger.error(f"[SettlementPoller] KIS 체결 조회 실패 ({market}): {e}")
        return []


def _match_ccld_record(
    records: list[dict],
    order_id: str,
    ticker: str,
    market: str,
) -> Optional[dict]:
    """KIS 체결 내역에서 주문 ID 또는 종목+수량으로 매칭되는 레코드를 찾는다.

    KIS 응답 필드 (국내):
        - ODNO: 주문번호
        - PDNO: 종목코드
        - TOT_CCLD_QTY: 총 체결 수량
        - TOT_CCLD_AMT: 총 체결 금액
        - AVG_PRVS: 체결 평균가
        - ORD_QTY: 주문 수량

    KIS 응답 필드 (해외):
        - ODNO: 주문번호
        - PDNO: 종목코드
        - FT_CCLD_QTY: 체결 수량
        - FT_CCLD_UNPR3: 체결 단가
        - FT_ORD_QTY: 주문 수량
    """
    for rec in records:
        kis_order_no = rec.get("ODNO", "")
        kis_ticker = rec.get("PDNO", "")

        # 1차: order_id가 KIS 주문번호와 일치
        if order_id and kis_order_no and order_id == kis_order_no:
            return rec

        # 2차: 종목코드 매칭 (order_id가 UUID 폴백인 경우)
        if kis_ticker == ticker:
            return rec

    return None


def _parse_ccld_status(
    record: dict,
    market: str,
) -> tuple[OrderStatus, int, float]:
    """KIS 체결 내역 레코드에서 상태, 체결수량, 체결가격을 추출한다.

    Returns:
        (new_status, filled_quantity, filled_price)
    """
    if market == Market.KRX.value or market == "KRX":
        ord_qty = int(record.get("ORD_QTY", "0") or "0")
        ccld_qty = int(record.get("TOT_CCLD_QTY", "0") or "0")
        avg_price = float(record.get("AVG_PRVS", "0") or "0")
    else:
        ord_qty = int(record.get("FT_ORD_QTY", "0") or "0")
        ccld_qty = int(record.get("FT_CCLD_QTY", "0") or "0")
        avg_price = float(record.get("FT_CCLD_UNPR3", "0") or "0")

    if ccld_qty <= 0:
        return OrderStatus.SUBMITTED, 0, 0.0
    elif ccld_qty >= ord_qty:
        return OrderStatus.FILLED, ccld_qty, avg_price
    else:
        return OrderStatus.PARTIAL, ccld_qty, avg_price


async def _update_order_status(
    order_id: str,
    current_status_str: str,
    new_status: OrderStatus,
    filled_quantity: int,
    filled_price: float,
) -> bool:
    """DB에서 주문 상태를 업데이트한다.

    상태 전이 규칙을 준수하며, 종결 상태이거나 동일 상태이면 스킵한다.

    Returns:
        업데이트 수행 여부
    """
    current_status = parse_order_status(current_status_str)

    if is_terminal_order_state(current_status):
        return False

    if current_status == new_status and new_status == OrderStatus.SUBMITTED:
        return False

    # 상태 전이 규칙 검증
    assert_order_transition(current_status, new_status, order_id=order_id)

    filled_at = datetime.now(timezone.utc) if new_status == OrderStatus.FILLED else None

    async with async_session_factory() as session:
        update_query = text(
            """
            UPDATE orders
            SET status = :new_status,
                filled_quantity = :filled_quantity,
                filled_price = :filled_price,
                filled_at = COALESCE(:filled_at, filled_at)
            WHERE order_id = :order_id
              AND status = :current_status
            """
        )
        result = await session.execute(
            update_query,
            {
                "new_status": new_status.value,
                "filled_quantity": filled_quantity,
                "filled_price": filled_price,
                "filled_at": filled_at,
                "order_id": order_id,
                "current_status": current_status_str,
            },
        )
        await session.commit()

        if result.rowcount > 0:
            logger.info(
                f"[SettlementPoller] 주문 상태 갱신: "
                f"{order_id} {current_status_str}→{new_status.value} "
                f"(체결 {filled_quantity}주 @ {filled_price})"
            )
            return True
        return False


async def poll_after_execution(
    kis_client: KISClient,
    order_id: str,
    ticker: str,
    market: str,
    interval: int = POLL_INTERVAL_SECONDS,
    max_retries: int = POLL_MAX_RETRIES,
) -> None:
    """주문 실행 직후 단기 폴링으로 체결 상태를 확인한다.

    asyncio.create_task()로 호출되어 백그라운드에서 동작한다.
    체결이 확인되거나 최대 재시도 횟수에 도달하면 종료한다.

    Args:
        kis_client: KIS API 클라이언트
        order_id: DB 주문 ID
        ticker: 종목코드
        market: 거래소 (KRX, NYSE, NASDAQ 등)
        interval: 폴링 간격 (초)
        max_retries: 최대 폴링 횟수
    """
    kst = timezone(timedelta(hours=9))
    today_str = datetime.now(kst).strftime("%Y%m%d")

    for attempt in range(1, max_retries + 1):
        await asyncio.sleep(interval)

        try:
            # 먼저 DB에서 현재 상태 확인 (WebSocket에서 이미 갱신되었을 수 있음)
            async with async_session_factory() as session:
                check_query = text("SELECT status FROM orders WHERE order_id = :order_id")
                result = await session.execute(check_query, {"order_id": order_id})
                row = result.fetchone()
                if not row:
                    logger.warning(f"[SettlementPoller] 주문 {order_id} DB에서 미발견, 폴링 중단")
                    return
                current_status = row[0]

            if is_terminal_order_state(parse_order_status(current_status)):
                logger.info(f"[SettlementPoller] 주문 {order_id} 이미 종결({current_status}), " f"폴링 중단")
                return

            records = await _fetch_kis_ccld_records(kis_client, today_str, market)
            matched = _match_ccld_record(records, order_id, ticker, market)

            if matched:
                new_status, filled_qty, filled_price = _parse_ccld_status(matched, market)
                if new_status != OrderStatus.SUBMITTED:
                    await _update_order_status(
                        order_id,
                        current_status,
                        new_status,
                        filled_qty,
                        filled_price,
                    )
                    logger.info(
                        f"[SettlementPoller] 체결 확인 완료: "
                        f"{order_id} → {new_status.value} "
                        f"(attempt {attempt}/{max_retries})"
                    )
                    return

            logger.debug(f"[SettlementPoller] 폴링 {attempt}/{max_retries}: " f"{order_id} ({ticker}) 아직 미체결")

        except Exception as e:
            logger.error(f"[SettlementPoller] 폴링 오류 (attempt {attempt}): " f"{order_id} — {e}")

    logger.info(f"[SettlementPoller] 최대 폴링 횟수 도달, POST_MARKET에서 재확인 예정: " f"{order_id}")


async def reconcile_all_submitted(
    kis_client: Optional[KISClient] = None,
) -> dict:
    """SUBMITTED 상태인 모든 주문을 일괄 조회하여 상태를 갱신한다.

    POST_MARKET 스케줄러에서 호출된다.

    Returns:
        {"checked": int, "updated": int, "errors": int}
    """
    if kis_client is None:
        kis_client = KISClient()

    kst = timezone(timedelta(hours=9))
    today_str = datetime.now(kst).strftime("%Y%m%d")

    stats = {"checked": 0, "updated": 0, "errors": 0}

    # SUBMITTED 주문 조회
    async with async_session_factory() as session:
        query = text(
            """
            SELECT order_id, ticker, market, status
            FROM orders
            WHERE status = 'SUBMITTED'
              AND DATE(created_at) = CURRENT_DATE
            ORDER BY created_at
            """
        )
        result = await session.execute(query)
        submitted_orders = result.fetchall()

    if not submitted_orders:
        logger.info("[SettlementPoller] reconcile: SUBMITTED 주문 없음")
        return stats

    logger.info(f"[SettlementPoller] reconcile 시작: SUBMITTED {len(submitted_orders)}건")

    # 마켓별로 KIS API를 한 번씩만 호출 (rate limit 절약)
    market_records: dict[str, list[dict]] = {}

    for order_id, ticker, market, status in submitted_orders:
        stats["checked"] += 1

        try:
            market_key = "KRX" if market == "KRX" else "US"
            if market_key not in market_records:
                market_records[market_key] = await _fetch_kis_ccld_records(
                    kis_client,
                    today_str,
                    market_key,
                )

            records = market_records[market_key]
            matched = _match_ccld_record(records, order_id, ticker, market)

            if matched:
                new_status, filled_qty, filled_price = _parse_ccld_status(
                    matched,
                    market,
                )
                if new_status != OrderStatus.SUBMITTED:
                    updated = await _update_order_status(
                        order_id,
                        status,
                        new_status,
                        filled_qty,
                        filled_price,
                    )
                    if updated:
                        stats["updated"] += 1
            else:
                logger.debug(f"[SettlementPoller] reconcile: {order_id} ({ticker}) " f"KIS 응답에서 매칭 미발견")

        except Exception as e:
            logger.error(f"[SettlementPoller] reconcile 오류: {order_id} — {e}")
            stats["errors"] += 1

    logger.info(
        f"[SettlementPoller] reconcile 완료: "
        f"checked={stats['checked']}, updated={stats['updated']}, "
        f"errors={stats['errors']}"
    )
    return stats
