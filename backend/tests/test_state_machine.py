"""
Stage 2-B 테스트: Pipeline Gates + StateMachine + GateRegistry + FallbackHandler

로드맵 목표: 10+ 통합 시나리오
"""

import unittest
from types import SimpleNamespace

import pytest

from core.fallback_handler import FallbackHandler
from core.gate_registry import GateRegistry
from core.gates.base import GateDecision, GateResult, GateSeverity
from core.gates.data_gate import DataGate
from core.gates.ensemble_gate import EnsembleGate
from core.gates.execution_gate import ExecutionGate
from core.gates.factor_gate import FactorGate
from core.gates.fill_gate import FillGate
from core.gates.portfolio_gate import PortfolioGate
from core.gates.recon_gate import ReconGate
from core.gates.signal_gate import SignalGate
from core.gates.trading_guard_gate import TradingGuardGate
from core.state_machine import (
    InvalidTransitionError,
    PipelineState,
    PipelineStateMachine,
)

# ══════════════════════════════════════════════════════════════
# 1. GateResult 스키마 테스트
# ══════════════════════════════════════════════════════════════


class TestGateResult:
    def test_pass_result(self):
        r = GateResult(gate_id="DataGate", decision=GateDecision.PASS)
        assert r.decision == GateDecision.PASS

    def test_block_result(self):
        r = GateResult(
            gate_id="DataGate",
            decision=GateDecision.BLOCK,
            reason="데이터 없음",
            severity=GateSeverity.CRITICAL,
        )
        assert r.severity == GateSeverity.CRITICAL

    def test_immutable(self):
        r = GateResult(gate_id="X", decision=GateDecision.PASS)
        with pytest.raises(Exception):
            r.decision = GateDecision.BLOCK

    def test_with_context(self):
        r = GateResult(
            gate_id="DataGate",
            decision=GateDecision.PASS,
            context={"count": 100},
        )
        assert r.context["count"] == 100


# ══════════════════════════════════════════════════════════════
# 2. 개별 Gate 테스트
# ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestDataGate(unittest.IsolatedAsyncioTestCase):
    async def test_pass_with_data(self):
        gate = DataGate()
        result = await gate.evaluate([{"ticker": "005930"}])
        assert result.decision == GateDecision.PASS

    async def test_block_empty_data(self):
        gate = DataGate()
        result = await gate.evaluate([])
        assert result.decision == GateDecision.BLOCK

    async def test_block_none_data(self):
        gate = DataGate()
        result = await gate.evaluate(None)
        assert result.decision == GateDecision.BLOCK

    async def test_block_high_outlier(self):
        gate = DataGate()
        result = await gate.evaluate([{"x": 1}], outlier_ratio=0.10)
        assert result.decision == GateDecision.BLOCK

    async def test_pass_low_outlier(self):
        gate = DataGate()
        result = await gate.evaluate([{"x": 1}], outlier_ratio=0.01)
        assert result.decision == GateDecision.PASS


@pytest.mark.asyncio
class TestFactorGate(unittest.IsolatedAsyncioTestCase):
    async def test_pass(self):
        gate = FactorGate()
        data = [SimpleNamespace(factor_value=0.5, factor_momentum=0.3)]
        result = await gate.evaluate(data)
        assert result.decision == GateDecision.PASS

    async def test_block_empty(self):
        gate = FactorGate()
        result = await gate.evaluate([])
        assert result.decision == GateDecision.BLOCK

    async def test_block_low_coverage(self):
        gate = FactorGate()
        data = [SimpleNamespace(), SimpleNamespace(factor_value=0.1)]
        result = await gate.evaluate(data, min_coverage=0.8)
        assert result.decision == GateDecision.BLOCK


@pytest.mark.asyncio
class TestSignalGate(unittest.IsolatedAsyncioTestCase):
    async def test_pass_with_buy(self):
        gate = SignalGate()
        signals = [SimpleNamespace(direction=SimpleNamespace(value="BUY"))]
        result = await gate.evaluate(signals)
        assert result.decision == GateDecision.PASS

    async def test_block_all_hold(self):
        gate = SignalGate()
        signals = [
            SimpleNamespace(direction=SimpleNamespace(value="HOLD")),
            SimpleNamespace(direction=SimpleNamespace(value="HOLD")),
        ]
        result = await gate.evaluate(signals)
        assert result.decision == GateDecision.BLOCK

    async def test_block_empty(self):
        gate = SignalGate()
        result = await gate.evaluate([])
        assert result.decision == GateDecision.BLOCK


