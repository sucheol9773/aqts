"""
비상 리밸런싱 모니터 (F-05-04)

5분 간격으로 포트폴리오 손실률을 모니터링하고,
임계값 초과 시 비상 리밸런싱을 자동 트리거합니다.

주요 기능:
- 5분 간격 실시간 손실률 모니터링 (장중 시간만)
- 사용자 설정 손실 허용도 기반 트리거
- 알고리즘 추천 동적 손절 기준 (변동성 기반)
- 매입가 기반 정확한 손실률 계산
- TelegramNotifier 연동 비상 알림 발송
- OrderExecutor 연동 자동 주문 체결
- 방어 포트폴리오 자동 전환

명세서 참조:
  F-05-04: 비상 리밸런싱
    - 트리거 조건 1: 사용자 설정 손실 감내 수준 초과
    - 트리거 조건 2: 알고리즘 추천 손절매 기준 초과
    - 모니터링 주기: 시장 시간 중 5분 간격
    - 처리: 시장 상황 재분석 → 방어적 포트폴리오 생성 → 주문 체결 → 알림 발송
"""

import asyncio
import math
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text

from config.constants import (
    InvestmentStyle,
    Market,
    OrderSide,
    OrderType,
    RebalancingType,
)
from config.logging import logger
from config.settings import get_settings
from db.database import async_session_factory


# ══════════════════════════════════════
# 포지션 및 모니터링 데이터 구조
# ══════════════════════════════════════
@dataclass
class PositionSnapshot:
    """개별 포지션 스냅샷"""

    ticker: str
    market: Market
    quantity: int
    avg_purchase_price: float  # 평균 매입가
    current_price: float  # 현재가
    sector: str = ""

    @property
    def purchase_value(self) -> float:
        """매입 금액"""
        return self.quantity * self.avg_purchase_price

    @property
    def current_value(self) -> float:
        """현재 평가 금액"""
        return self.quantity * self.current_price

    @property
    def pnl(self) -> float:
        """평가 손익"""
        return self.current_value - self.purchase_value

    @property
    def pnl_percent(self) -> float:
        """평가 손익률"""
        if self.purchase_value <= 0:
            return 0.0
        return self.pnl / self.purchase_value

    @property
    def weight(self) -> float:
        """비중 (총 자산 대비) - 외부에서 설정 필요"""
        return 0.0  # 포트폴리오 컨텍스트에서 계산


@dataclass
class PortfolioLossReport:
    """포트폴리오 손실 분석 결과"""

    total_purchase_value: float = 0.0  # 총 매입 금액
    total_current_value: float = 0.0  # 총 현재 평가금
    total_pnl: float = 0.0  # 총 평가 손익
    loss_percent: float = 0.0  # 손실률 (음수)
    user_threshold: float = 0.0  # 사용자 설정 임계값
    algo_threshold: float = 0.0  # 알고리즘 추천 임계값
    user_triggered: bool = False  # 사용자 기준 초과 여부
    algo_triggered: bool = False  # 알고리즘 기준 초과 여부
    worst_positions: list[PositionSnapshot] = field(default_factory=list)
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_triggered(self) -> bool:
        """트리거 발동 여부"""
        return self.user_triggered or self.algo_triggered

    @property
    def trigger_reason(self) -> str:
        """트리거 사유"""
        reasons = []
        if self.user_triggered:
            reasons.append(f"사용자 설정 손실 한도 초과 " f"({self.loss_percent:.2%} < {self.user_threshold:.2%})")
        if self.algo_triggered:
            reasons.append(f"알고리즘 추천 손절 기준 초과 " f"({self.loss_percent:.2%} < {self.algo_threshold:.2%})")
        return " / ".join(reasons) if reasons else ""


@dataclass
class EmergencyMonitorState:
    """비상 모니터 상태"""

    is_running: bool = False
    is_paused: bool = False
    last_check_at: Optional[datetime] = None
    check_count: int = 0
    trigger_count: int = 0
    last_trigger_at: Optional[datetime] = None
    cooldown_until: Optional[datetime] = None  # 연속 트리거 방지

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_running": self.is_running,
            "is_paused": self.is_paused,
            "last_check_at": self.last_check_at.isoformat() if self.last_check_at else None,
            "check_count": self.check_count,
            "trigger_count": self.trigger_count,
            "last_trigger_at": self.last_trigger_at.isoformat() if self.last_trigger_at else None,
            "cooldown_until": self.cooldown_until.isoformat() if self.cooldown_until else None,
        }


