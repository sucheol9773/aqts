"""
Gate C 손실 한도 시뮬레이션 테스트

일일 손실 -3%, MDD -20%, 연속 손실 5회 등
실제 트레이딩 시나리오를 시뮬레이션합니다.
"""

import unittest
from unittest.mock import MagicMock, patch


def _make_guard(
    initial_capital=50_000_000,
    daily_loss_limit_krw=1_500_000,
    max_drawdown=0.20,
    consecutive_loss_limit=5,
):
    """테스트용 TradingGuard 생성"""
    settings = MagicMock()
    settings.risk.initial_capital_krw = initial_capital
    settings.risk.daily_loss_limit_krw = daily_loss_limit_krw
    settings.risk.max_drawdown = max_drawdown
    settings.risk.consecutive_loss_limit = consecutive_loss_limit
    settings.risk.max_order_amount_krw = 10_000_000
    settings.kis.is_live = True
    settings.is_production = True
    settings.kis.active_credential = MagicMock(
        app_key="test_key",
        app_secret="test_secret",
        account_no="12345678-01",
    )
    settings.kis.live_app_key = "live_key"
    settings.kis.demo_app_key = "demo_key"

    with patch("core.trading_guard.get_settings", return_value=settings):
        from core.trading_guard import TradingGuard

        guard = TradingGuard()
    return guard


# ══════════════════════════════════════
# 1. 일일 손실 한도 시뮬레이션 (-3% = -1,500,000원 on 50M)
# ══════════════════════════════════════


class TestDailyLossSimulation(unittest.TestCase):
    """일일 손실 한도 시나리오 시뮬레이션"""

    def test_gradual_loss_triggers_at_threshold(self):
        """점진적 손실 누적이 한도에서 트리거되어야 함"""
        guard = _make_guard(daily_loss_limit_krw=1_500_000)

        # 3번의 거래로 점진적 손실 (-500K × 3 = -1.5M)
        guard.record_trade_result(-500_000, 49_500_000)
        result = guard.check_daily_loss_limit()
        assert result.allowed is True

        guard.record_trade_result(-500_000, 49_000_000)
        result = guard.check_daily_loss_limit()
        assert result.allowed is True

        guard.record_trade_result(-500_000, 48_500_000)
        result = guard.check_daily_loss_limit()
        assert result.allowed is False
        assert guard.state.kill_switch_on is True

    def test_single_large_loss_triggers_immediately(self):
        """단일 대형 손실이 즉시 트리거되어야 함"""
        guard = _make_guard(daily_loss_limit_krw=1_500_000)

        guard.record_trade_result(-2_000_000, 48_000_000)
        result = guard.check_daily_loss_limit()
        assert result.allowed is False
        assert "일일 손실 한도" in result.reason

    def test_loss_just_below_threshold_passes(self):
        """한도 바로 아래 손실은 통과되어야 함"""
        guard = _make_guard(daily_loss_limit_krw=1_500_000)

        guard.record_trade_result(-1_499_999, 48_500_001)
        result = guard.check_daily_loss_limit()
        assert result.allowed is True
        assert guard.state.kill_switch_on is False

    def test_exact_boundary_loss_triggers(self):
        """정확히 한도 금액의 손실에서 트리거되어야 함"""
        guard = _make_guard(daily_loss_limit_krw=1_500_000)

        guard.record_trade_result(-1_500_000, 48_500_000)
        result = guard.check_daily_loss_limit()
        assert result.allowed is False

    def test_profit_then_loss_net_positive(self):
        """이익 후 손실로 순손실이 한도 이내이면 통과"""
        guard = _make_guard(daily_loss_limit_krw=1_500_000)

        guard.record_trade_result(500_000, 50_500_000)
        guard.record_trade_result(-1_800_000, 48_700_000)

        # 순 일일 PnL = +500K - 1.8M = -1.3M (한도 1.5M 이내)
        result = guard.check_daily_loss_limit()
        assert result.allowed is True

    def test_profit_then_loss_exceeds_threshold(self):
        """이익 후 손실이 순손실 한도 초과 시 트리거"""
        guard = _make_guard(daily_loss_limit_krw=1_500_000)

        guard.record_trade_result(200_000, 50_200_000)
        guard.record_trade_result(-1_800_000, 48_400_000)
        # 순 PnL = -1.6M > -1.5M 한도
        result = guard.check_daily_loss_limit()
        assert result.allowed is False

    def test_daily_reset_clears_accumulated_loss(self):
        """일일 리셋 후 손실 누적이 초기화되어야 함"""
        guard = _make_guard(daily_loss_limit_krw=1_500_000)

        guard.record_trade_result(-1_000_000, 49_000_000)
        assert guard.state.daily_realized_pnl == -1_000_000

        guard.reset_daily_state()
        assert guard.state.daily_realized_pnl == 0.0

        result = guard.check_daily_loss_limit()
        assert result.allowed is True

    def test_percentage_based_3_percent_on_50m(self):
        """50M 자본금에서 3% 손실 한도 = 1.5M 확인"""
        capital = 50_000_000
        daily_limit = int(capital * 0.03)  # 1,500,000원
        guard = _make_guard(initial_capital=capital, daily_loss_limit_krw=daily_limit)

        guard.record_trade_result(-daily_limit, capital - daily_limit)
        result = guard.check_daily_loss_limit()
        assert result.allowed is False