@pytest.mark.asyncio
class TestEnsembleGate(unittest.IsolatedAsyncioTestCase):
    async def test_pass(self):
        gate = EnsembleGate()
        result = await gate.evaluate({"A": 0.5, "B": 0.5})
        assert result.decision == GateDecision.PASS

    async def test_block_weight_sum(self):
        gate = EnsembleGate()
        result = await gate.evaluate({"A": 0.5, "B": 0.3})
        assert result.decision == GateDecision.BLOCK

    async def test_block_concentrated(self):
        gate = EnsembleGate()
        result = await gate.evaluate(
            {"A": 0.9, "B": 0.1},
            max_single_strategy_weight=0.6,
        )
        assert result.decision == GateDecision.BLOCK


@pytest.mark.asyncio
class TestPortfolioGate(unittest.IsolatedAsyncioTestCase):
    async def test_pass(self):
        gate = PortfolioGate()
        portfolio = SimpleNamespace(
            positions=[SimpleNamespace(ticker="A", target_weight=0.2)],
            cash_weight=0.8,
        )
        result = await gate.evaluate(portfolio)
        assert result.decision == GateDecision.PASS

    async def test_block_concentrated(self):
        gate = PortfolioGate()
        portfolio = SimpleNamespace(
            positions=[SimpleNamespace(ticker="A", target_weight=0.5)],
            cash_weight=0.5,
        )
        result = await gate.evaluate(portfolio, max_single_weight=0.2)
        assert result.decision == GateDecision.BLOCK


@pytest.mark.asyncio
class TestTradingGuardGate(unittest.IsolatedAsyncioTestCase):
    async def test_pass(self):
        gate = TradingGuardGate()
        result = await gate.evaluate(
            None,
            guard_result={"approved": True, "reason": "OK"},
        )
        assert result.decision == GateDecision.PASS

    async def test_block(self):
        gate = TradingGuardGate()
        result = await gate.evaluate(
            None,
            guard_result={"approved": False, "reason": "MDD 초과"},
        )
        assert result.decision == GateDecision.BLOCK

    async def test_no_guard(self):
        gate = TradingGuardGate()
        result = await gate.evaluate(None)
        assert result.decision == GateDecision.BLOCK


@pytest.mark.asyncio
class TestReconGate(unittest.IsolatedAsyncioTestCase):
    async def test_pass(self):
        gate = ReconGate()
        data = {"broker_balance": 1_000_000, "internal_balance": 1_000_000, "mismatches": []}
        result = await gate.evaluate(data)
        assert result.decision == GateDecision.PASS

    async def test_block_mismatch(self):
        gate = ReconGate()
        data = {"broker_balance": 1_000_000, "internal_balance": 900_000, "mismatches": ["qty"]}
        result = await gate.evaluate(data)
        assert result.decision == GateDecision.BLOCK


@pytest.mark.asyncio
class TestExecutionGate(unittest.IsolatedAsyncioTestCase):
    async def test_pass(self):
        gate = ExecutionGate()
        data = {"submitted": True, "broker_order_id": "X-001"}
        result = await gate.evaluate(data)
        assert result.decision == GateDecision.PASS

    async def test_block_error(self):
        gate = ExecutionGate()
        data = {"submitted": False, "error": "Connection timeout"}
        result = await gate.evaluate(data)
        assert result.decision == GateDecision.BLOCK


