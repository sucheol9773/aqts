"""
민감도 분석기

탄성치 계산, 토네이도 차트 데이터, 안정 구간 분석을 수행합니다.
"""

from typing import Optional

import numpy as np

from config.logging import logger

from .models import ParamElasticity, ParamRange, ParamTrialResult


class SensitivityAnalyzer:
    """
    파라미터 민감도 분석기

    OAT 또는 Grid 스윕 결과에서 탄성치를 계산하고
    파라미터 영향도를 순위화합니다.
    """

    def __init__(
        self,
        param_ranges: list[ParamRange],
        base_values: dict[str, float],
    ):
        self._param_ranges = {pr.name: pr for pr in param_ranges}
        self._base_values = base_values

    def compute_elasticities(
        self,
        trial_results: list[ParamTrialResult],
    ) -> list[ParamElasticity]:
        """
        모든 파라미터의 탄성치 계산

        OAT 결과에서: 한 파라미터만 변경된 trial들을 그룹화하여 분석
        Grid 결과에서: 파라미터별 상관계수 기반 근사 탄성치 계산
        """
        if not trial_results:
            return []

        # 기본값 trial 찾기
        base_trial = self._find_base_trial(trial_results)
        if base_trial is None:
            logger.warning("Base trial not found, using first trial as baseline")
            base_trial = trial_results[0]

        elasticities = []
        for name, pr in self._param_ranges.items():
            # 해당 파라미터만 변경된 trial 필터링
            varied_trials = self._filter_varied_trials(name, trial_results)

            if len(varied_trials) < 2:
                # trial 부족 시 기본 탄성치
                elasticities.append(
                    ParamElasticity(
                        param_name=name,
                        category=pr.category,
                        base_value=pr.base_value,
                    )
                )
                continue

            elasticity = self._compute_single_elasticity(
                param_range=pr,
                base_trial=base_trial,
                varied_trials=varied_trials,
            )
            elasticities.append(elasticity)

        # 임팩트 점수순 정렬
        elasticities.sort(key=lambda e: e.impact_score, reverse=True)
        return elasticities

    def tornado_ranking(
        self,
        elasticities: list[ParamElasticity],
        metric: str = "sharpe",
    ) -> list[dict]:
        """
        토네이도 차트용 순위 데이터

        Args:
            elasticities: 탄성치 리스트
            metric: "sharpe", "cagr", "mdd" 중 하나

        Returns:
            [{"param": ..., "low": ..., "high": ..., "base": ...}, ...]
        """
        ranking = []
        for e in elasticities:
            if metric == "sharpe":
                low, high = e.sharpe_range
            elif metric == "cagr":
                low, high = e.cagr_range
            elif metric == "mdd":
                low, high = e.mdd_range
            else:
                continue

            ranking.append(
                {
                    "param": e.param_name,
                    "category": e.category.value,
                    "low": low,
                    "high": high,
                    "spread": abs(high - low),
                    "elasticity": getattr(e, f"{metric}_elasticity", 0.0),
                }
            )

        ranking.sort(key=lambda x: x["spread"], reverse=True)
        return ranking

    def _find_base_trial(
        self,
        trials: list[ParamTrialResult],
    ) -> Optional[ParamTrialResult]:
        """기본 파라미터 값과 일치하는 trial 찾기"""
        for trial in trials:
            is_base = all(
                abs(trial.param_values.get(name, 0) - base_val) < 1e-8 for name, base_val in self._base_values.items()
            )
            if is_base:
                return trial
        return None

    def _filter_varied_trials(
        self,
        target_param: str,
        trials: list[ParamTrialResult],
    ) -> list[ParamTrialResult]:
        """
        target_param만 변경된 trial 필터링 (OAT 결과용)

        다른 파라미터는 기본값과 동일한 trial만 반환
        """
        result = []
        for trial in trials:
            other_match = all(
                abs(trial.param_values.get(name, 0) - base_val) < 1e-8
                for name, base_val in self._base_values.items()
                if name != target_param
            )
            if other_match:
                result.append(trial)
        return result

    def _compute_single_elasticity(
        self,
        param_range: ParamRange,
        base_trial: ParamTrialResult,
        varied_trials: list[ParamTrialResult],
    ) -> ParamElasticity:
        """단일 파라미터의 탄성치 계산"""
        name = param_range.name
        base_val = param_range.base_value

        # 파라미터 값 및 메트릭 추출
        param_vals = [t.param_values.get(name, base_val) for t in varied_trials]
        sharpes = [t.sharpe_ratio for t in varied_trials]
        cagrs = [t.cagr for t in varied_trials]
        mdds = [t.mdd for t in varied_trials]

        # 탄성치: (Δmetric/metric) / (Δparam/param)
        sharpe_elast = self._calc_elasticity(param_vals, sharpes, base_val, base_trial.sharpe_ratio)
        cagr_elast = self._calc_elasticity(param_vals, cagrs, base_val, base_trial.cagr)
        mdd_elast = self._calc_elasticity(param_vals, mdds, base_val, base_trial.mdd)

        # 단조성 (Spearman-like)
        monotonicity = self._calc_monotonicity(param_vals, sharpes)

        # 안정 구간: Sharpe 변동 ±10% 이내인 파라미터 범위
        stable_range = self._calc_stable_range(param_vals, sharpes, base_trial.sharpe_ratio, tolerance=0.10)

        return ParamElasticity(
            param_name=name,
            category=param_range.category,
            base_value=base_val,
            sharpe_elasticity=sharpe_elast,
            cagr_elasticity=cagr_elast,
            mdd_elasticity=mdd_elast,
            sharpe_range=(min(sharpes), max(sharpes)),
            cagr_range=(min(cagrs), max(cagrs)),
            mdd_range=(min(mdds), max(mdds)),
            monotonicity=monotonicity,
            stable_range=stable_range,
        )

    @staticmethod
    def _calc_elasticity(
        param_vals: list[float],
        metric_vals: list[float],
        base_param: float,
        base_metric: float,
    ) -> float:
        """
        평균 탄성치 계산

        E = mean( (Δm/m₀) / (Δp/p₀) )
        base_param 또는 base_metric이 0이면 절대 변화량 기반으로 폴백
        """
        if abs(base_param) < 1e-12 or abs(base_metric) < 1e-12:
            # 0 기준일 때는 절대 변화량 기반
            deltas = [abs(m - base_metric) for m in metric_vals]
            return float(np.mean(deltas)) if deltas else 0.0

        elasticities = []
        for p, m in zip(param_vals, metric_vals):
            dp = (p - base_param) / base_param
            dm = (m - base_metric) / base_metric
            if abs(dp) > 1e-12:
                elasticities.append(dm / dp)

        return float(np.mean(elasticities)) if elasticities else 0.0

    @staticmethod
    def _calc_monotonicity(
        param_vals: list[float],
        metric_vals: list[float],
    ) -> float:
        """
        단조성 계산 (-1 ~ +1)

        파라미터 증가에 따른 메트릭 방향성 일관성
        """
        if len(param_vals) < 2:
            return 0.0

        # 파라미터순 정렬
        pairs = sorted(zip(param_vals, metric_vals))
        sorted_metrics = [m for _, m in pairs]

        # 연속 쌍의 방향성 비교
        increases = 0
        decreases = 0
        for i in range(len(sorted_metrics) - 1):
            diff = sorted_metrics[i + 1] - sorted_metrics[i]
            if diff > 1e-12:
                increases += 1
            elif diff < -1e-12:
                decreases += 1

        total = increases + decreases
        if total == 0:
            return 0.0

        return (increases - decreases) / total

    @staticmethod
    def _calc_stable_range(
        param_vals: list[float],
        metric_vals: list[float],
        base_metric: float,
        tolerance: float = 0.10,
    ) -> tuple[float, float]:
        """
        안정 구간 계산

        메트릭이 base_metric ± tolerance 이내인 파라미터 범위
        """
        if abs(base_metric) < 1e-12:
            threshold = tolerance
        else:
            threshold = abs(base_metric * tolerance)

        stable_params = [p for p, m in zip(param_vals, metric_vals) if abs(m - base_metric) <= threshold]

        if not stable_params:
            return (min(param_vals), min(param_vals)) if param_vals else (0.0, 0.0)

        return (min(stable_params), max(stable_params))
