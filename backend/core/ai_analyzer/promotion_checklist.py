"""
Stage 7 LLM Production Promotion Checklist

프로덕션 단계 승격 기준:
  - Production Tier 1: 모든 기준 pass (PROMOTE)
  - Research Tier 2: 일부 기준 fail (HOLD)
  - 재평가 필요: 모든 기준 fail (DEMOTE)

Mode A (Sentiment):
  - IR delta > 0.1
  - Reproducibility std < 0.1
  - Drift KS p-value > 0.05
  - Cost ratio < 20%

Mode B (Opinion):
  - IR delta > 0.15
  - Opinion match rate > 80%
  - Drift KS p-value > 0.05
  - Cost ratio < 20%
"""

from enum import Enum
from typing import Dict, Optional
import yaml


class PromotionDecision(Enum):
    """프로덕션 승격 결정"""

    PROMOTE = "PROMOTE"  # 프로덕션 Tier 1 승격
    HOLD = "HOLD"  # Research Tier 2 유지
    DEMOTE = "DEMOTE"  # 재평가 필요


class PromotionChecklist:
    """
    LLM 모델의 프로덕션 승격 기준을 검증하는 클래스
    """

    def __init__(self, thresholds: Optional[Dict] = None):
        """
        Parameters
        ----------
        thresholds : Optional[Dict]
            임계값 딕셔너리. None인 경우 config/operational_thresholds.yaml에서 읽음
            구조:
            {
                "ir_delta_mode_a": 0.1,
                "ir_delta_mode_b": 0.15,
                "sentiment_std_max": 0.1,
                "opinion_match_rate_min": 0.8,
                "drift_p_min": 0.05,
                "cost_ratio_max": 0.2
            }
        """
        if thresholds is None:
            thresholds = self._load_default_thresholds()

        self.ir_delta_mode_a = thresholds.get("ir_delta_mode_a", 0.1)
        self.ir_delta_mode_b = thresholds.get("ir_delta_mode_b", 0.15)
        self.sentiment_std_max = thresholds.get("sentiment_std_max", 0.1)
        self.opinion_match_rate_min = thresholds.get("opinion_match_rate_min", 0.8)
        self.drift_p_min = thresholds.get("drift_p_min", 0.05)
        self.cost_ratio_max = thresholds.get("cost_ratio_max", 0.2)

    @staticmethod
    def _load_default_thresholds() -> Dict:
        """config/operational_thresholds.yaml에서 기본 임계값을 로드합니다."""
        try:
            with open("/sessions/practical-eager-davinci/mnt/aqts/backend/config/operational_thresholds.yaml", "r") as f:
                config = yaml.safe_load(f)
                ai_config = config.get("ai", {})

                return {
                    "ir_delta_mode_a": 0.1,  # 기본값
                    "ir_delta_mode_b": 0.15,  # 기본값
                    "sentiment_std_max": ai_config.get("sentiment_reproducibility_std_max", 0.1),
                    "opinion_match_rate_min": ai_config.get("opinion_match_rate_min", 0.8),
                    "drift_p_min": ai_config.get("drift_ks_test_p_min", 0.05),
                    "cost_ratio_max": ai_config.get("cost_benefit_max_ratio", 0.2),
                }
        except Exception:
            # 파일을 읽을 수 없으면 기본값 사용
            return {
                "ir_delta_mode_a": 0.1,
                "ir_delta_mode_b": 0.15,
                "sentiment_std_max": 0.1,
                "opinion_match_rate_min": 0.8,
                "drift_p_min": 0.05,
                "cost_ratio_max": 0.2,
            }

    def check_mode_a(
        self,
        ir_delta: float,
        reproducibility_std: float,
        drift_p_value: float,
        cost_ratio: float,
    ) -> Dict:
        """
        Mode A (Sentiment) 프로덕션 승격 기준을 검증합니다.

        Parameters
        ----------
        ir_delta : float
            IR (Information Ratio) 초과 (예: 0.12)
        reproducibility_std : float
            재현성 표준편차 (예: 0.08)
        drift_p_value : float
            KS-test p-value (예: 0.10)
        cost_ratio : float
            비용 비율 (예: 0.15)

        Returns
        -------
        Dict
            {
                "ir_delta_pass": bool,
                "reproducibility_pass": bool,
                "drift_pass": bool,
                "cost_pass": bool,
                "pass_count": int (0-4),
                "overall_decision": PromotionDecision
            }
        """
        ir_delta_pass = ir_delta > self.ir_delta_mode_a
        reproducibility_pass = reproducibility_std < self.sentiment_std_max
        drift_pass = drift_p_value > self.drift_p_min
        cost_pass = cost_ratio < self.cost_ratio_max

        pass_count = sum([ir_delta_pass, reproducibility_pass, drift_pass, cost_pass])

        if pass_count == 4:
            decision = PromotionDecision.PROMOTE
        elif pass_count >= 2:
            decision = PromotionDecision.HOLD
        else:
            decision = PromotionDecision.DEMOTE

        return {
            "ir_delta_pass": ir_delta_pass,
            "reproducibility_pass": reproducibility_pass,
            "drift_pass": drift_pass,
            "cost_pass": cost_pass,
            "pass_count": pass_count,
            "overall_decision": decision,
        }

    def check_mode_b(
        self,
        ir_delta: float,
        match_rate: float,
        drift_p_value: float,
        cost_ratio: float,
    ) -> Dict:
        """
        Mode B (Opinion) 프로덕션 승격 기준을 검증합니다.

        Parameters
        ----------
        ir_delta : float
            IR 초과 (예: 0.18)
        match_rate : float
            의견 일치율 (0-1, 예: 0.85)
        drift_p_value : float
            KS-test p-value (예: 0.10)
        cost_ratio : float
            비용 비율 (예: 0.15)

        Returns
        -------
        Dict
            {
                "ir_delta_pass": bool,
                "match_rate_pass": bool,
                "drift_pass": bool,
                "cost_pass": bool,
                "pass_count": int (0-4),
                "overall_decision": PromotionDecision
            }
        """
        ir_delta_pass = ir_delta > self.ir_delta_mode_b
        match_rate_pass = match_rate > self.opinion_match_rate_min
        drift_pass = drift_p_value > self.drift_p_min
        cost_pass = cost_ratio < self.cost_ratio_max

        pass_count = sum([ir_delta_pass, match_rate_pass, drift_pass, cost_pass])

        if pass_count == 4:
            decision = PromotionDecision.PROMOTE
        elif pass_count >= 2:
            decision = PromotionDecision.HOLD
        else:
            decision = PromotionDecision.DEMOTE

        return {
            "ir_delta_pass": ir_delta_pass,
            "match_rate_pass": match_rate_pass,
            "drift_pass": drift_pass,
            "cost_pass": cost_pass,
            "pass_count": pass_count,
            "overall_decision": decision,
        }

    def generate_memo(self, mode_a_result: Dict, mode_b_result: Dict) -> str:
        """
        프로덕션 승격 메모를 생성합니다.

        Parameters
        ----------
        mode_a_result : Dict
            check_mode_a의 반환값
        mode_b_result : Dict
            check_mode_b의 반환값

        Returns
        -------
        str
            형식화된 프로덕션 승격 메모
        """
        memo = "═" * 70 + "\n"
        memo += "AQTS Stage 7: LLM Production Promotion Memo\n"
        memo += "═" * 70 + "\n\n"

        # Mode A 결과
        memo += "MODE A (SENTIMENT ANALYSIS)\n"
        memo += "─" * 70 + "\n"
        memo += f"IR Delta (> 0.10):          {'PASS' if mode_a_result['ir_delta_pass'] else 'FAIL'}\n"
        memo += f"Reproducibility Std (< 0.10): {'PASS' if mode_a_result['reproducibility_pass'] else 'FAIL'}\n"
        memo += f"Drift KS-test (p > 0.05):   {'PASS' if mode_a_result['drift_pass'] else 'FAIL'}\n"
        memo += f"Cost Ratio (< 20%):         {'PASS' if mode_a_result['cost_pass'] else 'FAIL'}\n"
        memo += f"Pass Count: {mode_a_result['pass_count']}/4\n"
        memo += f"Decision: {mode_a_result['overall_decision'].value}\n\n"

        # Mode B 결과
        memo += "MODE B (OPINION GENERATION)\n"
        memo += "─" * 70 + "\n"
        memo += f"IR Delta (> 0.15):          {'PASS' if mode_b_result['ir_delta_pass'] else 'FAIL'}\n"
        memo += f"Opinion Match Rate (> 80%): {'PASS' if mode_b_result['match_rate_pass'] else 'FAIL'}\n"
        memo += f"Drift KS-test (p > 0.05):   {'PASS' if mode_b_result['drift_pass'] else 'FAIL'}\n"
        memo += f"Cost Ratio (< 20%):         {'PASS' if mode_b_result['cost_pass'] else 'FAIL'}\n"
        memo += f"Pass Count: {mode_b_result['pass_count']}/4\n"
        memo += f"Decision: {mode_b_result['overall_decision'].value}\n\n"

        # 종합 판정
        memo += "═" * 70 + "\n"
        memo += "OVERALL DECISION\n"
        memo += "═" * 70 + "\n"

        mode_a_decision = mode_a_result["overall_decision"]
        mode_b_decision = mode_b_result["overall_decision"]

        if mode_a_decision == PromotionDecision.PROMOTE and mode_b_decision == PromotionDecision.PROMOTE:
            memo += "RECOMMENDATION: PROMOTE TO PRODUCTION TIER 1\n"
            memo += "Both Mode A and Mode B meet production readiness criteria.\n"
        elif mode_a_decision == PromotionDecision.DEMOTE or mode_b_decision == PromotionDecision.DEMOTE:
            memo += "RECOMMENDATION: DEMOTE / RETRAIN\n"
            memo += "Model requires retraining or parameter adjustment.\n"
        else:
            memo += "RECOMMENDATION: MAINTAIN RESEARCH TIER 2\n"
            memo += "Model shows promise but needs further validation.\n"

        memo += "═" * 70 + "\n"

        return memo
