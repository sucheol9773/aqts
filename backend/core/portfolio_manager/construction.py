"""
포트폴리오 구성 엔진 (F-05-02)

앙상블 시그널과 현재 포트폴리오를 기반으로 최적 목표 포트폴리오를 구성합니다.

평균-분산 최적화, 리스크 패리티 등 다양한 최적화 기법을 제공하며,
포트폴리오 제약 조건(최대 종목 비중, 섹터 제약, 최소 종목 수 등)을 적용합니다.

주요 기능:
- Mean-Variance Optimization
- Risk Parity Allocation
- Constraint application (max weight, sector weight, position count)
- Currency risk management (US asset ratio warning)
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Any

import numpy as np
from scipy.optimize import minimize

from config.constants import (
    Market,
    RiskProfile,
    PORTFOLIO_CONSTRAINTS,
)
from config.logging import logger


# ══════════════════════════════════════
# 포트폴리오 구성 데이터 구조
# ══════════════════════════════════════
@dataclass
class TargetAllocation:
    """
    목표 포트폴리오 할당량

    특정 종목의 목표 비중, 현재 비중, 신호 점수,
    시장 정보를 포함합니다.
    """

    ticker: str
    market: Market
    target_weight: float  # 목표 비중 (0.0 ~ 1.0)
    current_weight: float  # 현재 비중 (0.0 ~ 1.0)
    signal_score: float  # 앙상블 신호 점수 (-1.0 ~ 1.0)
    sector: str = ""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환"""
        return {
            "ticker": self.ticker,
            "market": self.market.value,
            "target_weight": round(self.target_weight, 4),
            "current_weight": round(self.current_weight, 4),
            "signal_score": round(self.signal_score, 4),
            "sector": self.sector,
        }


