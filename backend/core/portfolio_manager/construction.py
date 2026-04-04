"""
포트폴리오 구성 엔진 (F-05-02)

앙상블 시그널과 현재 포트폴리오를 기반으로 최적 목표 포트폴리오를 구성합니다.

평균-분산 최적화, 리스크 패리티, Black-Litterman 등 다양한 최적화 기법을 제공하며,
포트폴리오 제약 조건(최대 종목 비중, 섹터 제약, 최소 종목 수 등)을 적용합니다.

주요 기능:
- Mean-Variance Optimization (실제 공분산 행렬 기반)
- Risk Parity Allocation (변동성 기반 균등 위험 기여)
- Black-Litterman Model (시그널을 투자자 뷰로 활용)
- Constraint application (max weight, sector weight, position count)
- Currency risk management (USD 비중 제한 + 환율 리스크 할증)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np
from scipy.optimize import minimize

from config.constants import (
    PORTFOLIO_CONSTRAINTS,
    Market,
    RiskProfile,
)
from config.logging import logger

# ══════════════════════════════════════
# 최적화 기본 상수
# ══════════════════════════════════════
# 리스크 프로필별 위험회피 계수 (λ)
RISK_AVERSION_MAP = {
    RiskProfile.CONSERVATIVE: 5.0,
    RiskProfile.BALANCED: 2.5,
    RiskProfile.AGGRESSIVE: 1.0,
    RiskProfile.DIVIDEND: 4.0,
}

# 리스크 프로필별 현금 비중 하한
CASH_RATIO_MIN = {
    RiskProfile.CONSERVATIVE: 0.15,
    RiskProfile.BALANCED: 0.05,
    RiskProfile.AGGRESSIVE: 0.00,
    RiskProfile.DIVIDEND: 0.10,
}

# Black-Litterman 기본 하이퍼파라미터
BL_TAU = 0.05  # 사전분포 불확실성 스케일
BL_SIGNAL_CONFIDENCE = 0.6  # 시그널 뷰 신뢰도 기본값

# USD 비중 하드캡 (환율 리스크 관리)
USD_WEIGHT_HARD_CAP = 0.60


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
        self._risk_aversion = RISK_AVERSION_MAP.get(risk_profile, 2.5)

    async def construct(
        self,
        ensemble_signals: dict[str, float],  # {ticker: signal_score}
        current_portfolio: dict[str, float],  # {ticker: current_weight}
        seed_capital: float,
        method: str = "mean_variance",
        sector_info: Optional[dict[str, str]] = None,  # {ticker: sector}
        market_info: Optional[dict[str, Market]] = None,  # {ticker: Market}
        price_history: Optional[dict[str, list[float]]] = None,  # {ticker: [daily_prices]}
    ) -> TargetPortfolio:
        """
        목표 포트폴리오를 생성합니다.

        Args:
            ensemble_signals: 앙상블 시그널 {ticker: signal_score}
            current_portfolio: 현재 포트폴리오 {ticker: current_weight}
            seed_capital: 초기 자본 (원)
            method: 최적화 방법 ("mean_variance", "risk_parity", "black_litterman")
            sector_info: 종목별 섹터 정보
            market_info: 종목별 시장 정보
            price_history: 종목별 일별 종가 시계열 {ticker: [price1, price2, ...]}

        Returns:
            TargetPortfolio 인스턴스

        Raises:
            Exception: 최적화 실패 시
        """
        sector_info = sector_info or {}
        market_info = market_info or {ticker: Market.NYSE for ticker in ensemble_signals.keys()}

        try:
            # 공분산 행렬 추정 (price_history 제공 시)
            tickers = list(ensemble_signals.keys())
            cov_matrix = self._estimate_covariance(tickers, price_history)

            # 최적화 방법 선택
            if method == "black_litterman":
                weights = self._black_litterman_optimize(
                    ensemble_signals,
                    cov_matrix,
                    self.constraints,
                )
            elif method == "risk_parity":
                weights = self._risk_parity_optimize(
                    ensemble_signals,
                    self.constraints,
                    cov_matrix,
                )
            else:
                weights = self._mean_variance_optimize(
                    ensemble_signals,
                    self.constraints,
                    cov_matrix,
                )

            # 환율 리스크: USD 비중 하드캡 적용 (F-05-02-A)
            weights = self._apply_currency_cap(weights, market_info)

            # 제약 조건 적용
            weights = self._apply_constraints(weights, sector_info)

            # 현금 비중 계산 (리스크 프로필별 최소 현금 비중 보장)
            min_cash = CASH_RATIO_MIN.get(self.risk_profile, 0.05)
            total_weight = sum(weights.values())
            cash_ratio = max(min_cash, 1.0 - total_weight)

            # 현금 비중 적용으로 주식 비중 축소
            if total_weight > (1.0 - min_cash) and min_cash > 0:
                scale = (1.0 - min_cash) / total_weight
                weights = {t: w * scale for t, w in weights.items()}

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
            us_markets = {Market.NYSE.value, Market.NASDAQ.value, Market.AMEX.value}
            us_weight = sum(w for m, w in portfolio.market_weights.items() if m in us_markets)
            if us_weight > self.constraints.get("max_us_weight_warning", 0.50):
                logger.warning(
                    f"US asset ratio exceeds "
                    f"{self.constraints.get('max_us_weight_warning', 0.50)*100:.1f}%: "
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

    # ══════════════════════════════════════
    # 공분산 행렬 추정
    # ══════════════════════════════════════
    def _estimate_covariance(
        self,
        tickers: list[str],
        price_history: Optional[dict[str, list[float]]] = None,
    ) -> np.ndarray:
        """
        가격 시계열로부터 공분산 행렬을 추정합니다.

        price_history가 제공되지 않거나 데이터가 부족한 경우
        대각 행렬(분산 1%)로 폴백합니다.

        Args:
            tickers: 종목 코드 리스트 (순서 고정)
            price_history: {ticker: [daily_price, ...]}  최소 20일 권장

        Returns:
            (n, n) 공분산 행렬 (일별 수익률 기반, 연율화 ×252)
        """
        n = len(tickers)
        if n == 0:
            return np.empty((0, 0))

        if price_history is None:
            return np.eye(n) * 0.01

        # 수익률 행렬 구축
        returns_list = []
        valid_tickers = []
        for ticker in tickers:
            prices = price_history.get(ticker)
            if prices is not None and len(prices) >= 2:
                prices_arr = np.array(prices, dtype=float)
                # 로그 수익률
                rets = np.diff(np.log(prices_arr))
                returns_list.append(rets)
                valid_tickers.append(ticker)

        # 데이터 부족 시 폴백
        if len(valid_tickers) < 2:
            return np.eye(n) * 0.01

        # 동일 길이로 맞춤 (최소 공통 길이)
        min_len = min(len(r) for r in returns_list)
        if min_len < 5:
            return np.eye(n) * 0.01

        returns_matrix = np.column_stack([r[-min_len:] for r in returns_list])

        # 표본 공분산 (연율화: ×252)
        cov_sample = np.cov(returns_matrix, rowvar=False) * 252

        # Ledoit-Wolf 축소 추정 (간이 구현)
        cov_shrunk = self._shrink_covariance(cov_sample)

        # valid_tickers만 있을 수 있으므로 전체 tickers 순서에 맞게 확장
        if len(valid_tickers) == n:
            return cov_shrunk

        # 일부 종목만 유효한 경우: 대각 폴백과 결합
        full_cov = np.eye(n) * 0.01
        ticker_idx = {t: i for i, t in enumerate(tickers)}
        for i, ti in enumerate(valid_tickers):
            for j, tj in enumerate(valid_tickers):
                full_cov[ticker_idx[ti], ticker_idx[tj]] = cov_shrunk[i, j]

        return full_cov

    @staticmethod
    def _shrink_covariance(cov_sample: np.ndarray, shrinkage: float = 0.2) -> np.ndarray:
        """
        Ledoit-Wolf 스타일 축소 추정 (간이 버전)

        표본 공분산을 대각 타겟으로 축소하여 안정성을 높입니다.

        Args:
            cov_sample: 표본 공분산 행렬
            shrinkage: 축소 강도 (0 = 표본 그대로, 1 = 대각만)

        Returns:
            축소 추정된 공분산 행렬
        """
        target = np.diag(np.diag(cov_sample))  # 대각 타겟
        return (1 - shrinkage) * cov_sample + shrinkage * target

    # ══════════════════════════════════════
    # 평균-분산 최적화 (MVO)
    # ══════════════════════════════════════
    def _mean_variance_optimize(
        self,
        signals: dict[str, float],
        constraints: dict[str, float],
        cov_matrix: Optional[np.ndarray] = None,
    ) -> dict[str, float]:
        """
        평균-분산 최적화 (Markowitz MVO)

        시그널 점수로 기대 수익률을 추정하고, 공분산 행렬 기반
        샤프비율 극대화(= λ·w'Σw - w'μ 최소화) 가중치를 계산합니다.

        Args:
            signals: 시그널 {ticker: score (-1.0 ~ 1.0)}
            constraints: 제약 조건
            cov_matrix: (n, n) 공분산 행렬. None이면 대각 0.01

        Returns:
            최적 가중치 {ticker: weight}
        """
        tickers = list(signals.keys())
        n = len(tickers)

        if n == 0:
            return {}

        # 기대 수익률 추정 (신호 점수 → 연율 수익률 스케일)
        mu = np.array([max(0.0, signals[t] * 0.05) for t in tickers])

        # 공분산 행렬
        if cov_matrix is None or cov_matrix.shape[0] != n:
            cov_matrix = np.eye(n) * 0.01

        lam = self._risk_aversion

        # 목적 함수: λ·w'Σw − w'μ (위험회피 최적화)
        def objective(w):
            port_risk = np.dot(w, np.dot(cov_matrix, w))
            port_return = np.dot(w, mu)
            return lam * port_risk - port_return

        # 제약: 합계 ≤ 1.0
        constraints_scipy = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]

        # 경계: 각 종목당 0 ~ max_weight
        max_weight = constraints.get("max_single_weight", 0.20)
        bounds = [(0.0, max_weight) for _ in range(n)]

        # 초기값: 신호 비례 가중치
        x0 = np.maximum(mu, 0.0)
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
            logger.warning("Mean-variance optimization failed, using signal-proportional weights")
            weights = self._signal_proportional_weights(signals, max_weight)

        return weights

    # ══════════════════════════════════════
    # 리스크 패리티 (변동성 기반)
    # ══════════════════════════════════════
    def _risk_parity_optimize(
        self,
        signals: dict[str, float],
        constraints: dict[str, float],
        cov_matrix: Optional[np.ndarray] = None,
    ) -> dict[str, float]:
        """
        리스크 패리티 최적화 (Equal Risk Contribution)

        각 자산의 위험 기여도가 동일하도록 가중치를 배분합니다.
        공분산 행렬이 제공되면 자산별 변동성을 사용하고,
        없으면 시그널 기반 대리 변동성을 활용합니다.

        Args:
            signals: 시그널 {ticker: score}
            constraints: 제약 조건
            cov_matrix: (n, n) 공분산 행렬

        Returns:
            리스크 패리티 가중치 {ticker: weight}
        """
        tickers = list(signals.keys())
        n = len(tickers)

        if n == 0:
            return {}

        # 자산별 변동성 추출
        if cov_matrix is not None and cov_matrix.shape[0] == n:
            vols = np.sqrt(np.maximum(np.diag(cov_matrix), 1e-8))
        else:
            # 폴백: 시그널 절대값이 클수록 변동성이 높다고 가정
            vols = np.array([abs(signals[t]) * 0.3 + 0.05 for t in tickers])

        # 역변동성 가중 (변동성 낮을수록 비중 높음 → 동일 위험 기여)
        inv_vols = 1.0 / vols
        weights_raw = inv_vols / inv_vols.sum()

        # 수치 최적화로 정밀 리스크 패리티 수행
        if cov_matrix is not None and cov_matrix.shape[0] == n:
            weights_raw = self._solve_risk_parity(cov_matrix, weights_raw)

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

    @staticmethod
    def _solve_risk_parity(
        cov_matrix: np.ndarray,
        x0: np.ndarray,
    ) -> np.ndarray:
        """
        수치 최적화로 정밀 리스크 패리티 가중치를 계산합니다.

        목적: Σ_i Σ_j (RC_i − RC_j)^2 → 최소화
        여기서 RC_i = w_i × (Σw)_i

        Args:
            cov_matrix: (n, n) 공분산 행렬
            x0: 초기 가중치 (역변동성 가중)

        Returns:
            리스크 패리티 가중치 배열
        """
        n = cov_matrix.shape[0]

        def risk_contribution_obj(w):
            sigma_w = cov_matrix @ w
            rc = w * sigma_w  # 각 자산의 위험 기여
            total_rc = rc.sum()
            if total_rc < 1e-12:
                return 0.0
            rc_norm = rc / total_rc
            target = 1.0 / n
            return np.sum((rc_norm - target) ** 2)

        constraints_scipy = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
        bounds = [(0.01 / n, 1.0) for _ in range(n)]

        result = minimize(
            risk_contribution_obj,
            x0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints_scipy,
            options={"ftol": 1e-12, "maxiter": 1000},
        )

        if result.success:
            return result.x / result.x.sum()
        return x0 / x0.sum()

    # ══════════════════════════════════════
    # Black-Litterman 모델
    # ══════════════════════════════════════
    def _black_litterman_optimize(
        self,
        signals: dict[str, float],
        cov_matrix: np.ndarray,
        constraints: dict[str, float],
        tau: float = BL_TAU,
        signal_confidence: float = BL_SIGNAL_CONFIDENCE,
    ) -> dict[str, float]:
        """
        Black-Litterman 모델 기반 최적화

        시장 균형 수익률(사전분포)과 앙상블 시그널(투자자 뷰)을 결합하여
        사후 기대 수익률을 산출한 뒤 MVO를 수행합니다.

        Black-Litterman 공식:
            μ_BL = [(τΣ)^{-1} + P'Ω^{-1}P]^{-1} × [(τΣ)^{-1}π + P'Ω^{-1}Q]

        여기서:
            π = δΣw_mkt  (균형 기대 수익률)
            P = I         (절대 뷰: 각 자산에 개별 뷰)
            Q = signal scores × scale
            Ω = diag(τ × diag(Σ)) / confidence

        Args:
            signals: 앙상블 시그널 {ticker: score}
            cov_matrix: (n, n) 연율화 공분산 행렬
            constraints: 포트폴리오 제약
            tau: 사전분포 불확실성 스케일 파라미터
            signal_confidence: 시그널 뷰 신뢰도 (0~1)

        Returns:
            최적 가중치 {ticker: weight}
        """
        tickers = list(signals.keys())
        n = len(tickers)

        if n == 0:
            return {}

        if cov_matrix.shape[0] != n:
            cov_matrix = np.eye(n) * 0.01

        # 1) 시장 균형 가중치 (동일 가중 가정)
        w_mkt = np.ones(n) / n

        # 2) 균형 기대 수익률: π = δ Σ w_mkt
        delta = self._risk_aversion
        pi = delta * cov_matrix @ w_mkt

        # 3) 투자자 뷰 설정 (절대 뷰)
        P = np.eye(n)  # 각 자산에 대한 개별 뷰
        Q = np.array([signals[t] * 0.05 for t in tickers])  # 시그널 → 수익률 스케일

        # 4) 뷰 불확실성 행렬: Ω = diag(τ × diag(Σ)) / confidence
        confidence = max(0.01, min(1.0, signal_confidence))
        omega = np.diag(tau * np.diag(cov_matrix)) / confidence

        # 5) Black-Litterman 사후 기대 수익률
        tau_sigma = tau * cov_matrix
        tau_sigma_inv = np.linalg.inv(tau_sigma)
        omega_inv = np.linalg.inv(omega)

        # M = (τΣ)^{-1} + P'Ω^{-1}P
        M = tau_sigma_inv + P.T @ omega_inv @ P
        M_inv = np.linalg.inv(M)

        # μ_BL = M^{-1} × [(τΣ)^{-1}π + P'Ω^{-1}Q]
        mu_bl = M_inv @ (tau_sigma_inv @ pi + P.T @ omega_inv @ Q)

        # 6) 사후 공분산 (선택적 — MVO에서 사용)
        # Σ_BL = Σ + [(τΣ)^{-1} + P'Ω^{-1}P]^{-1}
        sigma_bl = cov_matrix + M_inv

        # 7) MVO 수행 (사후 기대 수익률 + 사후 공분산)
        max_weight = constraints.get("max_single_weight", 0.20)

        def objective(w):
            port_risk = np.dot(w, np.dot(sigma_bl, w))
            port_return = np.dot(w, mu_bl)
            return delta * port_risk - port_return

        constraints_scipy = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
        bounds = [(0.0, max_weight) for _ in range(n)]

        # 초기값: 사후 기대 수익률 비례
        x0 = np.maximum(mu_bl, 0.0)
        if x0.sum() > 0:
            x0 = x0 / x0.sum()
        else:
            x0 = np.ones(n) / n

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
            logger.info(
                f"Black-Litterman optimization succeeded: " f"mu_bl range=[{mu_bl.min():.4f}, {mu_bl.max():.4f}]"
            )
        else:
            logger.warning("Black-Litterman optimization failed, " "falling back to signal-proportional weights")
            weights = self._signal_proportional_weights(signals, max_weight)

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

    # ══════════════════════════════════════
    # 환율 리스크 관리 (F-05-02-A)
    # ══════════════════════════════════════
    def _apply_currency_cap(
        self,
        weights: dict[str, float],
        market_info: dict[str, Market],
    ) -> dict[str, float]:
        """
        USD 자산 비중 하드캡을 적용합니다.

        미국 시장(NYSE, NASDAQ, AMEX) 종목의 합산 비중이
        USD_WEIGHT_HARD_CAP(60%)을 초과하면 비례 축소합니다.

        Args:
            weights: 가중치 {ticker: weight}
            market_info: 종목별 시장 정보

        Returns:
            USD 비중 제한이 적용된 가중치
        """
        us_markets = {Market.NYSE, Market.NASDAQ, Market.AMEX}
        us_tickers = [t for t in weights if market_info.get(t) in us_markets and weights[t] > 0]
        kr_tickers = [t for t in weights if t not in us_tickers and weights[t] > 0]

        us_total = sum(weights[t] for t in us_tickers)
        cap = USD_WEIGHT_HARD_CAP

        if us_total <= cap or us_total < 1e-10:
            return weights

        # USD 종목 비례 축소, 한국 종목으로 재배분
        scale = cap / us_total
        adjusted = weights.copy()
        freed = 0.0
        for t in us_tickers:
            old_w = adjusted[t]
            adjusted[t] = old_w * scale
            freed += old_w - adjusted[t]

        # 한국 종목에 비례 재배분
        kr_total = sum(adjusted[t] for t in kr_tickers)
        if kr_total > 0 and freed > 0:
            for t in kr_tickers:
                adjusted[t] += freed * (adjusted[t] / kr_total)
        elif freed > 0 and kr_tickers:
            per_ticker = freed / len(kr_tickers)
            for t in kr_tickers:
                adjusted[t] += per_ticker

        logger.info(
            f"Currency cap applied: USD {us_total*100:.1f}% → {cap*100:.1f}%, "
            f"freed {freed*100:.1f}% redistributed to KR assets"
        )
        return adjusted

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
                orders.append(
                    {
                        "ticker": ticker,
                        "action": action,
                        "quantity": quantity,
                        "weight_diff": round(weight_diff, 4),
                        "reason": f"Rebalance from {current_weight*100:.1f}% to {target_weight*100:.1f}%",
                    }
                )

        return sorted(orders, key=lambda x: -abs(x["weight_diff"]))