@pytest.mark.asyncio
class TestFillGate(unittest.IsolatedAsyncioTestCase):
    async def test_pass_full_fill(self):
        gate = FillGate()
        data = {"status": "FILLED", "requested_quantity": 100, "filled_quantity": 100}
        result = await gate.evaluate(data)
        assert result.decision == GateDecision.PASS

    async def test_block_failed(self):
        gate = FillGate()
        data = {"status": "FAILED", "requested_quantity": 100, "filled_quantity": 0}
        result = await gate.evaluate(data)
        assert result.decision == GateDecision.BLOCK

    async def test_block_low_fill(self):
        gate = FillGate()
        data = {"status": "PARTIAL", "requested_quantity": 100, "filled_quantity": 5}
        result = await gate.evaluate(data, min_fill_ratio=0.1)
        assert result.decision == GateDecision.BLOCK


# ══════════════════════════════════════════════════════════════
# 3. GateRegistry 테스트
# ══════════════════════════════════════════════════════════════


@pytest.mark.smoke
class TestGateRegistry(unittest.IsolatedAsyncioTestCase):
    async def test_register_and_evaluate(self):
        registry = GateRegistry()
        registry.register(DataGate())
        assert len(registry) == 1
        result = await registry.evaluate_single("DataGate", [{"data": 1}])
        assert result.decision == GateDecision.PASS

    async def test_evaluate_all_pass(self):
        registry = GateRegistry()
        registry.register(DataGate())
        registry.register(SignalGate())
        data_map = {
            "DataGate": [{"data": 1}],
            "SignalGate": [SimpleNamespace(direction=SimpleNamespace(value="BUY"))],
        }
        results = await registry.evaluate_all(data_map)
        assert all(r.decision == GateDecision.PASS for r in results)

    async def test_evaluate_all_stop_on_block(self):
        registry = GateRegistry()
        registry.register(DataGate())
        registry.register(SignalGate())
        data_map = {"DataGate": [], "SignalGate": []}
        results = await registry.evaluate_all(data_map, stop_on_block=True)
        assert len(results) == 1  # DataGate에서 차단, SignalGate 실행 안됨
        assert results[0].decision == GateDecision.BLOCK

    async def test_unregister(self):
        registry = GateRegistry()
        registry.register(DataGate())
        registry.unregister("DataGate")
        assert len(registry) == 0

    async def test_unknown_gate_raises(self):
        registry = GateRegistry()
        with pytest.raises(ValueError, match="등록되지 않은"):
            await registry.evaluate_single("UnknownGate", {})


# ══════════════════════════════════════════════════════════════
# 4. StateMachine 테스트
# ══════════════════════════════════════════════════════════════


@pytest.mark.smoke
class TestPipelineStateMachine:
    def test_initial_state(self):
        sm = PipelineStateMachine()
        assert sm.state == PipelineState.IDLE

    def test_valid_transition(self):
        sm = PipelineStateMachine()
        sm.transition(PipelineState.COLLECTING, "시작")
        assert sm.state == PipelineState.COLLECTING

    def test_full_cycle(self):
        sm = PipelineStateMachine()
        sm.transition(PipelineState.COLLECTING, "수집 시작")
        sm.transition(PipelineState.ANALYZING, "분석 시작")
        sm.transition(PipelineState.CONSTRUCTING, "구성 시작")
        sm.transition(PipelineState.VALIDATING, "검증 시작")
        sm.transition(PipelineState.TRADING, "매매 시작")
        sm.transition(PipelineState.RECONCILING, "대사 시작")
        sm.transition(PipelineState.COMPLETED, "완료")
        sm.transition(PipelineState.IDLE, "리셋")
        assert sm.state == PipelineState.IDLE
        assert len(sm.history) == 9  # 초기 + 8 전이

    def test_invalid_transition_raises(self):
        sm = PipelineStateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.transition(PipelineState.TRADING)  # IDLE → TRADING 불가

    def test_halt_from_trading(self):
        sm = PipelineStateMachine()
        sm.transition(PipelineState.COLLECTING)
        sm.transition(PipelineState.ANALYZING)
        sm.transition(PipelineState.CONSTRUCTING)
        sm.transition(PipelineState.VALIDATING)
        sm.transition(PipelineState.TRADING)
        sm.halt("비상 정지")
        assert sm.state == PipelineState.HALTED

    def test_analyzing_fail_to_idle(self):
        sm = PipelineStateMachine()
        sm.transition(PipelineState.COLLECTING)
        sm.transition(PipelineState.ANALYZING)
        sm.transition(PipelineState.IDLE, "데이터 부족")
        assert sm.state == PipelineState.IDLE

    def test_error_to_idle(self):
        sm = PipelineStateMachine()
        sm.transition(PipelineState.COLLECTING)
        sm.transition(PipelineState.ERROR, "예외 발생")
        sm.transition(PipelineState.IDLE, "복구")
        assert sm.state == PipelineState.IDLE

    def test_can_transition(self):
        sm = PipelineStateMachine()
        assert sm.can_transition(PipelineState.COLLECTING) is True
        assert sm.can_transition(PipelineState.TRADING) is False

    def test_reset(self):
        sm = PipelineStateMachine(PipelineState.ERROR)
        sm.reset("강제 리셋")
        assert sm.state == PipelineState.IDLE


