"""
Stage 7 LLM Cost-Benefit Analyzer

API 비용이 초과수익(excess return)에 대해 정당한지 평가

핵심 메커니즘:
  - API 비용 < 초과수익의 20% → cost-effective로 간주
  - 월별 누적 분석 지원
"""

from typing import Dict, List, Optional


class CostAnalyzer:
    """
    LLM API 사용 비용과 초과수익의 비용-편익 분석

    기준: API 비용이 초과수익의 20% 이하여야 cost-effective로 간주
    """

    def __init__(self, max_cost_ratio: float = 0.20):
        """
        Parameters
        ----------
        max_cost_ratio : float
            최대 허용 비용 비율 (기본값: 0.20 = 20%)
            API_cost < excess_return * max_cost_ratio 를 만족해야 함
        """
        self.max_cost_ratio = max_cost_ratio

    def calculate_cost(self, api_calls: int, cost_per_call: float) -> float:
        """
        전체 API 비용을 계산합니다.

        Parameters
        ----------
        api_calls : int
            API 호출 횟수
        cost_per_call : float
            호출당 비용 (달러)

        Returns
        -------
        float
            총 API 비용
        """
        return api_calls * cost_per_call

    def calculate_benefit(self, excess_return_pct: float, portfolio_value: float) -> float:
        """
        초과수익을 달러 금액으로 계산합니다.

        Parameters
        ----------
        excess_return_pct : float
            초과수익률 (예: 0.15 = 15%)
        portfolio_value : float
            포트폴리오 가치 (달러)

        Returns
        -------
        float
            초과수익 (달러)
        """
        return excess_return_pct * portfolio_value

    def cost_benefit_ratio(self, total_cost: float, total_benefit: float) -> float:
        """
        비용-편익 비율을 계산합니다.

        Parameters
        ----------
        total_cost : float
            총 API 비용
        total_benefit : float
            총 초과수익 (달러)

        Returns
        -------
        float
            비율 (cost / benefit)

        Raises
        ------
        ValueError
            benefit이 0인 경우
        """
        if total_benefit == 0:
            return float("inf") if total_cost > 0 else 0.0

        return total_cost / total_benefit

    def is_cost_effective(
        self,
        api_calls: int,
        cost_per_call: float,
        excess_return_pct: float,
        portfolio_value: float,
    ) -> bool:
        """
        비용-편익 기준에 따라 cost-effective 여부를 판단합니다.

        Parameters
        ----------
        api_calls : int
            API 호출 횟수
        cost_per_call : float
            호출당 비용 (달러)
        excess_return_pct : float
            초과수익률 (예: 0.15 = 15%)
        portfolio_value : float
            포트폴리오 가치 (달러)

        Returns
        -------
        bool
            cost_ratio <= max_cost_ratio 인 경우 True
        """
        total_cost = self.calculate_cost(api_calls, cost_per_call)
        total_benefit = self.calculate_benefit(excess_return_pct, portfolio_value)

        if total_benefit == 0:
            return total_cost == 0

        ratio = self.cost_benefit_ratio(total_cost, total_benefit)
        return ratio <= self.max_cost_ratio

    def monthly_summary(self, monthly_data: List[Dict]) -> Dict:
        """
        월별 데이터로부터 누적 비용-편익 요약을 생성합니다.

        Parameters
        ----------
        monthly_data : List[Dict]
            월별 데이터 리스트, 각 항목은:
            {
                "month": str,
                "api_calls": int,
                "cost_per_call": float,
                "excess_return_pct": float,
                "portfolio_value": float
            }

        Returns
        -------
        Dict
            {
                "total_cost": float,
                "total_benefit": float,
                "avg_ratio": float,
                "is_cost_effective": bool
            }
        """
        total_cost = 0.0
        total_benefit = 0.0
        ratios = []

        for data in monthly_data:
            cost = self.calculate_cost(data["api_calls"], data["cost_per_call"])
            benefit = self.calculate_benefit(
                data["excess_return_pct"], data["portfolio_value"]
            )

            total_cost += cost
            total_benefit += benefit

            if benefit > 0:
                ratio = self.cost_benefit_ratio(cost, benefit)
                ratios.append(ratio)

        # 평균 비율 계산
        avg_ratio = sum(ratios) / len(ratios) if ratios else 0.0

        return {
            "total_cost": total_cost,
            "total_benefit": total_benefit,
            "avg_ratio": avg_ratio,
            "is_cost_effective": avg_ratio <= self.max_cost_ratio if ratios else True,
        }
