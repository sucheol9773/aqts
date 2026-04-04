"""
AQTS Phase 6 - Mode Manager Unit Tests

Tests for mode transitions (BACKTEST → DEMO → LIVE)
and transition history tracking.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from config.settings import TradingMode
from core.mode_manager import (
    ModeManager,
    TransitionCheckItem,
    TransitionCheckResult,
    TransitionStatus,
)


class TestTransitionCheckItem:
    """TransitionCheckItem 데이터클래스 테스트"""

    def test_creation_with_defaults(self):
        """기본값으로 TransitionCheckItem 생성"""
        item = TransitionCheckItem(name="Test Item", passed=True)
        assert item.name == "Test Item"
        assert item.passed is True
        assert item.required is True
        assert item.message == ""

    def test_creation_with_all_fields(self):
        """모든 필드를 지정하여 생성"""
        item = TransitionCheckItem(
            name="Custom Item",
            passed=False,
            required=False,
            message="Custom message",
        )
        assert item.name == "Custom Item"
        assert item.passed is False
        assert item.required is False
        assert item.message == "Custom message"

    def test_required_field_defaults_to_true(self):
        """required 필드는 기본값이 True"""
        item = TransitionCheckItem(name="Test", passed=True)
        assert item.required is True

    def test_message_field_defaults_to_empty_string(self):
        """message 필드는 기본값이 빈 문자열"""
        item = TransitionCheckItem(name="Test", passed=True)
        assert item.message == ""


class TestTransitionCheckResult:
    """TransitionCheckResult 데이터클래스 테스트"""

    def test_to_dict_basic(self):
        """to_dict() 메서드 기본 동작"""
        items = [
            TransitionCheckItem(name="Item 1", passed=True),
            TransitionCheckItem(name="Item 2", passed=False, required=False),
        ]
        result = TransitionCheckResult(
            current_mode="BACKTEST",
            target_mode="DEMO",
            status=TransitionStatus.READY,
            items=items,
        )

        result_dict = result.to_dict()

        assert result_dict["current_mode"] == "BACKTEST"
        assert result_dict["target_mode"] == "DEMO"
        assert result_dict["status"] == "READY"
        assert len(result_dict["items"]) == 2
        assert result_dict["items"][0]["name"] == "Item 1"
        assert result_dict["items"][0]["passed"] is True
        assert "timestamp" in result_dict

    def test_to_dict_item_structure(self):
        """to_dict()의 각 item 구조 확인"""
        item = TransitionCheckItem(
            name="Test Item",
            passed=True,
            required=False,
            message="Test message",
        )
        result = TransitionCheckResult(
            current_mode="DEMO",
            target_mode="LIVE",
            status=TransitionStatus.WARNINGS,
            items=[item],
        )

        result_dict = result.to_dict()
        item_dict = result_dict["items"][0]

        assert item_dict["name"] == "Test Item"
        assert item_dict["passed"] is True
        assert item_dict["required"] is False
        assert item_dict["message"] == "Test message"

    def test_to_dict_timestamp_format(self):
        """to_dict()의 timestamp가 ISO format"""
        result = TransitionCheckResult(
            current_mode="BACKTEST",
            target_mode="DEMO",
            status=TransitionStatus.READY,
        )

        result_dict = result.to_dict()
        timestamp_str = result_dict["timestamp"]

        # ISO format 파싱 가능 확인
        parsed = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        assert isinstance(parsed, datetime)

    def test_can_transition_all_required_pass(self):
        """모든 필수 항목이 통과하면 can_transition은 True"""
        items = [
            TransitionCheckItem(name="Item 1", passed=True, required=True),
            TransitionCheckItem(name="Item 2", passed=True, required=True),
        ]
        result = TransitionCheckResult(
            current_mode="BACKTEST",
            target_mode="DEMO",
            status=TransitionStatus.READY,
            items=items,
        )

        assert result.can_transition is True

    def test_can_transition_one_required_fails(self):
        """하나의 필수 항목이 실패하면 can_transition은 False"""
        items = [
            TransitionCheckItem(name="Item 1", passed=True, required=True),
            TransitionCheckItem(name="Item 2", passed=False, required=True),
        ]
        result = TransitionCheckResult(
            current_mode="BACKTEST",
            target_mode="DEMO",
            status=TransitionStatus.NOT_READY,
            items=items,
        )

        assert result.can_transition is False

    def test_can_transition_optional_fails_required_pass(self):
        """선택 항목이 실패해도 필수 항목이 모두 통과하면 True"""
        items = [
            TransitionCheckItem(name="Item 1", passed=True, required=True),
            TransitionCheckItem(name="Item 2", passed=False, required=False),
        ]
        result = TransitionCheckResult(
            current_mode="DEMO",
            target_mode="LIVE",
            status=TransitionStatus.WARNINGS,
            items=items,
        )

        assert result.can_transition is True

    def test_can_transition_empty_items(self):
        """items가 비어있으면 can_transition은 True"""
        result = TransitionCheckResult(
            current_mode="BACKTEST",
            target_mode="DEMO",
            status=TransitionStatus.READY,
            items=[],
        )

        assert result.can_transition is True


class TestModeManagerBacktestToDemo:
    """BACKTEST → DEMO 모드 전환 테스트"""

    @pytest.fixture
    def mock_settings_backtest(self):
        """BACKTEST 모드 설정 mock"""
        settings = MagicMock()
        settings.kis.trading_mode = TradingMode.BACKTEST
        settings.kis.is_backtest = True
        settings.kis.is_demo = False
        settings.kis.is_live = False
        return settings

    @patch("core.mode_manager.get_settings")
    def test_passes_with_valid_demo_credentials(self, mock_get_settings, mock_settings_backtest):
        """유효한 DEMO 자격증명으로 전환 조건 통과"""
        settings = mock_settings_backtest
        settings.kis.demo_app_key = "real_demo_key_12345"
        settings.kis.demo_app_secret = "real_demo_secret_12345"
        settings.kis.demo_account_no = "12345678-01"

        mock_get_settings.return_value = settings

        manager = ModeManager()
        result = manager.check_backtest_to_demo()

        assert result.status == TransitionStatus.READY
        assert result.can_transition is True
        assert all(item.passed for item in result.items if item.required)

    @patch("core.mode_manager.get_settings")
    def test_fails_when_not_in_backtest_mode(self, mock_get_settings):
        """BACKTEST 모드가 아닐 때 실패"""
        settings = MagicMock()
        settings.kis.trading_mode = TradingMode.DEMO
        settings.kis.is_backtest = False
        settings.kis.is_demo = True
        settings.kis.is_live = False
        settings.kis.demo_app_key = "valid_key"
        settings.kis.demo_app_secret = "valid_secret"
        settings.kis.demo_account_no = "valid_account"

        mock_get_settings.return_value = settings

        manager = ModeManager()
        result = manager.check_backtest_to_demo()

        assert result.status == TransitionStatus.NOT_READY
        assert result.can_transition is False

    @patch("core.mode_manager.get_settings")
    def test_fails_when_demo_api_key_missing(self, mock_get_settings, mock_settings_backtest):
        """DEMO API Key가 없으면 실패"""
        settings = mock_settings_backtest
        settings.kis.demo_app_key = ""
        settings.kis.demo_app_secret = "valid_secret"
        settings.kis.demo_account_no = "valid_account"

        mock_get_settings.return_value = settings

        manager = ModeManager()
        result = manager.check_backtest_to_demo()

        assert result.status == TransitionStatus.NOT_READY
        assert result.can_transition is False

    @patch("core.mode_manager.get_settings")
    def test_fails_when_demo_account_missing(self, mock_get_settings, mock_settings_backtest):
        """DEMO 계좌번호가 없으면 실패"""
        settings = mock_settings_backtest
        settings.kis.demo_app_key = "valid_key"
        settings.kis.demo_app_secret = "valid_secret"
        settings.kis.demo_account_no = ""

        mock_get_settings.return_value = settings

        manager = ModeManager()
        result = manager.check_backtest_to_demo()

        assert result.status == TransitionStatus.NOT_READY
        assert result.can_transition is False

    @patch("core.mode_manager.get_settings")
    def test_detects_test_demo_credentials(self, mock_get_settings, mock_settings_backtest):
        """테스트 더미 자격증명("test_key_demo") 감지"""
        settings = mock_settings_backtest
        settings.kis.demo_app_key = "test_key_demo"
        settings.kis.demo_app_secret = "test_secret_demo"
        settings.kis.demo_account_no = "87654321-01"

        mock_get_settings.return_value = settings

        manager = ModeManager()
        result = manager.check_backtest_to_demo()

        assert result.status == TransitionStatus.NOT_READY
        assert result.can_transition is False

    @patch("core.mode_manager.get_settings")
    def test_check_items_structure(self, mock_get_settings, mock_settings_backtest):
        """check_backtest_to_demo() 반환 결과의 items 구조"""
        settings = mock_settings_backtest
        settings.kis.demo_app_key = "valid_key"
        settings.kis.demo_app_secret = "valid_secret"
        settings.kis.demo_account_no = "valid_account"

        mock_get_settings.return_value = settings

        manager = ModeManager()
        result = manager.check_backtest_to_demo()

        # items 개수 확인
        assert len(result.items) >= 4

        # 각 item이 TransitionCheckItem 타입
        for item in result.items:
            assert isinstance(item, TransitionCheckItem)
            assert hasattr(item, "name")
            assert hasattr(item, "passed")
            assert hasattr(item, "required")


class TestModeManagerDemoToLive:
    """DEMO → LIVE 모드 전환 테스트"""

    @pytest.fixture
    def mock_settings_demo_prod(self):
        """DEMO 모드 + production 환경 설정 mock"""
        settings = MagicMock()
        settings.kis.trading_mode = TradingMode.DEMO
        settings.kis.is_backtest = False
        settings.kis.is_demo = True
        settings.kis.is_live = False
        settings.environment = "production"
        settings.is_production = True
        settings.risk = MagicMock()
        settings.risk.daily_loss_limit_krw = 5_000_000
        settings.risk.max_drawdown = 0.20
        settings.risk.max_order_amount_krw = 10_000_000
        settings.telegram = MagicMock()
        settings.telegram.bot_token = "valid_telegram_token"
        settings.telegram.chat_id = "123456789"
        settings.telegram.alert_level = "IMPORTANT"
        return settings

    @patch("core.mode_manager.get_settings")
    def test_passes_with_all_conditions_met(self, mock_get_settings, mock_settings_demo_prod):
        """모든 조건이 충족되면 READY 상태"""
        settings = mock_settings_demo_prod
        settings.kis.live_app_key = "real_live_key"
        settings.kis.live_app_secret = "real_live_secret"
        settings.kis.live_account_no = "real_live_account"
        settings.kis.demo_app_key = "different_demo_key"

        mock_get_settings.return_value = settings

        manager = ModeManager()
        result = manager.check_demo_to_live()

        assert result.status == TransitionStatus.READY
        assert result.can_transition is True

    @patch("core.mode_manager.get_settings")
    def test_fails_when_not_in_demo_mode(self, mock_get_settings):
        """DEMO 모드가 아닐 때 실패"""
        settings = MagicMock()
        settings.kis.trading_mode = TradingMode.BACKTEST
        settings.kis.is_backtest = True
        settings.kis.is_demo = False
        settings.kis.is_live = False
        settings.is_production = True
        settings.kis.live_app_key = "valid_key"
        settings.kis.live_app_secret = "valid_secret"
        settings.kis.live_account_no = "valid_account"
        settings.kis.demo_app_key = "different_key"
        settings.risk = MagicMock()
        settings.risk.daily_loss_limit_krw = 5_000_000
        settings.risk.max_drawdown = 0.20
        settings.risk.max_order_amount_krw = 10_000_000
        settings.telegram = MagicMock()
        settings.telegram.bot_token = "valid_token"
        settings.telegram.chat_id = "123456789"
        settings.telegram.alert_level = "IMPORTANT"

        mock_get_settings.return_value = settings

        manager = ModeManager()
        result = manager.check_demo_to_live()

        assert result.status == TransitionStatus.NOT_READY
        assert result.can_transition is False

    @patch("core.mode_manager.get_settings")
    def test_fails_in_non_production_environment(self, mock_get_settings, mock_settings_demo_prod):
        """non-production 환경에서는 실패"""
        settings = mock_settings_demo_prod
        settings.environment = "development"
        settings.is_production = False
        settings.kis.live_app_key = "valid_key"
        settings.kis.live_app_secret = "valid_secret"
        settings.kis.live_account_no = "valid_account"
        settings.kis.demo_app_key = "different_key"

        mock_get_settings.return_value = settings

        manager = ModeManager()
        result = manager.check_demo_to_live()

        assert result.status == TransitionStatus.NOT_READY
        assert result.can_transition is False

    @patch("core.mode_manager.get_settings")
    def test_fails_when_live_api_key_missing(self, mock_get_settings, mock_settings_demo_prod):
        """LIVE API Key가 없으면 실패"""
        settings = mock_settings_demo_prod
        settings.kis.live_app_key = ""
        settings.kis.live_app_secret = "valid_secret"
        settings.kis.live_account_no = "valid_account"
        settings.kis.demo_app_key = "different_key"

        mock_get_settings.return_value = settings

        manager = ModeManager()
        result = manager.check_demo_to_live()

        assert result.status == TransitionStatus.NOT_READY
        assert result.can_transition is False

    @patch("core.mode_manager.get_settings")
    def test_fails_when_live_account_missing(self, mock_get_settings, mock_settings_demo_prod):
        """LIVE 계좌번호가 없으면 실패"""
        settings = mock_settings_demo_prod
        settings.kis.live_app_key = "valid_key"
        settings.kis.live_app_secret = "valid_secret"
        settings.kis.live_account_no = ""
        settings.kis.demo_app_key = "different_key"

        mock_get_settings.return_value = settings

        manager = ModeManager()
        result = manager.check_demo_to_live()

        assert result.status == TransitionStatus.NOT_READY
        assert result.can_transition is False

    @patch("core.mode_manager.get_settings")
    def test_warns_when_live_equals_demo_api_keys(self, mock_get_settings, mock_settings_demo_prod):
        """LIVE == DEMO API Key일 때 경고"""
        settings = mock_settings_demo_prod
        same_key = "same_api_key_for_both"
        settings.kis.live_app_key = same_key
        settings.kis.live_app_secret = "valid_secret"
        settings.kis.live_account_no = "valid_account"
        settings.kis.demo_app_key = same_key
        settings.risk.daily_loss_limit_krw = 5_000_000
        settings.risk.max_drawdown = 0.20
        settings.risk.max_order_amount_krw = 10_000_000

        mock_get_settings.return_value = settings

        manager = ModeManager()
        result = manager.check_demo_to_live()

        # LIVE/DEMO 분리 항목 확인
        separation_item = next(
            (item for item in result.items if "분리" in item.name or "LIVE/DEMO" in item.name),
            None,
        )
        assert separation_item is not None
        assert separation_item.passed is False

    @patch("core.mode_manager.get_settings")
    def test_fails_when_risk_limits_not_configured(self, mock_get_settings, mock_settings_demo_prod):
        """리스크 한도가 설정되지 않으면 실패"""
        settings = mock_settings_demo_prod
        settings.kis.live_app_key = "valid_key"
        settings.kis.live_app_secret = "valid_secret"
        settings.kis.live_account_no = "valid_account"
        settings.kis.demo_app_key = "different_key"
        settings.risk.daily_loss_limit_krw = 0
        settings.risk.max_drawdown = 0.20
        settings.risk.max_order_amount_krw = 10_000_000

        mock_get_settings.return_value = settings

        manager = ModeManager()
        result = manager.check_demo_to_live()

        assert result.status == TransitionStatus.NOT_READY
        assert result.can_transition is False

    @patch("core.mode_manager.get_settings")
    def test_warns_when_telegram_not_configured_but_required_pass(self, mock_get_settings, mock_settings_demo_prod):
        """텔레그램이 설정되지 않아도 필수 항목이 통과하면 WARNINGS 상태"""
        settings = mock_settings_demo_prod
        settings.kis.live_app_key = "valid_key"
        settings.kis.live_app_secret = "valid_secret"
        settings.kis.live_account_no = "valid_account"
        settings.kis.demo_app_key = "different_key"
        settings.telegram.bot_token = ""
        settings.telegram.chat_id = ""

        mock_get_settings.return_value = settings

        manager = ModeManager()
        result = manager.check_demo_to_live()

        assert result.status == TransitionStatus.WARNINGS
        assert result.can_transition is True

    @patch("core.mode_manager.get_settings")
    def test_status_not_ready_when_required_fails(self, mock_get_settings, mock_settings_demo_prod):
        """필수 항목이 실패하면 상태는 NOT_READY"""
        settings = mock_settings_demo_prod
        settings.kis.live_app_key = ""
        settings.kis.live_app_secret = ""
        settings.kis.live_account_no = ""

        mock_get_settings.return_value = settings

        manager = ModeManager()
        result = manager.check_demo_to_live()

        assert result.status == TransitionStatus.NOT_READY

    @patch("core.mode_manager.get_settings")
    def test_status_ready_when_all_pass(self, mock_get_settings, mock_settings_demo_prod):
        """모든 항목(필수+선택)이 통과하면 상태는 READY"""
        settings = mock_settings_demo_prod
        settings.kis.live_app_key = "valid_key"
        settings.kis.live_app_secret = "valid_secret"
        settings.kis.live_account_no = "valid_account"
        settings.kis.demo_app_key = "different_key"
        settings.telegram.bot_token = "valid_token"
        settings.telegram.chat_id = "123456789"

        mock_get_settings.return_value = settings

        manager = ModeManager()
        result = manager.check_demo_to_live()

        assert result.status == TransitionStatus.READY


class TestModeManagerLiveToDemo:
    """LIVE → DEMO 비상 다운그레이드 테스트"""

    @pytest.fixture
    def mock_settings_live(self):
        """LIVE 모드 설정 mock"""
        settings = MagicMock()
        settings.kis.trading_mode = TradingMode.LIVE
        settings.kis.is_backtest = False
        settings.kis.is_demo = False
        settings.kis.is_live = True
        return settings

    @patch("core.mode_manager.get_settings")
    def test_always_allowed(self, mock_get_settings, mock_settings_live):
        """LIVE → DEMO는 항상 허용됨"""
        mock_get_settings.return_value = mock_settings_live

        manager = ModeManager()
        result = manager.check_live_to_demo()

        assert result.status == TransitionStatus.READY
        assert result.can_transition is True

    @patch("core.mode_manager.get_settings")
    def test_ready_even_not_in_live_mode(self, mock_get_settings):
        """LIVE 모드가 아니어도 이 체크는 READY 반환"""
        settings = MagicMock()
        settings.kis.trading_mode = TradingMode.DEMO
        settings.kis.is_backtest = False
        settings.kis.is_demo = True
        settings.kis.is_live = False

        mock_get_settings.return_value = settings

        manager = ModeManager()
        result = manager.check_live_to_demo()

        # LIVE → DEMO 비상 다운그레이드는 조건 없이 READY
        assert result.status == TransitionStatus.READY

    @patch("core.mode_manager.get_settings")
    def test_check_items_populated(self, mock_get_settings, mock_settings_live):
        """check_live_to_demo()의 items가 채워짐"""
        mock_get_settings.return_value = mock_settings_live

        manager = ModeManager()
        result = manager.check_live_to_demo()

        assert len(result.items) >= 2
        assert all(isinstance(item, TransitionCheckItem) for item in result.items)


class TestModeManagerCheckTransition:
    """ModeManager.check_transition() 범용 메서드 테스트"""

    @pytest.fixture
    def mock_manager(self):
        """ModeManager mock"""
        with patch("core.mode_manager.get_settings"):
            manager = ModeManager()
            manager.check_backtest_to_demo = MagicMock()
            manager.check_demo_to_live = MagicMock()
            manager.check_live_to_demo = MagicMock()
            return manager

    @patch("core.mode_manager.get_settings")
    def test_backtest_to_demo_routing(self, mock_get_settings):
        """BACKTEST → DEMO 라우팅"""
        settings = MagicMock()
        settings.kis.trading_mode = TradingMode.BACKTEST

        mock_get_settings.return_value = settings

        manager = ModeManager()
        with patch.object(manager, "check_backtest_to_demo") as mock_check:
            mock_check.return_value = TransitionCheckResult(
                current_mode="BACKTEST",
                target_mode="DEMO",
                status=TransitionStatus.READY,
            )

            result = manager.check_transition("DEMO")

            mock_check.assert_called_once()

    @patch("core.mode_manager.get_settings")
    def test_demo_to_live_routing(self, mock_get_settings):
        """DEMO → LIVE 라우팅"""
        settings = MagicMock()
        settings.kis.trading_mode = TradingMode.DEMO

        mock_get_settings.return_value = settings

        manager = ModeManager()
        with patch.object(manager, "check_demo_to_live") as mock_check:
            mock_check.return_value = TransitionCheckResult(
                current_mode="DEMO",
                target_mode="LIVE",
                status=TransitionStatus.READY,
            )

            result = manager.check_transition("LIVE")

            mock_check.assert_called_once()

    @patch("core.mode_manager.get_settings")
    def test_live_to_demo_routing(self, mock_get_settings):
        """LIVE → DEMO 라우팅"""
        settings = MagicMock()
        settings.kis.trading_mode = TradingMode.LIVE

        mock_get_settings.return_value = settings

        manager = ModeManager()
        with patch.object(manager, "check_live_to_demo") as mock_check:
            mock_check.return_value = TransitionCheckResult(
                current_mode="LIVE",
                target_mode="DEMO",
                status=TransitionStatus.READY,
            )

            result = manager.check_transition("DEMO")

            mock_check.assert_called_once()

    @patch("core.mode_manager.get_settings")
    def test_unsupported_transition_backtest_to_live(self, mock_get_settings):
        """지원하지 않는 전환 (BACKTEST → LIVE)"""
        settings = MagicMock()
        settings.kis.trading_mode = TradingMode.BACKTEST

        mock_get_settings.return_value = settings

        manager = ModeManager()
        result = manager.check_transition("LIVE")

        assert result.status == TransitionStatus.NOT_READY
        assert result.can_transition is False
        assert any("지원하지 않는" in item.message for item in result.items)

    @patch("core.mode_manager.get_settings")
    def test_same_mode_transition(self, mock_get_settings):
        """같은 모드로의 전환"""
        settings = MagicMock()
        settings.kis.trading_mode = TradingMode.DEMO

        mock_get_settings.return_value = settings

        manager = ModeManager()
        result = manager.check_transition("DEMO")

        assert result.status == TransitionStatus.NOT_READY
        assert result.can_transition is False


class TestModeManagerTransitionHistory:
    """모드 전환 이력 기록 및 조회 테스트"""

    @patch("core.mode_manager.get_settings")
    def test_record_transition_adds_to_history(self, mock_get_settings):
        """record_transition()이 이력에 추가"""
        mock_get_settings.return_value = MagicMock()

        manager = ModeManager()
        manager.record_transition("BACKTEST", "DEMO", reason="Manual transition")

        history = manager.get_transition_history()
        assert len(history) == 1
        assert history[0]["from_mode"] == "BACKTEST"
        assert history[0]["to_mode"] == "DEMO"
        assert history[0]["reason"] == "Manual transition"

    @patch("core.mode_manager.get_settings")
    def test_get_transition_history_returns_all(self, mock_get_settings):
        """get_transition_history()가 모든 기록 반환"""
        mock_get_settings.return_value = MagicMock()

        manager = ModeManager()
        manager.record_transition("BACKTEST", "DEMO", reason="First")
        manager.record_transition("DEMO", "LIVE", reason="Second")

        history = manager.get_transition_history()
        assert len(history) == 2
        assert history[0]["reason"] == "First"
        assert history[1]["reason"] == "Second"

    @patch("core.mode_manager.get_settings")
    def test_multiple_transitions_tracked(self, mock_get_settings):
        """여러 전환 기록"""
        mock_get_settings.return_value = MagicMock()

        manager = ModeManager()
        transitions = [
            ("BACKTEST", "DEMO", "Initial"),
            ("DEMO", "LIVE", "After testing"),
            ("LIVE", "DEMO", "Emergency"),
        ]

        for from_mode, to_mode, reason in transitions:
            manager.record_transition(from_mode, to_mode, reason)

        history = manager.get_transition_history()
        assert len(history) == 3
        assert all("timestamp" in record for record in history)

    @patch("core.mode_manager.get_settings")
    def test_transition_record_structure(self, mock_get_settings):
        """전환 기록의 구조"""
        mock_get_settings.return_value = MagicMock()

        manager = ModeManager()
        manager.record_transition("BACKTEST", "DEMO")

        history = manager.get_transition_history()
        record = history[0]

        assert "from_mode" in record
        assert "to_mode" in record
        assert "reason" in record
        assert "timestamp" in record
        assert isinstance(record["timestamp"], str)

    @patch("core.mode_manager.get_settings")
    def test_transition_reason_optional(self, mock_get_settings):
        """reason 매개변수는 선택사항"""
        mock_get_settings.return_value = MagicMock()

        manager = ModeManager()
        manager.record_transition("BACKTEST", "DEMO")

        history = manager.get_transition_history()
        record = history[0]

        assert record["reason"] == ""

    @patch("core.mode_manager.get_settings")
    def test_get_transition_history_returns_copy(self, mock_get_settings):
        """get_transition_history()가 복사본을 반환"""
        mock_get_settings.return_value = MagicMock()

        manager = ModeManager()
        manager.record_transition("BACKTEST", "DEMO")

        history1 = manager.get_transition_history()
        history1.append({"test": "modified"})

        history2 = manager.get_transition_history()
        assert len(history2) == 1  # 원본은 영향받지 않음
