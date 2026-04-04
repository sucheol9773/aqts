"""
Stage 6 Performance Validation: PASS/REVIEW/FAIL Decision Table

Operational decision framework for strategy validation:
- PASS: Exceeds all thresholds
- REVIEW: Some metrics in gray zone
- FAIL: Below minimum acceptable

Uses thresholds from config/operational_thresholds.yaml
"""

from pathlib import Path
from typing import Any, Dict, Optional

import yaml


class PerformanceJudge:
    """Judge strategy performance against operational thresholds."""

    DEFAULT_THRESHOLDS = {
        "ir_pass": 0.10,
        "ir_review": 0.05,
        "excess_cagr_pass_pct": 0.01,
        "mdd_tolerance_multiplier": 1.2,
        "max_turnover_pass": 3.0,
        "max_turnover_review": 5.0,
        "significance_ci": 0.95,
    }

    def __init__(self, thresholds: Optional[Dict[str, float]] = None):
        """
        Initialize judge with thresholds.

        Attempts to load from operational_thresholds.yaml.
        Falls back to defaults if not found.

        Args:
            thresholds: Optional override thresholds dict
        """
        if thresholds:
            self.thresholds = thresholds
        else:
            self.thresholds = self._load_thresholds()

    def _load_thresholds(self) -> Dict[str, float]:
        """
        Load thresholds from operational_thresholds.yaml.

        Returns:
            Dict of thresholds, or defaults if file not found
        """
        config_path = Path(__file__).parent.parent.parent / "config" / "operational_thresholds.yaml"

        if config_path.exists():
            try:
                with open(config_path) as f:
                    config = yaml.safe_load(f)
                    if config and "performance" in config:
                        return config["performance"]
            except Exception:
                pass

        return self.DEFAULT_THRESHOLDS.copy()

    def judge_ir(self, ir_value: float) -> str:
        """
        Judge Information Ratio.

        Rules:
        - PASS if IR > ir_pass threshold
        - REVIEW if ir_review < IR ≤ ir_pass
        - FAIL if IR ≤ ir_review

        Args:
            ir_value: Information ratio value

        Returns:
            "PASS" or "REVIEW" or "FAIL"
        """
        ir_pass = self.thresholds.get("ir_pass", 0.10)
        ir_review = self.thresholds.get("ir_review", 0.05)

        if ir_value > ir_pass:
            return "PASS"
        elif ir_value > ir_review:
            return "REVIEW"
        else:
            return "FAIL"

    def judge_excess_cagr(self, excess_cagr: float) -> str:
        """
        Judge excess CAGR (over benchmark).

        Rules:
        - PASS if excess_cagr > excess_cagr_pass_pct
        - REVIEW if 0 < excess_cagr ≤ excess_cagr_pass_pct
        - FAIL if excess_cagr ≤ 0

        Args:
            excess_cagr: Excess CAGR (e.g., 0.02 = 2%)

        Returns:
            "PASS" or "REVIEW" or "FAIL"
        """
        threshold = self.thresholds.get("excess_cagr_pass_pct", 0.01)

        if excess_cagr > threshold:
            return "PASS"
        elif excess_cagr > 0:
            return "REVIEW"
        else:
            return "FAIL"

    def judge_mdd(self, mdd: float, tolerance: float = 0.15) -> str:
        """
        Judge Maximum Drawdown.

        Rules:
        - PASS if |mdd| ≤ tolerance
        - REVIEW if tolerance < |mdd| ≤ tolerance * mdd_tolerance_multiplier
        - FAIL if |mdd| > tolerance * mdd_tolerance_multiplier

        Args:
            mdd: Max drawdown (negative, e.g., -0.20 = -20%)
            tolerance: Acceptable drawdown level (default 0.15 = 15%)

        Returns:
            "PASS" or "REVIEW" or "FAIL"
        """
        abs_mdd = abs(mdd)
        multiplier = self.thresholds.get("mdd_tolerance_multiplier", 1.2)
        fail_threshold = tolerance * multiplier

        if abs_mdd <= tolerance:
            return "PASS"
        elif abs_mdd <= fail_threshold:
            return "REVIEW"
        else:
            return "FAIL"

    def judge_turnover(self, turnover: float) -> str:
        """
        Judge annual turnover.

        Rules:
        - PASS if turnover < max_turnover_pass
        - REVIEW if max_turnover_pass ≤ turnover < max_turnover_review
        - FAIL if turnover ≥ max_turnover_review

        Args:
            turnover: Annual turnover ratio (e.g., 3.0 = 300%)

        Returns:
            "PASS" or "REVIEW" or "FAIL"
        """
        pass_threshold = self.thresholds.get("max_turnover_pass", 3.0)
        review_threshold = self.thresholds.get("max_turnover_review", 5.0)

        if turnover < pass_threshold:
            return "PASS"
        elif turnover < review_threshold:
            return "REVIEW"
        else:
            return "FAIL"

    def judge_significance(self, ci_lower: float) -> str:
        """
        Judge statistical significance.

        Rules:
        - PASS if CI lower bound > 0 (positive return is significant)
        - REVIEW if CI spans 0 (not significant but close)
        - FAIL if CI upper bound < 0 (negative return is significant)

        Args:
            ci_lower: Lower confidence interval bound for mean return

        Returns:
            "PASS" or "REVIEW" or "FAIL"
        """
        if ci_lower > 0:
            return "PASS"
        elif ci_lower > -0.001:  # Allow small negative for REVIEW
            return "REVIEW"
        else:
            return "FAIL"

    def overall_judgment(self, metrics_dict: Dict[str, float], tolerance: float = 0.15) -> Dict[str, Any]:
        """
        Overall judgment across all metrics.

        Makes individual judgments for each metric and determines
        overall status based on majority rule.

        Args:
            metrics_dict: Dict with keys like cagr, max_drawdown, etc.
            tolerance: MDD tolerance (default 0.15)

        Returns:
            Dict with:
            - individual_judgments: {metric: judgment}
            - pass_count, review_count, fail_count
            - overall: "PASS" or "REVIEW" or "FAIL"
        """
        judgments = {}

        # Judge IR
        if metrics_dict.get("information_ratio") is not None:
            judgments["information_ratio"] = self.judge_ir(metrics_dict["information_ratio"])

        # Judge excess CAGR (vs benchmark, if available)
        if metrics_dict.get("cagr") is not None:
            excess_cagr = metrics_dict.get("cagr", 0.0)
            judgments["excess_cagr"] = self.judge_excess_cagr(excess_cagr)

        # Judge max drawdown
        if metrics_dict.get("max_drawdown") is not None:
            judgments["max_drawdown"] = self.judge_mdd(metrics_dict["max_drawdown"], tolerance)

        # Judge turnover
        if metrics_dict.get("turnover") is not None:
            judgments["turnover"] = self.judge_turnover(metrics_dict["turnover"])

        # Count verdicts
        pass_count = sum(1 for j in judgments.values() if j == "PASS")
        review_count = sum(1 for j in judgments.values() if j == "REVIEW")
        fail_count = sum(1 for j in judgments.values() if j == "FAIL")

        # Determine overall (majority rule)
        if fail_count > 0:
            overall = "FAIL"
        elif review_count > pass_count:
            overall = "REVIEW"
        else:
            overall = "PASS"

        return {
            "individual_judgments": judgments,
            "pass_count": pass_count,
            "review_count": review_count,
            "fail_count": fail_count,
            "overall": overall,
        }
