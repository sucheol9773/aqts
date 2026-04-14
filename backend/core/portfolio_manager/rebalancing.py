"""
정기/비상 리밸런싱 엔진 (F-05-03, F-05-04)

포트폴리오 리밸런싱을 관리합니다:
- F-05-03: 정기 리밸런싱 (매월 첫 영업일 09:30 KST)
- F-05-04: 비상 리밸런싱 (5분 간격 모니터링, loss_tolerance 초과 시 자동 트리거)

투자 스타일별 처리:
- DISCRETIONARY (일임형): 자동 매매
- ADVISORY (자문형): 텔레그램 추천 전송

주요 기능:
- async check_scheduled_rebalancing: 정기 리밸런싱 시점 확인
- async execute_scheduled_rebalancing: 정기 리밸런싱 실행
- async check_emergency_trigger: 비상 리밸런싱 조건 확인
- async execute_emergency_rebalancing: 비상 리밸런싱 실행
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text

from config.constants import (
    InvestmentStyle,
    Market,
    OrderSide,
    OrderType,
    RebalancingFrequency,
    RebalancingType,
)
from config.logging import logger
from config.settings import get_settings
from core.notification.telegram_transport import TelegramTransport
from core.order_executor.executor import OrderExecutor, OrderRequest
from core.portfolio_manager.construction import (
    PortfolioConstructionEngine,
    TargetAllocation,
    TargetPortfolio,
)
from core.portfolio_manager.profile import InvestorProfile
from db.database import async_session_factory


# ══════════════════════════════════════
# 리밸런싱 주문 및 결과 데이터 구조
# ══════════════════════════════════════
@dataclass
class RebalancingOrder:
    """
    리밸런싱 주문

    현재 포트폴리오에서 목표 포트폴리오로 전환하기 위한 개별 주문
    """

    ticker: str
    market: Market
    action: OrderSide  # BUY / SELL
    quantity: int
    order_type: OrderType = OrderType.MARKET
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환"""
        return {
            "ticker": self.ticker,
            "market": self.market.value,
            "action": self.action.value,
            "quantity": self.quantity,
            "order_type": self.order_type.value,
            "reason": self.reason,
        }


