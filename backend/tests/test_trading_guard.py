"""
Comprehensive unit tests for TradingGuard (Phase 6)

Test Categories:
  1. TradingGuardState tests (dataclass functionality)
  2. Environment verification (LIVE/DEMO/BACKTEST mode validation)
  3. Capital verification (minimum capital checks)
  4. Circuit breakers (daily loss, MDD, consecutive losses)
  5. Pre-order checks (amount, weight, sector limits)
  6. Trade result recording (PnL tracking, state updates)
  7. Kill switch (activation, deactivation, state)
  8. All-in-one check (run_all_checks integration)
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest

from config.constants import OrderSide, PORTFOLIO_CONSTRAINTS
from core.trading_guard import (
    TradingGuard,
    TradingGuardState,
    PreOrderCheckResult,
)


# ══════════════════════════════════════
# Fixtures
# ══════════════════════════════════════
@pytest.fixture
def mock_settings():
    """Mock settings object with proper nested structure"""
    settings = MagicMock()
    settings.environment = "development"
    settings.is_production = False

    # KIS settings
    settings.kis = MagicMock()
    settings.kis.is_live = False
    settings.kis.is_demo = True
    settings.kis.is_backtest = False
    settings.kis.trading_mode = MagicMock()
    settings.kis.trading_mode.value = "DEMO"

    # Active credential
    settings.kis.active_credential = MagicMock()
    settings.kis.active_credential.app_key = "demo_key"
    settings.kis.active_credential.app_secret = "demo_secret"
    settings.kis.active_credential.account_no = "12345678-01"

    # API keys (for cross-check)
    settings.kis.live_app_key = "live_key"
    settings.kis.demo_app_key = "demo_key"

    # Risk settings
    settings.risk = MagicMock()
    settings.risk.initial_capital_krw = 50_000_000
    settings.risk.daily_loss_limit_krw = 5_000_000
    settings.risk.max_order_amount_krw = 10_000_000
    settings.risk.max_positions = 20
    settings.risk.max_position_weight = 0.20
    settings.risk.max_drawdown = 0.20
    settings.risk.consecutive_loss_limit = 5
    settings.risk.stop_loss_percent = -0.10

    # Telegram settings
    settings.telegram = MagicMock()
    settings.telegram.bot_token = "test-bot"
    settings.telegram.chat_id = "123"
    settings.telegram.alert_level = "ALL"

    # Dashboard settings
    settings.dashboard = MagicMock()
    settings.dashboard.secret_key = "secret"

    return settings


@pytest.fixture
def guard(mock_settings):
    """TradingGuard instance with mocked settings"""
    with patch("core.trading_guard.get_settings", return_value=mock_settings):
        return TradingGuard()


# ══════════════════════════════════════
# TradingGuardState Tests
# ══════════════════════════════════════
class TestTradingGuardState:
    """Test TradingGuardState dataclass functionality"""

    def test_default_state_creation(self):
        """Test default TradingGuardState initialization"""
        state = TradingGuardState()

        assert state.is_active is True
        assert state.kill_switch_on is False
        assert state.kill_switch_reason == ""
        assert state.daily_realized_pnl == 0.0
        assert state.daily_order_count == 0
        assert state.consecutive_losses == 0
        assert state.current_drawdown == 0.0
        assert state.peak_portfolio_value == 0.0
        assert state.current_portfolio_value == 0.0
        assert isinstance(state.last_updated, datetime)

    def test_state_with_initial_values(self):
        """Test TradingGuardState with explicit initial values"""
        state = TradingGuardState(
            peak_portfolio_value=50_000_000,
            current_portfolio_value=50_000_000,
        )

        assert state.peak_portfolio_value == 50_000_000
        assert state.current_portfolio_value == 50_000_000
        assert state.is_active is True

    def test_state_to_dict_serialization(self):
        """Test TradingGuardState.to_dict() method"""
        state = TradingGuardState(
            peak_portfolio_value=50_000_000,
            current_portfolio_value=49_000_000,
            daily_realized_pnl=-500_000,
            consecutive_losses=2,
        )

        result = state.to_dict()

        assert result["is_active"] is True
        assert result["kill_switch_on"] is False
        assert result["peak_portfolio_value"] == 50_000_000
        assert result["current_portfolio_value"] == 49_000_000
        assert result["daily_realized_pnl"] == -500_000
        assert result["consecutive_losses"] == 2
        assert "current_drawdown" in result
        assert "last_updated" in result
        assert isinstance(result["last_updated"], str)  # ISO format

    def test_state_with_kill_switch_values(self):
        """Test TradingGuardState with kill switch activation"""
        state = TradingGuardState(
            kill_switch_on=True,
            kill_switch_reason="Daily loss limit exceeded",
        )

        assert state.kill_switch_on is True
        assert state.kill_switch_reason == "Daily loss limit exceeded"

        state_dict = state.to_dict()
        assert state_dict["kill_switch_on"] is True
        assert state_dict["kill_switch_reason"] == "Daily loss limit exceeded"

    def test_state_drawdown_rounding(self):
        """Test that drawdown is properly rounded in to_dict()"""
        state = TradingGuardState(current_drawdown=0.123456789)
        result = state.to_dict()

        # Should be rounded to 4 decimal places
        assert result["current_drawdown"] == 0.1235

    def test_pre_order_check_result_creation(self):
        """Test PreOrderCheckResult dataclass"""
        result = PreOrderCheckResult(
            allowed=True,
            reason="",
            warnings=["Warning 1"],
        )

        assert result.allowed is True
        assert result.reason == ""
        assert result.warnings == ["Warning 1"]

    def test_pre_order_check_result_to_dict(self):
        """Test PreOrderCheckResult.to_dict() method"""
        result = PreOrderCheckResult(
            allowed=False,
            reason="Amount exceeded",
            warnings=["High sector weight"],
        )

        result_dict = result.to_dict()
        assert result_dict["allowed"] is False
        assert result_dict["reason"] == "Amount exceeded"
        assert result_dict["warnings"] == ["High sector weight"]


# ══════════════════════════════════════
# Environment Verification Tests
# ══════════════════════════════════════
class TestEnvironmentVerification:
    """Test environment validation for LIVE/DEMO/BACKTEST modes"""

    def test_backtest_mode_always_passes(self, guard, mock_settings):
        """Test that BACKTEST mode always passes environment check"""
        mock_settings.kis.is_backtest = True
        mock_settings.kis.is_demo = False
        mock_settings.kis.is_live = False

        result = guard.verify_environment()
        assert result.allowed is True

    def test_demo_mode_with_valid_credentials(self, guard, mock_settings):
        """Test DEMO mode passes with valid credentials"""
        mock_settings.kis.is_demo = True
        mock_settings.kis.is_live = False
        mock_settings.kis.active_credential.app_key = "demo_key"
        mock_settings.kis.active_credential.app_secret = "demo_secret"
        mock_settings.kis.active_credential.account_no = "12345678-01"

        result = guard.verify_environment()
        assert result.allowed is True

    def test_live_mode_fails_in_non_production_environment(self, guard, mock_settings):
        """Test LIVE mode fails when not in production environment"""
        mock_settings.kis.is_live = True
        mock_settings.kis.is_demo = False
        mock_settings.is_production = False  # Non-production!

        result = guard.verify_environment()
        assert result.allowed is False
        assert "production" in result.reason.lower()

    def test_live_mode_fails_without_app_key(self, guard, mock_settings):
        """Test LIVE mode fails when API key is missing"""
        mock_settings.kis.is_live = True
        mock_settings.is_production = True
        mock_settings.kis.active_credential.app_key = ""
        mock_settings.kis.active_credential.app_secret = "secret"

        result = guard.verify_environment()
        assert result.allowed is False
        assert "API" in result.reason

    def test_live_mode_fails_without_app_secret(self, guard, mock_settings):
        """Test LIVE mode fails when API secret is missing"""
        mock_settings.kis.is_live = True
        mock_settings.is_production = True
        mock_settings.kis.active_credential.app_key = "key"
        mock_settings.kis.active_credential.app_secret = ""

        result = guard.verify_environment()
        assert result.allowed is False
        assert "API" in result.reason

    def test_live_mode_fails_without_account_number(self, guard, mock_settings):
        """Test LIVE mode fails when account number is missing"""
        mock_settings.kis.is_live = True
        mock_settings.is_production = True
        mock_settings.kis.active_credential.app_key = "key"
        mock_settings.kis.active_credential.app_secret = "secret"
        mock_settings.kis.active_credential.account_no = ""

        result = guard.verify_environment()
        assert result.allowed is False
        assert "계좌" in result.reason or "account" in result.reason.lower()

    def test_live_mode_warns_when_live_equals_demo_keys(self, guard, mock_settings):
        """Test LIVE mode generates warning when LIVE and DEMO keys are identical"""
        mock_settings.kis.is_live = True
        mock_settings.is_production = True
        mock_settings.kis.active_credential.app_key = "same_key"
        mock_settings.kis.active_credential.app_secret = "same_secret"
        mock_settings.kis.active_credential.account_no = "account"

        mock_settings.kis.live_app_key = "same_key"
        mock_settings.kis.demo_app_key = "same_key"  # Same!

        result = guard.verify_environment()
        assert result.allowed is True
        assert len(result.warnings) > 0
        assert any("LIVE" in w and "DEMO" in w for w in result.warnings)


# ══════════════════════════════════════
# Capital Verification Tests
# ══════════════════════════════════════
class TestCapitalVerification:
    """Test capital/balance verification"""

    def test_sufficient_capital_passes(self, guard, mock_settings):
        """Test that sufficient balance passes verification"""
        min_required = mock_settings.risk.initial_capital_krw * 0.1
        current_balance = min_required + 1_000_000

        result = guard.verify_capital(current_balance)
        assert result.allowed is True

    def test_insufficient_capital_fails(self, guard, mock_settings):
        """Test that insufficient balance fails verification"""
        min_required = mock_settings.risk.initial_capital_krw * 0.1
        current_balance = min_required - 1_000_000

        result = guard.verify_capital(current_balance)
        assert result.allowed is False
        assert "부족" in result.reason or "insufficient" in result.reason.lower()

    def test_capital_at_exact_boundary(self, guard, mock_settings):
        """Test edge case where balance exactly equals minimum requirement"""
        min_required = mock_settings.risk.initial_capital_krw * 0.1
        current_balance = min_required

        result = guard.verify_capital(current_balance)
        assert result.allowed is True

    def test_capital_just_below_boundary_fails(self, guard, mock_settings):
        """Test that balance just below boundary fails"""
        min_required = mock_settings.risk.initial_capital_krw * 0.1
        current_balance = min_required - 1

        result = guard.verify_capital(current_balance)
        assert result.allowed is False

    def test_zero_balance_fails(self, guard):
        """Test that zero balance always fails"""
        result = guard.verify_capital(0)
        assert result.allowed is False


# ══════════════════════════════════════
# Circuit Breaker Tests
# ══════════════════════════════════════
class TestCircuitBreakers:
    """Test circuit breaker mechanisms (daily loss, MDD, consecutive losses)"""

    # ── Daily Loss Tests ──
    def test_daily_loss_within_limit_passes(self, guard, mock_settings):
        """Test daily loss within limit passes check"""
        guard._state.daily_realized_pnl = -3_000_000  # Within limit

        result = guard.check_daily_loss_limit()
        assert result.allowed is True
        assert guard._state.kill_switch_on is False

    def test_daily_loss_exceeds_limit_activates_kill_switch(self, guard, mock_settings):
        """Test daily loss exceeding limit activates kill switch"""
        limit = mock_settings.risk.daily_loss_limit_krw
        guard._state.daily_realized_pnl = -limit - 1_000_000

        result = guard.check_daily_loss_limit()
        assert result.allowed is False
        assert guard._state.kill_switch_on is True
        assert "일일 손실" in guard._state.kill_switch_reason

    def test_daily_loss_at_boundary(self, guard, mock_settings):
        """Test daily loss at exact boundary"""
        limit = mock_settings.risk.daily_loss_limit_krw
        guard._state.daily_realized_pnl = -limit  # Exactly at limit

        result = guard.check_daily_loss_limit()
        assert result.allowed is False  # >= triggers
        assert guard._state.kill_switch_on is True

    def test_daily_loss_just_within_limit(self, guard, mock_settings):
        """Test daily loss just within limit"""
        limit = mock_settings.risk.daily_loss_limit_krw
        guard._state.daily_realized_pnl = -limit + 1

        result = guard.check_daily_loss_limit()
        assert result.allowed is True

    def test_positive_daily_pnl_passes(self, guard):
        """Test positive daily PnL always passes"""
        guard._state.daily_realized_pnl = 1_000_000

        result = guard.check_daily_loss_limit()
        assert result.allowed is True

    # ── Max Drawdown Tests ──
    def test_mdd_within_limit_passes(self, guard, mock_settings):
        """Test MDD within limit passes check"""
        guard._state.peak_portfolio_value = 50_000_000
        guard._state.current_portfolio_value = 42_000_000  # 16% DD

        result = guard.check_max_drawdown()
        assert result.allowed is True

    def test_mdd_exceeds_limit_activates_kill_switch(self, guard, mock_settings):
        """Test MDD exceeding limit activates kill switch"""
        guard._state.peak_portfolio_value = 50_000_000
        guard._state.current_portfolio_value = 39_000_000  # 22% DD

        result = guard.check_max_drawdown()
        assert result.allowed is False
        assert guard._state.kill_switch_on is True
        assert "낙폭" in guard._state.kill_switch_reason

    def test_mdd_at_exact_boundary(self, guard, mock_settings):
        """Test MDD at exact boundary (20%)"""
        guard._state.peak_portfolio_value = 50_000_000
        guard._state.current_portfolio_value = 40_000_000  # Exactly 20%

        result = guard.check_max_drawdown()
        assert result.allowed is False  # >= triggers
        assert guard._state.kill_switch_on is True

    def test_mdd_with_zero_peak_value(self, guard):
        """Test MDD calculation with zero peak value (edge case)"""
        guard._state.peak_portfolio_value = 0
        guard._state.current_portfolio_value = 10_000_000

        result = guard.check_max_drawdown()
        assert result.allowed is True  # Skips calculation

    def test_mdd_state_updated_with_current_drawdown(self, guard):
        """Test that current_drawdown is updated in state"""
        guard._state.peak_portfolio_value = 50_000_000
        guard._state.current_portfolio_value = 45_000_000  # 10% DD

        guard.check_max_drawdown()
        assert guard._state.current_drawdown == pytest.approx(0.1, rel=1e-5)

    # ── Consecutive Loss Tests ──
    def test_consecutive_losses_within_limit_passes(self, guard, mock_settings):
        """Test consecutive losses within limit passes"""
        guard._state.consecutive_losses = 3  # Within limit of 5

        result = guard.check_consecutive_losses()
        assert result.allowed is True

    def test_consecutive_losses_exceed_limit_activates_kill_switch(
        self, guard, mock_settings
    ):
        """Test consecutive losses exceeding limit activates kill switch"""
        guard._state.consecutive_losses = mock_settings.risk.consecutive_loss_limit

        result = guard.check_consecutive_losses()
        assert result.allowed is False
        assert guard._state.kill_switch_on is True
        assert "연속 손실" in guard._state.kill_switch_reason

    def test_consecutive_losses_at_boundary(self, guard, mock_settings):
        """Test consecutive losses at exact boundary"""
        limit = mock_settings.risk.consecutive_loss_limit
        guard._state.consecutive_losses = limit  # Exactly at limit

        result = guard.check_consecutive_losses()
        assert result.allowed is False  # >= triggers
        assert guard._state.kill_switch_on is True

    def test_consecutive_losses_just_within_limit(self, guard, mock_settings):
        """Test consecutive losses just within limit"""
        limit = mock_settings.risk.consecutive_loss_limit
        guard._state.consecutive_losses = limit - 1

        result = guard.check_consecutive_losses()
        assert result.allowed is True


# ══════════════════════════════════════
# Pre-Order Check Tests
# ══════════════════════════════════════
class TestPreOrderCheck:
    """Test pre-order validation checks"""

    def test_valid_buy_order_passes(self, guard, mock_settings):
        """Test valid BUY order passes all checks"""
        result = guard.pre_order_check(
            order_amount_krw=5_000_000,
            ticker="005930",
            side=OrderSide.BUY,
            current_position_weight=0.05,
            current_sector_weight=0.15,
            new_position_weight=0.10,
            new_sector_weight=0.25,
        )

        assert result.allowed is True
        assert result.reason == ""

    def test_kill_switch_blocks_all_orders(self, guard):
        """Test kill switch blocks all orders"""
        guard._state.kill_switch_on = True
        guard._state.kill_switch_reason = "Daily loss limit exceeded"

        result = guard.pre_order_check(
            order_amount_krw=1_000_000,
            ticker="005930",
            side=OrderSide.BUY,
        )

        assert result.allowed is False
        assert "Kill Switch" in result.reason

    def test_order_amount_exceeds_limit(self, guard, mock_settings):
        """Test BUY order exceeding amount limit"""
        limit = mock_settings.risk.max_order_amount_krw
        result = guard.pre_order_check(
            order_amount_krw=limit + 1_000_000,
            ticker="005930",
            side=OrderSide.BUY,
            new_position_weight=0.10,
        )

        assert result.allowed is False
        assert "주문 금액" in result.reason or "주문 금액" in result.reason

    def test_order_at_amount_boundary(self, guard, mock_settings):
        """Test BUY order at exact amount boundary"""
        limit = mock_settings.risk.max_order_amount_krw
        result = guard.pre_order_check(
            order_amount_krw=limit,
            ticker="005930",
            side=OrderSide.BUY,
            new_position_weight=0.10,
        )

        assert result.allowed is True

    def test_position_weight_exceeds_limit(self, guard, mock_settings):
        """Test BUY order exceeding position weight limit"""
        max_weight = PORTFOLIO_CONSTRAINTS["max_single_weight"]
        result = guard.pre_order_check(
            order_amount_krw=5_000_000,
            ticker="005930",
            side=OrderSide.BUY,
            new_position_weight=max_weight + 0.05,
        )

        assert result.allowed is False
        assert "종목 비중" in result.reason or "weight" in result.reason.lower()

    def test_position_weight_at_boundary(self, guard):
        """Test position weight at exact boundary"""
        max_weight = PORTFOLIO_CONSTRAINTS["max_single_weight"]
        result = guard.pre_order_check(
            order_amount_krw=5_000_000,
            ticker="005930",
            side=OrderSide.BUY,
            new_position_weight=max_weight,
        )

        assert result.allowed is True

    def test_sector_weight_generates_warning(self, guard):
        """Test sector weight exceeding limit generates warning"""
        max_sector = PORTFOLIO_CONSTRAINTS["max_sector_weight"]
        result = guard.pre_order_check(
            order_amount_krw=5_000_000,
            ticker="005930",
            side=OrderSide.BUY,
            new_position_weight=0.10,
            new_sector_weight=max_sector + 0.05,
        )

        assert result.allowed is True  # Still allowed but with warning
        assert len(result.warnings) > 0
        assert any("섹터" in w or "sector" in w.lower() for w in result.warnings)

    def test_sell_orders_bypass_amount_checks(self, guard, mock_settings):
        """Test SELL orders bypass amount/weight checks"""
        limit = mock_settings.risk.max_order_amount_krw
        result = guard.pre_order_check(
            order_amount_krw=limit + 100_000_000,  # Way over limit
            ticker="005930",
            side=OrderSide.SELL,
            new_position_weight=0.50,  # Also over limit
        )

        assert result.allowed is True  # SELL bypasses these checks

    def test_multiple_circuit_breakers_triggered(self, guard, mock_settings):
        """Test that first triggered breaker is returned"""
        # Trigger daily loss limit
        guard._state.daily_realized_pnl = -mock_settings.risk.daily_loss_limit_krw - 1

        result = guard.pre_order_check(
            order_amount_krw=5_000_000,
            ticker="005930",
            side=OrderSide.BUY,
        )

        assert result.allowed is False
        assert "일일 손실" in result.reason or "daily" in result.reason.lower()

    def test_all_checks_in_sequence(self, guard, mock_settings):
        """Test that pre_order_check runs all checks in order"""
        # Kill switch should be checked first
        guard._state.kill_switch_on = True
        guard._state.kill_switch_reason = "Test"

        # Even with good order, should fail
        result = guard.pre_order_check(
            order_amount_krw=1_000_000,
            ticker="005930",
            side=OrderSide.BUY,
        )
        assert result.allowed is False


# ══════════════════════════════════════
# Trade Result Recording Tests
# ══════════════════════════════════════
class TestTradeResultRecording:
    """Test recording of trade results and state updates"""

    def test_positive_pnl_resets_consecutive_losses(self, guard):
        """Test that positive PnL resets consecutive losses counter"""
        guard._state.consecutive_losses = 3

        guard.record_trade_result(pnl=500_000, portfolio_value=50_500_000)

        assert guard._state.consecutive_losses == 0

    def test_negative_pnl_increments_consecutive_losses(self, guard):
        """Test that negative PnL increments consecutive losses"""
        guard._state.consecutive_losses = 2

        guard.record_trade_result(pnl=-300_000, portfolio_value=49_700_000)

        assert guard._state.consecutive_losses == 3

    def test_zero_pnl_resets_consecutive_losses(self, guard):
        """Test that zero PnL (break-even) resets consecutive losses"""
        guard._state.consecutive_losses = 2

        guard.record_trade_result(pnl=0, portfolio_value=50_000_000)

        assert guard._state.consecutive_losses == 0

    def test_peak_portfolio_value_updates_on_new_high(self, guard):
        """Test that peak portfolio value updates on new high"""
        guard._state.peak_portfolio_value = 50_000_000
        guard._state.current_portfolio_value = 50_000_000

        guard.record_trade_result(pnl=2_000_000, portfolio_value=52_000_000)

        assert guard._state.peak_portfolio_value == 52_000_000

    def test_peak_portfolio_value_not_downgraded(self, guard):
        """Test that peak portfolio value is never downgraded"""
        guard._state.peak_portfolio_value = 50_000_000

        guard.record_trade_result(pnl=-3_000_000, portfolio_value=47_000_000)

        assert guard._state.peak_portfolio_value == 50_000_000  # Unchanged

    def test_daily_pnl_accumulates(self, guard):
        """Test that daily PnL accumulates"""
        guard._state.daily_realized_pnl = 1_000_000

        guard.record_trade_result(pnl=500_000, portfolio_value=50_500_000)

        assert guard._state.daily_realized_pnl == 1_500_000

    def test_order_count_increments(self, guard):
        """Test that daily order count increments"""
        guard._state.daily_order_count = 5

        guard.record_trade_result(pnl=100_000, portfolio_value=50_100_000)

        assert guard._state.daily_order_count == 6

    def test_last_updated_timestamp_updated(self, guard):
        """Test that last_updated timestamp is updated"""
        old_time = guard._state.last_updated

        guard.record_trade_result(pnl=100_000, portfolio_value=50_100_000)

        assert guard._state.last_updated > old_time
        assert guard._state.last_updated.tzinfo == timezone.utc

    def test_current_portfolio_value_updated(self, guard):
        """Test that current portfolio value is updated"""
        new_value = 51_000_000

        guard.record_trade_result(pnl=1_000_000, portfolio_value=new_value)

        assert guard._state.current_portfolio_value == new_value

    def test_reset_daily_state_clears_daily_values(self, guard):
        """Test reset_daily_state clears daily statistics"""
        guard._state.daily_realized_pnl = -2_000_000
        guard._state.daily_order_count = 10

        guard.reset_daily_state()

        assert guard._state.daily_realized_pnl == 0.0
        assert guard._state.daily_order_count == 0

    def test_reset_daily_state_preserves_peak_and_consecutive(self, guard):
        """Test that reset_daily_state preserves peak and consecutive losses"""
        guard._state.peak_portfolio_value = 50_000_000
        guard._state.consecutive_losses = 3

        guard.reset_daily_state()

        assert guard._state.peak_portfolio_value == 50_000_000  # Preserved
        assert guard._state.consecutive_losses == 3  # Preserved


# ══════════════════════════════════════
# Kill Switch Tests
# ══════════════════════════════════════
class TestKillSwitch:
    """Test kill switch activation and deactivation"""

    def test_activate_kill_switch_sets_state(self, guard):
        """Test that kill switch activation sets state correctly"""
        reason = "Test activation"

        guard.activate_kill_switch(reason)

        assert guard._state.kill_switch_on is True
        assert guard._state.kill_switch_reason == reason

    def test_deactivate_kill_switch_clears_state(self, guard):
        """Test that deactivation clears kill switch state"""
        guard._state.kill_switch_on = True
        guard._state.kill_switch_reason = "Some reason"

        guard.deactivate_kill_switch()

        assert guard._state.kill_switch_on is False
        assert guard._state.kill_switch_reason == ""

    def test_internal_activate_kill_switch_works(self, guard):
        """Test internal _activate_kill_switch method"""
        reason = "Internal test"

        guard._activate_kill_switch(reason)

        assert guard._state.kill_switch_on is True
        assert guard._state.kill_switch_reason == reason

    def test_kill_switch_reason_preserved(self, guard):
        """Test that kill switch reason is preserved accurately"""
        reason = "MDD limit (22.5%) exceeded: 20% maximum"

        guard.activate_kill_switch(reason)

        assert guard._state.kill_switch_reason == reason


# ══════════════════════════════════════
# Run All Checks Integration Tests
# ══════════════════════════════════════
class TestRunAllChecks:
    """Test run_all_checks integration"""

    def test_all_checks_pass_returns_allowed(self, guard):
        """Test that run_all_checks returns allowed when all checks pass"""
        result = guard.run_all_checks()

        assert result.allowed is True
        assert result.reason == ""

    def test_environment_check_fails_stops_sequence(self, guard, mock_settings):
        """Test that if environment check fails, sequence stops"""
        mock_settings.kis.is_live = True
        mock_settings.is_production = False  # Fail environment check

        result = guard.run_all_checks()

        assert result.allowed is False
        assert "environment" in result.reason.lower() or "production" in result.reason.lower()

    def test_daily_loss_check_fails_stops_sequence(self, guard, mock_settings):
        """Test that daily loss failure stops sequence"""
        limit = mock_settings.risk.daily_loss_limit_krw
        guard._state.daily_realized_pnl = -limit - 1_000_000

        result = guard.run_all_checks()

        assert result.allowed is False
        assert "일일 손실" in result.reason or "daily" in result.reason.lower()

    def test_warnings_accumulated_in_result(self, guard):
        """Test that warnings are accumulated in result"""
        # Trigger a warning condition
        max_sector = PORTFOLIO_CONSTRAINTS["max_sector_weight"]

        # First, get environment check warning (if any would exist)
        # For now, create a scenario with a warning
        # We'll do this by mocking additional scenario

        # Since warnings come from environment check, let's test through
        # pre_order_check which also returns warnings
        result = guard.pre_order_check(
            order_amount_krw=1_000_000,
            ticker="005930",
            side=OrderSide.BUY,
            new_position_weight=0.10,
            new_sector_weight=max_sector + 0.05,
        )

        assert len(result.warnings) > 0

    def test_check_order_prevents_negative_checks(self, guard, mock_settings):
        """Test that checks prevent problematic order execution"""
        # Set up a scenario where MDD is exceeded
        guard._state.peak_portfolio_value = 50_000_000
        guard._state.current_portfolio_value = 39_000_000  # 22% DD

        result = guard.run_all_checks()

        assert result.allowed is False


# ══════════════════════════════════════
# Integration and Edge Cases
# ══════════════════════════════════════
class TestIntegrationAndEdgeCases:
    """Test complex scenarios and edge cases"""

    def test_guard_initialization_with_settings(self, mock_settings):
        """Test that guard properly initializes with settings"""
        with patch("core.trading_guard.get_settings", return_value=mock_settings):
            guard = TradingGuard()

            assert guard._risk.initial_capital_krw == 50_000_000
            assert guard._state.peak_portfolio_value == 50_000_000
            assert guard._state.current_portfolio_value == 50_000_000

    def test_multiple_trades_sequence(self, guard):
        """Test recording multiple consecutive trades"""
        # Trade 1: +500k (positive)
        guard.record_trade_result(500_000, 50_500_000)
        assert guard._state.consecutive_losses == 0
        assert guard._state.daily_order_count == 1

        # Trade 2: -300k (negative)
        guard.record_trade_result(-300_000, 50_200_000)
        assert guard._state.consecutive_losses == 1
        assert guard._state.daily_order_count == 2

        # Trade 3: -200k (negative)
        guard.record_trade_result(-200_000, 50_000_000)
        assert guard._state.consecutive_losses == 2
        assert guard._state.daily_order_count == 3

        # Trade 4: +150k (positive - resets)
        guard.record_trade_result(150_000, 50_150_000)
        assert guard._state.consecutive_losses == 0
        assert guard._state.daily_order_count == 4

    def test_daily_state_reset_workflow(self, guard):
        """Test typical daily workflow with reset"""
        # Trade during day
        guard.record_trade_result(1_000_000, 51_000_000)
        guard.record_trade_result(-500_000, 50_500_000)

        assert guard._state.daily_realized_pnl == 500_000
        assert guard._state.daily_order_count == 2

        # Day ends
        guard.reset_daily_state()

        assert guard._state.daily_realized_pnl == 0.0
        assert guard._state.daily_order_count == 0
        assert guard._state.peak_portfolio_value == 51_000_000  # Preserved

    def test_property_access(self, guard):
        """Test property access to state"""
        state = guard.state

        assert isinstance(state, TradingGuardState)
        assert state.is_active is True


# ══════════════════════════════════════
# Boundary and Extreme Cases
# ══════════════════════════════════════
class TestBoundaryAndExtremeCases:
    """Test boundary conditions and extreme scenarios"""

    def test_very_large_portfolio_value(self, guard):
        """Test with very large portfolio value"""
        guard._state.peak_portfolio_value = 10_000_000_000  # 10 billion won
        guard._state.current_portfolio_value = 9_500_000_000

        result = guard.check_max_drawdown()

        assert result.allowed is True
        assert guard._state.current_drawdown == pytest.approx(0.05, rel=1e-5)

    def test_very_small_portfolio_loss(self, guard):
        """Test with very small portfolio loss"""
        guard._state.peak_portfolio_value = 50_000_000
        guard._state.current_portfolio_value = 49_999_999  # 1 won loss

        result = guard.check_max_drawdown()

        assert result.allowed is True
        assert guard._state.current_drawdown > 0

    def test_negative_portfolio_value(self, guard):
        """Test edge case with negative portfolio value"""
        guard._state.peak_portfolio_value = 50_000_000
        guard._state.current_portfolio_value = -1_000_000  # Negative (unlikely but handle)

        result = guard.check_max_drawdown()

        # Should trigger kill switch due to extreme drawdown
        assert result.allowed is False

    def test_huge_single_loss(self, guard, mock_settings):
        """Test with single trade causing massive loss"""
        guard._state.daily_realized_pnl = -100_000_000  # Way over limit

        result = guard.check_daily_loss_limit()

        assert result.allowed is False
        assert guard._state.kill_switch_on is True

    def test_many_consecutive_losses(self, guard, mock_settings):
        """Test with many consecutive losses beyond limit"""
        guard._state.consecutive_losses = 100

        result = guard.check_consecutive_losses()

        assert result.allowed is False
        assert guard._state.kill_switch_on is True
