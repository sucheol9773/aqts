#!/usr/bin/env python3
"""
RL 에이전트 훈련 스크립트 (Run RL Agent Training)

사용법:
    # DB에서 데이터 로드 후 PPO 학습
    python scripts/run_rl_training.py --algorithm PPO --timesteps 500000

    # CSV에서 데이터 로드
    python scripts/run_rl_training.py --data-source csv --csv-dir data/ohlcv/

    # 합성 데이터로 빠른 테스트
    python scripts/run_rl_training.py --data-source synthetic --timesteps 50000

    # 특정 종목으로 학습
    python scripts/run_rl_training.py --algorithm SAC --ticker 005930

    # 저장된 모델 평가
    python scripts/run_rl_training.py --evaluate --model models/rl_agent_v1
"""

import argparse
from pathlib import Path

from config.logging import logger
from core.rl import RLConfig, RLDataLoader, RLTrainer


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
        "--data-source",
        choices=["db", "csv", "synthetic"],
        default="db",
        help="Data source: db (TimescaleDB), csv (파일), synthetic (합성)",
    )
    parser.add_argument(
        "--csv-dir",
        type=str,
        default="data/ohlcv/",
        help="CSV directory (--data-source csv 시)",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="DB URL (미지정 시 환경변수에서 구성)",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default="2015-01-01",
        help="데이터 시작일 (기본: 2015-01-01)",
    )
    parser.add_argument(
        "--n-synthetic",
        type=int,
        default=5,
        help="합성 종목 수 (--data-source synthetic 시)",
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
    parser.add_argument(
        "--checkpoint-dir",
        type=str,
        default="models/checkpoints",
        help="체크포인트 저장 디렉토리",
    )

    args = parser.parse_args()

    # 데이터 로드
    loader = RLDataLoader()

    if args.data_source == "db":
        tickers = [args.ticker] if args.ticker else None
        ohlcv_data = loader.load_from_db(
            db_url=args.db_url,
            tickers=tickers,
            start_date=args.start_date,
        )
    elif args.data_source == "csv":
        tickers = [args.ticker] if args.ticker else None
        ohlcv_data = loader.load_from_csv(args.csv_dir, tickers=tickers)
    else:  # synthetic
        ohlcv_data = loader.generate_synthetic(n_tickers=args.n_synthetic)

    if not ohlcv_data:
        logger.error("No data available for training")
        return

    logger.info(f"Data loaded: {len(ohlcv_data)} tickers")
    for ticker, df in list(ohlcv_data.items())[:5]:
        logger.info(f"  {ticker}: {len(df)} days ({df.index[0]} ~ {df.index[-1]})")

    # 설정
    config = RLConfig(total_timesteps=args.timesteps)
    trainer = RLTrainer(ohlcv_data, config)

    if args.evaluate and args.model:
        # 평가 모드
        logger.info(f"Evaluating model: {args.model}")
        model = trainer.load_model(args.model, algorithm=args.algorithm)
        eval_result = trainer.evaluate(model)

        _print_eval_results(eval_result)
    else:
        # 훈련 모드
        logger.info(f"Starting {args.algorithm} training " f"with {args.timesteps:,} timesteps...")
        train_result = trainer.train(
            algorithm=args.algorithm,
            ticker=args.ticker,
            checkpoint_dir=args.checkpoint_dir,
        )

        logger.info("Training completed")
        logger.info(f"  Algorithm: {train_result.algorithm}")
        logger.info(f"  Training Time: {train_result.training_time_seconds:.1f}s")
        logger.info(f"  Episodes: {len(train_result.episode_rewards)}")
        logger.info(f"  Final Reward: {train_result.final_reward:.4f}")
        logger.info(f"  Best Reward: {train_result.best_eval_reward:.4f}")

        if train_result.best_model_path:
            logger.info(f"  Best Model: {train_result.best_model_path}")

        # 모델 저장
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        trainer.save_model(train_result.model, str(output_path))
        logger.info(f"Model saved to: {output_path}")

        # 평가
        logger.info("Evaluating trained model...")
        eval_result = trainer.evaluate(train_result.model)
        _print_eval_results(eval_result)


def _print_eval_results(result):
    """평가 결과 출력"""
    logger.info("═" * 50)
    logger.info("  Evaluation Results")
    logger.info("═" * 50)
    logger.info(f"  Total Return:    {result.total_return:>10.2%}")
    logger.info(f"  Sharpe Ratio:    {result.sharpe_ratio:>10.2f}")
    logger.info(f"  Max Drawdown:    {result.max_drawdown:>10.2%}")
    logger.info(f"  Total Trades:    {result.total_trades:>10d}")
    logger.info(f"  Baseline Return: {result.baseline_return:>10.2%}")
    logger.info(f"  Improvement:     {result.improvement_pct:>+10.2f}%")
    logger.info("═" * 50)


if __name__ == "__main__":
    main()