@dataclass
class RebalancingResult:
    """
    리밸런싱 실행 결과

    리밸런싱 과정의 전체 정보를 포함합니다.
    """

    orders: list[RebalancingOrder] = field(default_factory=list)
    rebalancing_type: RebalancingType = RebalancingType.SCHEDULED
    trigger_reason: str = ""
    old_portfolio_summary: dict[str, Any] = field(default_factory=dict)
    new_portfolio_summary: dict[str, Any] = field(default_factory=dict)
    executed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환"""
        return {
            "orders": [o.to_dict() for o in self.orders],
            "rebalancing_type": self.rebalancing_type.value,
            "trigger_reason": self.trigger_reason,
            "old_portfolio_summary": self.old_portfolio_summary,
            "new_portfolio_summary": self.new_portfolio_summary,
            "executed_at": self.executed_at,
            "order_count": len(self.orders),
        }


# ══════════════════════════════════════
# 리밸런싱 엔진
# ══════════════════════════════════════
class RebalancingEngine:
    """
    정기/비상 리밸런싱 엔진

    사용자 프로필과 포트폴리오 구성 엔진을 기반으로
    정기 및 비상 리밸런싱을 관리합니다.

    정기 리밸런싱 (F-05-03):
    - 매월 첫 영업일 09:30 KST (기본값)
    - 리밸런싱_주기에 따라 월간/격월/분기별 수행

    비상 리밸런싱 (F-05-04):
    - 5분 간격 모니터링
    - 손실 허용도 초과 시 자동 트리거
    - 방어 포트폴리오로 자동 전환

    투자 스타일별 처리:
    - DISCRETIONARY (일임형): 자동 매매
    - ADVISORY (자문형): 텔레그램 추천 전송만
    """

    # 정기 리밸런싱 기본 시간 (09:30 KST)
    DEFAULT_REBALANCING_TIME = time(9, 30, 0)

    def __init__(
        self,
        profile: InvestorProfile,
        construction_engine: PortfolioConstructionEngine,
        telegram_notifier: Optional["TelegramTransport"] = None,
        order_executor: Optional["OrderExecutor"] = None,
    ):
        """
        리밸런싱 엔진 초기화

        Args:
            profile: 사용자 투자 프로필
            construction_engine: 포트폴리오 구성 엔진
            telegram_notifier: 텔레그램 Transport (HTTP 전송 레이어)
            order_executor: 주문 실행기
        """
        self.profile = profile
        self.construction_engine = construction_engine
        self._telegram = telegram_notifier
        self._order_executor = order_executor
        self.settings = get_settings()

    async def check_scheduled_rebalancing(self) -> bool:
        """
        정기 리밸런싱 시점인지 확인합니다.

        매월 첫 영업일 09:30 KST를 기준으로 확인하며,
        리밸런싱 주기에 따라 월간/격월/분기별로 실행 여부를 결정합니다.

        Returns:
            True if it's time to rebalance, False otherwise
        """
        now = datetime.now(timezone.utc)
        now_kst = now.astimezone()  # KST로 변환 (시스템 시간대 기반)

        # 시간 확인
        if now_kst.time() < self.DEFAULT_REBALANCING_TIME:
            return False

        # 마지막 리밸런싱 시간 확인
        last_rebal_time = await self._get_last_rebalancing_time(self.profile.user_id)

        if not last_rebal_time:
            # 처음 실행 시 현재 월의 첫 영업일인지 확인
            return await self._is_first_business_day_of_month(now_kst)

        # 리밸런싱 주기에 따른 간격 확인
        frequency = self.profile.rebalancing_frequency
        days_elapsed = (now_kst - last_rebal_time).days

        if frequency == RebalancingFrequency.MONTHLY:
            return days_elapsed >= 30
        elif frequency == RebalancingFrequency.BIMONTHLY:
            return days_elapsed >= 60
        elif frequency == RebalancingFrequency.QUARTERLY:
            return days_elapsed >= 90

        return False

    async def execute_scheduled_rebalancing(
        self,
        ensemble_signals: dict[str, float],  # {ticker: signal}
        current_portfolio: dict[str, float],  # {ticker: weight}
        seed_capital: float,
        sector_info: Optional[dict[str, str]] = None,
        market_info: Optional[dict[str, Market]] = None,
    ) -> RebalancingResult:
        """
        정기 리밸런싱을 실행합니다.

        현재 포트폴리오를 조회하고, 목표 포트폴리오를 생성한 뒤,
        리밸런싱 주문을 생성합니다. 투자 스타일에 따라 자동 매매 또는
        추천 알림을 수행합니다.

        Args:
            ensemble_signals: 앙상블 신호 {ticker: score}
            current_portfolio: 현재 포트폴리오 {ticker: weight}
            seed_capital: 포트폴리오 총액 (원)
            sector_info: 종목별 섹터 정보
            market_info: 종목별 시장 정보

        Returns:
            RebalancingResult

        Raises:
            Exception: 리밸런싱 실행 실패 시
        """
        try:
            logger.info(f"Starting scheduled rebalancing for user {self.profile.user_id}")

            # 목표 포트폴리오 생성
            target_portfolio = await self.construction_engine.construct(
                ensemble_signals=ensemble_signals,
                current_portfolio=current_portfolio,
                seed_capital=seed_capital,
                sector_info=sector_info,
                market_info=market_info,
            )

            # 리밸런싱 주문 생성
            orders = self._generate_rebalancing_orders(
                current_portfolio,
                target_portfolio,
                seed_capital,
            )

            # 결과 생성
            result = RebalancingResult(
                orders=orders,
                rebalancing_type=RebalancingType.SCHEDULED,
                trigger_reason=f"Scheduled rebalancing ({self.profile.rebalancing_frequency.value})",
                old_portfolio_summary=self._summarize_portfolio(current_portfolio),
                new_portfolio_summary=self._summarize_target_portfolio(target_portfolio),
            )

            # 투자 스타일별 처리
            await self._handle_rebalancing_by_style(result)

            # DB 기록
            await self._record_rebalancing(result)

            logger.info(
                f"Scheduled rebalancing completed: {len(orders)} orders, "
                f"style={self.profile.investment_style.value}"
            )
            return result

        except Exception as e:
            logger.error(f"Scheduled rebalancing failed for user {self.profile.user_id}: {e}")
            raise

    async def check_emergency_trigger(
        self,
        current_portfolio: dict[str, float],  # {ticker: weight}
        market_data: dict[str, float],  # {ticker: current_price}
        portfolio_values: dict[str, float],  # {ticker: position_value}
    ) -> Optional[str]:
        """
        비상 리밸런싱 트리거 조건을 확인합니다.

        손실 허용도(loss_tolerance)를 초과한 누적 손실이 발생했는지 확인합니다.
        매 5분 간격으로 호출됨을 가정합니다.

        Args:
            current_portfolio: 현재 포트폴리오 {ticker: weight}
            market_data: 종목별 현재 가격
            portfolio_values: 종목별 포지션 가치

        Returns:
            트리거 사유 문자열, 또는 트리거되지 않았으면 None
        """
        try:
            # 현재 포트폴리오 손실률 계산
            loss_pct = await self._calculate_portfolio_loss(current_portfolio, portfolio_values)

            if loss_pct > self.profile.loss_tolerance:
                trigger_reason = (
                    f"Emergency: Portfolio loss {loss_pct*100:.2f}% exceeds tolerance "
                    f"{self.profile.loss_tolerance*100:.2f}%"
                )
                logger.warning(trigger_reason)
                return trigger_reason

            return None

        except Exception as e:
            logger.error(f"Emergency trigger check failed: {e}")
            return None

    async def execute_emergency_rebalancing(
        self,
        current_portfolio: dict[str, float],  # {ticker: weight}
        market_data: dict[str, float],  # {ticker: current_price}
        portfolio_values: dict[str, float],  # {ticker: position_value}
        trigger_reason: str = "",
    ) -> RebalancingResult:
        """
        비상 리밸런싱을 실행합니다.

        손실 허용도 초과 시 포트폴리오를 방어 모드로 전환합니다.
        - 주식 비중을 50% → 30% 축소
        - 현금 및 안정 자산 비중을 50% → 70% 확대

        Args:
            current_portfolio: 현재 포트폴리오 {ticker: weight}
            market_data: 종목별 현재 가격
            portfolio_values: 종목별 포지션 가치
            trigger_reason: 트리거 사유

        Returns:
            RebalancingResult

        Raises:
            Exception: 리밸런싱 실행 실패 시
        """
        try:
            logger.warning(f"Starting emergency rebalancing: {trigger_reason}")

            # 방어 포트폴리오 생성
            defensive_portfolio = await self._generate_defensive_portfolio(
                current_portfolio,
                market_data,
            )

            # 리밸런싱 주문 생성
            orders = self._generate_rebalancing_orders(
                current_portfolio,
                defensive_portfolio,
                sum(portfolio_values.values()),
            )

            # 결과 생성
            result = RebalancingResult(
                orders=orders,
                rebalancing_type=RebalancingType.EMERGENCY,
                trigger_reason=trigger_reason,
                old_portfolio_summary=self._summarize_portfolio(current_portfolio),
                new_portfolio_summary=self._summarize_target_portfolio(defensive_portfolio),
            )

            # 투자 스타일별 처리 (비상의 경우 자문형도 신속 실행 추천)
            await self._handle_emergency_rebalancing_by_style(result)

            # DB 기록
            await self._record_rebalancing(result)

            logger.warning(f"Emergency rebalancing executed: {len(orders)} orders")
            return result

        except Exception as e:
            logger.error(f"Emergency rebalancing failed: {e}")
            raise

    # ══════════════════════════════════════
    # 내부 헬퍼 메서드
    # ══════════════════════════════════════

    async def _get_last_rebalancing_time(self, user_id: str) -> Optional[datetime]:
        """마지막 리밸런싱 시간 조회"""
        try:
            async with async_session_factory() as session:
                query = text(
                    """
                    SELECT MAX(executed_at)
                    FROM rebalancing_history
                    WHERE user_id = :user_id AND rebalancing_type = 'SCHEDULED'
                """
                )
                result = await session.execute(query, {"user_id": user_id})
                row = result.fetchone()
                return row[0] if row and row[0] else None
        except Exception as e:
            logger.warning(f"마지막 리밸런싱 시간 조회 실패: None 반환 → 첫 실행으로 간주될 수 있음. error={e}")
            return None

    async def _is_first_business_day_of_month(self, check_date: datetime) -> bool:
        """
        해당 날짜가 월의 첫 영업일인지 확인합니다.

        단순화: 1일~3일 범위의 평일을 첫 영업일로 간주
        """
        day_of_month = check_date.day
        weekday = check_date.weekday()  # 0=월, 6=일

        # 1일~3일 범위의 평일(월~금)
        return 1 <= day_of_month <= 3 and weekday < 5

    async def _calculate_portfolio_loss(
        self,
        current_portfolio: dict[str, float],
        portfolio_values: dict[str, float],
    ) -> float:
        """
        현재 포트폴리오의 누적 손실률을 계산합니다.

        Args:
            current_portfolio: {ticker: weight}
            portfolio_values: {ticker: position_value}

        Returns:
            손실률 (음수, 예: -0.15 = -15%)
        """
        try:
            total_value = sum(portfolio_values.values())
            if total_value <= 0:
                return 0.0

            # 간단한 구현: 현재 가치 기반 손실률
            # 실제로는 매입가를 기반으로 계산해야 함
            seed_amount = self.profile.seed_amount
            current_loss_pct = (total_value - seed_amount) / seed_amount

            return min(0.0, current_loss_pct)  # 손실만 반환 (음수)

        except Exception as e:
            logger.debug(f"Portfolio loss calculation failed: {e}")
            return 0.0

    async def _generate_defensive_portfolio(
        self,
        current_portfolio: dict[str, float],
        market_data: dict[str, float],
    ) -> TargetPortfolio:
        """
        방어 포트폴리오를 생성합니다.

        주식 비중을 50% → 30%로 축소하고,
        현금 및 방어 자산 비중을 50% → 70%로 확대합니다.

        Args:
            current_portfolio: 현재 포트폴리오
            market_data: 시장 데이터

        Returns:
            TargetPortfolio (방어 모드)
        """
        defensive_allocations = []

        # 현재 포트폴리오의 주식을 30%로 축소
        for ticker, weight in current_portfolio.items():
            if weight > 0.001:
                new_weight = weight * 0.3  # 30% 축소
                allocation = TargetAllocation(
                    ticker=ticker,
                    market=Market.KRX,  # 단순화
                    target_weight=new_weight,
                    current_weight=weight,
                    signal_score=-0.5,  # 방어 신호
                    sector="",
                )
                defensive_allocations.append(allocation)

        # 현금 비중: 70%
        cash_ratio = 0.7

        portfolio = TargetPortfolio(
            allocations=defensive_allocations,
            total_value=self.profile.seed_amount,
            cash_ratio=cash_ratio,
            optimization_method="defensive",
        )

        logger.info(
            f"Defensive portfolio generated: {len(defensive_allocations)} positions, cash={cash_ratio*100:.0f}%"
        )
        return portfolio

    def _generate_rebalancing_orders(
        self,
        current_portfolio: dict[str, float],
        target_portfolio: TargetPortfolio,
        seed_capital: float,
    ) -> list[RebalancingOrder]:
        """
        리밸런싱 주문을 생성합니다.

        현재와 목표의 비중 차이를 기반으로 BUY/SELL 주문을 생성합니다.

        Args:
            current_portfolio: 현재 {ticker: weight}
            target_portfolio: 목표 TargetPortfolio
            seed_capital: 포트폴리오 총액

        Returns:
            RebalancingOrder 리스트
        """
        orders = []
        target_dict = {a.ticker: a.target_weight for a in target_portfolio.allocations}

        all_tickers = set(current_portfolio.keys()) | set(target_dict.keys())

        for ticker in all_tickers:
            current_weight = current_portfolio.get(ticker, 0.0)
            target_weight = target_dict.get(ticker, 0.0)
            weight_diff = target_weight - current_weight

            if abs(weight_diff) < 0.001:  # 1bp 이하
                continue

            action = OrderSide.BUY if weight_diff > 0 else OrderSide.SELL
            quantity = int(abs(weight_diff) * seed_capital / 1000)

            if quantity > 0:
                order = RebalancingOrder(
                    ticker=ticker,
                    market=Market.NYSE,  # 단순화
                    action=action,
                    quantity=quantity,
                    order_type=OrderType.MARKET,
                    reason=f"Rebalance {action.value}: {current_weight*100:.1f}% → {target_weight*100:.1f}%",
                )
                orders.append(order)

        return sorted(orders, key=lambda o: -abs(o.quantity))

    def _summarize_portfolio(self, portfolio: dict[str, float]) -> dict[str, Any]:
        """포트폴리오 요약 정보 생성"""
        positions = {t: w for t, w in portfolio.items() if w > 0.0001}
        return {
            "position_count": len(positions),
            "top_3_positions": sorted(positions.items(), key=lambda x: -x[1])[:3],
            "largest_weight": max(portfolio.values()) if portfolio else 0.0,
        }

    def _summarize_target_portfolio(self, portfolio: TargetPortfolio) -> dict[str, Any]:
        """목표 포트폴리오 요약 정보 생성"""
        return {
            "position_count": portfolio.stock_count,
            "cash_ratio": portfolio.cash_ratio,
            "top_3_positions": [
                (a.ticker, a.target_weight) for a in sorted(portfolio.allocations, key=lambda a: -a.target_weight)[:3]
            ],
            "sector_weights": portfolio.sector_weights,
        }

    async def _handle_rebalancing_by_style(self, result: RebalancingResult) -> None:
        """
        투자 스타일별 리밸런싱 처리

        - DISCRETIONARY: 자동 매매 실행 + 결과 알림
        - ADVISORY: 텔레그램 추천 전송만
        """
        if self.profile.investment_style == InvestmentStyle.DISCRETIONARY:
            logger.info(f"Auto-executing rebalancing orders for {self.profile.user_id}")
            await self._execute_orders(result.orders)
            # 체결 완료 알림
            await self._send_rebalancing_completed_notification(result)
        else:
            await self._send_rebalancing_recommendation(result)

    async def _handle_emergency_rebalancing_by_style(self, result: RebalancingResult) -> None:
        """비상 리밸런싱은 모든 스타일에 신속 알림 + 일임형은 자동 체결"""
        await self._send_emergency_notification(result)
        if self.profile.investment_style == InvestmentStyle.DISCRETIONARY:
            logger.info("Auto-executing emergency rebalancing")
            await self._execute_orders(result.orders)

    async def _execute_orders(self, orders: list[RebalancingOrder]) -> None:
        """
        OrderExecutor를 통해 리밸런싱 주문을 실행합니다.

        매도 주문을 먼저 체결한 후 매수 주문을 실행합니다 (자금 확보 우선).

        Args:
            orders: 리밸런싱 주문 리스트
        """
        if not self._order_executor:
            logger.warning("OrderExecutor not available, orders not executed")
            return

        # 매도 주문 우선 실행
        sell_orders = [o for o in orders if o.action == OrderSide.SELL]
        buy_orders = [o for o in orders if o.action == OrderSide.BUY]

        for order_group, label in [(sell_orders, "SELL"), (buy_orders, "BUY")]:
            for order in order_group:
                try:
                    request = OrderRequest(
                        ticker=order.ticker,
                        market=order.market,
                        side=order.action,
                        quantity=order.quantity,
                        order_type=order.order_type,
                        reason=order.reason,
                    )
                    await self._order_executor.execute_order(request)
                    logger.info(f"Rebalancing order executed: {label} {order.ticker} x{order.quantity}")
                except Exception as e:
                    logger.error(f"Rebalancing order failed: {order.ticker}: {e}")

    async def _send_rebalancing_recommendation(self, result: RebalancingResult) -> None:
        """텔레그램으로 리밸런싱 추천 전송"""
        try:
            message = self._format_rebalancing_message(result)
            if self._telegram:
                await self._telegram.send_text(message, parse_mode="HTML")
                logger.info(f"Rebalancing recommendation sent to user {self.profile.user_id}")
            else:
                logger.info(f"Rebalancing recommendation (no Telegram): {message}")
        except Exception as e:
            logger.error(f"Failed to send recommendation: {e}")

    async def _send_rebalancing_completed_notification(self, result: RebalancingResult) -> None:
        """리밸런싱 완료 알림 전송"""
        try:
            message = (
                f"✅ <b>정기 리밸런싱 완료</b>\n\n"
                f"주문 {len(result.orders)}건 체결\n"
                f"유형: {result.rebalancing_type.value}\n"
                f"시간: {result.executed_at.astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M KST')}"
            )
            if self._telegram:
                await self._telegram.send_text(message, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to send completion notification: {e}")

    async def _send_emergency_notification(self, result: RebalancingResult) -> None:
        """텔레그램으로 비상 알림 전송"""
        try:
            message = (
                f"🚨 <b>비상 리밸런싱 실행</b>\n\n"
                f"<b>사유:</b> {result.trigger_reason}\n"
                f"<b>주문:</b> {len(result.orders)}건\n"
                f"<b>조치:</b> 포트폴리오 방어 모드 전환\n\n"
                f"<i>{result.executed_at.astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S KST')}</i>"
            )
            if self._telegram:
                await self._telegram.send_text(message, parse_mode="HTML")
                logger.warning(f"Emergency notification sent: {len(result.orders)} orders")
            else:
                logger.warning(f"Emergency notification (no Telegram): {message}")
        except Exception as e:
            logger.error(f"Failed to send emergency notification: {e}")

    def _format_rebalancing_message(self, result: RebalancingResult) -> str:
        """리밸런싱 메시지 포맷"""
        summary = f"리밸런싱: {len(result.orders)}건\n"
        for order in result.orders[:3]:
            summary += f"  {order.action.value} {order.ticker}: {order.quantity}주\n"
        return summary

    async def _record_rebalancing(self, result: RebalancingResult) -> None:
        """리밸런싱 결과를 DB에 기록"""
        try:
            async with async_session_factory() as session:
                query = text(
                    """
                    INSERT INTO rebalancing_history (
                        user_id, rebalancing_type, trigger_reason,
                        orders, old_summary, new_summary, executed_at
                    )
                    VALUES (
                        :user_id, :type, :reason,
                        :orders, :old_summary, :new_summary, :executed_at
                    )
                """
                )
                await session.execute(
                    query,
                    {
                        "user_id": self.profile.user_id,
                        "type": result.rebalancing_type.value,
                        "reason": result.trigger_reason,
                        "orders": json.dumps([o.to_dict() for o in result.orders]),
                        "old_summary": json.dumps(result.old_portfolio_summary),
                        "new_summary": json.dumps(result.new_portfolio_summary),
                        "executed_at": result.executed_at,
                    },
                )
                await session.commit()
        except Exception as e:
            logger.error(f"리밸런싱 이벤트 DB 기록 실패: 감사 추적 누락 위험. error={e}")
