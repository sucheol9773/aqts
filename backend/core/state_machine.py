"""
Pipeline StateMachine: 파이프라인 상태 전이 관리
"""

from enum import Enum
from typing import Dict, List, Optional, Set
import logging

logger = logging.getLogger(__name__)


class PipelineState(str, Enum):
    """파이프라인 상태."""
    IDLE = "IDLE"                    # 대기
    COLLECTING = "COLLECTING"        # 데이터 수집 중
    ANALYZING = "ANALYZING"          # 분석 중 (팩터/시그널/앙상블)
    CONSTRUCTING = "CONSTRUCTING"    # 포트폴리오 구성 중
    VALIDATING = "VALIDATING"        # 리스크 검증 중
    TRADING = "TRADING"              # 주문 실행 중
    RECONCILING = "RECONCILING"      # 대사 중
    COMPLETED = "COMPLETED"          # 사이클 완료
    HALTED = "HALTED"                # 비상 정지
    ERROR = "ERROR"                  # 오류


# 허용된 상태 전이
VALID_TRANSITIONS: Dict[PipelineState, Set[PipelineState]] = {
    PipelineState.IDLE: {PipelineState.COLLECTING},
    PipelineState.COLLECTING: {PipelineState.ANALYZING, PipelineState.ERROR, PipelineState.HALTED},
    PipelineState.ANALYZING: {PipelineState.CONSTRUCTING, PipelineState.IDLE, PipelineState.ERROR, PipelineState.HALTED},
    PipelineState.CONSTRUCTING: {PipelineState.VALIDATING, PipelineState.ERROR, PipelineState.HALTED},
    PipelineState.VALIDATING: {PipelineState.TRADING, PipelineState.IDLE, PipelineState.ERROR, PipelineState.HALTED},
    PipelineState.TRADING: {PipelineState.RECONCILING, PipelineState.HALTED, PipelineState.ERROR},
    PipelineState.RECONCILING: {PipelineState.COMPLETED, PipelineState.HALTED, PipelineState.ERROR},
    PipelineState.COMPLETED: {PipelineState.IDLE},
    PipelineState.HALTED: {PipelineState.IDLE},
    PipelineState.ERROR: {PipelineState.IDLE, PipelineState.HALTED},
}


class InvalidTransitionError(Exception):
    """잘못된 상태 전이 시도."""
    pass


class PipelineStateMachine:
    """파이프라인 상태 머신.

    허용된 전이만 수행하며, 잘못된 전이를 거부합니다.
    """

    def __init__(self, initial_state: PipelineState = PipelineState.IDLE):
        self._state = initial_state
        self._history: List[tuple] = [(initial_state, None)]

    @property
    def state(self) -> PipelineState:
        """현재 상태."""
        return self._state

    @property
    def history(self) -> List[tuple]:
        """상태 전이 이력."""
        return list(self._history)

    def can_transition(self, target: PipelineState) -> bool:
        """target으로 전이 가능한지 확인."""
        return target in VALID_TRANSITIONS.get(self._state, set())

    def transition(self, target: PipelineState, reason: str = "") -> PipelineState:
        """상태를 전이합니다.

        Raises:
            InvalidTransitionError: 허용되지 않은 전이
        """
        if not self.can_transition(target):
            raise InvalidTransitionError(
                f"{self._state.value} → {target.value} 전이는 허용되지 않습니다"
            )

        old_state = self._state
        self._state = target
        self._history.append((target, reason))
        logger.info(f"상태 전이: {old_state.value} → {target.value} ({reason})")
        return self._state

    def halt(self, reason: str = "") -> PipelineState:
        """비상 정지."""
        if self.can_transition(PipelineState.HALTED):
            return self.transition(PipelineState.HALTED, reason)
        # HALTED는 대부분 상태에서 허용됨
        self._state = PipelineState.HALTED
        self._history.append((PipelineState.HALTED, f"FORCED: {reason}"))
        return self._state

    def reset(self, reason: str = "리셋") -> PipelineState:
        """IDLE로 리셋."""
        if self.can_transition(PipelineState.IDLE):
            return self.transition(PipelineState.IDLE, reason)
        # 강제 리셋
        self._state = PipelineState.IDLE
        self._history.append((PipelineState.IDLE, f"FORCED RESET: {reason}"))
        return self._state
