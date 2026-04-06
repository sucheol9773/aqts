"""
RL 에이전트 훈련 파이프라인 (Reinforcement Learning Training Pipeline)

PPO/SAC 알고리즘을 사용하여 AQTS RL 에이전트를 훈련합니다.

v2 개선 (2026-04-06):
- SB3 EvalCallback으로 학습 중 best model 자동 체크포인팅
- 에피소드별 보상/학습곡선 추적 (RewardTrackingCallback)
- 자동 보상 스케일링 (initial_capital 기반)
- Walk-forward 평가: 훈련/검증/테스트 3분할
- 상세 로깅: 에피소드 보상, drawdown, 거래 횟수

사용법:
    trainer = RLTrainer(ohlcv_data, config)
    result = trainer.train(algorithm="PPO")
    trainer.evaluate(result.model)
    trainer.save_model(result.model, "models/rl_agent_v1")
"""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from stable_baselines3 import PPO, SAC
from stable_baselines3.common.callbacks import BaseCallback, EvalCallback

from config.logging import logger
from core.rl.config import RLConfig
from core.rl.environment import TradingEnv
from core.strategy_ensemble.dynamic_ensemble import DynamicEnsembleService


@dataclass
class TrainResult:
    """훈련 결과"""

    model: Any
    algorithm: str
    total_timesteps: int
    training_time_seconds: float
    final_reward: float
    best_eval_reward: float
    learning_curve: list[float]
    episode_rewards: list[float] = field(default_factory=list)
    episode_lengths: list[int] = field(default_factory=list)
    best_model_path: str | None = None


@dataclass
class EvalResult:
    """평가 결과"""

    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    total_trades: int
    avg_daily_return: float
    baseline_return: float
    improvement_pct: float
    episode_returns: list[float] = field(default_factory=list)


class RewardTrackingCallback(BaseCallback):
    """
    에피소드별 보상/메트릭 추적 콜백

    학습 중 에피소드가 끝날 때마다 보상, 길이, 포트폴리오 정보를 기록합니다.
    """

    def __init__(self, verbose: int = 0):
        super().__init__(verbose)
        self.episode_rewards: list[float] = []
        self.episode_lengths: list[int] = []
        self.current_episode_reward: float = 0.0
        self.current_episode_length: int = 0

    def _on_step(self) -> bool:
        self.current_episode_reward += self.locals.get("rewards", [0.0])[0]
        self.current_episode_length += 1

        # 에피소드 종료 체크
        dones = self.locals.get("dones", [False])
        if dones[0]:
            self.episode_rewards.append(self.current_episode_reward)
            self.episode_lengths.append(self.current_episode_length)

            if self.verbose >= 1 and len(self.episode_rewards) % 50 == 0:
                recent = self.episode_rewards[-50:]
                logger.info(
                    f"Episode {len(self.episode_rewards)}: "
                    f"avg_reward={np.mean(recent):.4f}, "
                    f"avg_length={np.mean(self.episode_lengths[-50:]):.0f}"
                )

            self.current_episode_reward = 0.0
            self.current_episode_length = 0

        return True


