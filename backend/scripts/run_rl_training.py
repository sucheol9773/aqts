#!/usr/bin/env python3
"""
RL 에이전트 훈련 스크립트 (Run RL Agent Training)

사용법:
    python scripts/run_rl_training.py --algorithm PPO --timesteps 500000
    python scripts/run_rl_training.py --algorithm SAC --ticker 005930
    python scripts/run_rl_training.py --evaluate --model models/rl_agent_v1
"""

import argparse
from pathlib import Path

import pandas as pd

from config.logging import logger
from core.rl import RLConfig, RLTrainer


def load_sample_data() -> dict[str, pd.DataFrame]:
    """
    샘플 데이터 로드

    실제 구현에서는 데이터 수집 모듈에서 로드합니다.
    """
    logger.info("Loading sample data...")
    # 여기에 실제 데이터 로드 로직 추가
    return {}


def main():
    parser = argparse.ArgumentParser(description="RL Agent Training")
    parser.add_argument(
        "--algorithm",
        choices=["PPO", "SAC"],
        default="PPO",
        help="Learning algorithm",
    )
    parser.add_argument(
        "--timesteps",
        type=int,
        default=500_000,
        help="Total training timesteps",
    )
    parser.add_argument(
        "--ticker",
        type=str,
        default=None,
        help="Specific ticker to train on",
    )
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help="Evaluate model on test set",
    )
    parser.add_argument(
        "--model",
        type=str,
        help="Path to model for evaluation",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="models/rl_agent_v1",
        help="Output path for trained model",
    )

    args = parser.parse_args()

    # 데이터 로드
    ohlcv_data = load_sample_data()
    if not ohlcv_data:
        logger.error("No data available for training")
        return

    # 설정
    config = RLConfig(total_timesteps=args.timesteps)
    trainer = RLTrainer(ohlcv_data, config)

    if args.evaluate and args.model:
        # 평가 모드
        logger.info(f"Evaluating model: {args.model}")
        model = trainer.load_model(args.model, algorithm=args.algorithm)
        eval_result = trainer.evaluate(model)

        logger.info("Evaluation Results:")
        logger.info(f"  Total Return: {eval_result.total_return:.2%}")
        logger.info(f"  Sharpe Ratio: {eval_result.sharpe_ratio:.2f}")
        logger.info(f"  Max Drawdown: {eval_result.max_drawdown:.2%}")
        logger.info(f"  Total Trades: {eval_result.total_trades}")
        logger.info(f"  Baseline Return: {eval_result.baseline_return:.2%}")
        logger.info(f"  Improvement: {eval_result.improvement_pct:.2f}%")
    else:
        # 훈련 모드
        logger.info(f"Starting {args.algorithm} training with {args.timesteps} timesteps...")
        train_result = trainer.train(algorithm=args.algorithm, ticker=args.ticker)

        logger.info("Training completed")
        logger.info(f"  Algorithm: {train_result.algorithm}")
        logger.info(f"  Training Time: {train_result.training_time_seconds:.1f}s")
        logger.info(f"  Final Reward: {train_result.final_reward:.4f}")
        logger.info(f"  Best Reward: {train_result.best_eval_reward:.4f}")

        # 모델 저장
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        trainer.save_model(train_result.model, str(output_path))
        logger.info(f"Model saved to: {output_path}")

        # 평가
        logger.info("Evaluating trained model...")
        eval_result = trainer.evaluate(train_result.model)

        logger.info("Evaluation Results:")
        logger.info(f"  Total Return: {eval_result.total_return:.2%}")
        logger.info(f"  Sharpe Ratio: {eval_result.sharpe_ratio:.2f}")
        logger.info(f"  Max Drawdown: {eval_result.max_drawdown:.2%}")
        logger.info(f"  Total Trades: {eval_result.total_trades}")
        logger.info(f"  Baseline Return: {eval_result.baseline_return:.2%}")
        logger.info(f"  Improvement: {eval_result.improvement_pct:.2f}%")


if __name__ == "__main__":
    main()
