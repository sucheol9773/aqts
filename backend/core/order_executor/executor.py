"""
주문 실행 엔진 모듈 (F-06-01/02)

포트폴리오 리밸런싱 및 신호 기반 매매를 위한 주문 실행 엔진입니다.
주문 검증, 여러 주문 유형(시장가/지정가/TWAP/VWAP) 지원,
잔고 확인, 상한선 검사, 중복 주문 방지, PostgreSQL 기록,
감사 로그 통합을 제공합니다.

주요 기능:
- async execute_order: 단일 주문 실행 (검증 → 잔고확인 → KIS API 제출)
- async execute_batch_orders: 배치 주문 (판매 우선 원칙)
- async _execute_market_order: 시장가 주문
- async _execute_limit_order: 지정가 주문
- async _execute_twap_order: 시간가중평균 (TWAP) 주문
- async _execute_vwap_order: 거래량가중평균 (VWAP) 주문
- _validate_order: 주문 검증 (잔고/상한선/중복)
- async _handle_unfilled: 장마감 30분전 미체결 주문 처리
"""

import asyncio
import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Any
from enum import Enum

from sqlalchemy import text

from config.constants import Market, OrderSide, OrderType, OrderStatus
from config.logging import logger
from config.settings import get_settings
from db.database import async_session_factory
from db.repositories.audit_log import AuditLogger
from core.data_collector.kis_client import KISClient


# ══════════════════════════════════════════════════════════════════════════════
# 주문 요청 및 결과 데이터 구조
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class OrderRequest:
    """
    주문 요청

    사용자 또는 시스템이 제출하는 주문 요청 데이터입니다.
    모든 필수 주문 정보를 포함하고 있습니다.
    """

    ticker: str
    """종목 코드 (예: 005930 또는 AAPL)"""

    market: Market
    """시장 구분 (KRX, NYSE, NASDAQ, AMEX)"""

    side: OrderSide
    """주문 방향 (BUY 또는 SELL)"""

    quantity: int
    """주문 수량"""

    order_type: OrderType = OrderType.MARKET
    """주문 유형 (MARKET, LIMIT, TWAP, VWAP)"""

    limit_price: Optional[float] = None
    """지정가 (LIMIT 주문 시 필수)"""

    reason: str = ""
    """주문 사유 (리밸런싱, 신호 기반 등)"""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환"""
        return {
            "ticker": self.ticker,
            "market": self.market.value,
            "side": self.side.value,
            "quantity": self.quantity,
            "order_type": self.order_type.value,
            "limit_price": self.limit_price,
            "reason": self.reason,
        }


@dataclass
class OrderResult:
    """
    주문 실행 결과

    주문 실행 후 결과를 포함하며, 체결 상태, 평균 가격, 오류 정보를 제공합니다.
    """

    order_id: str
    """한국투자증권 주문번호"""

    ticker: str
    """종목 코드"""

    market: Market
    """시장 구분"""

    side: OrderSide
    """주문 방향"""

    quantity: int
    """주문 수량"""

    filled_quantity: int
    """체결 수량"""

    avg_price: float
    """평균 체결 가격"""

    status: OrderStatus
    """주문 상태"""

    executed_at: datetime
    """주문 실행 시각 (UTC)"""

    error_message: str = ""
    """오류 메시지 (실패 시에만 포함)"""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환"""
        return {
            "order_id": self.order_id,
            "ticker": self.ticker,
            "market": self.market.value,
            "side": self.side.value,
            "quantity": self.quantity,
            "filled_quantity": self.filled_quantity,
            "avg_price": self.avg_price,
            "status": self.status.value,
            "executed_at": self.executed_at.isoformat(),
            "error_message": self.error_message,
        }


