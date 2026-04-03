"""
GateRegistry: Gate 동적 등록 및 실행 관리
"""

from typing import Any, Dict, List, Optional
import logging

from core.gates.base import BaseGate, GateResult, GateDecision

logger = logging.getLogger(__name__)


class GateRegistry:
    """Gate를 동적으로 등록/실행하는 레지스트리.

    파이프라인 실행 시 등록된 Gate를 순차적으로 실행하며,
    BLOCK 발생 시 즉시 중단합니다.
    """

    def __init__(self):
        self._gates: Dict[str, BaseGate] = {}
        self._execution_order: List[str] = []

    def register(self, gate: BaseGate) -> None:
        """Gate를 레지스트리에 등록합니다."""
        self._gates[gate.gate_id] = gate
        if gate.gate_id not in self._execution_order:
            self._execution_order.append(gate.gate_id)
        logger.info(f"Gate 등록: {gate.gate_id}")

    def unregister(self, gate_id: str) -> None:
        """Gate를 레지스트리에서 제거합니다."""
        self._gates.pop(gate_id, None)
        if gate_id in self._execution_order:
            self._execution_order.remove(gate_id)

    def get(self, gate_id: str) -> Optional[BaseGate]:
        """Gate를 조회합니다."""
        return self._gates.get(gate_id)

    @property
    def gate_ids(self) -> List[str]:
        """등록된 Gate ID 목록 (실행 순서)."""
        return list(self._execution_order)

    async def evaluate_single(
        self, gate_id: str, data: Any, **kwargs
    ) -> GateResult:
        """단일 Gate를 실행합니다."""
        gate = self._gates.get(gate_id)
        if gate is None:
            raise ValueError(f"등록되지 않은 Gate: {gate_id}")
        return await gate.evaluate(data, **kwargs)

    async def evaluate_all(
        self,
        data_map: Dict[str, Any],
        stop_on_block: bool = True,
        **kwargs,
    ) -> List[GateResult]:
        """모든 Gate를 순서대로 실행합니다.

        Args:
            data_map: {gate_id: data} 맵. 없는 Gate는 None으로 호출.
            stop_on_block: True면 첫 BLOCK에서 중단.

        Returns:
            GateResult 목록
        """
        results: List[GateResult] = []

        for gate_id in self._execution_order:
            gate = self._gates.get(gate_id)
            if gate is None:
                continue

            data = data_map.get(gate_id)
            gate_kwargs = kwargs.get(gate_id, {}) if isinstance(
                kwargs.get(gate_id), dict
            ) else {}

            result = await gate.evaluate(data, **gate_kwargs)
            results.append(result)

            if stop_on_block and result.decision == GateDecision.BLOCK:
                logger.warning(f"Gate 차단: {gate_id} → {result.reason}")
                break

        return results

    def __len__(self) -> int:
        return len(self._gates)
