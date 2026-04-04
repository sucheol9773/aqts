"""
OOS 게이트 평가기 (Gate Evaluator)

3단계 게이트로 OOS 결과를 판정:

Gate-A (절대 기준): MDD 상한, turnover 상한 → FAIL
Gate-B (상대 기준): Sharpe/Calmar 최소, 레짐별 최악 MDD → REVIEW/FAIL
Gate-C (안정성 기준): 윈도우 간 변동성, 양수 윈도우 비율 → REVIEW

임계값은 operational_thresholds.yaml의 oos_gate 섹션에서 로드.
pass_fail.py와 동일한 config 로딩 패턴 사용.
"""

from pathlib import Path
from typing import Any, Optional

import yaml

from config.logging import logger
from core.oos.models import GateResult, OOSWindowResult


class GateEvaluator:
    """
    OOS 결과에 대한 3단계 게이트 판정

    Gate-A: 절대 기준 (위반 시 즉시 FAIL)
    Gate-B: 상대 기준 (미달 시 REVIEW)
    Gate-C: 안정성 기준 (불안정 시 REVIEW)
    """

    DEFAULT_THRESHOLDS = {
        # Gate-A
        "mdd_hard_limit": 0.25,
        "max_turnover": 5.0,
        # Gate-B
        "min_sharpe_ratio": 0.3,
        "min_calmar_ratio": 0.2,
        "regime_worst_mdd": 0.30,
        # Gate-C
        "max_window_variance": 0.5,
        "min_positive_windows_ratio": 0.5,
    }

    def __init__(self, thresholds: Optional[dict[str, float]] = None):
        if thresholds:
            self.thresholds = thresholds
        else:
            self.thresholds = self._load_thresholds()

    def _load_thresholds(self) -> dict[str, float]:
        """operational_thresholds.yaml에서 OOS 게이트 임계값 로드"""
        config_path = Path(__file__).parent.parent.parent / "config" / "operational_thresholds.yaml"

        if config_path.exists():
            try:
                with open(config_path) as f:
                    config = yaml.safe_load(f)
                    if config and "oos_gate" in config:
                        return config["oos_gate"]
            except Exception as e:
                logger.warning(f"GateEvaluator config load failed: {e}")

        return self.DEFAULT_THRESHOLDS.copy()

    def evaluate_all(
        self,
        windows: list[OOSWindowResult],
        avg_sharpe: float,
        avg_calmar: float,
        worst_mdd: float,
        sharpe_variance: float,
    ) -> dict[str, Any]:
        """
        전체 게이트 평가

        Args:
            windows: walk-forward 윈도우 결과 리스트
            avg_sharpe: 전체 평균 Sharpe
            avg_calmar: 전체 평균 Calmar
            worst_mdd: 전체 중 최악 MDD
            sharpe_variance: 윈도우 간 Sharpe 분산

        Returns:
            {
                "gate_a": {"result": "PASS/FAIL", "reasons": [...]},
                "gate_b": {"result": "PASS/REVIEW/FAIL", "reasons": [...]},
                "gate_c": {"result": "PASS/REVIEW", "reasons": [...]},
                "overall": "PASS/REVIEW/FAIL",
                "all_reasons": [...]
            }
        """
        gate_a = self._evaluate_gate_a(windows, worst_mdd)
        gate_b = self._evaluate_gate_b(windows, avg_sharpe, avg_calmar, worst_mdd)
        gate_c = self._evaluate_gate_c(windows, sharpe_variance)

        # 최종 판정: FAIL > REVIEW > PASS
        results = [gate_a["result"], gate_b["result"], gate_c["result"]]

        if GateResult.FAIL.value in results:
            overall = GateResult.FAIL.value
        elif GateResult.REVIEW.value in results:
            overall = GateResult.REVIEW.value
        else:
            overall = GateResult.PASS.value

        all_reasons = gate_a["reasons"] + gate_b["reasons"] + gate_c["reasons"]

        logger.info(
            f"OOS Gate evaluation: A={gate_a['result']}, B={gate_b['result']}, "
            f"C={gate_c['result']} → overall={overall}"
        )

        return {
            "gate_a": gate_a,
            "gate_b": gate_b,
            "gate_c": gate_c,
            "overall": overall,
            "all_reasons": all_reasons,
        }

    def _evaluate_gate_a(
        self,
        windows: list[OOSWindowResult],
        worst_mdd: float,
    ) -> dict[str, Any]:
        """
        Gate-A: 절대 기준

        - MDD 상한 초과 → FAIL
        - Turnover 상한 초과 → FAIL (현재 turnover가 윈도우에 없으면 skip)
        """
        reasons = []
        result = GateResult.PASS.value

        mdd_limit = self.thresholds.get("mdd_hard_limit", 0.25)

        if abs(worst_mdd) > mdd_limit:
            result = GateResult.FAIL.value
            reasons.append(f"GATE_A: worst MDD {worst_mdd:.2%} exceeds hard limit {mdd_limit:.2%}")

        return {"result": result, "reasons": reasons}

    def _evaluate_gate_b(
        self,
        windows: list[OOSWindowResult],
        avg_sharpe: float,
        avg_calmar: float,
        worst_mdd: float,
    ) -> dict[str, Any]:
        """
        Gate-B: 상대 기준

        - 평균 Sharpe < 최소 → REVIEW
        - 평균 Calmar < 최소 → REVIEW
        - 레짐별 최악 MDD > 임계 → REVIEW
        - 모든 기준 미달 시 FAIL
        """
        reasons = []
        review_count = 0

        min_sharpe = self.thresholds.get("min_sharpe_ratio", 0.3)
        min_calmar = self.thresholds.get("min_calmar_ratio", 0.2)
        regime_worst = self.thresholds.get("regime_worst_mdd", 0.30)

        if avg_sharpe < min_sharpe:
            review_count += 1
            reasons.append(f"GATE_B: avg Sharpe {avg_sharpe:.3f} < minimum {min_sharpe}")

        if avg_calmar < min_calmar:
            review_count += 1
            reasons.append(f"GATE_B: avg Calmar {avg_calmar:.3f} < minimum {min_calmar}")

        # 레짐별 최악 MDD 확인
        for w in windows:
            if w.regime_metrics:
                for regime, metrics in w.regime_metrics.items():
                    regime_mdd = abs(metrics.get("max_drawdown", 0.0))
                    if regime_mdd > regime_worst:
                        review_count += 1
                        reasons.append(
                            f"GATE_B: window {w.window_index} regime {regime} "
                            f"MDD {regime_mdd:.2%} > limit {regime_worst:.2%}"
                        )

        if review_count >= 3:
            result = GateResult.FAIL.value
        elif review_count > 0:
            result = GateResult.REVIEW.value
        else:
            result = GateResult.PASS.value

        return {"result": result, "reasons": reasons}

    def _evaluate_gate_c(
        self,
        windows: list[OOSWindowResult],
        sharpe_variance: float,
    ) -> dict[str, Any]:
        """
        Gate-C: 안정성 기준

        - 윈도우 간 Sharpe 분산 > 임계 → REVIEW
        - 양수 수익 윈도우 비율 < 임계 → REVIEW
        """
        reasons = []
        result = GateResult.PASS.value

        max_var = self.thresholds.get("max_window_variance", 0.5)
        min_positive = self.thresholds.get("min_positive_windows_ratio", 0.5)

        if sharpe_variance > max_var:
            result = GateResult.REVIEW.value
            reasons.append(f"GATE_C: Sharpe variance {sharpe_variance:.4f} > limit {max_var}")

        if windows:
            positive_count = sum(1 for w in windows if w.total_return > 0)
            positive_ratio = positive_count / len(windows)
            if positive_ratio < min_positive:
                result = GateResult.REVIEW.value
                reasons.append(f"GATE_C: positive window ratio {positive_ratio:.1%} " f"< minimum {min_positive:.1%}")

        return {"result": result, "reasons": reasons}