@dataclass
class TargetPortfolio:
    """
    목표 포트폴리오 구성

    최적화 과정을 통해 생성된 최종 할당 정보를 포함합니다.
    """

    allocations: list[TargetAllocation] = field(default_factory=list)
    total_value: float = 0.0  # 포트폴리오 총 자산 (원)
    cash_ratio: float = 0.0  # 현금 비중
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    optimization_method: str = "mean_variance"  # 최적화 방법

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환"""
        return {
            "allocations": [a.to_dict() for a in self.allocations],
            "total_value": round(self.total_value, 2),
            "cash_ratio": round(self.cash_ratio, 4),
            "generated_at": self.generated_at,
            "optimization_method": self.optimization_method,
        }

    @property
    def stock_count(self) -> int:
        """보유 종목 수"""
        return len([a for a in self.allocations if a.target_weight > 0])

    @property
    def sector_weights(self) -> dict[str, float]:
        """섹터별 가중치"""
        weights = {}
        for allocation in self.allocations:
            if allocation.sector:
                weights[allocation.sector] = weights.get(allocation.sector, 0.0) + allocation.target_weight
        return weights

    @property
    def market_weights(self) -> dict[str, float]:
        """시장별 가중치"""
        weights = {}
        for allocation in self.allocations:
            market_key = allocation.market.value
            weights[market_key] = weights.get(market_key, 0.0) + allocation.target_weight
        return weights


# ══════════════════════════════════════
# 포트폴리오 구성 엔진
# ══════════════════════════════════════
class PortfolioConstructionEngine:
    """
    포트폴리오 구성 엔진

    앙상블 시그널을 기반으로 최적 포트폴리오를 구성합니다.
    평균-분산 최적화, 리스크 패리티 등 다양한 기법을 제공합니다.

    주요 기능:
    - async construct: 목표 포트폴리오 생성
    - _mean_variance_optimize: 평균-분산 최적화
    - _risk_parity_optimize: 리스크 패리티 할당
    - _apply_constraints: 제약 조건 적용
    - _calculate_rebalancing_orders: 리밸런싱 주문 계산

    제약 조건:
    - max_single_weight: 종목당 최대 20%
    - max_sector_weight: 섹터당 최대 40%
    - min_positions: 최소 5개 종목
    - max_us_weight_warning: 미국 자산 50% 초과 시 경고
    """

    def __init__(
        self,
        risk_profile: RiskProfile,
        constraints: Optional[dict[str, float]] = None,
    ):
        """
        포트폴리오 구성 엔진 초기화

        Args:
            risk_profile: 투자 성향
            constraints: 포트폴리오 제약 조건 (기본값: PORTFOLIO_CONSTRAINTS)
        """
        self.risk_profile = risk_profile
        self.constraints = constraints or PORTFOLIO_CONSTRAINTS

    async def construct(
        self,
        ensemble_signals: dict[str, float],  # {ticker: signal_score}
        current_portfolio: dict[str, float],  # {ticker: current_weight}
        seed_capital: float,
        method: str = "mean_variance",
        sector_info: Optional[dict[str, str]] = None,  # {ticker: sector}
        market_info: Optional[dict[str, Market]] = None,  # {ticker: Market}
    ) -> TargetPortfolio:
        """
        목표 포트폴리오를 생성합니다.

        Args:
            ensemble_signals: 앙상블 시그널 {ticker: signal_score}
            current_portfolio: 현재 포트폴리오 {ticker: current_weight}
            seed_capital: 초기 자본 (원)
            method: 최적화 방법 ("mean_variance" 또는 "risk_parity")
            sector_info: 종목별 섹터 정보
            market_info: 종목별 시장 정보

        Returns:
            TargetPortfolio 인스턴스

        Raises:
            Exception: 최적화 실패 시
        """
        sector_info = sector_info or {}
        market_info = market_info or {ticker: Market.NYSE for ticker in ensemble_signals.keys()}

        try:
            # 최적화 방법 선택
            if method == "risk_parity":
                weights = self._risk_parity_optimize(ensemble_signals, self.constraints)
            else:
                weights = self._mean_variance_optimize(ensemble_signals, self.constraints)

            # 제약 조건 적용
            weights = self._apply_constraints(weights, sector_info)

            # 현금 비중 계산
            total_weight = sum(weights.values())
            cash_ratio = max(0.0, 1.0 - total_weight)

            # 목표 할당량 생성
            allocations = []
            for ticker, target_weight in weights.items():
                if target_weight > 0.0001:  # 최소 비중 필터
                    allocation = TargetAllocation(
                        ticker=ticker,
                        market=market_info.get(ticker, Market.NYSE),
                        target_weight=target_weight,
                        current_weight=current_portfolio.get(ticker, 0.0),
                        signal_score=ensemble_signals.get(ticker, 0.0),
                        sector=sector_info.get(ticker, ""),
                    )
                    allocations.append(allocation)

            # 목표 포트폴리오 생성
            portfolio = TargetPortfolio(
                allocations=allocations,
                total_value=seed_capital,
                cash_ratio=cash_ratio,
                optimization_method=method,
            )

            # 환위험 관리 경고 (F-05-02-A)
            us_weight = portfolio.market_weights.get(Market.NYSE.value, 0.0)
            if us_weight > self.constraints.get("max_us_weight_warning", 0.50):
                logger.warning(
                    f"US asset ratio exceeds {self.constraints.get('max_us_weight_warning', 0.50)*100:.1f}%: "
                    f"{us_weight*100:.1f}% (currency risk)"
                )

            logger.info(
                f"Portfolio constructed: {portfolio.stock_count} positions, "
                f"method={method}, cash={cash_ratio*100:.1f}%"
            )
            return portfolio

        except Exception as e:
            logger.error(f"Portfolio construction failed: {e}")
            raise

    def _mean_variance_optimize(
        self,
        signals: dict[str, float],
        constraints: dict[str, float],
    ) -> dict[str, float]:
        """
        평균-분산 최적화를 수행합니다.

        신호 점수를 기반으로 기대 수익률을 추정하고,
        포트폴리오 분산을 최소화하는 가중치를 계산합니다.

        Args:
            signals: 시그널 {ticker: score (-1.0 ~ 1.0)}
            constraints: 제약 조건

        Returns:
            최적 가중치 {ticker: weight}
        """
        tickers = list(signals.keys())
        n = len(tickers)

        if n == 0:
            return {}

        # 기대 수익률 추정 (신호 점수 기반)
        returns = np.array([max(0.0, signals[t] * 0.05) for t in tickers])  # 신호를 5% 수익률로 스케일

        # 상관계수 행렬 (단순화: 신호 강도 기반 분산)
        cov_matrix = np.eye(n) * 0.01

        # 목적 함수: 포트폴리오 분산
        def objective(w):
            return np.dot(w, np.dot(cov_matrix, w))

        # 제약: 합계 = 1.0
        constraints_scipy = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]

        # 경계: 각 종목당 0 ~ max_weight
        max_weight = constraints.get("max_single_weight", 0.20)
        bounds = [(0.0, max_weight) for _ in range(n)]

        # 초기값: 신호 비례 가중치
        x0 = np.maximum(returns, 0.0)
        if x0.sum() > 0:
            x0 = x0 / x0.sum()
        else:
            x0 = np.ones(n) / n

        # 최적화 수행
        result = minimize(
            objective,
            x0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints_scipy,
            options={"ftol": 1e-9, "maxiter": 1000},
        )

        if result.success:
            weights = {tickers[i]: float(result.x[i]) for i in range(n)}
        else:
            # 실패 시 신호 비례 가중치 반환
            logger.warning("Mean-variance optimization failed, using signal-proportional weights")
            weights = self._signal_proportional_weights(signals, max_weight)

        return weights

    def _risk_parity_optimize(
        self,
        signals: dict[str, float],
        constraints: dict[str, float],
    ) -> dict[str, float]:
        """
        리스크 패리티 할당을 수행합니다.

        신호의 절대값에 역비례하여 가중치를 할당하므로,
        낮은 신호 신뢰도를 갖는 종목의 리스크를 제한합니다.

        Args:
            signals: 시그널 {ticker: score}
            constraints: 제약 조건

        Returns:
            리스크 패리티 가중치 {ticker: weight}
        """
        tickers = list(signals.keys())

        if not tickers:
            return {}

        # 신호의 절대값 (활성도)
        activities = np.array([abs(signals[t]) + 0.01 for t in tickers])  # +0.01은 0 신호 처리

        # 역비례: 활성도 낮을수록 가중치 높음 (리스크 제한)
        inversed = 1.0 / activities
        weights_raw = inversed / inversed.sum()

        # 제약 조건 적용
        max_weight = constraints.get("max_single_weight", 0.20)
        weights = {}
        for i, ticker in enumerate(tickers):
            weights[ticker] = min(float(weights_raw[i]), max_weight)

        # 정규화
        total = sum(weights.values())
        if total > 0:
            weights = {t: w / total for t, w in weights.items()}

        return weights

    def _signal_proportional_weights(
        self,
        signals: dict[str, float],
        max_weight: float,
    ) -> dict[str, float]:
        """
        신호에 비례하는 가중치를 계산합니다.

        Args:
            signals: 시그널 딕셔너리
            max_weight: 최대 비중

        Returns:
            신호 비례 가중치
        """
        # 양수 신호만 선택
        positive_signals = {t: max(s, 0.0) for t, s in signals.items()}
        total = sum(positive_signals.values())

        if total < 1e-10:
            # 모든 신호가 음수 또는 0 → 동일 가중
            n = len(signals)
            return {t: 1.0 / n for t in signals.keys()}

        # 비례 가중치 + 최대값 제한
        weights = {}
        for ticker, signal in positive_signals.items():
            weights[ticker] = min(signal / total, max_weight)

        # 정규화
        total_weights = sum(weights.values())
        return {t: w / total_weights for t, w in weights.items()}

    def _apply_constraints(
        self,
        weights: dict[str, float],
        sector_info: dict[str, str],
    ) -> dict[str, float]:
        """
        포트폴리오 제약 조건을 적용합니다.

        - max_single_weight: 종목당 최대 20%
        - max_sector_weight: 섹터당 최대 40%
        - min_positions: 최소 5개 종목

        Args:
            weights: 가중치 {ticker: weight}
            sector_info: 종목별 섹터 정보

        Returns:
            제약 조건 적용된 가중치
        """
        constrained = weights.copy()
        max_single = self.constraints.get("max_single_weight", 0.20)
        max_sector = self.constraints.get("max_sector_weight", 0.40)
        min_pos = self.constraints.get("min_positions", 5)

        # 1. 종목당 최대 비중 제한
        for ticker in constrained:
            if constrained[ticker] > max_single:
                constrained[ticker] = max_single

        # 2. 섹터별 최대 비중 제한
        sector_weights = {}
        for ticker, weight in constrained.items():
            sector = sector_info.get(ticker, "UNKNOWN")
            sector_weights[sector] = sector_weights.get(sector, 0.0) + weight

        for sector, total_weight in sector_weights.items():
            if total_weight > max_sector:
                # 섹터 내 종목 축소
                sector_tickers = [t for t, s in sector_info.items() if s == sector]
                scale = max_sector / total_weight
                for ticker in sector_tickers:
                    constrained[ticker] *= scale

        # 3. 최소 종목 수 보장 (가장 높은 가중치부터 선택)
        active_count = len([t for t, w in constrained.items() if w > 0.0001])
        if active_count < min_pos:
            # 모든 비활성 종목에 최소 가중치 부여
            min_weight = 1.0 / (min_pos * 2)
            inactive_count = 0
            for ticker in sorted(weights.keys(), key=lambda t: -weights[t]):
                if constrained[ticker] < 0.0001 and inactive_count < min_pos - active_count:
                    constrained[ticker] = min_weight
                    inactive_count += 1

        # 정규화
        total = sum(constrained.values())
        if total > 0:
            constrained = {t: w / total for t, w in constrained.items()}

        return constrained

    def _calculate_rebalancing_orders(
        self,
        current_portfolio: dict[str, float],
        target_portfolio: dict[str, float],
        seed_capital: float,
    ) -> list[dict[str, Any]]:
        """
        현재 포트폴리오에서 목표 포트폴리오로의 리밸런싱 주문을 계산합니다.

        Args:
            current_portfolio: 현재 {ticker: weight}
            target_portfolio: 목표 {ticker: weight}
            seed_capital: 포트폴리오 총액 (원)

        Returns:
            주문 목록: [{ticker, action, quantity, reason}, ...]
        """
        orders = []

        all_tickers = set(current_portfolio.keys()) | set(target_portfolio.keys())

        for ticker in all_tickers:
            current_weight = current_portfolio.get(ticker, 0.0)
            target_weight = target_portfolio.get(ticker, 0.0)
            weight_diff = target_weight - current_weight

            if abs(weight_diff) < 0.001:  # 1bp 이하면 무시
                continue

            action = "BUY" if weight_diff > 0 else "SELL"
            quantity = int(abs(weight_diff) * seed_capital / 1000)  # 단순화

            if quantity > 0:
                orders.append({
                    "ticker": ticker,
                    "action": action,
                    "quantity": quantity,
                    "weight_diff": round(weight_diff, 4),
                    "reason": f"Rebalance from {current_weight*100:.1f}% to {target_weight*100:.1f}%",
                })

        return sorted(orders, key=lambda x: -abs(x["weight_diff"]))
