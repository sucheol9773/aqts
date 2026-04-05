#!/usr/bin/env python3
"""
AQTS 하이퍼파라미터 최적화 실행 스크립트

Optuna 기반 베이지안 최적화로 동적 앙상블 + 리스크 관리 파라미터를
OOS Sharpe 기준으로 자동 최적화합니다.

사용법:
    # 전체 파라미터 최적화 (50 trials)
    python scripts/run_hyperopt.py

    # 앙상블 파라미터만 100 trials
    python scripts/run_hyperopt.py --groups ensemble --trials 100

    # 앙상블 + 리스크 관리
    python scripts/run_hyperopt.py --groups ensemble risk --trials 80

    # 특정 종목으로 테스트
    python scripts/run_hyperopt.py --tickers 005930,000660 --trials 20

    # 시간 제한 (1시간)
    python scripts/run_hyperopt.py --timeout 3600

    # 미국 주식
    python scripts/run_hyperopt.py --market us --tickers AAPL,MSFT,NVDA
"""

import argparse
import json
import os
import sys
import time

import pandas as pd
from dotenv import load_dotenv

# 프로젝트 루트의 .env 파일 로드
_project_root = os.path.join(os.path.dirname(__file__), "..")
load_dotenv(os.path.join(_project_root, ".env"))

# backend 경로 추가
sys.path.insert(0, os.path.join(_project_root, "backend"))

os.environ.setdefault("KIS_TRADING_MODE", "BACKTEST")
os.environ.setdefault("TESTING", "1")


def load_ohlcv_from_db(
    tickers: list[str], start_date: str, end_date: str, db_url: str
) -> dict[str, pd.DataFrame]:
    """PostgreSQL에서 OHLCV 데이터 로드"""
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)
    ohlcv_data = {}

    with engine.connect() as conn:
        for ticker in tickers:
            query = text(
                """
                SELECT time, open, high, low, close, volume
                FROM market_ohlcv
                WHERE ticker = :ticker AND interval = '1d'
                  AND time >= :start AND time <= :end
                ORDER BY time
            """
            )
            rows = conn.execute(
                query,
                {"ticker": ticker, "start": start_date, "end": end_date},
            ).fetchall()

            if len(rows) >= 200:
                df = pd.DataFrame(
                    rows,
                    columns=["time", "open", "high", "low", "close", "volume"],
                )
                df["time"] = pd.to_datetime(df["time"])
                df = df.set_index("time")
                for col in ["open", "high", "low", "close", "volume"]:
                    df[col] = df[col].astype(float)
                ohlcv_data[ticker] = df

    return ohlcv_data


def load_all_tickers_from_db(db_url: str, market: str = "kr") -> list[str]:
    """DB에서 시장별 전체 종목코드 조회"""
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)
    with engine.connect() as conn:
        if market == "kr":
            query = text(
                "SELECT DISTINCT ticker FROM market_ohlcv "
                "WHERE ticker ~ '^[0-9]+$' ORDER BY ticker"
            )
        else:
            query = text(
                "SELECT DISTINCT ticker FROM market_ohlcv "
                "WHERE ticker ~ '^[A-Z]+$' ORDER BY ticker"
            )
        rows = conn.execute(query).fetchall()
    return [row[0] for row in rows]


