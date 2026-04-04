"""
백테스트 기반 가중치 자동 최적화 (F-04-02)

BacktestEngine 결과를 분석하여 StrategyEnsembleEngine의
전략 가중치를 자동 최적화합니다.

최적화 방식:
- sharpe: Sharpe 비율 비례 (기본)
- risk_adjusted: Sharpe / MDD 복합 지표 비례
- min_variance: 최소 분산 (전략 수익률 상관관계 기반)
- walk_forward: 슬라이딩 윈도우 Walk-Forward 최적화

스케줄:
- 월 1회 자동 실행 (trading_scheduler에서 호출)
- 수동 트리거 가능 (API 엔드포인트)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from config.constants import RiskProfile
from config.logging import logger
from core.backtest_engine.engine import (
    BacktestConfig,
    BacktestEngine,
    BacktestResult,
)


# ══════════════════════════════════════
# 최적화 결과 데이터
# ══════════════════════════════════════
@dataclass
class OptimizationResult:
    """가중치 최적화 결과"""

    method: str
    risk_profile: str
    old_weights: dict[str, float]
    new_weights: dict[str, float]
    strategy_metrics: dict[str, dict[str, float]]  # {strategy: {metric: value}}
    optimization_score: float = 0.0  # 최적화 목적함수 값
    backtest_period: str = ""
    optimized_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def weight_changes(self) -> dict[str, float]:
        """가중치 변화량 (new - old)"""
        all_keys = set(self.old_weights) | set(self.new_weights)
        return {k: round(self.new_weights.get(k, 0.0) - self.old_weights.get(k, 0.0), 4) for k in all_keys}


# ══════════════════════════════════════
# 가중치 자동 최적화 엔진
# ══════════════════════════════════════
class WeightOptimizer:
    """
    백테스트 기반 전략 가중치 자동 최적화 엔진

    BacktestEngine의 성과 지표를 활용하여
    StrategyEnsembleEngine의 가중치를 자동 산출합니다.

    사용법:
        optimizer = WeightOptimizer(risk_profile=RiskProfile.BALANCED)
        result = optimizer.optimize(backtest_results, method="risk_adjusted")

        # Walk-Forward 최적화
        result = optimizer.walk_forward_optimize(
            strategy_signals, prices, window=120, step=20
        )
    """

    # 가중치 제약 조건
    MIN_WEIGHT = 0.05  # 최소 5%
    MAX_WEIGHT = 0.40  # 최대 40%
    SENTIMENT_MAX = 0.25  # 감성 시그널 최대 25%
    SMOOTHING_ALPHA = 0.3  # 지수이동평균 평활화 계수

    def __init__(self, risk_profile: RiskProfile = RiskProfile.BALANCED):
        self._risk_profile = risk_profile

    # ══════════════════════════════════════
    # 핵심 최적화 메서드
    # ══════════════════════════════════════
    def optimize(
        self,
        backtest_results: list[BacktestResult],
        method: str = "sharpe",
        current_weights: Optional[dict[str, float]] = None,
    ) -> OptimizationResult:
        """
        백테스트 결과 기반 가중치 최적화

        Args:
            backtest_results: 전략별 백테스트 결과 리스트
            method: 최적화 방식 ("sharpe", "risk_adjusted", "min_variance")
            current_weights: 현재 가중치 (평활화에 사용)

        Returns:
            OptimizationResult
        """
        if not backtest_results:
            logger.warning("No backtest results provided for optimization")
            return OptimizationResult(
                method=method,
                risk_profile=self._risk_profile.value,
                old_weights=current_weights or {},
                new_weights=current_weights or {},
                strategy_metrics={},
            )

        # 전략별 성과 지표 추출
        metrics = self._extract_metrics(backtest_results)

        # 최적화 방식별 가중치 산출
        if method == "risk_adjusted":
            raw_weights = self._risk_adjusted_weights(metrics)
        elif method == "min_variance":
            raw_weights = self._min_variance_weights(backtest_results)
        else:
            raw_weights = self._sharpe_weights(metrics)

        # 제약 조건 적용 (최소/최대, 감성 상한)
        constrained = self._apply_constraints(raw_weights)

        # 평활화 (이전 가중치와 지수평균)
        if current_weights:
            smoothed = self._smooth_weights(current_weights, constrained)
        else:
            smoothed = constrained

        # 정규화
        final_weights = self._normalize(smoothed)

        # 기간 문자열
        dates = [r.start_date for r in backtest_results if r.start_date]
        end_dates = [r.end_date for r in backtest_results if r.end_date]
        period = ""
        if dates and end_dates:
            period = f"{min(dates)} ~ {max(end_dates)}"

        # 최적화 점수 (가중 평균 Sharpe)
        opt_score = sum(final_weights.get(name, 0) * metrics.get(name, {}).get("sharpe", 0) for name in final_weights)

        return OptimizationResult(
            method=method,
            risk_profile=self._risk_profile.value,
            old_weights=current_weights or {},
            new_weights=final_weights,
            strategy_metrics=metrics,
            optimization_score=round(opt_score, 4),
            backtest_period=period,
        )

    def walk_forward_optimize(
        self,
        strategy_signals: dict[str, pd.DataFrame],
        prices: pd.DataFrame,
        window: int = 120,
        step: int = 20,
        method: str = "risk_adjusted",
        backtest_config: Optional[BacktestConfig] = None,
    ) -> OptimizationResult:
        """
        Walk-Forward 최적화

        슬라이딩 윈도우 방식으로 과거 구간별 최적 가중치를 산출하고,
        최근 윈도우의 가중치에 높은 비중을 부여합니다.

        Args:
            strategy_signals: {전략명: 시그널 DataFrame}
            prices: 가격 DataFrame
            window: 학습 윈도우 크기 (거래일)
            step: 윈도우 이동 간격 (거래일)
            method: 윈도우별 최적화 방식
            backtest_config: 백테스트 설정

        Returns:
            최종 OptimizationResult (시간가중 평균)
        """
        if not strategy_signals or len(prices) < window:
            logger.warning(f"Insufficient data for walk-forward: " f"{len(prices)} days < {window} window")
            return OptimizationResult(
                method=f"walk_forward_{method}",
                risk_profile=self._risk_profile.value,
                old_weights={},
                new_weights={},
                strategy_metrics={},
            )

        config = backtest_config or BacktestConfig()
        dates = prices.index.sort_values()
        n_dates = len(dates)

        # 윈도우별 최적화 결과 수집
        window_weights: list[dict[str, float]] = []
        window_scores: list[float] = []

        start_idx = 0
        while start_idx + window <= n_dates:
            end_idx = start_idx + window
            win_dates = dates[start_idx:end_idx]
            win_prices = prices.loc[win_dates]

            # 윈도우 내 백테스트 실행
            results = []
            for name, signals in strategy_signals.items():
                win_signals = signals.reindex(win_dates).fillna(0)
                engine = BacktestEngine(config)
                result = engine.run(name, win_signals, win_prices)
                results.append(result)

            if results:
                opt = self.optimize(results, method=method)
                window_weights.append(opt.new_weights)
                window_scores.append(opt.optimization_score)

            start_idx += step

        if not window_weights:
            return OptimizationResult(
                method=f"walk_forward_{method}",
                risk_profile=self._risk_profile.value,
                old_weights={},
                new_weights={},
                strategy_metrics={},
            )

        # 시간 가중 평균 (최근 윈도우에 높은 비중)
        final_weights = self._time_weighted_average(window_weights)

        # 제약 조건 + 정규화
        final_weights = self._normalize(self._apply_constraints(final_weights))

        # 전체 기간 메트릭 (마지막 윈도우 기준)
        all_keys = set()
        for w in window_weights:
            all_keys.update(w.keys())

        return OptimizationResult(
            method=f"walk_forward_{method}",
            risk_profile=self._risk_profile.value,
            old_weights={},
            new_weights=final_weights,
            strategy_metrics={},
            optimization_score=round(np.mean(window_scores), 4) if window_scores else 0.0,
            backtest_period=f"{dates[0].date()} ~ {dates[-1].date()}",
        )

    # ══════════════════════════════════════
    # 최적화 방식 구현
    # ══════════════════════════════════════
    @staticmethod
    def _extract_metrics(
        results: list[BacktestResult],
    ) -> dict[str, dict[str, float]]:
        """백테스트 결과에서 최적화에 필요한 지표 추출"""
        metrics = {}
        for r in results:
            metrics[r.strategy_name] = {
                "sharpe": r.sharpe_ratio,
                "sortino": r.sortino_ratio,
                "calmar": r.calmar_ratio,
                "mdd": r.mdd,
                "cagr": r.cagr,
                "total_return": r.total_return,
                "win_rate": r.win_rate,
                "profit_factor": r.profit_factor,
                "alpha": r.alpha,
                "beta": r.beta,
            }
        return metrics

    @staticmethod
    def _sharpe_weights(
        metrics: dict[str, dict[str, float]],
    ) -> dict[str, float]:
        """Sharpe 비율 비례 가중치"""
        sharpes = {}
        for name, m in metrics.items():
            sharpes[name] = max(m.get("sharpe", 0.0), 0.0)

        total = sum(sharpes.values())
        if total < 1e-10:
            n = max(len(sharpes), 1)
            return {k: 1.0 / n for k in sharpes}

        return {k: v / total for k, v in sharpes.items()}

    @staticmethod
    def _risk_adjusted_weights(
        metrics: dict[str, dict[str, float]],
    ) -> dict[str, float]:
        """
        리스크 조정 복합 지표 기반 가중치

        Score = Sharpe × (1 + Calmar) × (1 - |MDD|)
        → Sharpe가 높으면서 MDD가 낮은 전략에 높은 비중
        """
        scores = {}
        for name, m in metrics.items():
            sharpe = max(m.get("sharpe", 0.0), 0.0)
            calmar = max(m.get("calmar", 0.0), 0.0)
            mdd_abs = abs(m.get("mdd", 0.0))

            score = sharpe * (1 + calmar) * (1 - min(mdd_abs, 0.99))
            scores[name] = score

        total = sum(scores.values())
        if total < 1e-10:
            n = max(len(scores), 1)
            return {k: 1.0 / n for k in scores}

        return {k: v / total for k, v in scores.items()}

    @staticmethod
    def _min_variance_weights(
        results: list[BacktestResult],
    ) -> dict[str, float]:
        """
        최소 분산 가중치 (전략 수익률 상관관계 기반)

        전략 간 수익률 공분산 행렬을 구하고,
        포트폴리오 분산을 최소화하는 가중치를 산출합니다.
        """
        # 전략별 일별 수익률 DataFrame 구성
        returns_dict = {}
        for r in results:
            if len(r.equity_curve) > 1:
                daily_ret = r.equity_curve.pct_change().dropna()
                returns_dict[r.strategy_name] = daily_ret

        if len(returns_dict) < 2:
            n = max(len(results), 1)
            return {r.strategy_name: 1.0 / n for r in results}

        returns_df = pd.DataFrame(returns_dict).dropna()
        if len(returns_df) < 10:
            n = max(len(results), 1)
            return {r.strategy_name: 1.0 / n for r in results}

        # 공분산 행렬
        cov = returns_df.cov().values
        n = cov.shape[0]
        names = list(returns_df.columns)

        # 최소 분산 해석해: w = Σ⁻¹ · 1 / (1' · Σ⁻¹ · 1)
        try:
            cov_inv = np.linalg.inv(cov + np.eye(n) * 1e-8)
            ones = np.ones(n)
            w = cov_inv @ ones
            w = w / w.sum()

            # 음수 가중치 → 0 처리
            w = np.maximum(w, 0.0)
            total = w.sum()
            if total < 1e-10:
                w = np.ones(n) / n
            else:
                w = w / total

            return {names[i]: float(w[i]) for i in range(n)}
        except np.linalg.LinAlgError:
            return {name: 1.0 / n for name in names}

    # ══════════════════════════════════════
    # 제약 조건 및 정규화
    # ══════════════════════════════════════
    def _apply_constraints(self, weights: dict[str, float]) -> dict[str, float]:
        """
        가중치 제약 조건 적용

        - 최소 5%, 최대 40%
        - SENTIMENT 계열 최대 25%
        """
        constrained = {}
        for name, w in weights.items():
            if w < 1e-6:
                constrained[name] = 0.0
                continue

            capped = max(self.MIN_WEIGHT, min(self.MAX_WEIGHT, w))

            # 감성 시그널 상한
            if "SENTIMENT" in name.upper():
                capped = min(capped, self.SENTIMENT_MAX)

            constrained[name] = capped

        return constrained

    def _smooth_weights(
        self,
        old_weights: dict[str, float],
        new_weights: dict[str, float],
    ) -> dict[str, float]:
        """
        지수이동평균 평활화

        급격한 가중치 변동 방지:
        w_final = α · w_new + (1 - α) · w_old
        """
        alpha = self.SMOOTHING_ALPHA
        all_keys = set(old_weights) | set(new_weights)
        smoothed = {}

        for k in all_keys:
            old_val = old_weights.get(k, 0.0)
            new_val = new_weights.get(k, 0.0)
            smoothed[k] = alpha * new_val + (1 - alpha) * old_val

        return smoothed

    @staticmethod
    def _normalize(weights: dict[str, float]) -> dict[str, float]:
        """가중치 합계 = 1.0 정규화"""
        active = {k: v for k, v in weights.items() if v > 1e-6}
        total = sum(active.values())

        if total < 1e-10:
            if active:
                n = len(active)
                return {k: round(1.0 / n, 4) for k in active}
            return weights

        result = {}
        for k, v in weights.items():
            if v > 1e-6:
                result[k] = round(v / total, 4)
            else:
                result[k] = 0.0
        return result

    def _time_weighted_average(
        self,
        weight_series: list[dict[str, float]],
    ) -> dict[str, float]:
        """
        시간 가중 평균 (최근 윈도우에 높은 비중)

        지수적 감소: 최근 윈도우 비중 = 2^0, 이전 = 2^-1, ...
        """
        if not weight_series:
            return {}

        n = len(weight_series)
        # 지수적 시간 가중치: 최근 = 1.0, 이전 = 0.5, ...
        time_weights = [2.0 ** (i - n + 1) for i in range(n)]
        total_tw = sum(time_weights)

        all_keys: set[str] = set()
        for w in weight_series:
            all_keys.update(w.keys())

        avg = {}
        for key in all_keys:
            weighted_sum = sum(tw * ws.get(key, 0.0) for tw, ws in zip(time_weights, weight_series))
            avg[key] = weighted_sum / total_tw

        return avg

    # ══════════════════════════════════════
    # 앙상블 엔진 연동 편의 메서드
    # ══════════════════════════════════════
    async def optimize_and_apply(
        self,
        backtest_results: list[BacktestResult],
        ensemble_engine,
        method: str = "risk_adjusted",
    ) -> OptimizationResult:
        """
        최적화 수행 후 앙상블 엔진에 즉시 반영

        Args:
            backtest_results: 전략별 백테스트 결과
            ensemble_engine: StrategyEnsembleEngine 인스턴스
            method: 최적화 방식

        Returns:
            OptimizationResult
        """
        current_weights = await ensemble_engine.get_weights()

        result = self.optimize(
            backtest_results,
            method=method,
            current_weights=current_weights,
        )

        if result.new_weights and result.strategy_metrics:
            # 앙상블 엔진에 성과 지표 전달하여 recalibrate
            performances = {name: m.get("sharpe", 0.0) for name, m in result.strategy_metrics.items()}
            await ensemble_engine.recalibrate_weights(performances, method="sharpe")

            # 최적화 결과의 가중치로 덮어쓰기 (더 정교한 방식 적용)
            ensemble_engine._weights = result.new_weights

            logger.info(
                f"Weight optimization applied: method={method}, "
                f"profile={self._risk_profile.value}, "
                f"score={result.optimization_score:.4f}"
            )

        return result