# ══════════════════════════════════════════════════════════════
# 5. FallbackHandler 테스트
# ══════════════════════════════════════════════════════════════


class TestFallbackHandler(unittest.IsolatedAsyncioTestCase):
    def test_pass_no_change(self):
        sm = PipelineStateMachine()
        sm.transition(PipelineState.COLLECTING)
        handler = FallbackHandler(sm)
        result = GateResult(gate_id="DataGate", decision=GateDecision.PASS)
        state = handler.handle(result)
        assert state == PipelineState.COLLECTING

    def test_data_block_to_idle(self):
        sm = PipelineStateMachine()
        sm.transition(PipelineState.COLLECTING)
        handler = FallbackHandler(sm)
        result = GateResult(
            gate_id="DataGate",
            decision=GateDecision.BLOCK,
            reason="데이터 없음",
        )
        state = handler.handle(result)
        assert state == PipelineState.IDLE

    def test_recon_block_to_halted(self):
        sm = PipelineStateMachine()
        sm.transition(PipelineState.COLLECTING)
        sm.transition(PipelineState.ANALYZING)
        sm.transition(PipelineState.CONSTRUCTING)
        sm.transition(PipelineState.VALIDATING)
        sm.transition(PipelineState.TRADING)
        sm.transition(PipelineState.RECONCILING)
        handler = FallbackHandler(sm)
        result = GateResult(
            gate_id="ReconGate",
            decision=GateDecision.BLOCK,
            reason="대사 불일치",
        )
        state = handler.handle(result)
        assert state == PipelineState.HALTED

    def test_callback_on_block(self):
        sm = PipelineStateMachine()
        sm.transition(PipelineState.COLLECTING)
        callback_called = []
        handler = FallbackHandler(
            sm,
            on_block_callback=lambda r, s: callback_called.append((r.gate_id, s)),
        )
        result = GateResult(
            gate_id="DataGate",
            decision=GateDecision.BLOCK,
            reason="test",
        )
        handler.handle(result)
        assert len(callback_called) == 1
        assert callback_called[0][0] == "DataGate"


