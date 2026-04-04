"""
Gate C 매매 중단/재개 테스트

HALTED 상태 전이, 미체결 주문 차단, 킬 스위치 연동,
복구 절차를 검증합니다.
"""

import unittest
from unittest.mock import MagicMock, patch

import pytest

from core.state_machine import (
    InvalidTransitionError,
    PipelineState,
    PipelineStateMachine,
)

# ══════════════════════════════════════
# 1. HALTED 상태 전이 테스트
# ══════════════════════════════════════


class TestHaltedTransitions(unittest.TestCase):
    """HALTED 상태 전이 규칙 검증"""

    def test_halt_from_trading_state(self):
        """TRADING → HALTED 전이 가능"""
        sm = PipelineStateMachine(PipelineState.TRADING)
        result = sm.halt("긴급 정지: MDD 초과")
        assert result == PipelineState.HALTED

    def test_halt_from_collecting_state(self):
        """COLLECTING → HALTED 전이 가능"""
        sm = PipelineStateMachine(PipelineState.COLLECTING)
        result = sm.halt("데이터 수집 중 장애")
        assert result == PipelineState.HALTED

    def test_halt_from_analyzing_state(self):
        """ANALYZING → HALTED 전이 가능"""
        sm = PipelineStateMachine(PipelineState.ANALYZING)
        result = sm.halt("분석 중 비상 정지")
        assert result == PipelineState.HALTED

    def test_halt_from_constructing_state(self):
        """CONSTRUCTING → HALTED 전이 가능"""
        sm = PipelineStateMachine(PipelineState.CONSTRUCTING)
        result = sm.halt("포트폴리오 구성 중 비상")
        assert result == PipelineState.HALTED

    def test_halt_from_validating_state(self):
        """VALIDATING → HALTED 전이 가능"""
        sm = PipelineStateMachine(PipelineState.VALIDATING)
        result = sm.halt("검증 중 비상 정지")
        assert result == PipelineState.HALTED

    def test_halt_from_reconciling_state(self):
        """RECONCILING → HALTED 전이 가능"""
        sm = PipelineStateMachine(PipelineState.RECONCILING)
        result = sm.halt("대사 중 비상 정지")
        assert result == PipelineState.HALTED

    def test_halt_from_idle_is_forced(self):
        """IDLE → HALTED는 강제 전이"""
        sm = PipelineStateMachine(PipelineState.IDLE)
        # IDLE에서 HALTED는 정상 전이 목록에 없지만, 강제 전이 가능
        result = sm.halt("유지보수 정지")
        assert result == PipelineState.HALTED
        assert "FORCED" in sm.history[-1][1]

    def test_halted_only_transitions_to_idle(self):
        """HALTED에서는 IDLE로만 전이 가능"""
        sm = PipelineStateMachine(PipelineState.HALTED)

        assert sm.can_transition(PipelineState.IDLE) is True
        assert sm.can_transition(PipelineState.TRADING) is False
        assert sm.can_transition(PipelineState.COLLECTING) is False
        assert sm.can_transition(PipelineState.ERROR) is False

    def test_halted_to_idle_reset(self):
        """HALTED → IDLE 리셋 가능"""
        sm = PipelineStateMachine(PipelineState.HALTED)
        result = sm.reset("비상 해제 후 재시작")
        assert result == PipelineState.IDLE

    def test_halt_records_reason_in_history(self):
        """HALT 사유가 이력에 기록됨"""
        sm = PipelineStateMachine(PipelineState.TRADING)
        sm.halt("MDD -20% 도달")

        last_entry = sm.history[-1]
        assert last_entry[0] == PipelineState.HALTED
        assert "MDD -20% 도달" in last_entry[1]


# ══════════════════════════════════════
# 2. TradingGuard + 상태 머신 연동
# ══════════════════════════════════════


