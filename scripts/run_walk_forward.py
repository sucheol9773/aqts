#!/usr/bin/env python3
"""
AQTS Walk-Forward OOS 검증 스크립트

전체 데이터를 학습/검증 구간으로 분할하여 out-of-sample 성과를 측정합니다.
과적합 여부를 판단하기 위해 in-sample과 OOS 성과를 비교합니다.

사용법:
    # 기본: 24개월 학습, 3개월 검증 (한국 전종목)
    python scripts/run_walk_forward.py --all --market kr

    # 학습/검증 기간 조정
    python scripts/run_walk_forward.py --all --market kr --train 36 --test 6

    # 특정 종목
    python scripts/run_walk_forward.py --tickers 005930,000660 --train 24 --test 3
"""

import argparse
import os
import sys

import pandas as pd
from dotenv import load_dotenv

# 프로젝트 루트의 .env 파일 로드
_project_root = os.path.join(os.path.dirname(__file__), "..")
load_dotenv(os.path.join(_project_root, ".env"))

# backend 경로 추가
sys.path.insert(0, os.path.join(_project_root, "backend"))

os.environ.setdefault("KIS_TRADING_MODE", "BACKTEST")
os.environ.setdefault("TESTING", "1")

from run_backtest import (
    KOSPI_TOP10,
    US_TOP10,
    build_db_url,
    generate_strategy_signals_vectorized,
    load_all_tickers_from_db,
    load_ohlcv_from_db,
)

from core.oos.walk_forward import WalkForwardEngine


def run_walk_forward(
    ohlcv_data: dict[str, pd.DataFrame],
    train_months: int = 24,
    test_months: int = 3,
) -> dict[str, object]:
    """
    전 전략에 대해 walk-forward OOS 검증 실행

    Returns:
        {strategy_name: OOSRun}
    """
    # 1) 시그널 생성 (벡터화 + 병렬)
    print("\n══ 시그널 생성 ══")
    ticker_signals = {}
    n_tickers = len(ohlcv_data)

    if n_tickers >= 4:
        import multiprocessing as mp

        from run_backtest import _generate_signals_worker

        n_workers = min(mp.cpu_count(), n_tickers)
        print(f"  병렬 처리: {n_workers} workers × {n_tickers} 종목")
        with mp.Pool(n_workers) as pool:
            results = pool.map(_generate_signals_worker, list(ohlcv_data.items()))
        for ticker, signals in results:
            ticker_signals[ticker] = signals
            print(f"  ✓ [{ticker}] 완료")
    else:
        for ticker, ohlcv in ohlcv_data.items():
            print(f"  [{ticker}] 시그널 계산 중...")
            ticker_signals[ticker] = generate_strategy_signals_vectorized(ticker, ohlcv)

    # 2) 전략별 walk-forward
    strategy_names = [
        "MEAN_REVERSION",
        "TREND_FOLLOWING",
        "RISK_PARITY",
        "ENSEMBLE",
    ]
    wf_engine = WalkForwardEngine()
    all_results = {}

    for strategy in strategy_names:
        print(f"\n══ {strategy} Walk-Forward ══")
        print(f"  학습: {train_months}개월 / 검증: {test_months}개월")

        signals_df = pd.DataFrame({ticker: ticker_signals[ticker][strategy] for ticker in ohlcv_data})
        prices_df = pd.DataFrame({ticker: ohlcv_data[ticker]["close"] for ticker in ohlcv_data})

        common_idx = signals_df.index.intersection(prices_df.index)
        signals_df = signals_df.loc[common_idx].fillna(0.0)
        prices_df = prices_df.loc[common_idx].ffill().bfill()

        if len(common_idx) < (train_months + test_months) * 21:
            print(f"  ⚠ 데이터 부족 ({len(common_idx)}일), skip")
            continue

        oos_run = wf_engine.run(
            strategy_name=strategy,
            signals=signals_df,
            prices=prices_df,
            train_months=train_months,
            test_months=test_months,
        )

        all_results[strategy] = oos_run

        # 윈도우별 결과 출력
        print(f"  윈도우: {oos_run.total_windows}개")
        print(f"  양수 수익 윈도우: {oos_run.passed_windows}개")
        print(f"  평균 Sharpe: {oos_run.avg_sharpe:.3f}")
        print(f"  평균 CAGR:   {oos_run.avg_cagr:+.2%}")
        print(f"  평균 MDD:    {oos_run.avg_mdd:.2%}")
        print(f"  최악 MDD:    {oos_run.worst_mdd:.2%}")
        print(f"  Sharpe 분산: {oos_run.sharpe_variance:.4f}")
        print(f"  Gate 판정:   {oos_run.overall_gate}")

    return all_results