def main():
    parser = argparse.ArgumentParser(
        description="AQTS 하이퍼파라미터 최적화 (Optuna)"
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=50,
        help="최적화 시행 횟수 (기본: 50)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="최대 실행 시간 초 (기본: 무제한)",
    )
    parser.add_argument(
        "--groups",
        nargs="+",
        default=None,
        choices=["ensemble", "regime_weights", "risk"],
        help="최적화할 파라미터 그룹 (기본: 전체)",
    )
    parser.add_argument(
        "--tickers",
        type=str,
        default=None,
        help="종목코드 (콤마 구분, 기본: DB 전종목)",
    )
    parser.add_argument(
        "--market",
        type=str,
        default="kr",
        choices=["kr", "us"],
        help="시장 (기본: kr)",
    )
    parser.add_argument(
        "--start",
        type=str,
        default="2000-01-01",
        help="데이터 시작일 (기본: 2000-01-01)",
    )
    parser.add_argument(
        "--end",
        type=str,
        default="2026-04-03",
        help="데이터 종료일 (기본: 2026-04-03)",
    )
    parser.add_argument(
        "--train-months",
        type=int,
        default=24,
        help="Walk-forward 학습 기간 (기본: 24개월)",
    )
    parser.add_argument(
        "--test-months",
        type=int,
        default=3,
        help="Walk-forward 평가 기간 (기본: 3개월)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="결과 JSON 파일 경로",
    )
    parser.add_argument(
        "--startup-trials",
        type=int,
        default=10,
        help="랜덤 탐색 시행 수 (기본: 10)",
    )

    args = parser.parse_args()

    from config.settings import get_settings

    settings = get_settings()
    db_url = str(settings.database.url).replace("+asyncpg", "")

    # ── 데이터 로드 ──
    print("══ AQTS Hyperparameter Optimization ══")
    print(f"  Market: {args.market.upper()}")
    print(f"  Period: {args.start} ~ {args.end}")
    print(f"  Trials: {args.trials}")
    print(f"  Groups: {args.groups or 'ALL'}")
    print()

    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",")]
    else:
        print("  Loading tickers from DB...")
        tickers = load_all_tickers_from_db(db_url, args.market)

    print(f"  Tickers: {len(tickers)}개")
    print("  Loading OHLCV data...")

    start_load = time.time()
    ohlcv_data = load_ohlcv_from_db(tickers, args.start, args.end, db_url)
    load_time = time.time() - start_load

    print(f"  Loaded: {len(ohlcv_data)}/{len(tickers)} tickers ({load_time:.1f}s)")
    print()

    if not ohlcv_data:
        print("ERROR: No OHLCV data loaded. Run backfill_market_data.py first.")
        sys.exit(1)

    # ── 최적화 실행 ──
    from core.hyperopt.optimizer import HyperoptOptimizer

    optimizer = HyperoptOptimizer(
        ohlcv_data=ohlcv_data,
        train_months=args.train_months,
        test_months=args.test_months,
        groups=args.groups,
    )

    result = optimizer.optimize(
        n_trials=args.trials,
        timeout=args.timeout,
        n_startup_trials=args.startup_trials,
    )

    # ── 결과 출력 ──
    print()
    print("══ 최적화 결과 ══")
    print(f"  Study: {result.study_name}")
    print(f"  Completed: {result.n_completed}/{result.n_trials} trials")
    print(f"  Pruned: {result.n_pruned} trials")
    print(f"  Duration: {result.total_duration_seconds:.0f}s")
    print()
    print(f"  Baseline OOS Sharpe: {result.baseline_oos_sharpe:.4f}")
    print(f"  Best OOS Sharpe:     {result.best_oos_sharpe:.4f}")
    print(f"  Improvement:         {result.improvement_pct:+.1f}%")
    print()

    print("── Best Parameters ──")
    for k, v in sorted(result.best_params.items()):
        default = result.baseline_params.get(k)
        if default is not None:
            delta = v - default
            print(f"  {k}: {v:.4f} (default={default:.4f}, delta={delta:+.4f})")
        else:
            print(f"  {k}: {v:.4f}")

    if result.param_importances:
        print()
        print("── Parameter Importances ──")
        sorted_imp = sorted(
            result.param_importances.items(), key=lambda x: x[1], reverse=True
        )
        for k, v in sorted_imp[:10]:
            bar = "█" * int(v * 50)
            print(f"  {k:35s} {v:.3f} {bar}")

    # ── 결과 저장 ──
    if args.output:
        output_path = args.output
    else:
        os.makedirs("results", exist_ok=True)
        output_path = f"results/hyperopt_{result.study_name}.json"

    with open(output_path, "w") as f:
        json.dump(result.to_dict(), f, indent=2, ensure_ascii=False)

    print()
    print(f"  Results saved: {output_path}")


if __name__ == "__main__":
    main()