class RLTrainer:
    """
    PPO/SAC 학습 파이프라인

    주요 기능:
    - 데이터 3분할 (훈련 70% / 검증 15% / 테스트 15%)
    - EvalCallback으로 학습 중 best model 자동 체크포인팅
    - RewardTrackingCallback으로 학습곡선 추적
    - 모델 저장/로드
    """

    def __init__(self, ohlcv_data: dict[str, pd.DataFrame], config: RLConfig = None):
        """
        Args:
            ohlcv_data: {ticker: DataFrame} 딕셔너리
            config: RLConfig 설정
        """
        self.ohlcv_data = ohlcv_data
        self.config = config or RLConfig()

    def train(
        self,
        algorithm: str = "PPO",
        ticker: str = None,
        checkpoint_dir: str = "models/checkpoints",
    ) -> TrainResult:
        """
        RL 에이전트 훈련

        Args:
            algorithm: "PPO" 또는 "SAC"
            ticker: 사용할 티커 (None이면 가장 큰 데이터셋 선택)
            checkpoint_dir: 체크포인트 저장 디렉토리

        Returns:
            TrainResult
        """
        # 티커 선택
        if ticker is None:
            ticker = max(
                self.ohlcv_data.keys(),
                key=lambda k: len(self.ohlcv_data[k]),
            )

        ohlcv = self.ohlcv_data[ticker]
        logger.info(f"Training on {ticker} ({len(ohlcv)} days)")

        # 데이터 3분할: 70% 훈련 / 15% 검증 / 15% 테스트
        n = len(ohlcv)
        train_end = int(n * 0.70)
        val_end = int(n * 0.85)

        train_data = ohlcv.iloc[:train_end]
        val_data = ohlcv.iloc[train_end:val_end]

        logger.info(f"Split: train={len(train_data)}, val={len(val_data)}, " f"test={n - val_end}")

        # 환경 생성
        train_env = TradingEnv(train_data, self.config)

        # 검증 환경 (EvalCallback용)
        val_env = None
        eval_callback = None
        if len(val_data) >= self.config.lookback_window + 60:
            val_env = TradingEnv(val_data, self.config)
            checkpoint_path = Path(checkpoint_dir) / ticker / algorithm.lower()
            checkpoint_path.mkdir(parents=True, exist_ok=True)

            eval_callback = EvalCallback(
                val_env,
                best_model_save_path=str(checkpoint_path),
                log_path=str(checkpoint_path),
                eval_freq=self.config.eval_freq,
                n_eval_episodes=3,
                deterministic=True,
                verbose=0,
            )

        # 보상 추적 콜백
        reward_tracker = RewardTrackingCallback(verbose=1)

        # 콜백 리스트
        callbacks = [reward_tracker]
        if eval_callback is not None:
            callbacks.append(eval_callback)

        # 모델 생성
        model = self._create_model(algorithm, train_env)

        # 훈련
        start_time = time.time()
        logger.info(f"Starting {algorithm} training: {self.config.total_timesteps} timesteps")
        model.learn(
            total_timesteps=self.config.total_timesteps,
            callback=callbacks,
        )
        training_time = time.time() - start_time

        # 학습곡선 (50 에피소드 rolling mean)
        ep_rewards = reward_tracker.episode_rewards
        if len(ep_rewards) > 50:
            learning_curve = pd.Series(ep_rewards).rolling(50).mean().dropna().tolist()
        else:
            learning_curve = ep_rewards.copy() if ep_rewards else [0.0]

        # best model 경로
        best_model_path = None
        if eval_callback is not None:
            best_path = checkpoint_path / "best_model.zip"
            if best_path.exists():
                best_model_path = str(best_path)
                logger.info(f"Best model saved at: {best_model_path}")

        # 최종 메트릭
        final_reward = ep_rewards[-1] if ep_rewards else 0.0
        best_eval_reward = max(ep_rewards) if ep_rewards else 0.0

        logger.info(
            f"Training complete: {training_time:.1f}s, "
            f"{len(ep_rewards)} episodes, "
            f"final_reward={final_reward:.4f}"
        )

        return TrainResult(
            model=model,
            algorithm=algorithm,
            total_timesteps=self.config.total_timesteps,
            training_time_seconds=training_time,
            final_reward=final_reward,
            best_eval_reward=best_eval_reward,
            learning_curve=learning_curve,
            episode_rewards=ep_rewards,
            episode_lengths=reward_tracker.episode_lengths,
            best_model_path=best_model_path,
        )

    def _create_model(self, algorithm: str, env: TradingEnv):
        """SB3 모델 생성"""
        if algorithm == "PPO":
            return PPO(
                "MlpPolicy",
                env,
                learning_rate=self.config.learning_rate,
                batch_size=self.config.batch_size,
                gamma=self.config.gamma,
                gae_lambda=self.config.gae_lambda,
                clip_range=self.config.clip_range,
                n_epochs=self.config.n_epochs,
                verbose=0,
            )
        else:  # SAC
            return SAC(
                "MlpPolicy",
                env,
                learning_rate=self.config.learning_rate,
                batch_size=self.config.batch_size,
                gamma=self.config.gamma,
                verbose=0,
            )

    def evaluate(self, model, ohlcv: pd.DataFrame = None) -> EvalResult:
        """
        훈련된 에이전트 평가

        Args:
            model: 훈련된 모델
            ohlcv: 평가 데이터 (None이면 테스트 셋 사용)

        Returns:
            EvalResult
        """
        if ohlcv is None:
            # 테스트 셋에서 평가 (마지막 15%)
            ticker = list(self.ohlcv_data.keys())[0]
            full_data = self.ohlcv_data[ticker]
            split_idx = int(len(full_data) * 0.85)
            ohlcv = full_data.iloc[split_idx:]

        env = TradingEnv(ohlcv, self.config)

        total_rewards = []
        equity_curves = []
        trades = []
        episode_returns = []

        for ep in range(self.config.eval_episodes):
            obs, _ = env.reset()
            episode_reward = 0.0
            equity_curve = [env.config.initial_capital]

            while True:
                action, _ = model.predict(obs, deterministic=True)
                obs, reward, terminated, truncated, info = env.step(action)
                episode_reward += reward
                equity_curve.append(info["portfolio_value"])
                trades.append(info["trade_count"])

                if terminated or truncated:
                    break

            total_rewards.append(episode_reward)
            equity_curves.append(equity_curve)

            ep_return = (equity_curve[-1] - self.config.initial_capital) / self.config.initial_capital
            episode_returns.append(ep_return)

        # 메트릭 계산
        avg_equity_curve = np.mean(equity_curves, axis=0)
        total_return = (avg_equity_curve[-1] - self.config.initial_capital) / self.config.initial_capital

        returns = np.diff(avg_equity_curve) / avg_equity_curve[:-1]
        sharpe_ratio = np.mean(returns) / (np.std(returns) + 1e-8) * np.sqrt(252)

        peak = np.max(avg_equity_curve)
        max_drawdown = (peak - np.min(avg_equity_curve)) / peak

        avg_daily_return = np.mean(returns)
        avg_trades = np.mean(trades)

        # 베이스라인 비교
        baseline_return = self._compute_baseline_return(ohlcv)
        improvement_pct = (total_return - baseline_return) / (abs(baseline_return) + 1e-8) * 100

        logger.info(
            f"Eval: return={total_return:.2%}, sharpe={sharpe_ratio:.2f}, "
            f"mdd={max_drawdown:.2%}, trades={avg_trades:.0f}, "
            f"vs_baseline={improvement_pct:+.1f}%"
        )

        return EvalResult(
            total_return=float(total_return),
            sharpe_ratio=float(sharpe_ratio),
            max_drawdown=float(max_drawdown),
            total_trades=int(avg_trades),
            avg_daily_return=float(avg_daily_return),
            baseline_return=float(baseline_return),
            improvement_pct=float(improvement_pct),
            episode_returns=episode_returns,
        )

    def _compute_baseline_return(self, ohlcv: pd.DataFrame) -> float:
        """
        베이스라인 수익률 계산 (DynamicEnsembleService)
        """
        try:
            ensemble = DynamicEnsembleService()
            signals = ensemble.blend_signals(ohlcv)

            close = ohlcv["close"].values
            returns = np.diff(close) / close[:-1]

            positions = np.clip(signals.values[1:], -1.0, 1.0)
            pnl = np.sum(returns * positions)

            return float(pnl)
        except Exception:
            return 0.0

    def save_model(self, model, path: str):
        """모델 저장"""
        path_obj = Path(path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        model.save(path)
        logger.info(f"Model saved: {path}")

    def load_model(self, path: str, algorithm: str = "PPO"):
        """모델 로드"""
        model_class = PPO if algorithm == "PPO" else SAC
        return model_class.load(path)