class TestHaltWithKillSwitch(unittest.TestCase):
    """TradingGuard 킬 스위치 + 파이프라인 HALTED 연동"""

    def _make_guard(self):
        settings = MagicMock()
        settings.risk.initial_capital_krw = 50_000_000
        settings.risk.daily_loss_limit_krw = 1_500_000
        settings.risk.max_drawdown = 0.20
        settings.risk.consecutive_loss_limit = 5
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

    def test_loss_triggers_halt_and_kill_switch(self):
        """손실 한도 초과 → 킬 스위치 + HALTED 전이"""
        guard = self._make_guard()
        sm = PipelineStateMachine(PipelineState.TRADING)

        # 큰 손실 발생
        guard.record_trade_result(-2_000_000, 48_000_000)
        check = guard.check_daily_loss_limit()

        # 킬 스위치 활성화
        assert guard.state.kill_switch_on is True
        assert check.allowed is False

        # 파이프라인도 HALTED
        sm.halt(f"킬 스위치: {guard.state.kill_switch_reason}")
        assert sm.state == PipelineState.HALTED

    def test_kill_switch_blocks_orders_in_halted_state(self):
        """HALTED 상태에서 킬 스위치가 주문을 차단"""
        from config.constants import OrderSide

        guard = self._make_guard()
        sm = PipelineStateMachine(PipelineState.TRADING)

        # 킬 스위치 트리거 + HALT
        guard.activate_kill_switch("수동 비상 정지")
        sm.halt("수동 비상 정지")

        # 주문 시도 → 차단
        result = guard.pre_order_check(
            order_amount_krw=100_000,
            ticker="005930",
            side=OrderSide.BUY,
        )
        assert result.allowed is False
        assert "Kill Switch" in result.reason
        assert sm.state == PipelineState.HALTED

    def test_full_halt_and_resume_workflow(self):
        """전체 중단-복구 워크플로우"""
        from config.constants import OrderSide

        guard = self._make_guard()
        sm = PipelineStateMachine(PipelineState.TRADING)

        # 1. 손실 → 킬 스위치 → HALT
        guard.record_trade_result(-2_000_000, 48_000_000)
        guard.check_daily_loss_limit()
        sm.halt("일일 손실 한도 초과")

        assert sm.state == PipelineState.HALTED
        assert guard.state.kill_switch_on is True

        # 2. 관리자 복구 조치
        guard.deactivate_kill_switch()
        guard.reset_daily_state()
        sm.reset("관리자 승인 후 재시작")

        assert sm.state == PipelineState.IDLE
        assert guard.state.kill_switch_on is False

        # 3. 파이프라인 재시작
        sm.transition(PipelineState.COLLECTING, "파이프라인 재시작")
        assert sm.state == PipelineState.COLLECTING

        # 4. 주문 가능
        result = guard.pre_order_check(
            order_amount_krw=100_000,
            ticker="005930",
            side=OrderSide.BUY,
        )
        assert result.allowed is True

    def test_mdd_triggers_halt_from_reconciling(self):
        """대사 중 MDD 초과 → HALTED"""
        guard = self._make_guard()
        sm = PipelineStateMachine(PipelineState.RECONCILING)

        guard.record_trade_result(-12_000_000, 38_000_000)
        check = guard.check_max_drawdown()

        assert check.allowed is False
        sm.halt("MDD 한도 초과")
        assert sm.state == PipelineState.HALTED


# ══════════════════════════════════════
# 3. 중단 시 주문 상태 관리
# ══════════════════════════════════════


