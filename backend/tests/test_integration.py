"""
AQTS Phase 6 - Integration Tests

실제 모듈 간 상호작용 통합 테스트:
- TradingGuard + ModeManager 연동
- HealthChecker + ModeManager 연동
- TradingGuard + OrderExecutor 연동
- AlertManager + TelegramNotifier 연동
- E2E 시나리오 (거래일, 비상 리밸런싱, 모드 전환)

모든 외부 의존성(DB, Redis, KIS API, Anthropic) mock 처리.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.constants import OrderSide
from config.settings import TradingMode

# ══════════════════════════════════════
# Fixtures
# ══════════════════════════════════════


@pytest.fixture
def demo_settings():
    """Production 환경, DEMO 모드 설정"""
    settings = MagicMock()
    settings.environment = "production"
    settings.is_production = True

    # KIS
    settings.kis.trading_mode = TradingMode.DEMO
    settings.kis.is_live = False
    settings.kis.is_demo = True
    settings.kis.is_backtest = False
    settings.kis.demo_app_key = "real_demo_key"
    settings.kis.demo_app_secret = "real_demo_secret"
    settings.kis.demo_account_no = "99999999-01"
    settings.kis.live_app_key = ""
    settings.kis.live_app_secret = ""
    settings.kis.live_account_no = ""
    cred = MagicMock()
    cred.app_key = "real_demo_key"
    cred.app_secret = "real_demo_secret"
    cred.account_no = "99999999-01"
    settings.kis.active_credential = cred

    # Risk
    settings.risk.initial_capital_krw = 50_000_000
    settings.risk.daily_loss_limit_krw = 5_000_000
    settings.risk.max_order_amount_krw = 10_000_000
    settings.risk.max_drawdown = 0.20
    settings.risk.consecutive_loss_limit = 5
    settings.risk.max_positions = 20

    # Telegram
    settings.telegram.bot_token = "test-bot-token"
    settings.telegram.chat_id = "123456789"
    settings.telegram.alert_level = "IMPORTANT"

    # Dashboard
    settings.dashboard.secret_key = "test-secret-key"

    return settings


@pytest.fixture
def live_settings():
    """Production 환경, LIVE 모드 설정"""
    settings = MagicMock()
    settings.environment = "production"
    settings.is_production = True

    # KIS
    settings.kis.trading_mode = TradingMode.LIVE
    settings.kis.is_live = True
    settings.kis.is_demo = False
    settings.kis.is_backtest = False
    settings.kis.live_app_key = "real_live_key"
    settings.kis.live_app_secret = "real_live_secret"
    settings.kis.live_account_no = "88888888-01"
    settings.kis.demo_app_key = "real_demo_key"
    settings.kis.demo_app_secret = "real_demo_secret"
    settings.kis.demo_account_no = "99999999-01"
    cred = MagicMock()
    cred.app_key = "real_live_key"
    cred.app_secret = "real_live_secret"
    cred.account_no = "88888888-01"
    settings.kis.active_credential = cred

    # Risk
    settings.risk.initial_capital_krw = 50_000_000
    settings.risk.daily_loss_limit_krw = 5_000_000
    settings.risk.max_order_amount_krw = 10_000_000
    settings.risk.max_drawdown = 0.20
    settings.risk.consecutive_loss_limit = 5
    settings.risk.max_positions = 20

    # Telegram
    settings.telegram.bot_token = "real-bot-token"
    settings.telegram.chat_id = "123456789"
    settings.telegram.alert_level = "ALL"

    # Dashboard
    settings.dashboard.secret_key = "real-secret-key"

    return settings


@pytest.fixture
def backtest_settings():
    """Development 환경, BACKTEST 모드 설정"""
    settings = MagicMock()
    settings.environment = "development"
    settings.is_production = False

    settings.kis.trading_mode = TradingMode.BACKTEST
    settings.kis.is_live = False
    settings.kis.is_demo = False
    settings.kis.is_backtest = True
    settings.kis.demo_app_key = "real_demo_key"
    settings.kis.demo_app_secret = "real_demo_secret"
    settings.kis.demo_account_no = "99999999-01"

    settings.risk.initial_capital_krw = 10_000_000
    settings.risk.daily_loss_limit_krw = 1_000_000
    settings.risk.max_order_amount_krw = 2_000_000
    settings.risk.max_drawdown = 0.20
    settings.risk.consecutive_loss_limit = 5

    settings.telegram.bot_token = "test-bot-token"
    settings.telegram.chat_id = "123456789"
    settings.telegram.alert_level = "ALL"

    settings.dashboard.secret_key = "test-secret-key"

    return settings


# ══════════════════════════════════════
# 1. TradingGuard + ModeManager 연동
# ══════════════════════════════════════


class TestTradingGuardModeManagerIntegration:
    """TradingGuard와 ModeManager 간 연동 테스트"""

    @patch("core.trading_guard.get_settings")
    @patch("core.mode_manager.get_settings")
    def test_demo_mode_guard_allows_trading(self, mock_mm_settings, mock_tg_settings, demo_settings):
        """DEMO 모드에서 TradingGuard가 거래를 허용"""
        mock_mm_settings.return_value = demo_settings
        mock_tg_settings.return_value = demo_settings

        from core.mode_manager import ModeManager
        from core.trading_guard import TradingGuard

        guard = TradingGuard()
        manager = ModeManager()

        # ModeManager: 현재 DEMO 모드 확인
        assert manager.current_mode == TradingMode.DEMO

        # TradingGuard: 환경 검증 통과
        env_check = guard.verify_environment()
        assert env_check.allowed is True

        # TradingGuard: 주문 사전 검증 통과
        order_check = guard.pre_order_check(
            order_amount_krw=5_000_000,
            ticker="005930",
            side=OrderSide.BUY,
            new_position_weight=0.10,
            new_sector_weight=0.15,
        )
        assert order_check.allowed is True

    @patch("core.trading_guard.get_settings")
    @patch("core.mode_manager.get_settings")
    def test_live_mode_requires_production_environment(self, mock_mm_settings, mock_tg_settings, live_settings):
        """LIVE 모드 전환 시 production 환경 필수"""
        mock_mm_settings.return_value = live_settings
        mock_tg_settings.return_value = live_settings

        from core.trading_guard import TradingGuard

        guard = TradingGuard()

        # LIVE 모드 + production → 환경 검증 통과
        env_check = guard.verify_environment()
        assert env_check.allowed is True

    @patch("core.trading_guard.get_settings")
    @patch("core.mode_manager.get_settings")
    def test_live_mode_non_production_blocked(self, mock_mm_settings, mock_tg_settings, live_settings):
        """LIVE 모드가 non-production 환경에서 차단"""
        live_settings.is_production = False
        mock_mm_settings.return_value = live_settings
        mock_tg_settings.return_value = live_settings

        from core.trading_guard import TradingGuard

        guard = TradingGuard()
        env_check = guard.verify_environment()
        assert env_check.allowed is False
        assert "production" in env_check.reason

    @patch("core.trading_guard.get_settings")
    @patch("core.mode_manager.get_settings")
    def test_guard_kill_switch_blocks_all_orders(self, mock_mm_settings, mock_tg_settings, demo_settings):
        """Kill Switch 활성화 시 모든 주문 차단"""
        mock_mm_settings.return_value = demo_settings
        mock_tg_settings.return_value = demo_settings

        from core.trading_guard import TradingGuard

        guard = TradingGuard()
        guard.activate_kill_switch("일일 손실 한도 초과")

        order_check = guard.pre_order_check(
            order_amount_krw=1_000_000,
            ticker="005930",
            side=OrderSide.BUY,
        )
        assert order_check.allowed is False
        assert "Kill Switch" in order_check.reason

    @patch("core.trading_guard.get_settings")
    @patch("core.mode_manager.get_settings")
    def test_guard_deactivate_kill_switch_allows_orders(self, mock_mm_settings, mock_tg_settings, demo_settings):
        """Kill Switch 해제 후 주문 다시 허용"""
        mock_mm_settings.return_value = demo_settings
        mock_tg_settings.return_value = demo_settings

        from core.trading_guard import TradingGuard

        guard = TradingGuard()
        guard.activate_kill_switch("테스트")
        assert guard.state.kill_switch_on is True

        guard.deactivate_kill_switch()
        assert guard.state.kill_switch_on is False

        order_check = guard.pre_order_check(
            order_amount_krw=1_000_000,
            ticker="005930",
            side=OrderSide.BUY,
        )
        assert order_check.allowed is True


# ══════════════════════════════════════
# 2. ModeManager 모드 전환 시나리오
# ══════════════════════════════════════


class TestModeTransitionIntegration:
    """모드 전환 종합 시나리오 테스트"""

    @patch("core.mode_manager.get_settings")
    def test_backtest_to_demo_transition_ready(self, mock_settings, backtest_settings):
        """BACKTEST → DEMO 전환 조건 충족"""
        mock_settings.return_value = backtest_settings

        from core.mode_manager import ModeManager

        manager = ModeManager()
        result = manager.check_backtest_to_demo()

        assert result.can_transition is True
        assert result.status.value == "READY"

    @patch("core.mode_manager.get_settings")
    def test_backtest_to_demo_without_credentials_blocked(self, mock_settings, backtest_settings):
        """BACKTEST → DEMO 전환 - 자격증명 미설정 시 차단"""
        backtest_settings.kis.demo_app_key = ""
        backtest_settings.kis.demo_app_secret = ""
        backtest_settings.kis.demo_account_no = ""
        mock_settings.return_value = backtest_settings

        from core.mode_manager import ModeManager

        manager = ModeManager()
        result = manager.check_backtest_to_demo()

        assert result.can_transition is False
        assert result.status.value == "NOT_READY"

    @patch("core.mode_manager.get_settings")
    def test_demo_to_live_transition_full_check(self, mock_settings, demo_settings):
        """DEMO → LIVE 전환 종합 검증"""
        # LIVE 자격증명 설정
        demo_settings.kis.live_app_key = "live_key_real"
        demo_settings.kis.live_app_secret = "live_secret_real"
        demo_settings.kis.live_account_no = "88888888-01"
        demo_settings.risk.daily_loss_limit_krw = 5_000_000
        demo_settings.risk.max_drawdown = 0.20
        demo_settings.risk.max_order_amount_krw = 10_000_000
        mock_settings.return_value = demo_settings

        from core.mode_manager import ModeManager

        manager = ModeManager()
        result = manager.check_demo_to_live()

        # 텔레그램이 test-bot-token이라 WARNINGS 상태
        assert result.status.value in ("READY", "WARNINGS")
        # 필수 항목은 모두 통과
        required_items = [i for i in result.items if i.required]
        assert all(i.passed for i in required_items)

    @patch("core.mode_manager.get_settings")
    def test_demo_to_live_same_credentials_blocked(self, mock_settings, demo_settings):
        """DEMO → LIVE 전환 - 동일 자격증명 시 차단"""
        demo_settings.kis.live_app_key = "real_demo_key"  # DEMO와 동일
        demo_settings.kis.live_app_secret = "live_secret_different"
        demo_settings.kis.live_account_no = "88888888-01"
        mock_settings.return_value = demo_settings

        from core.mode_manager import ModeManager

        manager = ModeManager()
        result = manager.check_demo_to_live()

        # LIVE/DEMO 자격증명 분리 항목이 실패
        separation_item = next(i for i in result.items if "분리" in i.name)
        assert separation_item.passed is False

    @patch("core.mode_manager.get_settings")
    def test_live_to_demo_emergency_downgrade_always_ready(self, mock_settings, live_settings):
        """LIVE → DEMO 비상 다운그레이드는 항상 가능"""
        mock_settings.return_value = live_settings

        from core.mode_manager import ModeManager

        manager = ModeManager()
        result = manager.check_live_to_demo()

        assert result.can_transition is True
        assert result.status.value == "READY"

    @patch("core.mode_manager.get_settings")
    def test_transition_history_recorded(self, mock_settings, demo_settings):
        """전환 이력이 올바르게 기록"""
        mock_settings.return_value = demo_settings

        from core.mode_manager import ModeManager

        manager = ModeManager()
        manager.record_transition("BACKTEST", "DEMO", "자격증명 확인 완료")
        manager.record_transition("DEMO", "LIVE", "모든 검증 통과")

        history = manager.get_transition_history()
        assert len(history) == 2
        assert history[0]["from_mode"] == "BACKTEST"
        assert history[1]["to_mode"] == "LIVE"

    @patch("core.mode_manager.get_settings")
    def test_unsupported_transition_path_blocked(self, mock_settings, backtest_settings):
        """지원하지 않는 전환 경로 차단 (BACKTEST → LIVE)"""
        mock_settings.return_value = backtest_settings

        from core.mode_manager import ModeManager

        manager = ModeManager()
        result = manager.check_transition("LIVE")

        assert result.can_transition is False
        assert "지원하지 않는 전환" in result.items[0].message


# ══════════════════════════════════════
# 3. HealthChecker + ModeManager 연동
# ══════════════════════════════════════


class TestHealthCheckerModeManagerIntegration:
    """HealthChecker와 ModeManager 연동 테스트"""

    @pytest.mark.asyncio
    @patch("core.health_checker.get_settings")
    async def test_backtest_mode_health_check(self, mock_settings, backtest_settings):
        """BACKTEST 모드 건전성 검사 (DB 없이 통과)"""
        mock_settings.return_value = backtest_settings

        from core.health_checker import HealthChecker, HealthStatus

        checker = HealthChecker()
        # _check_trading_mode_readiness만 직접 호출 (DB 의존성 회피)
        result = await checker._check_trading_mode_readiness()

        assert result.status == HealthStatus.HEALTHY
        assert "BACKTEST" in result.message

    @pytest.mark.asyncio
    @patch("core.health_checker.get_settings")
    async def test_demo_mode_with_valid_credentials_healthy(self, mock_settings, demo_settings):
        """DEMO 모드 + 유효한 자격증명 → HEALTHY"""
        mock_settings.return_value = demo_settings

        from core.health_checker import HealthChecker, HealthStatus

        checker = HealthChecker()
        result = await checker._check_trading_mode_readiness()

        assert result.status == HealthStatus.HEALTHY
        assert "모의투자 자격증명 확인됨" in result.message

    @pytest.mark.asyncio
    @patch("core.health_checker.get_settings")
    async def test_live_mode_non_production_unhealthy(self, mock_settings, live_settings):
        """LIVE 모드 + non-production → UNHEALTHY"""
        live_settings.is_production = False
        mock_settings.return_value = live_settings

        from core.health_checker import HealthChecker, HealthStatus

        checker = HealthChecker()
        result = await checker._check_trading_mode_readiness()

        assert result.status == HealthStatus.UNHEALTHY
        assert "production" in result.message

    @pytest.mark.asyncio
    @patch("core.health_checker.get_settings")
    async def test_settings_validity_degraded_with_defaults(self, mock_settings, demo_settings):
        """기본 설정값 사용 시 DEGRADED"""
        mock_settings.return_value = demo_settings

        from core.health_checker import HealthChecker, HealthStatus

        checker = HealthChecker()
        result = await checker._check_settings_validity()

        # test-bot-token, test-secret-key → DEGRADED
        assert result.status == HealthStatus.DEGRADED


# ══════════════════════════════════════
# 4. TradingGuard 서킷브레이커 시나리오
# ══════════════════════════════════════


class TestCircuitBreakerScenarios:
    """서킷브레이커 연쇄 동작 시나리오"""

    @patch("core.trading_guard.get_settings")
    def test_consecutive_losses_trigger_kill_switch(self, mock_settings, demo_settings):
        """연속 손실 → Kill Switch 활성화 → 주문 차단"""
        mock_settings.return_value = demo_settings

        from core.trading_guard import TradingGuard

        guard = TradingGuard()

        # 5회 연속 손실 기록
        for i in range(5):
            guard.record_trade_result(pnl=-100_000, portfolio_value=49_000_000 - i * 100_000)

        assert guard.state.consecutive_losses == 5

        # 연속 손실 한도 도달 → 차단
        check = guard.check_consecutive_losses()
        assert check.allowed is False
        assert guard.state.kill_switch_on is True

    @patch("core.trading_guard.get_settings")
    def test_daily_loss_limit_triggers_kill_switch(self, mock_settings, demo_settings):
        """일일 손실 한도 도달 → Kill Switch"""
        mock_settings.return_value = demo_settings

        from core.trading_guard import TradingGuard

        guard = TradingGuard()

        # 큰 손실 기록 (5백만원 한도 초과)
        guard.record_trade_result(pnl=-5_500_000, portfolio_value=44_500_000)

        check = guard.check_daily_loss_limit()
        assert check.allowed is False
        assert guard.state.kill_switch_on is True

    @patch("core.trading_guard.get_settings")
    def test_mdd_triggers_kill_switch(self, mock_settings, demo_settings):
        """MDD 한도 도달 → Kill Switch"""
        mock_settings.return_value = demo_settings

        from core.trading_guard import TradingGuard

        guard = TradingGuard()

        # 20% 이상 하락 시뮬레이션
        guard._state.peak_portfolio_value = 50_000_000
        guard._state.current_portfolio_value = 39_000_000  # 22% 하락

        check = guard.check_max_drawdown()
        assert check.allowed is False
        assert guard.state.kill_switch_on is True

    @patch("core.trading_guard.get_settings")
    def test_daily_reset_clears_daily_state(self, mock_settings, demo_settings):
        """일일 리셋이 daily 상태만 초기화"""
        mock_settings.return_value = demo_settings

        from core.trading_guard import TradingGuard

        guard = TradingGuard()

        # 상태 누적
        guard.record_trade_result(pnl=-1_000_000, portfolio_value=49_000_000)
        guard.record_trade_result(pnl=-500_000, portfolio_value=48_500_000)
        assert guard.state.daily_realized_pnl == -1_500_000
        assert guard.state.daily_order_count == 2

        # 일일 리셋
        guard.reset_daily_state()
        assert guard.state.daily_realized_pnl == 0.0
        assert guard.state.daily_order_count == 0
        # 연속 손실과 포트폴리오 가치는 유지
        assert guard.state.consecutive_losses == 2

    @patch("core.trading_guard.get_settings")
    def test_profit_resets_consecutive_losses(self, mock_settings, demo_settings):
        """이익 발생 시 연속 손실 카운터 초기화"""
        mock_settings.return_value = demo_settings

        from core.trading_guard import TradingGuard

        guard = TradingGuard()

        # 3회 연속 손실
        for _ in range(3):
            guard.record_trade_result(pnl=-100_000, portfolio_value=49_700_000)
        assert guard.state.consecutive_losses == 3

        # 1회 이익
        guard.record_trade_result(pnl=500_000, portfolio_value=50_200_000)
        assert guard.state.consecutive_losses == 0

    @patch("core.trading_guard.get_settings")
    def test_pre_order_check_validates_amount_limit(self, mock_settings, demo_settings):
        """주문 금액 한도 초과 검증"""
        mock_settings.return_value = demo_settings

        from core.trading_guard import TradingGuard

        guard = TradingGuard()

        # 한도 초과 주문
        check = guard.pre_order_check(
            order_amount_krw=15_000_000,  # 한도 1천만원 초과
            ticker="005930",
            side=OrderSide.BUY,
        )
        assert check.allowed is False
        assert "주문 금액 초과" in check.reason


# ══════════════════════════════════════
# 5. AlertManager + TelegramNotifier 연동
# ══════════════════════════════════════


class TestNotificationIntegration:
    """알림 시스템 통합 테스트"""

    def test_create_alert_from_template(self, demo_settings):
        """템플릿 기반 알림 생성"""
        from config.constants import AlertType
        from core.notification.alert_manager import AlertLevel, AlertManager

        manager = AlertManager()
        alert = manager.create_from_template(
            AlertType.SYSTEM_ERROR,
            template_data={
                "module": "PostgreSQL",
                "error_message": "DB 연결 실패",
                "occurred_at": "2026-04-03T09:00:00",
                "details": "Connection refused",
            },
        )

        assert alert is not None
        assert alert.level == AlertLevel.ERROR

    @pytest.mark.asyncio
    async def test_alert_stats_calculation(self, demo_settings):
        """알림 통계 계산"""
        from config.constants import AlertType
        from core.notification.alert_manager import AlertLevel, AlertManager

        manager = AlertManager()

        # 다양한 레벨 알림 생성
        manager.create_alert(
            alert_type=AlertType.SYSTEM_ERROR, title="정보", message="시스템 정상", level=AlertLevel.INFO
        )
        manager.create_alert(
            alert_type=AlertType.SYSTEM_ERROR, title="경고", message="메모리 사용량 높음", level=AlertLevel.WARNING
        )
        manager.create_alert(
            alert_type=AlertType.SYSTEM_ERROR, title="오류", message="API 타임아웃", level=AlertLevel.ERROR
        )

        stats = await manager.get_alert_stats()
        assert stats["total"] == 3
        assert stats["by_level"]["INFO"] == 1
        assert stats["by_level"]["WARNING"] == 1
        assert stats["by_level"]["ERROR"] == 1

    @pytest.mark.asyncio
    @patch("core.notification.telegram_notifier.get_settings")
    async def test_telegram_dispatch_alert(self, mock_settings, demo_settings):
        """TelegramNotifier가 알림을 발송"""
        demo_settings.telegram.bot_token = "real-bot-token"
        mock_settings.return_value = demo_settings

        from config.constants import AlertType
        from core.notification.alert_manager import AlertLevel
        from core.notification.telegram_notifier import TelegramNotifier

        notifier = TelegramNotifier()

        # AlertManager 에 등록하여 claim_for_sending / mark_sent_by_id 경로 활성화
        alert = notifier._alert_manager.create_alert(
            alert_type=AlertType.SYSTEM_ERROR,
            title="테스트 알림",
            message="통합 테스트",
            level=AlertLevel.WARNING,
        )

        # httpx.AsyncClient mock (Transport 모듈에서 import)
        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"ok": True}
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            MockClient.return_value = mock_client

            success = await notifier.dispatch_alert(alert)
            assert success is True


# ══════════════════════════════════════
# 6. E2E 시나리오
# ══════════════════════════════════════


class TestE2EScenarios:
    """종단 간(E2E) 시나리오 테스트"""

    @patch("core.trading_guard.get_settings")
    @patch("core.mode_manager.get_settings")
    def test_complete_trading_guard_cycle(self, mock_mm_settings, mock_tg_settings, demo_settings):
        """완전한 거래일 사이클: 리셋 → 거래 → 손실 → 차단 → 리셋"""
        mock_mm_settings.return_value = demo_settings
        mock_tg_settings.return_value = demo_settings

        from core.trading_guard import TradingGuard

        guard = TradingGuard()

        # 1. 장 시작: 일일 리셋
        guard.reset_daily_state()
        assert guard.state.daily_realized_pnl == 0.0

        # 2. 정상 거래
        check = guard.run_all_checks()
        assert check.allowed is True

        # 3. 거래 결과 기록 (이익)
        guard.record_trade_result(pnl=500_000, portfolio_value=50_500_000)
        assert guard.state.daily_realized_pnl == 500_000

        # 4. 큰 손실 발생
        guard.record_trade_result(pnl=-6_000_000, portfolio_value=44_500_000)
        assert guard.state.daily_realized_pnl == -5_500_000

        # 5. 일일 손실 한도 → 차단
        check = guard.check_daily_loss_limit()
        assert check.allowed is False
        assert guard.state.kill_switch_on is True

        # 6. 주문 시도 → 차단
        order_check = guard.pre_order_check(
            order_amount_krw=1_000_000,
            ticker="005930",
            side=OrderSide.BUY,
        )
        assert order_check.allowed is False

        # 7. 다음 장 시작: Kill Switch 해제 + 리셋
        guard.deactivate_kill_switch()
        guard.reset_daily_state()
        assert guard.state.kill_switch_on is False
        assert guard.state.daily_realized_pnl == 0.0

        # 8. 거래 재개
        check = guard.run_all_checks()
        assert check.allowed is True

    @patch("core.mode_manager.get_settings")
    def test_full_mode_progression_backtest_demo_live(self, mock_settings, backtest_settings):
        """모드 진행: BACKTEST → DEMO → LIVE 전체 과정"""
        mock_settings.return_value = backtest_settings

        from core.mode_manager import ModeManager

        manager = ModeManager()

        # Step 1: BACKTEST → DEMO 전환 검증
        bt_to_demo = manager.check_backtest_to_demo()
        assert bt_to_demo.can_transition is True
        manager.record_transition("BACKTEST", "DEMO", "DEMO 자격증명 확인")

        # Step 2: DEMO 모드로 전환 (settings 변경 시뮬레이션)
        demo_settings = MagicMock()
        demo_settings.kis.trading_mode = TradingMode.DEMO
        demo_settings.kis.is_demo = True
        demo_settings.kis.is_live = False
        demo_settings.kis.is_backtest = False
        demo_settings.is_production = True
        demo_settings.environment = "production"
        demo_settings.kis.live_app_key = "live_key_real"
        demo_settings.kis.live_app_secret = "live_secret_real"
        demo_settings.kis.live_account_no = "88888888-01"
        demo_settings.kis.demo_app_key = "demo_key_real"
        demo_settings.risk.daily_loss_limit_krw = 5_000_000
        demo_settings.risk.max_drawdown = 0.20
        demo_settings.risk.max_order_amount_krw = 10_000_000
        demo_settings.telegram.bot_token = "real-bot-token"
        demo_settings.telegram.chat_id = "123456789"
        demo_settings.telegram.alert_level = "ALL"
        mock_settings.return_value = demo_settings

        manager2 = ModeManager()

        # Step 3: DEMO → LIVE 전환 검증
        demo_to_live = manager2.check_demo_to_live()
        required_passed = all(i.passed for i in demo_to_live.items if i.required)
        assert required_passed is True

        # Step 4: 전환 이력 기록
        manager.record_transition("DEMO", "LIVE", "모든 검증 통과")
        history = manager.get_transition_history()
        assert len(history) == 2

    @patch("core.trading_guard.get_settings")
    def test_emergency_scenario_mdd_breach(self, mock_settings, demo_settings):
        """비상 시나리오: MDD 위반 → Kill Switch → 전량 차단"""
        mock_settings.return_value = demo_settings

        from core.trading_guard import TradingGuard

        guard = TradingGuard()

        # 점진적 손실 시뮬레이션
        portfolio_values = [
            49_000_000,
            47_000_000,
            45_000_000,
            42_000_000,
            40_000_000,
            38_000_000,
        ]

        for i, pv in enumerate(portfolio_values):
            pnl = -(50_000_000 - pv) / len(portfolio_values)
            guard.record_trade_result(pnl=pnl, portfolio_value=pv)

        # MDD 검사 (50M → 38M = 24% 하락, 한도 20%)
        check = guard.check_max_drawdown()
        assert check.allowed is False
        assert guard.state.kill_switch_on is True

        # 이후 모든 주문 차단
        for ticker in ["005930", "000660", "035720"]:
            order_check = guard.pre_order_check(
                order_amount_krw=1_000_000,
                ticker=ticker,
                side=OrderSide.BUY,
            )
            assert order_check.allowed is False

    @patch("core.health_checker.get_settings")
    async def test_health_check_settings_validity_for_live(self, mock_settings, live_settings):
        """LIVE 모드 설정 유효성 검사"""
        mock_settings.return_value = live_settings

        from core.health_checker import HealthChecker, HealthStatus

        checker = HealthChecker()
        result = await checker._check_settings_validity()

        # real-bot-token, real-secret-key → HEALTHY
        assert result.status == HealthStatus.HEALTHY

    @patch("core.mode_manager.get_settings")
    def test_generic_transition_routing(self, mock_settings, demo_settings):
        """check_transition() 범용 라우팅 검증"""
        mock_settings.return_value = demo_settings

        from core.mode_manager import ModeManager

        manager = ModeManager()

        # DEMO → LIVE 라우팅
        result = manager.check_transition("LIVE")
        assert result.target_mode == "LIVE"
        assert result.current_mode == "DEMO"

        # DEMO → BACKTEST (지원하지 않는 경로)
        result = manager.check_transition("BACKTEST")
        assert result.can_transition is False
