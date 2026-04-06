"""
드라이런 엔진 (Dry-Run Engine)

실제 주문을 실행하지 않고 전체 파이프라인(시그널 생성 → 리스크 체크 → 주문 생성)을
시뮬레이션하여 시스템 동작을 검증합니다.

주요 기능:
  1. DryRunOrder: 실행되지 않은 가상 주문 기록
  2. DryRunSession: 단일 드라이런 세션 (시작~종료)
  3. DryRunReport: 세션 종합 리포트
  4. DryRunEngine: 드라이런 오케스트레이터

설계 원칙:
  - OrderExecutor를 직접 수정하지 않고, 주문 실행 단계에서 가로채기(intercept)
  - 파이프라인의 시그널/리스크 체크는 실제와 동일하게 수행
  - 주문만 "기록"하고 실제 KIS API 호출을 차단
  - DecisionRecord와 연동하여 7단계 의사결정 체인 전체를 감사 가능
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from config.constants import Market, OrderSide, OrderType
from config.logging import logger


class DryRunStatus(str, Enum):
    """드라이런 세션 상태"""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class DryRunOrder:
    """
    드라이런 가상 주문 기록

    실제 주문이 실행되었다면 어떤 주문이 나갔을지를 기록합니다.
    KIS API 호출 없이 주문 의도만 기록합니다.

    Attributes:
        order_id: 드라이런 주문 ID (DRY_{ticker}_{timestamp})
        ticker: 종목 코드
        market: 시장 구분
        side: 주문 방향 (BUY/SELL)
        quantity: 주문 수량
        order_type: 주문 유형 (MARKET/LIMIT/TWAP/VWAP)
        limit_price: 지정가 (LIMIT 주문 시)
        reason: 주문 사유
        risk_check_passed: TradingGuard 사전 검증 통과 여부
        risk_check_details: TradingGuard 검증 상세
        estimated_price: 추정 체결 가격 (현재가 기준)
        estimated_amount: 추정 주문 금액
        created_at: 생성 시각
    """

    order_id: str
    ticker: str
    market: Market
    side: OrderSide
    quantity: int
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    reason: str = ""
    risk_check_passed: bool = True
    risk_check_details: str = ""
    estimated_price: float = 0.0
    estimated_amount: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환"""
        return {
            "order_id": self.order_id,
            "ticker": self.ticker,
            "market": self.market.value,
            "side": self.side.value,
            "quantity": self.quantity,
            "order_type": self.order_type.value,
            "limit_price": self.limit_price,
            "reason": self.reason,
            "risk_check_passed": self.risk_check_passed,
            "risk_check_details": self.risk_check_details,
            "estimated_price": self.estimated_price,
            "estimated_amount": self.estimated_amount,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class DryRunSession:
    """
    드라이런 세션

    하나의 드라이런 실행 단위를 나타냅니다.
    세션 내 모든 가상 주문과 파이프라인 결과를 기록합니다.

    Attributes:
        session_id: 세션 고유 ID
        status: 세션 상태
        started_at: 시작 시각
        ended_at: 종료 시각
        orders: 가상 주문 목록
        pipeline_results: 파이프라인 실행 결과 요약
        decision_id: 연관된 DecisionRecord ID
        error_message: 오류 메시지 (실패 시)
    """

    session_id: str = field(default_factory=lambda: str(uuid4()))
    status: DryRunStatus = DryRunStatus.RUNNING
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: Optional[datetime] = None
    orders: list[DryRunOrder] = field(default_factory=list)
    pipeline_results: dict[str, Any] = field(default_factory=dict)
    decision_id: Optional[str] = None
    error_message: str = ""

    def add_order(self, order: DryRunOrder) -> None:
        """가상 주문 추가"""
        self.orders.append(order)
        logger.info(
            f"[DRY_RUN] 가상 주문 기록: {order.ticker} "
            f"{order.side.value} {order.quantity}주 "
            f"(사유: {order.reason})"
        )

    def complete(self) -> None:
        """세션 정상 완료"""
        self.status = DryRunStatus.COMPLETED
        self.ended_at = datetime.now(timezone.utc)

    def fail(self, error: str) -> None:
        """세션 실패 처리"""
        self.status = DryRunStatus.FAILED
        self.ended_at = datetime.now(timezone.utc)
        self.error_message = error

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환"""
        return {
            "session_id": self.session_id,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "orders": [o.to_dict() for o in self.orders],
            "pipeline_results": self.pipeline_results,
            "decision_id": self.decision_id,
            "error_message": self.error_message,
            "summary": self.get_summary(),
        }

    def get_summary(self) -> dict[str, Any]:
        """세션 요약 통계"""
        buy_orders = [o for o in self.orders if o.side == OrderSide.BUY]
        sell_orders = [o for o in self.orders if o.side == OrderSide.SELL]
        blocked_orders = [o for o in self.orders if not o.risk_check_passed]

        total_buy_amount = sum(o.estimated_amount for o in buy_orders)
        total_sell_amount = sum(o.estimated_amount for o in sell_orders)

        return {
            "total_orders": len(self.orders),
            "buy_orders": len(buy_orders),
            "sell_orders": len(sell_orders),
            "blocked_by_risk": len(blocked_orders),
            "total_buy_amount": total_buy_amount,
            "total_sell_amount": total_sell_amount,
            "net_amount": total_buy_amount - total_sell_amount,
            "unique_tickers": list({o.ticker for o in self.orders}),
        }


@dataclass
class DryRunReport:
    """
    드라이런 종합 리포트

    여러 세션의 결과를 종합하여 시스템 동작 검증 리포트를 생성합니다.

    Attributes:
        report_id: 리포트 고유 ID
        sessions: 포함된 세션 목록
        created_at: 생성 시각
    """

    report_id: str = field(default_factory=lambda: str(uuid4()))
    sessions: list[DryRunSession] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def add_session(self, session: DryRunSession) -> None:
        """세션 추가"""
        self.sessions.append(session)

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환"""
        all_orders = []
        for s in self.sessions:
            all_orders.extend(s.orders)

        return {
            "report_id": self.report_id,
            "created_at": self.created_at.isoformat(),
            "total_sessions": len(self.sessions),
            "completed_sessions": len([s for s in self.sessions if s.status == DryRunStatus.COMPLETED]),
            "failed_sessions": len([s for s in self.sessions if s.status == DryRunStatus.FAILED]),
            "total_orders": len(all_orders),
            "sessions": [s.to_dict() for s in self.sessions],
        }


class DryRunEngine:
    """
    드라이런 엔진

    전체 파이프라인을 실행하되, 주문 실행 단계에서 실제 API 호출 대신
    가상 주문을 기록합니다.

    사용법:
        engine = DryRunEngine()
        session = await engine.run_single_ticker("005930")
        report = engine.get_report()

    인터셉트 방식:
        OrderExecutor._execute_market_order()에서 is_backtest 분기와 유사하게,
        dry_run=True 시 KIS API 호출을 건너뛰고 DryRunOrder를 생성합니다.
    """

    def __init__(self) -> None:
        """드라이런 엔진 초기화"""
        self._sessions: list[DryRunSession] = []
        self._current_session: Optional[DryRunSession] = None
        logger.info("[DRY_RUN] DryRunEngine 초기화")

    @property
    def current_session(self) -> Optional[DryRunSession]:
        """현재 진행 중인 세션"""
        return self._current_session

    @property
    def sessions(self) -> list[DryRunSession]:
        """전체 세션 목록"""
        return self._sessions.copy()

    def start_session(self) -> DryRunSession:
        """새 드라이런 세션 시작

        Returns:
            DryRunSession: 새로 생성된 세션
        """
        session = DryRunSession()
        self._current_session = session
        self._sessions.append(session)
        logger.info(f"[DRY_RUN] 세션 시작: {session.session_id}")
        return session

    def end_session(self, error: Optional[str] = None) -> Optional[DryRunSession]:
        """현재 세션 종료

        Args:
            error: 오류 메시지 (실패 시)

        Returns:
            종료된 DryRunSession 또는 None
        """
        if self._current_session is None:
            logger.warning("[DRY_RUN] 종료할 세션이 없습니다")
            return None

        session = self._current_session
        if error:
            session.fail(error)
            logger.error(f"[DRY_RUN] 세션 실패: {session.session_id} — {error}")
        else:
            session.complete()
            logger.info(f"[DRY_RUN] 세션 완료: {session.session_id} " f"(주문 {len(session.orders)}건)")

        self._current_session = None
        return session

    def record_order(
        self,
        ticker: str,
        market: Market,
        side: OrderSide,
        quantity: int,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[float] = None,
        reason: str = "",
        risk_check_passed: bool = True,
        risk_check_details: str = "",
        estimated_price: float = 0.0,
    ) -> DryRunOrder:
        """가상 주문 기록

        현재 세션에 가상 주문을 추가합니다.
        세션이 없으면 자동으로 시작합니다.

        Args:
            ticker: 종목 코드
            market: 시장 구분
            side: 주문 방향
            quantity: 주문 수량
            order_type: 주문 유형
            limit_price: 지정가
            reason: 주문 사유
            risk_check_passed: 리스크 체크 통과 여부
            risk_check_details: 리스크 체크 상세
            estimated_price: 추정 가격

        Returns:
            DryRunOrder: 기록된 가상 주문
        """
        if self._current_session is None:
            self.start_session()

        order = DryRunOrder(
            order_id=f"DRY_{ticker}_{datetime.now(timezone.utc).timestamp():.0f}",
            ticker=ticker,
            market=market,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            reason=reason,
            risk_check_passed=risk_check_passed,
            risk_check_details=risk_check_details,
            estimated_price=estimated_price,
            estimated_amount=estimated_price * quantity,
        )

        self._current_session.add_order(order)
        return order

    def get_report(self) -> DryRunReport:
        """전체 드라이런 리포트 생성

        Returns:
            DryRunReport: 종합 리포트
        """
        report = DryRunReport()
        for session in self._sessions:
            report.add_session(session)
        return report

    def get_session(self, session_id: str) -> Optional[DryRunSession]:
        """세션 ID로 조회

        Args:
            session_id: 세션 ID

        Returns:
            DryRunSession 또는 None
        """
        for session in self._sessions:
            if session.session_id == session_id:
                return session
        return None

    def clear_sessions(self) -> int:
        """모든 세션 데이터 초기화

        Returns:
            삭제된 세션 수
        """
        count = len(self._sessions)
        self._sessions.clear()
        self._current_session = None
        logger.info(f"[DRY_RUN] {count}개 세션 초기화")
        return count


# ──────────────────────────────────────
# 글로벌 인스턴스
# ──────────────────────────────────────
_engine_instance: Optional[DryRunEngine] = None


def get_dry_run_engine() -> DryRunEngine:
    """글로벌 DryRunEngine 인스턴스 반환"""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = DryRunEngine()
    return _engine_instance