class TestHaltOrderManagement(unittest.TestCase):
    """중단 시 주문 상태 관리"""

    def test_halt_prevents_new_pipeline_cycle(self):
        """HALTED 상태에서 새 파이프라인 사이클 시작 불가"""
        sm = PipelineStateMachine(PipelineState.HALTED)

        assert sm.can_transition(PipelineState.COLLECTING) is False
        assert sm.can_transition(PipelineState.ANALYZING) is False
        assert sm.can_transition(PipelineState.TRADING) is False

        with pytest.raises(InvalidTransitionError):
            sm.transition(PipelineState.COLLECTING, "시도")

    def test_error_state_can_transition_to_halted(self):
        """ERROR → HALTED 전이 가능"""
        sm = PipelineStateMachine(PipelineState.ERROR)
        assert sm.can_transition(PipelineState.HALTED) is True

        result = sm.transition(PipelineState.HALTED, "에러 후 비상 정지")
        assert result == PipelineState.HALTED

    def test_consecutive_halts_idempotent(self):
        """이미 HALTED 상태에서 다시 halt() 호출해도 안전"""
        sm = PipelineStateMachine(PipelineState.HALTED)
        result = sm.halt("중복 정지 시도")
        assert result == PipelineState.HALTED
        # 강제 전이이므로 FORCED가 기록됨
        assert "FORCED" in sm.history[-1][1]

    def test_halt_resume_history_preserved(self):
        """중단-복구 이력이 완전히 보존"""
        sm = PipelineStateMachine()

        sm.transition(PipelineState.COLLECTING, "시작")
        sm.transition(PipelineState.ANALYZING, "분석 개시")
        sm.halt("비상 정지")
        sm.reset("복구")
        sm.transition(PipelineState.COLLECTING, "재시작")

        assert len(sm.history) == 6  # IDLE + 4 transitions + initial
        states = [h[0] for h in sm.history]
        assert PipelineState.HALTED in states
        assert states[-1] == PipelineState.COLLECTING


# ══════════════════════════════════════
# 4. 복구 검증
# ══════════════════════════════════════


class TestRecoveryValidation(unittest.TestCase):
    """복구 절차 검증"""

    def test_reset_returns_to_clean_idle(self):
        """리셋 후 IDLE 상태 확인"""
        sm = PipelineStateMachine(PipelineState.HALTED)
        result = sm.reset("복구 완료")
        assert result == PipelineState.IDLE
        assert sm.state == PipelineState.IDLE

    def test_guard_state_after_reset(self):
        """TradingGuard 리셋 후 상태 검증"""
        settings = MagicMock()
        settings.risk.initial_capital_krw = 50_000_000
        settings.risk.daily_loss_limit_krw = 1_500_000
        settings.risk.max_drawdown = 0.20
        settings.risk.consecutive_loss_limit = 5
        settings.risk.max_order_amount_krw = 10_000_000
        settings.kis.is_live = False
        settings.is_production = False

        with patch("core.trading_guard.get_settings", return_value=settings):
            from core.trading_guard import TradingGuard

            guard = TradingGuard()

        # 손실 누적
        guard.record_trade_result(-1_000_000, 49_000_000)
        guard.record_trade_result(-1_000_000, 48_000_000)
        assert guard.state.daily_realized_pnl == -2_000_000
        assert guard.state.daily_order_count == 2

        # 리셋
        guard.reset_daily_state()
        assert guard.state.daily_realized_pnl == 0.0
        assert guard.state.daily_order_count == 0
        # MDD 상태는 리셋되지 않음 (의도적)
        assert guard.state.current_portfolio_value == 48_000_000

    def test_forced_reset_from_error(self):
        """ERROR 상태에서 강제 리셋"""
        sm = PipelineStateMachine(PipelineState.ERROR)
        result = sm.reset("에러 복구")
        assert result == PipelineState.IDLE

    def test_full_pipeline_cycle_after_recovery(self):
        """복구 후 전체 파이프라인 사이클 실행 가능"""
        sm = PipelineStateMachine()

        # 정상 흐름 시작 → HALT
        sm.transition(PipelineState.COLLECTING, "시작")
        sm.halt("테스트 중단")
        assert sm.state == PipelineState.HALTED

        # 복구
        sm.reset("복구 완료")
        assert sm.state == PipelineState.IDLE

        # 새 사이클 실행
        sm.transition(PipelineState.COLLECTING, "재시작")
        sm.transition(PipelineState.ANALYZING, "분석")
        sm.transition(PipelineState.CONSTRUCTING, "구성")
        sm.transition(PipelineState.VALIDATING, "검증")
        sm.transition(PipelineState.TRADING, "매매")
        sm.transition(PipelineState.RECONCILING, "대사")
        sm.transition(PipelineState.COMPLETED, "완료")

        assert sm.state == PipelineState.COMPLETED