# ══════════════════════════════════════════════════════════════
# 6. 통합 시나리오 테스트
# ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestIntegrationScenarios(unittest.IsolatedAsyncioTestCase):
    """로드맵 요구: 10+ 통합 시나리오."""

    async def test_scenario_normal_flow(self):
        """정상 파이프라인 전체 흐름."""
        registry = GateRegistry()
        registry.register(DataGate())
        registry.register(EnsembleGate())

        data_map = {
            "DataGate": [{"price": 70000}],
            "EnsembleGate": {"FACTOR": 0.5, "TREND": 0.5},
        }
        results = await registry.evaluate_all(data_map)
        assert all(r.decision == GateDecision.PASS for r in results)

    async def test_scenario_data_gate_blocks(self):
        """데이터 부족 → DataGate 차단 → IDLE 복귀."""
        sm = PipelineStateMachine()
        sm.transition(PipelineState.COLLECTING)
        handler = FallbackHandler(sm)

        gate = DataGate()
        result = await gate.evaluate(None)
        handler.handle(result)
        assert sm.state == PipelineState.IDLE

    async def test_scenario_signal_all_hold(self):
        """모든 시그널 HOLD → 차단."""
        gate = SignalGate()
        signals = [SimpleNamespace(direction=SimpleNamespace(value="HOLD"))] * 5
        result = await gate.evaluate(signals)
        assert result.decision == GateDecision.BLOCK

    async def test_scenario_ensemble_concentrated(self):
        """단일 전략 집중 → 차단."""
        gate = EnsembleGate()
        result = await gate.evaluate(
            {"TREND": 0.95, "FACTOR": 0.05},
            max_single_strategy_weight=0.6,
        )
        assert result.decision == GateDecision.BLOCK

    async def test_scenario_recon_mismatch_halts(self):
        """대사 불일치 → HALTED."""
        sm = PipelineStateMachine()
        for s in [
            PipelineState.COLLECTING,
            PipelineState.ANALYZING,
            PipelineState.CONSTRUCTING,
            PipelineState.VALIDATING,
            PipelineState.TRADING,
            PipelineState.RECONCILING,
        ]:
            sm.transition(s)

        handler = FallbackHandler(sm)
        gate = ReconGate()
        result = await gate.evaluate(
            {
                "broker_balance": 1_000_000,
                "internal_balance": 800_000,
                "mismatches": ["qty_mismatch"],
            }
        )
        handler.handle(result)
        assert sm.state == PipelineState.HALTED

    async def test_scenario_execution_error(self):
        """주문 실행 오류 → HALTED."""
        sm = PipelineStateMachine()
        sm.transition(PipelineState.COLLECTING)
        sm.transition(PipelineState.ANALYZING)
        sm.transition(PipelineState.CONSTRUCTING)
        sm.transition(PipelineState.VALIDATING)
        sm.transition(PipelineState.TRADING)

        handler = FallbackHandler(sm)
        gate = ExecutionGate()
        result = await gate.evaluate({"submitted": False, "error": "Timeout"})
        handler.handle(result)
        assert sm.state == PipelineState.HALTED

    async def test_scenario_fill_partial_ok(self):
        """부분 체결이지만 최소 비율 충족 → PASS."""
        gate = FillGate()
        result = await gate.evaluate(
            {"status": "PARTIAL", "requested_quantity": 100, "filled_quantity": 80},
            min_fill_ratio=0.5,
        )
        assert result.decision == GateDecision.PASS

    async def test_scenario_guard_blocks(self):
        """TradingGuard 거부 → IDLE 복귀."""
        sm = PipelineStateMachine()
        sm.transition(PipelineState.COLLECTING)
        handler = FallbackHandler(sm)

        gate = TradingGuardGate()
        result = await gate.evaluate(
            None,
            guard_result={"approved": False, "reason": "자본 한도 초과"},
        )
        handler.handle(result)
        assert sm.state == PipelineState.IDLE

    async def test_scenario_portfolio_concentrated(self):
        """종목 집중 포트폴리오 → 차단."""
        gate = PortfolioGate()
        portfolio = SimpleNamespace(
            positions=[
                SimpleNamespace(ticker="AAPL", target_weight=0.25),
                SimpleNamespace(ticker="MSFT", target_weight=0.75),
            ],
            cash_weight=0.0,
        )
        result = await gate.evaluate(portfolio, max_single_weight=0.2)
        assert result.decision == GateDecision.BLOCK

    async def test_scenario_error_recovery(self):
        """오류 → IDLE → 재시작."""
        sm = PipelineStateMachine()
        sm.transition(PipelineState.COLLECTING)
        sm.transition(PipelineState.ERROR, "DB timeout")
        sm.transition(PipelineState.IDLE, "복구")
        sm.transition(PipelineState.COLLECTING, "재시작")
        assert sm.state == PipelineState.COLLECTING

    async def test_scenario_halt_and_resume(self):
        """비상 정지 → IDLE → 정상 재개."""
        sm = PipelineStateMachine()
        sm.transition(PipelineState.COLLECTING)
        sm.halt("외부 이벤트")
        assert sm.state == PipelineState.HALTED
        sm.transition(PipelineState.IDLE, "운영자 해제")
        sm.transition(PipelineState.COLLECTING, "재개")
        assert sm.state == PipelineState.COLLECTING
