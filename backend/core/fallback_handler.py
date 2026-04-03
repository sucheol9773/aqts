"""
FallbackHandler: Gate 차단 시 폴백 행동 정의
"""

from typing import Callable, Dict, Optional
import logging

from core.gates.base import GateResult, GateDecision
from core.state_machine import PipelineStateMachine, PipelineState

logger = logging.getLogger(__name__)


class FallbackHandler:
    """Gate 차단 시 파이프라인 상태를 안전하게 전환합니다.

    각 Gate별 폴백 동작:
    - ANALYZING 실패 → IDLE (데이터 부족, 재수집 대기)
    - TRADING 실패 → HALTED (주문 중 오류, 비상 정지)
    - RECONCILING 실패 → HALTED (대사 불일치, 비상 정지)
    """

    # Gate별 기본 폴백 상태
    DEFAULT_FALLBACKS: Dict[str, PipelineState] = {
        "DataGate": PipelineState.IDLE,
        "FactorGate": PipelineState.IDLE,
        "SignalGate": PipelineState.IDLE,
        "EnsembleGate": PipelineState.IDLE,
        "PortfolioGate": PipelineState.IDLE,
        "TradingGuardGate": PipelineState.IDLE,
        "ReconGate": PipelineState.HALTED,
        "ExecutionGate": PipelineState.HALTED,
        "FillGate": PipelineState.HALTED,
    }

    def __init__(
        self,
        state_machine: PipelineStateMachine,
        custom_fallbacks: Optional[Dict[str, PipelineState]] = None,
        on_block_callback: Optional[Callable] = None,
    ):
        self._sm = state_machine
        self._fallbacks = {**self.DEFAULT_FALLBACKS}
        if custom_fallbacks:
            self._fallbacks.update(custom_fallbacks)
        self._on_block = on_block_callback

    def handle(self, gate_result: GateResult) -> PipelineState:
        """Gate 결과를 처리하고 필요 시 상태를 전이합니다.

        Returns:
            전이 후 현재 상태
        """
        if gate_result.decision == GateDecision.PASS:
            return self._sm.state

        # BLOCK 처리
        gate_id = gate_result.gate_id
        fallback_state = self._fallbacks.get(gate_id, PipelineState.IDLE)

        logger.warning(
            f"[FallbackHandler] {gate_id} BLOCK → {fallback_state.value}: "
            f"{gate_result.reason}"
        )

        if fallback_state == PipelineState.HALTED:
            self._sm.halt(f"{gate_id}: {gate_result.reason}")
        else:
            self._sm.reset(f"{gate_id}: {gate_result.reason}")

        # 콜백 호출 (알림 등)
        if self._on_block:
            try:
                self._on_block(gate_result, fallback_state)
            except Exception as e:
                logger.error(f"폴백 콜백 오류: {e}")

        return self._sm.state
