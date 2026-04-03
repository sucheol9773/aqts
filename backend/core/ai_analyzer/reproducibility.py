"""
Stage 7 LLM Reproducibility Test

재현성 검증 목표:
  - Mode A (Sentiment): 동일 입력에 대해 점수의 표준편차 < 0.10
  - Mode B (Opinion): 동일 입력에 대한 의견의 일치율 > 80%
"""

from typing import Dict, List
from statistics import mean, stdev


class ReproducibilityTest:
    """
    LLM 출력의 재현성(reproducibility)을 검증하는 클래스
    """

    def __init__(self, max_std: float = 0.10, min_match_rate: float = 0.80):
        """
        Parameters
        ----------
        max_std : float
            감성 점수의 최대 허용 표준편차 (기본값: 0.10)
        min_match_rate : float
            의견 일치율의 최소 기준 (기본값: 0.80 = 80%)
        """
        self.max_std = max_std
        self.min_match_rate = min_match_rate

    def test_sentiment_reproducibility(self, scores: List[float]) -> Dict:
        """
        감성 점수의 재현성을 검증합니다.

        동일 입력에 대해 여러 번 실행한 감성 점수들의 변동성을 측정

        Parameters
        ----------
        scores : List[float]
            감성 점수 리스트 (예: 동일 뉴스를 5회 분석한 점수)

        Returns
        -------
        Dict
            {
                "mean": float,
                "std": float,
                "is_reproducible": bool (std < max_std)
            }

        Raises
        ------
        ValueError
            scores가 2개 미만인 경우 (표준편차 계산 불가)
        """
        if not scores or len(scores) < 2:
            raise ValueError("At least 2 scores required for reproducibility test")

        mean_score = mean(scores)
        std_score = stdev(scores)

        return {
            "mean": mean_score,
            "std": std_score,
            "is_reproducible": std_score < self.max_std,
        }

    def test_opinion_reproducibility(self, opinions: List[str]) -> Dict:
        """
        투자 의견의 재현성을 검증합니다.

        동일 입력에 대해 여러 번 생성한 의견들 중 가장 빈도가 높은 의견과
        일치하는 의견의 비율을 측정

        Parameters
        ----------
        opinions : List[str]
            의견 리스트 (예: ["BUY", "BUY", "HOLD", "BUY"])

        Returns
        -------
        Dict
            {
                "mode": str,  # 가장 빈도가 높은 의견
                "match_rate": float,  # 모드 의견과 일치하는 비율 (0-1)
                "is_reproducible": bool (match_rate > min_match_rate)
            }

        Raises
        ------
        ValueError
            opinions가 비어있는 경우
        """
        if not opinions:
            raise ValueError("Opinions list cannot be empty")

        # 가장 빈도가 높은 의견 찾기
        from collections import Counter

        counter = Counter(opinions)
        mode_opinion, mode_count = counter.most_common(1)[0]

        match_rate = mode_count / len(opinions)

        return {
            "mode": mode_opinion,
            "match_rate": match_rate,
            "is_reproducible": match_rate >= self.min_match_rate,
        }

    def run_full_test(
        self, sentiment_runs: List[List[float]], opinion_runs: List[List[str]]
    ) -> Dict:
        """
        감성과 의견의 전체 재현성 테스트를 실행합니다.

        Parameters
        ----------
        sentiment_runs : List[List[float]]
            여러 run의 감성 점수들
            예: [[0.5, 0.51, 0.49], [0.52, 0.48, 0.50], ...]
        opinion_runs : List[List[str]]
            여러 run의 의견들
            예: [["BUY", "BUY", "HOLD"], ["BUY", "BUY", "BUY"], ...]

        Returns
        -------
        Dict
            {
                "sentiment": {각 run별 테스트 결과},
                "opinion": {각 run별 테스트 결과},
                "all_sentiment_reproducible": bool,
                "all_opinion_reproducible": bool
            }

        Raises
        ------
        ValueError
            데이터가 비어있는 경우
        """
        if not sentiment_runs or not opinion_runs:
            raise ValueError("Both sentiment_runs and opinion_runs must be non-empty")

        sentiment_results = []
        for run_scores in sentiment_runs:
            if run_scores:
                result = self.test_sentiment_reproducibility(run_scores)
                sentiment_results.append(result)

        opinion_results = []
        for run_opinions in opinion_runs:
            if run_opinions:
                result = self.test_opinion_reproducibility(run_opinions)
                opinion_results.append(result)

        all_sentiment_reproducible = all(r.get("is_reproducible", False) for r in sentiment_results)
        all_opinion_reproducible = all(r.get("is_reproducible", False) for r in opinion_results)

        return {
            "sentiment": sentiment_results,
            "opinion": opinion_results,
            "all_sentiment_reproducible": all_sentiment_reproducible,
            "all_opinion_reproducible": all_opinion_reproducible,
        }