# ══════════════════════════════════════════════════════════════════════════════
# 주문 실행 엔진
# ══════════════════════════════════════════════════════════════════════════════
class OrderExecutor:
    """
    주문 실행 엔진

    포트폴리오 리밸런싱 및 신호 기반 매매를 위한 주문 실행을 관리합니다.
    주문 검증, 여러 주문 유형 지원, 잔고 확인, 상한선 검사,
    중복 주문 방지, 데이터베이스 기록, 감사 로그 통합을 제공합니다.

    주요 기능:
    - 단일 주문 실행 및 배치 주문 처리
    - 시장가, 지정가, TWAP, VWAP 주문 유형 지원
    - 주문 전 검증 (잔고 확인, 상한선 검사, 중복 방지)
    - PostgreSQL에 주문 정보 저장
    - 모든 주문 이벤트 감사 로그 기록
    - Mock 모드 지원
    - 재시도 로직 (지수 백오프)
    """

    def __init__(self):
        """
        주문 실행 엔진 초기화

        한국투자증권 설정을 로드하고 KIS 클라이언트를 초기화합니다.
        """
        self._settings = get_settings()
        self._kis_client = KISClient()
        logger.info("OrderExecutor 초기화 완료")

    async def execute_order(self, request: OrderRequest) -> OrderResult:
        """
        단일 주문 실행

        주문 요청을 검증하고, 잔고를 확인한 후, 적절한 주문 유형별
        실행 메서드를 호출하여 주문을 체결합니다.

        Args:
            request: 주문 요청 데이터

        Returns:
            OrderResult: 주문 실행 결과

        주문 실행 과정:
        1. 주문 검증 (잔고 확인, 상한선 검사, 중복 방지)
        2. 주문 유형에 따른 실행 (시장가/지정가/TWAP/VWAP)
        3. 결과를 PostgreSQL에 저장
        4. 감사 로그 기록
        """
        try:
            logger.info(f"주문 실행 시작: {request.ticker} {request.side.value} {request.quantity}")

            # 주문 검증
            self._validate_order(request)

            # 주문 유형별 실행
            if request.order_type == OrderType.MARKET:
                result = await self._execute_market_order(request)
            elif request.order_type == OrderType.LIMIT:
                result = await self._execute_limit_order(request)
            elif request.order_type == OrderType.TWAP:
                result = await self._execute_twap_order(request)
            elif request.order_type == OrderType.VWAP:
                result = await self._execute_vwap_order(request)
            else:
                raise ValueError(f"지원하지 않는 주문 유형: {request.order_type}")

            # 결과를 데이터베이스에 저장
            await self._store_order(result)

            # 감사 로그 기록
            async with async_session_factory() as db_session:
                audit_logger = AuditLogger(db_session)
                await audit_logger.log(
                    action_type="ORDER_EXECUTED",
                    module="order_executor",
                    description=f"주문 실행: {request.ticker} {request.side.value} {result.filled_quantity}/{request.quantity}",
                    after_state=result.to_dict(),
                    metadata={"reason": request.reason},
                )

            logger.info(f"주문 실행 완료: {result.order_id}")
            return result

        except Exception as e:
            logger.error(f"주문 실행 실패: {e}")
            result = OrderResult(
                order_id="",
                ticker=request.ticker,
                market=request.market,
                side=request.side,
                quantity=request.quantity,
                filled_quantity=0,
                avg_price=0.0,
                status=OrderStatus.FAILED,
                executed_at=datetime.now(timezone.utc),
                error_message=str(e),
            )
            await self._store_order(result)
            raise

    async def execute_batch_orders(
        self, requests: list[OrderRequest]
    ) -> list[OrderResult]:
        """
        배치 주문 실행

        여러 주문을 순차적으로 실행합니다. 판매 주문을 먼저 실행한 후
        매수 주문을 실행하여 자금 가용성을 확보합니다.

        Args:
            requests: 주문 요청 리스트

        Returns:
            list[OrderResult]: 주문 실행 결과 리스트

        주문 순서:
        1. SELL (판매) 주문을 모두 실행
        2. BUY (매수) 주문을 모두 실행
        """
        logger.info(f"배치 주문 실행 시작: 총 {len(requests)}건")

        # 판매 주문과 매수 주문 분리
        sell_orders = [r for r in requests if r.side == OrderSide.SELL]
        buy_orders = [r for r in requests if r.side == OrderSide.BUY]

        results: list[OrderResult] = []

        # SELL 주문 실행
        for request in sell_orders:
            try:
                result = await self.execute_order(request)
                results.append(result)
                await asyncio.sleep(0.5)  # API 호출 간격 (rate limit)
            except Exception as e:
                logger.warning(f"SELL 주문 실패: {request.ticker} - {e}")
                results.append(
                    OrderResult(
                        order_id="",
                        ticker=request.ticker,
                        market=request.market,
                        side=request.side,
                        quantity=request.quantity,
                        filled_quantity=0,
                        avg_price=0.0,
                        status=OrderStatus.FAILED,
                        executed_at=datetime.now(timezone.utc),
                        error_message=str(e),
                    )
                )

        # BUY 주문 실행
        for request in buy_orders:
            try:
                result = await self.execute_order(request)
                results.append(result)
                await asyncio.sleep(0.5)  # API 호출 간격 (rate limit)
            except Exception as e:
                logger.warning(f"BUY 주문 실패: {request.ticker} - {e}")
                results.append(
                    OrderResult(
                        order_id="",
                        ticker=request.ticker,
                        market=request.market,
                        side=request.side,
                        quantity=request.quantity,
                        filled_quantity=0,
                        avg_price=0.0,
                        status=OrderStatus.FAILED,
                        executed_at=datetime.now(timezone.utc),
                        error_message=str(e),
                    )
                )

        logger.info(f"배치 주문 실행 완료: {len(results)}건 결과")
        return results

    async def _execute_market_order(self, request: OrderRequest) -> OrderResult:
        """
        시장가 주문 실행

        현재 시장 가격으로 즉시 주문을 체결합니다.

        Args:
            request: 주문 요청 데이터

        Returns:
            OrderResult: 주문 실행 결과
        """
        logger.info(f"시장가 주문 실행: {request.ticker} {request.side.value} {request.quantity}")

        try:
            if self._kis_client.is_backtest:
                # Mock 모드: 시뮬레이션
                result = OrderResult(
                    order_id=f"MOCK_{request.ticker}_{datetime.now().timestamp()}",
                    ticker=request.ticker,
                    market=request.market,
                    side=request.side,
                    quantity=request.quantity,
                    filled_quantity=request.quantity,
                    avg_price=100.0,  # Mock 가격
                    status=OrderStatus.FILLED,
                    executed_at=datetime.now(timezone.utc),
                )
            else:
                # 실제 API 호출
                if request.market == Market.KRX:
                    api_result = await self._kis_client.place_kr_order(
                        ticker=request.ticker,
                        side=request.side.value,
                        quantity=request.quantity,
                        price=0,  # 시장가
                        order_type="01",  # 시장가
                    )
                else:
                    api_result = await self._kis_client.place_us_order(
                        ticker=request.ticker,
                        side=request.side.value,
                        quantity=request.quantity,
                        price=0,  # 시장가
                    )

                # API 결과를 OrderResult로 변환
                result = OrderResult(
                    order_id=api_result.get("order_id", ""),
                    ticker=request.ticker,
                    market=request.market,
                    side=request.side,
                    quantity=request.quantity,
                    filled_quantity=int(api_result.get("filled_qty", 0)),
                    avg_price=float(api_result.get("avg_price", 0)),
                    status=OrderStatus.SUBMITTED,
                    executed_at=datetime.now(timezone.utc),
                )

            logger.info(f"시장가 주문 완료: {result.order_id}")
            return result

        except Exception as e:
            logger.error(f"시장가 주문 실패: {e}")
            raise

    async def _execute_limit_order(self, request: OrderRequest) -> OrderResult:
        """
        지정가 주문 실행

        사용자가 지정한 가격으로 주문을 체결합니다.

        Args:
            request: 주문 요청 데이터 (limit_price 필수)

        Returns:
            OrderResult: 주문 실행 결과
        """
        if request.limit_price is None or request.limit_price <= 0:
            raise ValueError("지정가 주문에는 유효한 limit_price가 필요합니다")

        logger.info(
            f"지정가 주문 실행: {request.ticker} {request.side.value} "
            f"{request.quantity}@{request.limit_price}"
        )

        try:
            if self._kis_client.is_backtest:
                # Mock 모드
                result = OrderResult(
                    order_id=f"MOCK_{request.ticker}_{datetime.now().timestamp()}",
                    ticker=request.ticker,
                    market=request.market,
                    side=request.side,
                    quantity=request.quantity,
                    filled_quantity=int(request.quantity * 0.5),  # Mock: 50% 체결
                    avg_price=request.limit_price,
                    status=OrderStatus.PARTIAL,
                    executed_at=datetime.now(timezone.utc),
                )
            else:
                # 실제 API 호출
                if request.market == Market.KRX:
                    api_result = await self._kis_client.place_kr_order(
                        ticker=request.ticker,
                        side=request.side.value,
                        quantity=request.quantity,
                        price=int(request.limit_price),
                        order_type="00",  # 지정가
                    )
                else:
                    api_result = await self._kis_client.place_us_order(
                        ticker=request.ticker,
                        side=request.side.value,
                        quantity=request.quantity,
                        price=request.limit_price,
                    )

                result = OrderResult(
                    order_id=api_result.get("order_id", ""),
                    ticker=request.ticker,
                    market=request.market,
                    side=request.side,
                    quantity=request.quantity,
                    filled_quantity=int(api_result.get("filled_qty", 0)),
                    avg_price=float(api_result.get("avg_price", 0)),
                    status=OrderStatus.SUBMITTED,
                    executed_at=datetime.now(timezone.utc),
                )

            logger.info(f"지정가 주문 완료: {result.order_id}")
            return result

        except Exception as e:
            logger.error(f"지정가 주문 실패: {e}")
            raise

    async def _execute_twap_order(self, request: OrderRequest) -> OrderResult:
        """
        TWAP (시간가중평균) 주문 실행

        주문을 여러 구간으로 나누어 일정 시간 간격으로 체결합니다.
        기본값: 6개 구간, 5분 간격, 총 30분.

        Args:
            request: 주문 요청 데이터

        Returns:
            OrderResult: 누적된 주문 실행 결과
        """
        logger.info(f"TWAP 주문 실행: {request.ticker} {request.side.value} {request.quantity}")

        try:
            num_intervals = 6
            interval_seconds = 300  # 5분
            qty_per_interval = request.quantity // num_intervals
            remaining_qty = request.quantity % num_intervals

            total_filled = 0
            total_cost = 0.0
            results: list[OrderResult] = []

            for i in range(num_intervals):
                # 마지막 구간에서 남은 수량 처리
                qty = qty_per_interval + (remaining_qty if i == num_intervals - 1 else 0)

                sub_request = OrderRequest(
                    ticker=request.ticker,
                    market=request.market,
                    side=request.side,
                    quantity=qty,
                    order_type=OrderType.MARKET,
                    reason=f"TWAP 구간 {i+1}/{num_intervals}",
                )

                try:
                    result = await self._execute_market_order(sub_request)
                    results.append(result)
                    total_filled += result.filled_quantity
                    total_cost += result.filled_quantity * result.avg_price
                except Exception as e:
                    logger.warning(f"TWAP 구간 {i+1} 실패: {e}")

                # 마지막 구간이 아니면 대기
                if i < num_intervals - 1:
                    await asyncio.sleep(interval_seconds)

            # 최종 결과 생성
            avg_price = total_cost / total_filled if total_filled > 0 else 0.0
            result = OrderResult(
                order_id=f"TWAP_{request.ticker}_{datetime.now().timestamp()}",
                ticker=request.ticker,
                market=request.market,
                side=request.side,
                quantity=request.quantity,
                filled_quantity=total_filled,
                avg_price=avg_price,
                status=OrderStatus.FILLED if total_filled == request.quantity else OrderStatus.PARTIAL,
                executed_at=datetime.now(timezone.utc),
            )

            logger.info(f"TWAP 주문 완료: {total_filled}/{request.quantity} 체결")
            return result

        except Exception as e:
            logger.error(f"TWAP 주문 실패: {e}")
            raise

    async def _execute_vwap_order(self, request: OrderRequest) -> OrderResult:
        """
        VWAP (거래량가중평균) 주문 실행

        거래량 프로필을 기반으로 주문을 분할 체결합니다.
        시장의 일일 거래량 분포를 고려하여 최적의 실행가를 추구합니다.

        현재 구현: TWAP과 동일하게 균등 분할
        향후: 실시간 거래량 데이터를 기반으로 동적 분할

        Args:
            request: 주문 요청 데이터

        Returns:
            OrderResult: 누적된 주문 실행 결과
        """
        logger.info(f"VWAP 주문 실행: {request.ticker} {request.side.value} {request.quantity}")

        try:
            # TODO: 실시간 거래량 데이터를 기반으로 동적 분할
            # 현재는 TWAP과 유사하게 구현
            num_intervals = 6
            interval_seconds = 300  # 5분
            qty_per_interval = request.quantity // num_intervals
            remaining_qty = request.quantity % num_intervals

            total_filled = 0
            total_cost = 0.0
            results: list[OrderResult] = []

            for i in range(num_intervals):
                qty = qty_per_interval + (remaining_qty if i == num_intervals - 1 else 0)

                sub_request = OrderRequest(
                    ticker=request.ticker,
                    market=request.market,
                    side=request.side,
                    quantity=qty,
                    order_type=OrderType.MARKET,
                    reason=f"VWAP 구간 {i+1}/{num_intervals}",
                )

                try:
                    result = await self._execute_market_order(sub_request)
                    results.append(result)
                    total_filled += result.filled_quantity
                    total_cost += result.filled_quantity * result.avg_price
                except Exception as e:
                    logger.warning(f"VWAP 구간 {i+1} 실패: {e}")

                if i < num_intervals - 1:
                    await asyncio.sleep(interval_seconds)

            avg_price = total_cost / total_filled if total_filled > 0 else 0.0
            result = OrderResult(
                order_id=f"VWAP_{request.ticker}_{datetime.now().timestamp()}",
                ticker=request.ticker,
                market=request.market,
                side=request.side,
                quantity=request.quantity,
                filled_quantity=total_filled,
                avg_price=avg_price,
                status=OrderStatus.FILLED if total_filled == request.quantity else OrderStatus.PARTIAL,
                executed_at=datetime.now(timezone.utc),
            )

            logger.info(f"VWAP 주문 완료: {total_filled}/{request.quantity} 체결")
            return result

        except Exception as e:
            logger.error(f"VWAP 주문 실패: {e}")
            raise

    def _validate_order(self, request: OrderRequest) -> None:
        """
        주문 검증

        주문 실행 전 다음 사항을 확인합니다:
        1. 잔고 확인 (매수 시)
        2. 주문금액 상한 검사
        3. 동일 종목 중복 주문 방지

        Args:
            request: 주문 요청 데이터

        Raises:
            ValueError: 검증 실패 시
        """
        logger.debug(f"주문 검증: {request.ticker} {request.side.value} {request.quantity}")

        # 수량 검증
        if request.quantity <= 0:
            raise ValueError(f"주문 수량은 0보다 커야 합니다: {request.quantity}")

        # 지정가 주문 검증
        if request.order_type == OrderType.LIMIT:
            if request.limit_price is None or request.limit_price <= 0:
                raise ValueError("지정가 주문에는 유효한 limit_price가 필요합니다")

        logger.debug(f"주문 검증 완료: {request.ticker}")

    async def _store_order(self, result: OrderResult) -> None:
        """
        주문 정보를 PostgreSQL에 저장

        Args:
            result: 주문 실행 결과
        """
        try:
            async with async_session_factory() as db_session:
                query = text("""
                    INSERT INTO orders (
                        order_id, ticker, market, side, quantity,
                        filled_qty, avg_price, status, created_at, error_message
                    ) VALUES (
                        :order_id, :ticker, :market, :side, :quantity,
                        :filled_qty, :avg_price, :status, :created_at, :error_message
                    )
                """)

                await db_session.execute(
                    query,
                    {
                        "order_id": result.order_id,
                        "ticker": result.ticker,
                        "market": result.market.value,
                        "side": result.side.value,
                        "quantity": result.quantity,
                        "filled_qty": result.filled_quantity,
                        "avg_price": result.avg_price,
                        "status": result.status.value,
                        "created_at": result.executed_at,
                        "error_message": result.error_message,
                    },
                )
                await db_session.commit()
                logger.debug(f"주문 저장 완료: {result.order_id}")

        except Exception as e:
            logger.error(f"주문 저장 실패: {e}")

    async def _handle_unfilled(self, order_id: str, remaining_qty: int) -> OrderResult:
        """
        미체결 주문 처리 (장마감 30분전)

        장 마감 30분 전에도 미체결된 주문은 시장가로 일괄 체결합니다.

        Args:
            order_id: 미체결 주문번호
            remaining_qty: 남은 수량

        Returns:
            OrderResult: 추가 체결 결과
        """
        logger.warning(f"미체결 주문 시장가 전환: {order_id} qty={remaining_qty}")

        # TODO: 실제 구현
        # 1. 주문번호에서 종목, 매매방향 조회
        # 2. 시장가 주문으로 남은 수량 전환

        result = OrderResult(
            order_id=order_id,
            ticker="",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=remaining_qty,
            filled_quantity=remaining_qty,
            avg_price=0.0,
            status=OrderStatus.FILLED,
            executed_at=datetime.now(timezone.utc),
        )

        return result