def print_oos_summary(results: dict[str, object]):
    """OOS 결과 요약 테이블"""
    print("\n" + "═" * 90)
    print("  Walk-Forward OOS 검증 요약")
    print("═" * 90)

    header = (
        f"{'전략':<20} {'윈도우':>6} {'양수':>4} "
        f"{'평균Sharpe':>10} {'평균CAGR':>10} {'평균MDD':>10} "
        f"{'최악MDD':>10} {'분산':>8} {'Gate':>8}"
    )
    print(header)
    print("─" * 90)

    for name, r in results.items():
        win_ratio = f"{r.passed_windows}/{r.total_windows}"
        row = (
            f"{name:<20} {r.total_windows:>6} {win_ratio:>4} "
            f"{r.avg_sharpe:>+9.3f} {r.avg_cagr:>+9.2%} "
            f"{r.avg_mdd:>9.2%} {r.worst_mdd:>9.2%} "
            f"{r.sharpe_variance:>7.4f} {r.overall_gate:>8}"
        )
        print(row)

    print("─" * 90)

    # 과적합 판단 기준 설명
    if results:
        best = max(results.items(), key=lambda x: x[1].avg_sharpe)
        print(f"\n  📊 OOS 최고 Sharpe: {best[0]} ({best[1].avg_sharpe:+.3f})")

        # OOS Sharpe > 0 이면 과적합 아닐 가능성
        positive_oos = [n for n, r in results.items() if r.avg_sharpe > 0]
        if positive_oos:
            print(f"  ✅ OOS 양수 Sharpe: {', '.join(positive_oos)}")
        else:
            print("  ⚠ 모든 전략의 OOS Sharpe가 음수 — 과적합 가능성 높음")


def print_window_details(results: dict[str, object]):
    """윈도우별 상세 출력"""
    for name, r in results.items():
        print(f"\n── {name} 윈도우별 상세 ──")
        print(f"  {'#':>3} {'검증기간':<25} {'수익률':>10} {'Sharpe':>8} " f"{'MDD':>10} {'거래':>6}")
        for w in r.windows:
            print(
                f"  {w.window_index:>3} "
                f"{w.test_start}~{w.test_end}  "
                f"{w.total_return:>+9.2%} "
                f"{w.sharpe_ratio:>+7.3f} "
                f"{w.mdd:>9.2%} "
                f"{w.total_trades:>5}"
            )


def main():
    parser = argparse.ArgumentParser(description="AQTS Walk-Forward OOS 검증")
    parser.add_argument(
        "--tickers",
        type=str,
        default=None,
        help="종목코드 콤마 구분",
    )
    parser.add_argument(
        "--market",
        type=str,
        default="kr",
        choices=["kr", "us"],
        help="시장 (kr/us)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="DB의 해당 시장 전체 종목",
    )
    parser.add_argument(
        "--start",
        type=str,
        default="2000-01-02",
        help="시작일",
    )
    parser.add_argument(
        "--end",
        type=str,
        default="2026-04-04",
        help="종료일",
    )
    parser.add_argument(
        "--train",
        type=int,
        default=24,
        help="학습 기간 (개월, 기본: 24)",
    )
    parser.add_argument(
        "--test",
        type=int,
        default=3,
        help="검증 기간 (개월, 기본: 3)",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="DB URL",
    )
    parser.add_argument(
        "--detail",
        action="store_true",
        help="윈도우별 상세 출력",
    )
    args = parser.parse_args()

    db_url = args.db_url or build_db_url()

    # 종목 설정
    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",")]
    elif getattr(args, "all"):
        tickers = load_all_tickers_from_db(db_url, args.market)
    else:
        tickers = US_TOP10 if args.market == "us" else KOSPI_TOP10

    print("═" * 60)
    print("  AQTS Walk-Forward OOS 검증")
    print("═" * 60)
    print(f"  시장:   {args.market.upper()}")
    print(f"  종목:   {len(tickers)}개 " f"({', '.join(tickers[:5])}{'...' if len(tickers) > 5 else ''})")
    print(f"  기간:   {args.start} ~ {args.end}")
    print(f"  학습:   {args.train}개월 / 검증: {args.test}개월")
    print()

    # 1) 데이터 로드
    print("══ OHLCV 데이터 로드 ══")
    ohlcv_data = load_ohlcv_from_db(tickers, args.start, args.end, db_url)

    if not ohlcv_data:
        print("\n❌ 로드된 데이터가 없습니다.")
        sys.exit(1)

    print(f"\n  총 {len(ohlcv_data)}개 종목 로드 완료")

    # 2) Walk-forward 실행
    results = run_walk_forward(ohlcv_data, args.train, args.test)

    if not results:
        print("\n❌ 결과 없음")
        sys.exit(1)

    # 3) 요약 출력
    print_oos_summary(results)

    # 4) 상세 출력
    if args.detail:
        print_window_details(results)

    print("\n✅ Walk-Forward 검증 완료")
    return results


if __name__ == "__main__":
    main()
