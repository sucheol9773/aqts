"""
Hyperopt + RL 결합 (Optuna-based RL Hyperparameter Optimization)

Optuna TPE 베이지안 최적화로 RL 보상함수 파라미터와
학습 하이퍼파라미터를 동시 최적화합니다.

최적화 대상 (14 파라미터):
1. 보상함수: risk_penalty, cost_penalty, max_drawdown_limit
2. 학습: learning_rate, batch_size, gamma, gae_lambda, clip_range, n_epochs
3. 환경: lookback_window, returns_window, volatility_window
4. 알고리즘: PPO vs SAC

목적함수: OOS Sharpe ratio (검증 셋 기준)

사용법:
    optimizer = RLHyperoptOptimizer(ohlcv_data)
    best_config, study = optimizer.optimize(n_trials=50)
"""

import numpy as np
import optuna
import pandas as pd

from config.logging import logger
from core.rl.config import RLConfig
from core.rl.trainer import RLTrainer


class RLHyperoptOptimizer:
    """
    Optuna 기반 RL 하이퍼파라미터 최적화

    TPE (Tree-structured Parzen Estimator) 알고리즘으로
    보상함수 + 학습 파라미터를 동시 최적화합니다.
    """

    def __init__(
        self,
        ohlcv_data: dict[str, pd.DataFrame],
        timesteps_per_trial: int = 100_000,
        ticker: str | None = None,
    ):
        """
        Args:
            ohlcv_data: {ticker: DataFrame} 학습 데이터
            timesteps_per_trial: 시행당 학습 스텝 수 (빠른 탐색)
            ticker: 최적화에 사용할 특정 티커
        """
        self.ohlcv_data = ohlcv_data
        self.timesteps_per_trial = timesteps_per_trial
        self.ticker = ticker

    def optimize(
        self,
        n_trials: int = 50,
        n_jobs: int = 1,
        study_name: str = "rl_hyperopt",
        storage: str | None = None,
    ) -> tuple[RLConfig, optuna.Study]:
        """
        Optuna 최적화 실행

        Args:
            n_trials: 시행 횟수
            n_jobs: 병렬 실행 수 (1 = 순차)
            study_name: Optuna 스터디 이름
            storage: Optuna DB 저장 경로

        Returns:
            (best_config, study) 튜플
        """
        study = optuna.create_study(
            study_name=study_name,
            direction="maximize",  # Sharpe ratio 최대화
            storage=storage,
            load_if_exists=True,
            sampler=optuna.samplers.TPESampler(seed=42),
            pruner=optuna.pruners.MedianPruner(n_startup_trials=5, n_warmup_steps=3),
        )

        logger.info(f"Starting RL hyperopt: {n_trials} trials, " f"{self.timesteps_per_trial:,} timesteps/trial")

        study.optimize(
            self._objective,
            n_trials=n_trials,
            n_jobs=n_jobs,
            show_progress_bar=True,
        )

        # 최적 설정 추출
        best_config = self._trial_to_config(study.best_trial)

        logger.info(f"Best Sharpe: {study.best_value:.4f}")
        logger.info(f"Best params: {study.best_params}")

        return best_config, study

    def _objective(self, trial: optuna.Trial) -> float:
        """
        Optuna 목적함수

        Returns:
            OOS Sharpe ratio (검증 셋 기준)
        """
        config = self._suggest_config(trial)
        config.total_timesteps = self.timesteps_per_trial

        try:
            trainer = RLTrainer(self.ohlcv_data, config)

            # 알고리즘 선택
            algorithm = trial.suggest_categorical("algorithm", ["PPO", "SAC"])

            # 학습 (체크포인트 없이 빠르게)
            result = trainer.train(
                algorithm=algorithm,
                ticker=self.ticker,
                checkpoint_dir=f"models/hyperopt/{trial.number}",
            )

            # 평가 (검증 셋)
            eval_result = trainer.evaluate(result.model)

            # Sharpe가 NaN/Inf이면 pruning
            if np.isnan(eval_result.sharpe_ratio) or np.isinf(eval_result.sharpe_ratio):
                return -10.0

            # 중간 보고 (pruning 지원)
            trial.report(eval_result.sharpe_ratio, step=0)
            if trial.should_prune():
                raise optuna.TrialPruned()

            logger.info(
                f"Trial {trial.number}: sharpe={eval_result.sharpe_ratio:.4f}, "
                f"return={eval_result.total_return:.2%}, "
                f"mdd={eval_result.max_drawdown:.2%}"
            )

            return eval_result.sharpe_ratio

        except optuna.TrialPruned:
            raise
        except Exception as e:
            logger.warning(f"Trial {trial.number} failed: {e}")
            return -10.0

    def _suggest_config(self, trial: optuna.Trial) -> RLConfig:
        """Optuna trial에서 RLConfig 파라미터 제안"""
        return RLConfig(
            # ── 보상함수 ──
            risk_penalty=trial.suggest_float("risk_penalty", 0.5, 5.0),
            cost_penalty=trial.suggest_float("cost_penalty", 0.1, 3.0),
            max_drawdown_limit=trial.suggest_float("max_drawdown_limit", 0.10, 0.30),
            # ── 학습 ──
            learning_rate=trial.suggest_float("learning_rate", 1e-5, 1e-3, log=True),
            batch_size=trial.suggest_categorical("batch_size", [64, 128, 256, 512]),
            gamma=trial.suggest_float("gamma", 0.95, 0.999),
            gae_lambda=trial.suggest_float("gae_lambda", 0.9, 0.99),
            clip_range=trial.suggest_float("clip_range", 0.1, 0.3),
            n_epochs=trial.suggest_int("n_epochs", 3, 20),
            # ── 환경 ──
            lookback_window=trial.suggest_int("lookback_window", 30, 120),
            returns_window=trial.suggest_int("returns_window", 3, 20),
            volatility_window=trial.suggest_int("volatility_window", 10, 60),
        )

    def _trial_to_config(self, trial: "optuna.trial.FrozenTrial") -> RLConfig:
        """완료된 trial에서 RLConfig 생성"""
        params = trial.params
        return RLConfig(
            risk_penalty=params.get("risk_penalty", 2.0),
            cost_penalty=params.get("cost_penalty", 1.0),
            max_drawdown_limit=params.get("max_drawdown_limit", 0.20),
            learning_rate=params.get("learning_rate", 3e-4),
            batch_size=params.get("batch_size", 256),
            gamma=params.get("gamma", 0.99),
            gae_lambda=params.get("gae_lambda", 0.95),
            clip_range=params.get("clip_range", 0.2),
            n_epochs=params.get("n_epochs", 10),
            lookback_window=params.get("lookback_window", 60),
            returns_window=params.get("returns_window", 5),
            volatility_window=params.get("volatility_window", 20),
        )
