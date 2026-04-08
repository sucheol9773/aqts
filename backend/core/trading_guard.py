"""
트레이딩 안전 장치 (Trading Guard)

Phase 6: 실투자 전환을 위한 다층 안전 메커니즘

보호 계층:
  1. 환경 검증 (production + LIVE 모드 확인)
  2. 자본금 검증 (최소 자본금 확인)
  3. 일일 손실 한도 서킷브레이커
  4. 최대 낙폭 서킷브레이커
  5. 연속 손실 제한
  6. 주문별 사전 검증 (포지션 비중·섹터 비중·금액 한도)
  7. 긴급 정지 (Kill Switch)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Optional

from config.constants import PORTFOLIO_CONSTRAINTS, OrderSide
from config.logging import logger
from config.settings import get_settings
from core.monitoring.metrics import TRADING_GUARD_KILL_SWITCH_ACTIVE


@dataclass
class TradingGuardState:
    """트레이딩 안전 장치 상태"""

    is_active: bool = True
    kill_switch_on: bool = False
    kill_switch_reason: str = ""
    daily_realized_pnl: float = 0.0
    daily_order_count: int = 0
    consecutive_losses: int = 0
    current_drawdown: float = 0.0
    peak_portfolio_value: float = 0.0
    current_portfolio_value: float = 0.0
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "is_active": self.is_active,
            "kill_switch_on": self.kill_switch_on,
            "kill_switch_reason": self.kill_switch_reason,
            "daily_realized_pnl": self.daily_realized_pnl,
            "daily_order_count": self.daily_order_count,
            "consecutive_losses": self.consecutive_losses,
            "current_drawdown": round(self.current_drawdown, 4),
            "peak_portfolio_value": self.peak_portfolio_value,
            "current_portfolio_value": self.current_portfolio_value,
            "last_updated": self.last_updated.isoformat(),
        }


@dataclass
class PreOrderCheckResult:
    """주문 사전 검증 결과"""

    allowed: bool
    reason: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "warnings": self.warnings,
        }


class TradingGuard:
    """
    트레이딩 안전 장치

    주문 실행 전·후에 호출되어 위험 한도를 실시간 모니터링합니다.
    한도 초과 시 거래를 차단하고 긴급 알림을 발행합니다.
    """

    def __init__(self):
        self._settings = get_settings()
        self._risk = self._settings.risk
        self._state = TradingGuardState(
            peak_portfolio_value=self._risk.initial_capital_krw,
            current_portfolio_value=self._risk.initial_capital_krw,
        )

    @property
    def state(self) -> TradingGuardState:
        return self._state

    # ══════════════════════════════════════
    # 1. 환경 검증
    # ══════════════════════════════════════
    def verify_environment(self) -> PreOrderCheckResult:
        """LIVE 모드 진입 전 환경 검증"""
        warnings = []

        # LIVE 모드는 production 환경에서만 허용
        if self._settings.kis.is_live and not self._settings.is_production:
            return PreOrderCheckResult(
                allowed=False,
                reason="LIVE 모드는 production 환경에서만 허용됩니다.",
            )

        # LIVE 모드 자격증명 확인
        if self._settings.kis.is_live:
            cred = self._settings.kis.active_credential
            if not cred.app_key or not cred.app_secret:
                return PreOrderCheckResult(
                    allowed=False,
                    reason="LIVE 모드 API 키가 설정되지 않았습니다.",
                )
            if not cred.account_no:
                return PreOrderCheckResult(
                    allowed=False,
                    reason="LIVE 모드 계좌번호가 설정되지 않았습니다.",
                )

            # LIVE와 DEMO 자격증명 교차 확인
            if (
                self._settings.kis.live_app_key == self._settings.kis.demo_app_key
                and self._settings.kis.live_app_key != ""
            ):
                warnings.append("LIVE와 DEMO API 키가 동일합니다. 확인해 주세요.")

        return PreOrderCheckResult(allowed=True, warnings=warnings)

    # ══════════════════════════════════════
    # 2. 자본금 검증
    # ══════════════════════════════════════
    def verify_capital(self, current_balance: float) -> PreOrderCheckResult:
        """현재 잔고가 최소 요건을 충족하는지 확인"""
        min_capital = self._risk.initial_capital_krw * 0.1  # 초기 자본금의 10%

        if current_balance < min_capital:
            return PreOrderCheckResult(
                allowed=False,
                reason=(f"잔고 부족: {current_balance:,.0f}원 < " f"최소 요구 자본금 {min_capital:,.0f}원"),
            )
        return PreOrderCheckResult(allowed=True)

    # ══════════════════════════════════════
    # 3. 서킷브레이커 (일일 손실)
    # ══════════════════════════════════════
    def check_daily_loss_limit(self) -> PreOrderCheckResult:
        """일일 손실 한도 확인"""
        if self._state.daily_realized_pnl <= -self._risk.daily_loss_limit_krw:
            self._activate_kill_switch(f"일일 손실 한도 도달: {self._state.daily_realized_pnl:,.0f}원")
            return PreOrderCheckResult(
                allowed=False,
                reason=f"일일 손실 한도 초과: {self._state.daily_realized_pnl:,.0f}원",
            )
        return PreOrderCheckResult(allowed=True)

    # ══════════════════════════════════════
    # 4. 서킷브레이커 (최대 낙폭)
    # ══════════════════════════════════════
    def check_max_drawdown(self) -> PreOrderCheckResult:
        """최대 낙폭(MDD) 한도 확인"""
        if self._state.peak_portfolio_value > 0:
            dd = (
                self._state.peak_portfolio_value - self._state.current_portfolio_value
            ) / self._state.peak_portfolio_value
            self._state.current_drawdown = dd

            if dd >= self._risk.max_drawdown:
                self._activate_kill_switch(f"최대 낙폭 한도 도달: {dd:.2%}")
                return PreOrderCheckResult(
                    allowed=False,
                    reason=f"MDD 한도 초과: {dd:.2%} >= {self._risk.max_drawdown:.2%}",
                )
        return PreOrderCheckResult(allowed=True)

    # ══════════════════════════════════════
    # 5. 연속 손실 제한
    # ══════════════════════════════════════
    def check_consecutive_losses(self) -> PreOrderCheckResult:
        """연속 손실 횟수 확인"""
        if self._state.consecutive_losses >= self._risk.consecutive_loss_limit:
            self._activate_kill_switch(f"연속 손실 한도 도달: {self._state.consecutive_losses}회")
            return PreOrderCheckResult(
                allowed=False,
                reason=(
                    f"연속 손실 한도 초과: "
                    f"{self._state.consecutive_losses}회 >= "
                    f"{self._risk.consecutive_loss_limit}회"
                ),
            )
        return PreOrderCheckResult(allowed=True)

    # ══════════════════════════════════════
    # 6. 주문 사전 검증
    # ══════════════════════════════════════
    def pre_order_check(
        self,
        order_amount_krw: float,
        ticker: str,
        side: OrderSide,
        current_position_weight: float = 0.0,
        current_sector_weight: float = 0.0,
        new_position_weight: float = 0.0,
        new_sector_weight: float = 0.0,
    ) -> PreOrderCheckResult:
        """
        주문 실행 전 종합 사전 검증

        Args:
            order_amount_krw: 주문 금액 (원)
            ticker: 종목코드
            side: 주문 방향
            current_position_weight: 현재 해당 종목 비중
            current_sector_weight: 현재 섹터 비중
            new_position_weight: 주문 후 예상 종목 비중
            new_sector_weight: 주문 후 예상 섹터 비중

        Returns:
            PreOrderCheckResult
        """
        warnings = []

        # Kill Switch 확인
        if self._state.kill_switch_on:
            return PreOrderCheckResult(
                allowed=False,
                reason=f"Kill Switch 활성화됨: {self._state.kill_switch_reason}",
            )

        # 일일 손실 한도
        check = self.check_daily_loss_limit()
        if not check.allowed:
            return check

        # 최대 낙폭
        check = self.check_max_drawdown()
        if not check.allowed:
            return check

        # 연속 손실
        check = self.check_consecutive_losses()
        if not check.allowed:
            return check

        # 매수 주문 추가 검증
        if side == OrderSide.BUY:
            # 주문 금액 한도
            if order_amount_krw > self._risk.max_order_amount_krw:
                return PreOrderCheckResult(
                    allowed=False,
                    reason=(
                        f"주문 금액 초과: {order_amount_krw:,.0f}원 > " f"한도 {self._risk.max_order_amount_krw:,.0f}원"
                    ),
                )

            # 종목별 비중 한도
            max_weight = PORTFOLIO_CONSTRAINTS["max_single_weight"]
            if new_position_weight > max_weight:
                return PreOrderCheckResult(
                    allowed=False,
                    reason=(f"종목 비중 초과: {ticker} " f"{new_position_weight:.1%} > {max_weight:.1%}"),
                )

            # 섹터 비중 한도
            max_sector = PORTFOLIO_CONSTRAINTS["max_sector_weight"]
            if new_sector_weight > max_sector:
                warnings.append(f"섹터 비중 경고: {new_sector_weight:.1%} > {max_sector:.1%}")

        return PreOrderCheckResult(allowed=True, warnings=warnings)

    # ══════════════════════════════════════
    # 7. 거래 결과 업데이트
    # ══════════════════════════════════════
    def record_trade_result(self, pnl: float, portfolio_value: float) -> None:
        """
        거래 결과를 기록하고 상태를 업데이트합니다.

        Args:
            pnl: 실현 손익 (원)
            portfolio_value: 현재 포트폴리오 총 가치 (원)
        """
        self._state.daily_realized_pnl += pnl
        self._state.daily_order_count += 1
        self._state.current_portfolio_value = portfolio_value
        self._state.last_updated = datetime.now(timezone.utc)

        # 고점 갱신
        if portfolio_value > self._state.peak_portfolio_value:
            self._state.peak_portfolio_value = portfolio_value

        # 연속 손실 추적
        if pnl < 0:
            self._state.consecutive_losses += 1
        else:
            self._state.consecutive_losses = 0

        logger.debug(
            f"Trade recorded: PnL={pnl:+,.0f}, "
            f"daily={self._state.daily_realized_pnl:+,.0f}, "
            f"consecutive_losses={self._state.consecutive_losses}"
        )

    def reset_daily_state(self) -> None:
        """일일 상태 리셋 (장 시작 시 호출)"""
        self._state.daily_realized_pnl = 0.0
        self._state.daily_order_count = 0
        self._state.last_updated = datetime.now(timezone.utc)
        logger.info("Trading guard daily state reset")

    # ══════════════════════════════════════
    # Kill Switch
    # ══════════════════════════════════════
    def _activate_kill_switch(self, reason: str) -> None:
        """긴급 정지 활성화"""
        self._state.kill_switch_on = True
        self._state.kill_switch_reason = reason
        TRADING_GUARD_KILL_SWITCH_ACTIVE.set(1)
        logger.critical(f"KILL SWITCH ACTIVATED: {reason}")

    def deactivate_kill_switch(self) -> None:
        """긴급 정지 해제 (관리자 수동 조작)"""
        self._state.kill_switch_on = False
        self._state.kill_switch_reason = ""
        TRADING_GUARD_KILL_SWITCH_ACTIVE.set(0)
        logger.warning("Kill switch deactivated manually")

    def activate_kill_switch(self, reason: str) -> None:
        """외부에서 긴급 정지 활성화"""
        self._activate_kill_switch(reason)

    # ══════════════════════════════════════
    # P0-5: OrderExecutor 진입점 — 단일 주문에 대한 종합 pre-check.
    # 인자를 scalar 로 받아서 OrderRequest 와의 순환 import 를 피한다.
    # ══════════════════════════════════════
    def check_pre_order(
        self,
        ticker: str,
        side: OrderSide,
        quantity: int,
        limit_price: Optional[float] = None,
    ) -> PreOrderCheckResult:
        """
        OrderExecutor 가 실제 주문 전에 호출하는 단일 진입점.

        검증 순서:
          1. kill switch (다른 경로에서 활성화됐을 수 있음)
          2. `run_all_checks()` — 환경/일일손실/MDD/연속손실
          3. BUY + 가격 알려진 경우 주문 금액 한도

        Returns:
            PreOrderCheckResult — 실패 사유는 `reason_code` 대신 한국어
            `reason` 에 담기며, 차단 카운터는 executor 가 증가시킨다.
        """
        # 1) Kill switch — pre_order_check 와 달리 run_all_checks 이전에 명시적 확인.
        if self._state.kill_switch_on:
            return PreOrderCheckResult(
                allowed=False,
                reason=f"Kill Switch 활성화됨: {self._state.kill_switch_reason}",
            )

        # 2) 환경/손실/MDD/연속손실
        base = self.run_all_checks()
        if not base.allowed:
            return base

        # 3) BUY 주문 금액 한도 (limit_price 미지정 MARKET 은 사후 체결가로 검증 불가)
        if side == OrderSide.BUY and limit_price is not None and limit_price > 0:
            order_amount = limit_price * quantity
            if order_amount > self._risk.max_order_amount_krw:
                return PreOrderCheckResult(
                    allowed=False,
                    reason=(
                        f"주문 금액 초과: {order_amount:,.0f}원 > " f"한도 {self._risk.max_order_amount_krw:,.0f}원"
                    ),
                )

        return PreOrderCheckResult(allowed=True, warnings=base.warnings)

    # ══════════════════════════════════════
    # 종합 검증 (All-in-one)
    # ══════════════════════════════════════
    def run_all_checks(self) -> PreOrderCheckResult:
        """모든 안전 검증을 순차 실행"""
        checks = [
            ("환경 검증", self.verify_environment),
            ("일일 손실", self.check_daily_loss_limit),
            ("최대 낙폭", self.check_max_drawdown),
            ("연속 손실", self.check_consecutive_losses),
        ]

        all_warnings = []
        for name, check_fn in checks:
            result = check_fn()
            if not result.allowed:
                return result
            all_warnings.extend(result.warnings)

        return PreOrderCheckResult(allowed=True, warnings=all_warnings)


# ══════════════════════════════════════════════════════════════════════════════
# P0-5: 프로세스 전역 싱글톤
# ══════════════════════════════════════════════════════════════════════════════
# 관리자 API 에서 kill switch 를 활성화했을 때 OrderExecutor 가 즉시 인지해야
# 하므로 한 프로세스 내에서 상태를 공유한다. 여러 OrderExecutor 인스턴스가
# 같은 guard 를 바라보지 않으면 kill switch 가 형식적 통제에 그친다.
_guard_instance: Optional[TradingGuard] = None
_guard_lock = RLock()


def get_trading_guard() -> TradingGuard:
    """프로세스 전역 TradingGuard 싱글톤."""
    global _guard_instance
    with _guard_lock:
        if _guard_instance is None:
            _guard_instance = TradingGuard()
        return _guard_instance


def reset_trading_guard() -> None:
    """테스트 전용: 싱글톤을 재초기화한다. 운영 경로에서는 호출하지 않는다."""
    global _guard_instance
    with _guard_lock:
        _guard_instance = None
        TRADING_GUARD_KILL_SWITCH_ACTIVE.set(0)


class TradingGuardBlocked(Exception):
    """TradingGuard 가 주문 실행을 차단했을 때 executor 가 전파하는 예외."""

    def __init__(self, reason: str, reason_code: str = "unknown") -> None:
        super().__init__(reason)
        self.reason = reason
        self.reason_code = reason_code
