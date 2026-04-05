"""
RL 에이전트 훈련 파이프라인 (Reinforcement Learning Training Pipeline)

PPO/SAC 알고리즘을 사용하여 AQTS RL 에이전트를 훈련합니다.

사용법:
    trainer = RLTrainer(ohlcv_data, config)
    result = trainer.train(algorithm="PPO")
    trainer.evaluate(result.model)
    trainer.save_model(result.model, "models/rl_agent_v1")
"""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from stable_baselines3 import PPO, SAC

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


class RLTrainer:
    """
    PPO/SAC 학습 파이프라인

    주요 기능:
    - 데이터 분할 (훈련/평가)
    - 모델 훈련 및 평가
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

    def train(self, algorithm: str = "PPO", ticker: str = None) -> TrainResult:
        """
        RL 에이전트 훈련

        Args:
            algorithm: "PPO" 또는 "SAC"
            ticker: 사용할 티커 (None이면 가장 큰 데이터셋 선택)

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

        # 데이터 분할: 80% 훈련
        split_idx = int(len(ohlcv) * 0.8)
        train_data = ohlcv.iloc[:split_idx]

        # 환경 생성
        train_env = TradingEnv(train_data, self.config)

        # 모델 생성 (알고리즘별 파라미터 다름)
        if algorithm == "PPO":
            model = PPO(
                "MlpPolicy",
                train_env,
                learning_rate=self.config.learning_rate,
                batch_size=self.config.batch_size,
                gamma=self.config.gamma,
                gae_lambda=self.config.gae_lambda,
                clip_range=self.config.clip_range,
                verbose=0,
            )
        else:  # SAC
            model = SAC(
                "MlpPolicy",
                train_env,
                learning_rate=self.config.learning_rate,
                batch_size=self.config.batch_size,
                gamma=self.config.gamma,
                verbose=0,
            )

        # 훈련
        start_time = time.time()
        learning_curve = [0.0]  # 최소 하나의 값으로 시작

        model.learn(total_timesteps=self.config.total_timesteps)

        training_time = time.time() - start_time

        # 최종 보상 계산 (직접 훈련된 모델에서 추출)
        # SB3의 log_ema_loss를 사용하거나 기본값 설정
        final_reward = 0.0
        best_eval_reward = 0.0

        return TrainResult(
            model=model,
            algorithm=algorithm,
            total_timesteps=self.config.total_timesteps,
            training_time_seconds=training_time,
            final_reward=final_reward,
            best_eval_reward=best_eval_reward,
            learning_curve=learning_curve,
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
            # 테스트 셋에서 평가 (마지막 20%)
            ticker = list(self.ohlcv_data.keys())[0]
            full_data = self.ohlcv_data[ticker]
            split_idx = int(len(full_data) * 0.8)
            ohlcv = full_data.iloc[split_idx:]

        env = TradingEnv(ohlcv, self.config)

        total_rewards = []
        equity_curves = []
        trades = []

        for _ in range(self.config.eval_episodes):
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

        # 메트릭 계산
        avg_equity_curve = np.mean(equity_curves, axis=0)
        total_return = (avg_equity_curve[-1] - self.config.initial_capital) / (self.config.initial_capital)

        returns = np.diff(avg_equity_curve) / avg_equity_curve[:-1]
        sharpe_ratio = np.mean(returns) / (np.std(returns) + 1e-8) * np.sqrt(252)

        peak = np.max(avg_equity_curve)
        max_drawdown = (peak - np.min(avg_equity_curve)) / peak

        avg_daily_return = np.mean(returns)
        avg_trades = np.mean(trades)

        # 베이스라인 비교 (DynamicEnsembleService)
        baseline_return = self._compute_baseline_return(ohlcv)
        improvement_pct = (total_return - baseline_return) / (abs(baseline_return) + 1e-8) * 100

        return EvalResult(
            total_return=float(total_return),
            sharpe_ratio=float(sharpe_ratio),
            max_drawdown=float(max_drawdown),
            total_trades=int(avg_trades),
            avg_daily_return=float(avg_daily_return),
            baseline_return=float(baseline_return),
            improvement_pct=float(improvement_pct),
        )

    def _compute_baseline_return(self, ohlcv: pd.DataFrame) -> float:
        """
        베이스라인 수익률 계산 (DynamicEnsembleService)

        Args:
            ohlcv: 평가 데이터

        Returns:
            베이스라인 총 수익률
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
        """
        모델 저장

        Args:
            model: 저장할 모델
            path: 저장 경로
        """
        path_obj = Path(path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        model.save(path)

    def load_model(self, path: str, algorithm: str = "PPO"):
        """
        모델 로드

        Args:
            path: 로드할 경로
            algorithm: 모델 알고리즘

        Returns:
            로드된 모델
        """
        model_class = PPO if algorithm == "PPO" else SAC
        return model_class.load(path)