# ══════════════════════════════════════
# 비상 리밸런싱 모니터
# ══════════════════════════════════════
class EmergencyRebalancingMonitor:
    """
    비상 리밸런싱 모니터 (F-05-04)

    장중 5분 간격으로 포트폴리오 손실률을 확인하고,
    사용자 설정 또는 알고리즘 추천 임계값 초과 시
    비상 리밸런싱을 자동 트리거합니다.

    트리거 조건:
      1. 사용자 설정 손실 감내 수준 초과 (loss_tolerance)
      2. 알고리즘 추천 손절매 기준 초과 (변동성 기반 동적 계산)

    처리 흐름:
      손실률 계산 → 임계값 비교 → 트리거 발동
      → 방어 포트폴리오 생성 → 주문 체결 → 알림 발송

    안전 장치:
      - 트리거 쿨다운 (30분): 연속 트리거 방지
      - 장중 시간만 모니터링 (09:00~15:30 KST)
      - Kill Switch 활성화 시 모니터링 중단
    """

    # 모니터링 간격 (초)
    CHECK_INTERVAL_SECONDS = 300  # 5분

    # 트리거 쿨다운 (분)
    TRIGGER_COOLDOWN_MINUTES = 30

    # 장중 시간 (KST)
    MARKET_OPEN = time(9, 0, 0)
    MARKET_CLOSE = time(15, 30, 0)

    # 방어 포트폴리오 주식 비중 축소율
    DEFENSIVE_STOCK_RATIO = 0.3  # 현재 비중의 30%로 축소

    def __init__(
        self,
        kis_client=None,
        telegram_notifier=None,
        order_executor=None,
        trading_guard=None,
        rebalancing_engine=None,
    ):
        """
        비상 리밸런싱 모니터 초기화

        Args:
            kis_client: KIS API 클라이언트 (잔고/시세 조회)
            telegram_notifier: 텔레그램 알림 발송기
            order_executor: 주문 실행기
            trading_guard: 트레이딩 안전 장치
            rebalancing_engine: 리밸런싱 엔진
        """
        self._settings = get_settings()
        self._kis_client = kis_client
        self._telegram = telegram_notifier
        self._order_executor = order_executor
        self._trading_guard = trading_guard
        self._rebalancing_engine = rebalancing_engine
        self._state = EmergencyMonitorState()
        self._task: Optional[asyncio.Task] = None

    @property
    def state(self) -> EmergencyMonitorState:
        return self._state

    # ══════════════════════════════════════
    # 모니터 수명주기
    # ══════════════════════════════════════
    async def start(self) -> None:
        """비상 모니터링 시작"""
        if self._state.is_running:
            logger.warning("Emergency monitor already running")
            return

        self._state.is_running = True
        self._state.is_paused = False
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info(f"Emergency rebalancing monitor started " f"(interval={self.CHECK_INTERVAL_SECONDS}s)")

    async def stop(self) -> None:
        """비상 모니터링 중지"""
        self._state.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Emergency rebalancing monitor stopped")

    def pause(self) -> None:
        """모니터링 일시 정지"""
        self._state.is_paused = True
        logger.info("Emergency monitor paused")

    def resume(self) -> None:
        """모니터링 재개"""
        self._state.is_paused = False
        logger.info("Emergency monitor resumed")

    # ══════════════════════════════════════
    # 핵심 모니터링 루프
    # ══════════════════════════════════════
    async def _monitor_loop(self) -> None:
        """
        5분 간격 모니터링 루프

        장중 시간(09:00~15:30 KST)에만 실행되며,
        Kill Switch 활성화 시 자동 중단됩니다.
        """
        while self._state.is_running:
            try:
                await asyncio.sleep(self.CHECK_INTERVAL_SECONDS)

                # 일시 정지 상태
                if self._state.is_paused:
                    continue

                # Kill Switch 확인
                if self._trading_guard and self._trading_guard.state.kill_switch_on:
                    logger.debug("Emergency monitor skipped: Kill Switch active")
                    continue

                # 장중 시간 확인
                if not self._is_market_hours():
                    continue

                # 쿨다운 확인
                if self._is_in_cooldown():
                    logger.debug("Emergency monitor skipped: in cooldown")
                    continue

                # 손실률 체크 실행
                await self.run_check()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Emergency monitor loop error: {e}")
                await asyncio.sleep(60)  # 오류 시 1분 대기 후 재시도

    async def run_check(self) -> Optional[PortfolioLossReport]:
        """
        단일 손실률 체크 실행

        수동 호출도 가능합니다 (스케줄러 또는 API에서 호출).

        Returns:
            PortfolioLossReport (트리거 여부 포함)
        """
        try:
            # 1. 현재 포지션 조회
            positions = await self._fetch_current_positions()
            if not positions:
                logger.debug("No positions to monitor")
                return None

            # 2. 손실률 계산
            report = self._calculate_loss(positions)

            # 3. 상태 업데이트
            self._state.last_check_at = datetime.now(timezone.utc)
            self._state.check_count += 1

            # 4. 트리거 확인 및 처리
            if report.is_triggered:
                logger.warning(f"Emergency trigger fired: {report.trigger_reason}")
                await self._handle_trigger(report, positions)

            return report

        except Exception as e:
            logger.error(f"Emergency check failed: {e}")
            return None

    # ══════════════════════════════════════
    # 포지션 조회
    # ══════════════════════════════════════
    async def _fetch_current_positions(self) -> list[PositionSnapshot]:
        """
        KIS API를 통해 현재 포지션을 조회합니다.

        Returns:
            PositionSnapshot 리스트
        """
        positions = []

        if not self._kis_client:
            logger.debug("KIS client not available, loading from DB")
            return await self._load_positions_from_db()

        try:
            # 한국 잔고 조회
            kr_balance = await self._kis_client.get_kr_balance()
            if kr_balance and "output1" in kr_balance:
                for item in kr_balance["output1"]:
                    qty = int(item.get("hldg_qty", "0"))
                    if qty <= 0:
                        continue
                    positions.append(
                        PositionSnapshot(
                            ticker=item.get("pdno", ""),
                            market=Market.KRX,
                            quantity=qty,
                            avg_purchase_price=float(item.get("pchs_avg_pric", "0")),
                            current_price=float(item.get("prpr", "0")),
                        )
                    )

            # 미국 잔고 조회
            us_balance = await self._kis_client.get_us_balance()
            if us_balance and "output1" in us_balance:
                for item in us_balance["output1"]:
                    qty = int(float(item.get("hldg_qty", "0")))
                    if qty <= 0:
                        continue
                    positions.append(
                        PositionSnapshot(
                            ticker=item.get("pdno", ""),
                            market=Market.NYSE,
                            quantity=qty,
                            avg_purchase_price=float(item.get("pchs_avg_pric", "0")),
                            current_price=float(item.get("now_pric2", "0")),
                        )
                    )

        except Exception as e:
            logger.error(f"Failed to fetch positions from KIS: {e}")
            return await self._load_positions_from_db()

        return positions

    async def _load_positions_from_db(self) -> list[PositionSnapshot]:
        """DB에서 최근 포지션 스냅샷을 로드합니다 (폴백)"""
        try:
            async with async_session_factory() as session:
                query = text(
                    """
                    SELECT ticker, market, quantity, avg_purchase_price,
                           current_price, sector
                    FROM positions
                    WHERE quantity > 0
                    ORDER BY current_price * quantity DESC
                """
                )
                result = await session.execute(query)
                rows = result.fetchall()
                return [
                    PositionSnapshot(
                        ticker=row[0],
                        market=Market(row[1]) if row[1] else Market.KRX,
                        quantity=int(row[2]),
                        avg_purchase_price=float(row[3]),
                        current_price=float(row[4]),
                        sector=row[5] or "",
                    )
                    for row in rows
                ]
        except Exception as e:
            logger.debug(f"Failed to load positions from DB: {e}")
            return []

    # ══════════════════════════════════════
    # 손실률 계산
    # ══════════════════════════════════════
    def _calculate_loss(
        self,
        positions: list[PositionSnapshot],
    ) -> PortfolioLossReport:
        """
        매입가 기반 정확한 포트폴리오 손실률을 계산합니다.

        사용자 설정 임계값과 알고리즘 추천 임계값을
        모두 비교하여 트리거 여부를 판정합니다.

        Args:
            positions: 현재 포지션 스냅샷 리스트

        Returns:
            PortfolioLossReport
        """
        total_purchase = sum(p.purchase_value for p in positions)
        total_current = sum(p.current_value for p in positions)
        total_pnl = total_current - total_purchase

        # 손실률 계산 (음수 = 손실)
        loss_pct = total_pnl / total_purchase if total_purchase > 0 else 0.0

        # 사용자 설정 임계값 (예: -0.10 → -10%)
        user_threshold = self._settings.risk.stop_loss_percent

        # 알고리즘 추천 임계값 (변동성 기반 동적 계산)
        algo_threshold = self._calculate_algo_threshold(positions)

        # 임계값 비교 (loss_pct는 음수, threshold도 음수)
        user_triggered = loss_pct <= user_threshold if user_threshold < 0 else False
        algo_triggered = loss_pct <= algo_threshold if algo_threshold < 0 else False

        # 최악 포지션 (손실률 기준 하위 3개)
        worst = sorted(positions, key=lambda p: p.pnl_percent)[:3]

        report = PortfolioLossReport(
            total_purchase_value=total_purchase,
            total_current_value=total_current,
            total_pnl=total_pnl,
            loss_percent=loss_pct,
            user_threshold=user_threshold,
            algo_threshold=algo_threshold,
            user_triggered=user_triggered,
            algo_triggered=algo_triggered,
            worst_positions=worst,
        )

        logger.debug(
            f"Loss check: PnL={loss_pct:.2%}, "
            f"user_thresh={user_threshold:.2%}, "
            f"algo_thresh={algo_threshold:.2%}, "
            f"triggered={report.is_triggered}"
        )

        return report

    def _calculate_algo_threshold(
        self,
        positions: list[PositionSnapshot],
    ) -> float:
        """
        알고리즘 추천 동적 손절 기준을 계산합니다.

        포트폴리오 가중 변동성을 기반으로 적응적 임계값을 설정합니다.
        고변동성 포트폴리오 → 넓은 허용 범위, 저변동성 → 좁은 허용 범위

        계산 방식:
          - 각 포지션의 손익률 분산으로 포트폴리오 변동성 추정
          - 기본 임계값 = -2σ (95% 신뢰구간)
          - 최소 -5%, 최대 -25% 범위로 클램핑

        Args:
            positions: 현재 포지션 리스트

        Returns:
            알고리즘 추천 손절 기준 (음수, 예: -0.15)
        """
        if not positions:
            return self._settings.risk.stop_loss_percent

        # 포지션별 수익률을 포트폴리오 가중치로 가중
        total_value = sum(p.current_value for p in positions)
        if total_value <= 0:
            return self._settings.risk.stop_loss_percent

        # 가중 수익률 및 분산 계산
        weighted_returns = []
        for p in positions:
            weight = p.current_value / total_value
            weighted_returns.append(p.pnl_percent * weight)

        portfolio_return = sum(weighted_returns)

        # 분산 계산 (포지션 수익률 편차의 가중 합)
        if len(positions) > 1:
            variance = sum((p.pnl_percent - portfolio_return) ** 2 * (p.current_value / total_value) for p in positions)
            volatility = math.sqrt(variance)
        else:
            # 단일 포지션: 절대 수익률의 10%를 변동성으로 추정
            volatility = abs(positions[0].pnl_percent) * 0.1 + 0.05

        # 동적 임계값: -2σ (95% 신뢰구간)
        dynamic_threshold = -(2.0 * volatility)

        # 클램핑: 최소 -5%, 최대 -25%
        clamped = max(-0.25, min(-0.05, dynamic_threshold))

        logger.debug(f"Algo threshold: vol={volatility:.4f}, " f"raw={dynamic_threshold:.4f}, clamped={clamped:.4f}")

        return clamped

    # ══════════════════════════════════════
    # 트리거 처리
    # ══════════════════════════════════════
    async def _handle_trigger(
        self,
        report: PortfolioLossReport,
        positions: list[PositionSnapshot],
    ) -> None:
        """
        트리거 발동 시 비상 리밸런싱을 실행합니다.

        처리 흐름:
          1. 방어 포트폴리오 생성 (주식 비중 30% 축소)
          2. 매도 주문 생성
          3. 투자 스타일별 처리 (일임형: 자동 체결, 자문형: 알림만)
          4. 텔레그램 비상 알림 발송
          5. DB 기록
          6. 쿨다운 설정

        Args:
            report: 손실 분석 결과
            positions: 현재 포지션 리스트
        """
        self._state.trigger_count += 1
        self._state.last_trigger_at = datetime.now(timezone.utc)

        try:
            # 1. 매도 주문 생성 (각 포지션의 70% 매도)
            sell_orders = self._generate_defensive_orders(positions)

            # 2. 투자 스타일별 처리
            investment_style = self._get_investment_style()

            if investment_style == InvestmentStyle.DISCRETIONARY:
                # 일임형: 자동 체결
                await self._execute_defensive_orders(sell_orders)
            else:
                # 자문형: 알림만 발송 (사용자 승인 대기)
                logger.info(f"Advisory mode: {len(sell_orders)} defensive orders pending approval")

            # 3. 텔레그램 비상 알림
            await self._send_emergency_alert(report, sell_orders)

            # 4. DB 기록
            await self._record_emergency_event(report, sell_orders)

            # 5. 쿨다운 설정
            self._state.cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=self.TRIGGER_COOLDOWN_MINUTES)

            logger.warning(
                f"Emergency rebalancing processed: "
                f"{len(sell_orders)} orders, "
                f"cooldown until {self._state.cooldown_until.isoformat()}"
            )

        except Exception as e:
            logger.error(f"Emergency trigger handling failed: {e}")
            # 실패해도 텔레그램 알림은 시도
            await self._send_error_alert(str(e))

    def _generate_defensive_orders(
        self,
        positions: list[PositionSnapshot],
    ) -> list[dict[str, Any]]:
        """
        방어 포트폴리오를 위한 매도 주문을 생성합니다.

        각 포지션의 70%를 매도하여 현금 비중을 확대합니다.
        (현재 비중의 30%만 유지)

        Args:
            positions: 현재 포지션 리스트

        Returns:
            매도 주문 리스트
        """
        orders = []
        for pos in positions:
            sell_qty = int(pos.quantity * (1.0 - self.DEFENSIVE_STOCK_RATIO))
            if sell_qty <= 0:
                continue

            orders.append(
                {
                    "ticker": pos.ticker,
                    "market": pos.market.value,
                    "side": OrderSide.SELL.value,
                    "quantity": sell_qty,
                    "order_type": OrderType.MARKET.value,
                    "reason": (f"비상 리밸런싱: {pos.pnl_percent:.2%} 손실, " f"{pos.quantity}주 중 {sell_qty}주 매도"),
                    "current_price": pos.current_price,
                    "estimated_amount": sell_qty * pos.current_price,
                }
            )

        # 예상 매도 금액 기준 내림차순 정렬 (큰 포지션 우선)
        orders.sort(key=lambda o: -o["estimated_amount"])
        return orders

    async def _execute_defensive_orders(
        self,
        orders: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        방어 주문을 OrderExecutor를 통해 실행합니다.

        Args:
            orders: 매도 주문 리스트

        Returns:
            체결 결과 리스트
        """
        results = []

        if not self._order_executor:
            logger.warning("OrderExecutor not available, orders not executed")
            return results

        for order_data in orders:
            try:
                from core.order_executor.executor import OrderRequest

                request = OrderRequest(
                    ticker=order_data["ticker"],
                    market=Market(order_data["market"]),
                    side=OrderSide.SELL,
                    quantity=order_data["quantity"],
                    order_type=OrderType.MARKET,
                    reason=order_data["reason"],
                )

                result = await self._order_executor.execute_order(request)
                results.append({"order": order_data, "result": result})

                logger.info(f"Emergency order executed: {order_data['ticker']} " f"SELL {order_data['quantity']}")

                # 주문 간 200ms 딜레이 (API Rate Limit)
                await asyncio.sleep(0.2)

            except Exception as e:
                logger.error(f"Emergency order failed: {order_data['ticker']}: {e}")
                results.append({"order": order_data, "error": str(e)})

        return results

    # ══════════════════════════════════════
    # 알림 발송
    # ══════════════════════════════════════
    async def _send_emergency_alert(
        self,
        report: PortfolioLossReport,
        orders: list[dict[str, Any]],
    ) -> None:
        """
        텔레그램 비상 리밸런싱 알림을 발송합니다.

        Args:
            report: 손실 분석 결과
            orders: 생성된 방어 주문 리스트
        """
        if not self._telegram:
            logger.warning("TelegramNotifier not available, alert not sent")
            return

        try:
            message = self._format_emergency_message(report, orders)
            await self._telegram.send_message(message, parse_mode="HTML")
            logger.info("Emergency alert sent via Telegram")
        except Exception as e:
            logger.error(f"Failed to send emergency alert: {e}")

    async def _send_error_alert(self, error_message: str) -> None:
        """비상 처리 실패 시 오류 알림"""
        if not self._telegram:
            return
        try:
            await self._telegram.send_error_alert(
                module="EmergencyRebalancingMonitor",
                error_message=error_message,
            )
        except Exception:
            pass  # 이중 실패 방지

    def _format_emergency_message(
        self,
        report: PortfolioLossReport,
        orders: list[dict[str, Any]],
    ) -> str:
        """
        비상 리밸런싱 텔레그램 메시지를 포맷팅합니다.

        Args:
            report: 손실 분석 결과
            orders: 방어 주문 리스트

        Returns:
            HTML 형식 텔레그램 메시지
        """
        # 트리거 유형
        trigger_icon = "🚨" if report.user_triggered else "⚠️"
        trigger_type = "사용자 설정" if report.user_triggered else "알고리즘 추천"

        # 주문 요약
        total_sell_amount = sum(o.get("estimated_amount", 0) for o in orders)
        order_summary = "\n".join(
            f"  • {o['ticker']} {o['quantity']}주 매도 " f"(≈{o['estimated_amount']:,.0f}원)" for o in orders[:5]
        )
        if len(orders) > 5:
            order_summary += f"\n  ... 외 {len(orders) - 5}건"

        # 최악 포지션
        worst_summary = "\n".join(
            f"  • {p.ticker}: {p.pnl_percent:+.2%} ({p.pnl:+,.0f}원)" for p in report.worst_positions[:3]
        )

        message = (
            f"{trigger_icon} <b>비상 리밸런싱 트리거</b>\n"
            f"<code>━━━━━━━━━━━━━━━━━━━━</code>\n\n"
            f"<b>트리거 유형:</b> {trigger_type}\n"
            f"<b>현재 손실률:</b> <code>{report.loss_percent:+.2%}</code>\n"
            f"<b>사용자 한도:</b> <code>{report.user_threshold:.2%}</code>\n"
            f"<b>알고리즘 한도:</b> <code>{report.algo_threshold:.2%}</code>\n\n"
            f"<b>📊 포트폴리오 현황</b>\n"
            f"  매입가 합계: {report.total_purchase_value:,.0f}원\n"
            f"  현재 평가액: {report.total_current_value:,.0f}원\n"
            f"  평가 손익: <code>{report.total_pnl:+,.0f}원</code>\n\n"
            f"<b>📉 최악 포지션</b>\n{worst_summary}\n\n"
            f"<b>🛡️ 방어 주문 ({len(orders)}건)</b>\n{order_summary}\n"
            f"  총 매도 예정액: {total_sell_amount:,.0f}원\n\n"
            f"<i>{report.checked_at.strftime('%Y-%m-%d %H:%M:%S UTC')}</i>"
        )

        return message

    # ══════════════════════════════════════
    # DB 기록
    # ══════════════════════════════════════
    async def _record_emergency_event(
        self,
        report: PortfolioLossReport,
        orders: list[dict[str, Any]],
    ) -> None:
        """비상 리밸런싱 이벤트를 DB에 기록합니다."""
        try:
            import json

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
                        "user_id": "default",
                        "type": RebalancingType.EMERGENCY.value,
                        "reason": report.trigger_reason,
                        "orders": json.dumps(orders, default=str),
                        "old_summary": json.dumps(
                            {
                                "total_value": report.total_current_value,
                                "loss_percent": report.loss_percent,
                                "position_count": len(report.worst_positions),
                            }
                        ),
                        "new_summary": json.dumps(
                            {
                                "defensive_mode": True,
                                "stock_ratio": self.DEFENSIVE_STOCK_RATIO,
                                "order_count": len(orders),
                            }
                        ),
                        "executed_at": datetime.now(timezone.utc),
                    },
                )
                await session.commit()
        except Exception as e:
            logger.debug(f"Failed to record emergency event: {e}")

    # ══════════════════════════════════════
    # 유틸리티
    # ══════════════════════════════════════
    def _is_market_hours(self) -> bool:
        """현재 시간이 장중인지 확인 (KST 기준)"""
        from datetime import timezone as tz

        kst = tz(timedelta(hours=9))
        now_kst = datetime.now(kst)

        # 주말 제외
        if now_kst.weekday() >= 5:
            return False

        current_time = now_kst.time()
        return self.MARKET_OPEN <= current_time <= self.MARKET_CLOSE

    def _is_in_cooldown(self) -> bool:
        """트리거 쿨다운 중인지 확인"""
        if self._state.cooldown_until is None:
            return False
        return datetime.now(timezone.utc) < self._state.cooldown_until

    def _get_investment_style(self) -> InvestmentStyle:
        """현재 투자 스타일을 반환합니다."""
        if self._rebalancing_engine and hasattr(self._rebalancing_engine, "profile"):
            return self._rebalancing_engine.profile.investment_style
        return InvestmentStyle.ADVISORY  # 기본: 자문형 (안전)
