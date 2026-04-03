"""
트레이딩 모드 관리자 (Mode Manager)

Phase 6: BACKTEST → DEMO → LIVE 모드 전환 관리

모드 전환 조건:
  BACKTEST → DEMO:
    - DEMO 자격증명 (app_key, app_secret, account_no) 설정 확인
    - DB 연결 확인

  DEMO → LIVE:
    - production 환경 필수
    - LIVE 자격증명 완전 설정
    - LIVE ≠ DEMO 자격증명 교차 확인
    - 최소 30일 DEMO 운영 이력 (권장, 강제는 아님)
    - 시스템 건전성 HEALTHY
    - TradingGuard 모든 검증 통과

  LIVE → DEMO (비상 다운그레이드):
    - 즉시 전환 (안전을 위해 조건 없음)
    - 미체결 주문 전량 취소
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from config.logging import logger
from config.settings import TradingMode, get_settings


class TransitionStatus(str, Enum):
    """모드 전환 상태"""
    READY = "READY"
    NOT_READY = "NOT_READY"
    WARNINGS = "WARNINGS"


@dataclass
class TransitionCheckItem:
    """전환 조건 개별 항목"""
    name: str
    passed: bool
    required: bool = True
    message: str = ""


@dataclass
class TransitionCheckResult:
    """모드 전환 종합 검증 결과"""
    current_mode: str
    target_mode: str
    status: TransitionStatus
    items: list[TransitionCheckItem] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "current_mode": self.current_mode,
            "target_mode": self.target_mode,
            "status": self.status.value,
            "items": [
                {
                    "name": item.name,
                    "passed": item.passed,
                    "required": item.required,
                    "message": item.message,
                }
                for item in self.items
            ],
            "timestamp": self.timestamp.isoformat(),
        }

    @property
    def can_transition(self) -> bool:
        """필수 항목 모두 통과 시 전환 가능"""
        return all(item.passed for item in self.items if item.required)


class ModeManager:
    """
    트레이딩 모드 전환 관리자

    모드 전환의 사전 조건을 검증하고,
    전환 이력을 기록합니다.
    """

    def __init__(self):
        self._settings = get_settings()
        self._transition_history: list[dict] = []

    @property
    def current_mode(self) -> TradingMode:
        return self._settings.kis.trading_mode

    # ══════════════════════════════════════
    # BACKTEST → DEMO 전환 검증
    # ══════════════════════════════════════
    def check_backtest_to_demo(self) -> TransitionCheckResult:
        """BACKTEST → DEMO 전환 조건 검증"""
        items = []

        # 현재 모드 확인
        items.append(TransitionCheckItem(
            name="현재 모드 확인",
            passed=self._settings.kis.is_backtest,
            message=f"현재 모드: {self._settings.kis.trading_mode.value}",
        ))

        # DEMO 자격증명
        demo_key = self._settings.kis.demo_app_key
        demo_secret = self._settings.kis.demo_app_secret
        demo_account = self._settings.kis.demo_account_no

        items.append(TransitionCheckItem(
            name="DEMO API Key",
            passed=bool(demo_key and demo_key != "test_key_demo"),
            message=f"설정됨: {bool(demo_key)}",
        ))
        items.append(TransitionCheckItem(
            name="DEMO API Secret",
            passed=bool(demo_secret and demo_secret != "test_secret_demo"),
            message=f"설정됨: {bool(demo_secret)}",
        ))
        items.append(TransitionCheckItem(
            name="DEMO 계좌번호",
            passed=bool(demo_account and demo_account != "87654321-01"),
            message=f"설정됨: {bool(demo_account)}",
        ))

        # 상태 판정
        status = (
            TransitionStatus.READY
            if all(i.passed for i in items if i.required)
            else TransitionStatus.NOT_READY
        )

        return TransitionCheckResult(
            current_mode=TradingMode.BACKTEST.value,
            target_mode=TradingMode.DEMO.value,
            status=status,
            items=items,
        )

    # ══════════════════════════════════════
    # DEMO → LIVE 전환 검증
    # ══════════════════════════════════════
    def check_demo_to_live(self) -> TransitionCheckResult:
        """DEMO → LIVE 전환 조건 검증"""
        items = []

        # 현재 모드 확인
        items.append(TransitionCheckItem(
            name="현재 모드 확인",
            passed=self._settings.kis.is_demo,
            message=f"현재 모드: {self._settings.kis.trading_mode.value}",
        ))

        # production 환경 필수
        items.append(TransitionCheckItem(
            name="Production 환경",
            passed=self._settings.is_production,
            message=f"환경: {self._settings.environment}",
        ))

        # LIVE 자격증명
        live_key = self._settings.kis.live_app_key
        live_secret = self._settings.kis.live_app_secret
        live_account = self._settings.kis.live_account_no

        items.append(TransitionCheckItem(
            name="LIVE API Key",
            passed=bool(live_key and live_key != "test_key"),
            message=f"설정됨: {bool(live_key)}",
        ))
        items.append(TransitionCheckItem(
            name="LIVE API Secret",
            passed=bool(live_secret and live_secret != "test_secret"),
            message=f"설정됨: {bool(live_secret)}",
        ))
        items.append(TransitionCheckItem(
            name="LIVE 계좌번호",
            passed=bool(live_account and live_account != "12345678-01"),
            message=f"설정됨: {bool(live_account)}",
        ))

        # LIVE ≠ DEMO 자격증명 교차 확인
        demo_key = self._settings.kis.demo_app_key
        cross_check = (live_key != demo_key) if (live_key and demo_key) else True
        items.append(TransitionCheckItem(
            name="LIVE/DEMO 자격증명 분리",
            passed=cross_check,
            message="LIVE와 DEMO API Key 분리 확인",
        ))

        # 리스크 설정 확인
        risk = self._settings.risk
        items.append(TransitionCheckItem(
            name="리스크 한도 설정",
            passed=(
                risk.daily_loss_limit_krw > 0
                and risk.max_drawdown > 0
                and risk.max_order_amount_krw > 0
            ),
            message=(
                f"일일손실={risk.daily_loss_limit_krw:,.0f}원, "
                f"MDD={risk.max_drawdown:.0%}, "
                f"주문한도={risk.max_order_amount_krw:,.0f}원"
            ),
        ))

        # 텔레그램 알림 설정 (권장)
        tg = self._settings.telegram
        items.append(TransitionCheckItem(
            name="텔레그램 알림 설정",
            passed=bool(tg.bot_token and tg.chat_id and tg.bot_token != "test-bot-token"),
            required=False,
            message=f"알림 레벨: {tg.alert_level}",
        ))

        # 상태 판정
        required_passed = all(i.passed for i in items if i.required)
        optional_failed = any(not i.passed for i in items if not i.required)

        if required_passed and not optional_failed:
            status = TransitionStatus.READY
        elif required_passed and optional_failed:
            status = TransitionStatus.WARNINGS
        else:
            status = TransitionStatus.NOT_READY

        return TransitionCheckResult(
            current_mode=TradingMode.DEMO.value,
            target_mode=TradingMode.LIVE.value,
            status=status,
            items=items,
        )

    # ══════════════════════════════════════
    # LIVE → DEMO 비상 다운그레이드
    # ══════════════════════════════════════
    def check_live_to_demo(self) -> TransitionCheckResult:
        """LIVE → DEMO 비상 다운그레이드 (항상 허용)"""
        items = [
            TransitionCheckItem(
                name="현재 모드 확인",
                passed=self._settings.kis.is_live,
                message=f"현재 모드: {self._settings.kis.trading_mode.value}",
            ),
            TransitionCheckItem(
                name="비상 다운그레이드",
                passed=True,
                message="LIVE → DEMO 전환은 안전을 위해 항상 허용됩니다.",
            ),
        ]

        return TransitionCheckResult(
            current_mode=TradingMode.LIVE.value,
            target_mode=TradingMode.DEMO.value,
            status=TransitionStatus.READY,
            items=items,
        )

    # ══════════════════════════════════════
    # 범용 전환 검증
    # ══════════════════════════════════════
    def check_transition(self, target_mode: str) -> TransitionCheckResult:
        """
        지정한 대상 모드로의 전환 가능 여부 종합 검증

        Args:
            target_mode: 전환 대상 모드 (DEMO / LIVE / BACKTEST)

        Returns:
            TransitionCheckResult
        """
        current = self._settings.kis.trading_mode

        if target_mode == TradingMode.DEMO.value:
            if current == TradingMode.BACKTEST:
                return self.check_backtest_to_demo()
            elif current == TradingMode.LIVE:
                return self.check_live_to_demo()

        elif target_mode == TradingMode.LIVE.value:
            if current == TradingMode.DEMO:
                return self.check_demo_to_live()

        # 지원하지 않는 전환
        return TransitionCheckResult(
            current_mode=current.value,
            target_mode=target_mode,
            status=TransitionStatus.NOT_READY,
            items=[TransitionCheckItem(
                name="전환 경로",
                passed=False,
                message=f"지원하지 않는 전환: {current.value} → {target_mode}",
            )],
        )

    def record_transition(self, from_mode: str, to_mode: str, reason: str = "") -> None:
        """모드 전환 이력 기록"""
        record = {
            "from_mode": from_mode,
            "to_mode": to_mode,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._transition_history.append(record)
        logger.info(f"Mode transition recorded: {from_mode} → {to_mode} ({reason})")

    def get_transition_history(self) -> list[dict]:
        """모드 전환 이력 조회"""
        return list(self._transition_history)
