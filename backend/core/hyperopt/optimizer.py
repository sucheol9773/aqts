"""
하이퍼파라미터 최적화 오케스트레이터 (Hyperopt Optimizer)

Optuna study를 생성하고 TPE 기반 베이지안 최적화를 실행합니다.

사용법:
    optimizer = HyperoptOptimizer(ohlcv_data)
    result = optimizer.optimize(n_trials=100)
    print(result.best_params)
    print(result.improvement_pct)
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

import optuna
import pandas as pd

from config.logging import logger
from core.hyperopt.models import OptimizationResult, TrialResult
from core.hyperopt.objective import ObjectiveFunction
from core.hyperopt.search_space import SearchSpace


class HyperoptOptimizer:
    """
    Optuna 기반 하이퍼파라미터 최적화 오케스트레이터

    TPE (Tree-structured Parzen Estimator) 샘플러로
    OOS Sharpe를 최대화하는 파라미터 조합을 탐색합니다.
    """

    def __init__(
        self,
        ohlcv_data: dict[str, pd.DataFrame],
        train_months: int = 24,
        test_months: int = 3,
        initial_capital: float = 50_000_000.0,
        groups: Optional[list[str]] = None,
        study_name: Optional[str] = None,
    ):
        """
        Args:
            ohlcv_data: {ticker: OHLCV DataFrame}
            train_months: Walk-forward 학습 기간
            test_months: Walk-forward 평가 기간
            initial_capital: 백테스트 초기 자본
            groups: 최적화할 파라미터 그룹 (None=전체)
                    예: ["ensemble"], ["ensemble", "risk"],
                        ["ensemble", "regime_weights", "risk"]
            study_name: Optuna study 이름
        """
        self._ohlcv_data = ohlcv_data
        self._groups = groups
        self._study_name = study_name or self._generate_study_name()

        self._objective = ObjectiveFunction(
            ohlcv_data=ohlcv_data,
            train_months=train_months,
            test_months=test_months,
            initial_capital=initial_capital,
            groups=groups,
        )

    def optimize(
        self,
        n_trials: int = 50,
        timeout: Optional[int] = None,
        n_startup_trials: int = 10,
        show_progress: bool = True,
    ) -> OptimizationResult:
        """
        최적화 실행

        Args:
            n_trials: 총 시행 횟수
            timeout: 최대 실행 시간 (초)
            n_startup_trials: 랜덤 탐색 시행 수 (TPE 시작 전)
            show_progress: 진행 상황 로깅

        Returns:
            OptimizationResult
        """
        started_at = datetime.now(timezone.utc)
        start_time = time.time()

        logger.info(
            f"[Hyperopt] 최적화 시작: study={self._study_name}, "
            f"n_trials={n_trials}, groups={self._groups}, "
            f"tickers={len(self._ohlcv_data)}개"
        )

        # ── 1. 기준선 측정 ──
        baseline_sharpe, baseline_params = self._objective.get_baseline_score()
        logger.info(f"[Hyperopt] 기준선 OOS Sharpe: {baseline_sharpe:.4f}")

        # ── 2. Optuna Study 생성 ──
        sampler = optuna.samplers.TPESampler(
            n_startup_trials=n_startup_trials,
            seed=42,
            multivariate=True,
        )
        pruner = optuna.pruners.MedianPruner(
            n_startup_trials=5,
            n_warmup_steps=2,
        )

        study = optuna.create_study(
            study_name=self._study_name,
            direction="maximize",
            sampler=sampler,
            pruner=pruner,
        )

        # ── 3. 기본값을 첫 번째 trial로 추가 (enqueue) ──
        defaults = SearchSpace.get_defaults()
        if self._groups:
            active_params = SearchSpace.get_params_by_groups(self._groups)
            active_names = {p.name for p in active_params}
            defaults = {k: v for k, v in defaults.items() if k in active_names}
        study.enqueue_trial(defaults)

        # ── 4. 최적화 실행 ──
        optuna.logging.set_verbosity(optuna.logging.WARNING)

        if show_progress:
            callback = self._make_progress_callback(n_trials)
        else:
            callback = None

        study.optimize(
            self._objective,
            n_trials=n_trials,
            timeout=timeout,
            callbacks=[callback] if callback else None,
        )

        # ── 5. 결과 수집 ──
        total_duration = time.time() - start_time

        trials = self._collect_trials(study)

        best = study.best_trial
        best_sharpe = best.value
        best_params = best.params

        # 개선율
        if abs(baseline_sharpe) > 1e-6:
            improvement = (best_sharpe - baseline_sharpe) / abs(baseline_sharpe) * 100
        else:
            improvement = float("inf") if best_sharpe > baseline_sharpe else 0.0

        # 파라미터 중요도
        param_importances = self._compute_importances(study)

        result = OptimizationResult(
            study_name=self._study_name,
            n_trials=n_trials,
            n_completed=len([t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]),
            n_pruned=len([t for t in study.trials if t.state == optuna.trial.TrialState.PRUNED]),
            best_params=best_params,
            best_oos_sharpe=best_sharpe,
            best_trial_number=best.number,
            baseline_oos_sharpe=baseline_sharpe,
            baseline_params=baseline_params,
            improvement_pct=improvement,
            trials=trials,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            total_duration_seconds=total_duration,
            param_importances=param_importances,
        )

        logger.info(
            f"[Hyperopt] 최적화 완료: "
            f"best_sharpe={best_sharpe:.4f} "
            f"(baseline={baseline_sharpe:.4f}, "
            f"개선={improvement:+.1f}%), "
            f"소요={total_duration:.0f}s"
        )

        return result

    def _collect_trials(self, study: optuna.Study) -> list[TrialResult]:
        """Optuna trial → TrialResult 변환"""
        results = []
        for t in study.trials:
            if t.state == optuna.trial.TrialState.COMPLETE:
                oos_sharpes = t.user_attrs.get("oos_sharpes", [])
                results.append(
                    TrialResult(
                        trial_number=t.number,
                        params=t.params,
                        oos_sharpe=t.value,
                        oos_cagr=0.0,  # walk-forward에선 개별 CAGR 미추적
                        oos_mdd=0.0,
                        oos_sortino=0.0,
                        oos_calmar=0.0,
                        oos_win_rate=0.0,
                        oos_window_count=t.user_attrs.get("n_windows", 0),
                        oos_positive_windows=sum(1 for s in oos_sharpes if s > 0),
                        oos_sharpe_variance=t.user_attrs.get("sharpe_std", 0.0) ** 2,
                        duration_seconds=t.user_attrs.get("duration_s", 0.0),
                        pruned=False,
                    )
                )
            elif t.state == optuna.trial.TrialState.PRUNED:
                results.append(
                    TrialResult(
                        trial_number=t.number,
                        params=t.params,
                        oos_sharpe=float("-inf"),
                        oos_cagr=0.0,
                        oos_mdd=0.0,
                        oos_sortino=0.0,
                        oos_calmar=0.0,
                        oos_win_rate=0.0,
                        pruned=True,
                    )
                )
        return results

    def _compute_importances(self, study: optuna.Study) -> dict[str, float]:
        """파라미터 중요도 계산 (fANOVA)"""
        try:
            completed = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
            if len(completed) < 10:
                return {}

            importances = optuna.importance.get_param_importances(study)
            return {k: float(v) for k, v in importances.items()}
        except Exception as e:
            logger.warning(f"[Hyperopt] 중요도 계산 실패: {e}")
            return {}

    @staticmethod
    def _make_progress_callback(total_trials: int):
        """진행 상황 로깅 콜백"""

        def callback(study: optuna.Study, trial: optuna.trial.FrozenTrial):
            n = trial.number + 1
            if n % 5 == 0 or n == total_trials:
                best = study.best_value
                logger.info(f"[Hyperopt] Progress: {n}/{total_trials} trials, " f"best_sharpe={best:.4f}")

        return callback

    @staticmethod
    def _generate_study_name() -> str:
        """고유 study 이름 생성"""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return f"aqts_hyperopt_{ts}"