# ══════════════════════════════════════
# 2. 최대 낙폭(MDD) 시뮬레이션
# ══════════════════════════════════════


class TestMDDSimulation(unittest.TestCase):
    """최대 낙폭(MDD) 한도 시뮬레이션"""

    def test_mdd_20_percent_triggers_halt(self):
        """20% MDD에서 킬 스위치 작동"""
        guard = _make_guard(initial_capital=50_000_000, max_drawdown=0.20)

        # 50M → 40M = 20% 낙폭
        guard.record_trade_result(-10_000_000, 40_000_000)
        result = guard.check_max_drawdown()
        assert result.allowed is False
        assert "MDD" in result.reason

    def test_mdd_recovery_then_new_drop(self):
        """고점 갱신 후 새로운 낙폭 측정"""
        guard = _make_guard(initial_capital=50_000_000, max_drawdown=0.20)

        # 50M → 55M (고점 갱신)
        guard.record_trade_result(5_000_000, 55_000_000)
        assert guard.state.peak_portfolio_value == 55_000_000

        # 55M → 45M = 18.2% (한도 이내)
        guard.record_trade_result(-10_000_000, 45_000_000)
        result = guard.check_max_drawdown()
        assert result.allowed is True

        # 45M → 43M = 21.8% (한도 초과)
        guard.record_trade_result(-2_000_000, 43_000_000)
        result = guard.check_max_drawdown()
        assert result.allowed is False

    def test_mdd_just_below_threshold(self):
        """MDD 한도 바로 아래에서 통과"""
        guard = _make_guard(initial_capital=50_000_000, max_drawdown=0.20)

        # 50M → 40,000,001 = 19.99...% (한도 미만)
        guard.record_trade_result(-9_999_999, 40_000_001)
        result = guard.check_max_drawdown()
        assert result.allowed is True

    def test_mdd_tracks_current_drawdown_state(self):
        """MDD 상태가 current_drawdown에 정확히 반영"""
        guard = _make_guard(initial_capital=100_000_000, max_drawdown=0.20)

        guard.record_trade_result(-15_000_000, 85_000_000)
        guard.check_max_drawdown()
        assert abs(guard.state.current_drawdown - 0.15) < 0.001


# ══════════════════════════════════════
# 3. 연속 손실 제한 시뮬레이션
# ══════════════════════════════════════


class TestConsecutiveLossSimulation(unittest.TestCase):
    """연속 손실 제한 시뮬레이션"""

    def test_5_consecutive_losses_triggers(self):
        """5회 연속 손실에서 킬 스위치 작동"""
        guard = _make_guard(consecutive_loss_limit=5)

        for i in range(5):
            guard.record_trade_result(-100_000, 50_000_000 - (i + 1) * 100_000)

        assert guard.state.consecutive_losses == 5
        result = guard.check_consecutive_losses()
        assert result.allowed is False

    def test_profit_breaks_consecutive_count(self):
        """수익 거래가 연속 손실 카운트를 초기화"""
        guard = _make_guard(consecutive_loss_limit=5)

        # 4연속 손실
        for i in range(4):
            guard.record_trade_result(-100_000, 50_000_000 - (i + 1) * 100_000)
        assert guard.state.consecutive_losses == 4

        # 수익 거래로 리셋
        guard.record_trade_result(50_000, 49_650_000)
        assert guard.state.consecutive_losses == 0

        result = guard.check_consecutive_losses()
        assert result.allowed is True

    def test_4_losses_within_limit(self):
        """4회 연속 손실은 한도 이내"""
        guard = _make_guard(consecutive_loss_limit=5)

        for i in range(4):
            guard.record_trade_result(-100_000, 50_000_000 - (i + 1) * 100_000)

        result = guard.check_consecutive_losses()
        assert result.allowed is True


