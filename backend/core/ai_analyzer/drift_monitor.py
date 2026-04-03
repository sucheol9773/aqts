"""
Stage 7 LLM Drift Monitor

모니터링 목표: LLM 출력이 참조 분포에서 통계적으로 유의한 변화(drift)가 있는지 감지

핵심 메커니즘:
  - Kolmogorov-Smirnov (KS) 검정: 두 분포의 차이를 정량화
  - p-value < 0.05 → drift 감지됨
  - 월별 레포팅: 각 월의 drift 상태 추적
"""

from typing import Optional, List, Dict
from scipy import stats


class DriftMonitor:
    """
    LLM 모델 드리프트를 감시하는 클래스

    KS-test를 사용하여 현재 분포와 참조 분포 간 통계적 유의성 검증
    """

    def __init__(self, reference_distribution: Optional[List[float]] = None):
        """
        Parameters
        ----------
        reference_distribution : Optional[List[float]]
            참조 분포 (초기 학습/안정화 시기의 점수들)
        """
        self.reference_distribution = reference_distribution or []

    def set_reference(self, scores: List[float]) -> None:
        """
        참조 분포를 설정합니다.

        Parameters
        ----------
        scores : List[float]
            참조로 사용할 점수 리스트
        """
        self.reference_distribution = list(scores)

    def check_drift(self, current_scores: List[float]) -> Dict:
        """
        KS-test를 사용하여 현재 분포와 참조 분포 간 드리프트를 감지합니다.

        Parameters
        ----------
        current_scores : List[float]
            검사할 현재 점수 리스트

        Returns
        -------
        Dict
            - ks_statistic: float (KS-test 통계량)
            - p_value: float (p-value, 낮을수록 drift 가능성 높음)
            - is_drifted: bool (p-value < 0.05 인 경우 True)

        Raises
        ------
        ValueError
            참조 분포가 설정되지 않은 경우
        """
        if not self.reference_distribution:
            raise ValueError("Reference distribution must be set before checking drift")

        if not current_scores:
            raise ValueError("Current scores must not be empty")

        # KS-test 실행
        ks_statistic, p_value = stats.ks_2samp(self.reference_distribution, current_scores)

        return {
            "ks_statistic": ks_statistic,
            "p_value": p_value,
            "is_drifted": p_value < 0.05,
        }

    def monthly_report(self, monthly_scores: Dict[str, List[float]]) -> List[Dict]:
        """
        월별 드리프트 분석 리포트를 생성합니다.

        Parameters
        ----------
        monthly_scores : Dict[str, List[float]]
            월별 점수 (예: {"2026-01": [0.5, 0.6, ...], "2026-02": [...]})

        Returns
        -------
        List[Dict]
            각 월별 {month, ks_stat, p_value, drifted}

        Raises
        ------
        ValueError
            참조 분포가 설정되지 않은 경우
        """
        if not self.reference_distribution:
            raise ValueError("Reference distribution must be set before monthly report")

        report = []
        for month, scores in monthly_scores.items():
            if not scores:
                continue

            drift_result = self.check_drift(scores)
            report.append(
                {
                    "month": month,
                    "ks_stat": drift_result["ks_statistic"],
                    "p_value": drift_result["p_value"],
                    "drifted": drift_result["is_drifted"],
                }
            )

        return report