# ══════════════════════════════════════
# 4. 복합 시나리오 (일일 + MDD + 연속 손실)
# ══════════════════════════════════════


class TestCombinedLossScenarios(unittest.TestCase):
    """복합 손실 시나리오"""

    def test_daily_loss_and_mdd_simultaneous(self):
        """일일 손실과 MDD가 동시에 초과되는 시나리오"""
        guard = _make_guard(
            initial_capital=50_000_000,
            daily_loss_limit_krw=1_500_000,
            max_drawdown=0.05,  # 낮은 MDD 한도
        )

        # 큰 손실 발생
        guard.record_trade_result(-3_000_000, 47_000_000)

        daily_check = guard.check_daily_loss_limit()
        mdd_check = guard.check_max_drawdown()

        assert daily_check.allowed is False
        assert mdd_check.allowed is False
        assert guard.state.kill_switch_on is True

    def test_kill_switch_blocks_all_subsequent_orders(self):
        """킬 스위치 활성화 후 모든 주문이 차단되어야 함"""
        from config.constants import OrderSide

        guard = _make_guard(daily_loss_limit_krw=1_500_000)

        # 킬 스위치 트리거
        guard.record_trade_result(-2_000_000, 48_000_000)
        guard.check_daily_loss_limit()
        assert guard.state.kill_switch_on is True

        # 매수 주문 차단
        result = guard.pre_order_check(
            order_amount_krw=100_000,
            ticker="005930",
            side=OrderSide.BUY,
        )
        assert result.allowed is False
        assert "Kill Switch" in result.reason

    def test_kill_switch_deactivation_and_resume(self):
        """킬 스위치 해제 후 거래 재개"""
        from config.constants import OrderSide

        guard = _make_guard(daily_loss_limit_krw=1_500_000)

        # 킬 스위치 트리거
        guard.record_trade_result(-2_000_000, 48_000_000)
        guard.check_daily_loss_limit()
        assert guard.state.kill_switch_on is True

        # 관리자가 킬 스위치 해제 + 일일 리셋
        guard.deactivate_kill_switch()
        guard.reset_daily_state()

        assert guard.state.kill_switch_on is False

        # 거래 재개 가능
        result = guard.pre_order_check(
            order_amount_krw=100_000,
            ticker="005930",
            side=OrderSide.BUY,
        )
        assert result.allowed is True

    def test_run_all_checks_stops_at_first_failure(self):
        """run_all_checks는 첫 번째 실패에서 중단"""
        guard = _make_guard(
            daily_loss_limit_krw=1_500_000,
            max_drawdown=0.05,
        )

        guard.record_trade_result(-2_000_000, 47_000_000)
        result = guard.run_all_checks()
        assert result.allowed is False

    def test_multi_day_accumulation_with_reset(self):
        """여러 날에 걸친 손실 누적 + 리셋 시뮬레이션"""
        guard = _make_guard(
            initial_capital=50_000_000,
            daily_loss_limit_krw=1_500_000,
            max_drawdown=0.20,
        )

        # Day 1: -1M 손실
        guard.record_trade_result(-1_000_000, 49_000_000)
        result = guard.check_daily_loss_limit()
        assert result.allowed is True

        # Day 1 종료 → 리셋
        guard.reset_daily_state()

        # Day 2: -1M 손실 (일일 한도 이내)
        guard.record_trade_result(-1_000_000, 48_000_000)
        daily_result = guard.check_daily_loss_limit()
        assert daily_result.allowed is True

        # 하지만 MDD는 누적: (50M - 48M) / 50M = 4%
        mdd_result = guard.check_max_drawdown()
        assert mdd_result.allowed is True
        assert abs(guard.state.current_drawdown - 0.04) < 0.001
