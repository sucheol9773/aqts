#!/usr/bin/env python3
"""
AQTS 백테스트 실행 스크립트

DB에 적재된 과거 OHLCV 데이터를 활용하여
전략별 백테스트를 수행하고 결과를 비교합니다.

사용법:
    # KOSPI 대형주 10종목, 전 전략 비교
    python scripts/run_backtest.py

    # 특정 종목만
    python scripts/run_backtest.py --tickers 005930,000660,035420

    # 기간 지정
    python scripts/run_backtest.py --start 2024-06-01 --end 2025-12-31

    # 미국 주식
    python scripts/run_backtest.py --market us --tickers AAPL,MSFT,NVDA

    # 결과 CSV 저장
    python scripts/run_backtest.py --output results/backtest_report.csv
"""

import argparse
import os
import sys

import pandas as pd

# backend 경로 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

os.environ.setdefault("KIS_TRADING_MODE", "BACKTEST")
os.environ.setdefault("TESTING", "1")

from config.constants import TRANSACTION_COSTS, Country
from core.backtest_engine.engine import BacktestConfig, BacktestEngine, StrategyComparator
from core.quant_engine.signal_generator import SignalGenerator

# ══════════════════════════════════════
# 기본 유니버스
# ══════════════════════════════════════
KOSPI_TOP10 = ["005930", "000660", "035420", "005380", "006400", "051910", "003670", "105560", "055550", "000270"]

US_TOP10 = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "TSM", "AVGO", "AMD"]


def load_ohlcv_from_db(tickers: list[str], start_date: str, end_date: str, db_url: str) -> dict[str, pd.DataFrame]:
    """
    PostgreSQL에서 OHLCV 데이터 로드

    Returns:
        {ticker: DataFrame(columns=[open, high, low, close, volume], index=날짜)}
    """
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)
    result = {}

    with engine.connect() as conn:
        for ticker in tickers:
            query = text(
                """
                SELECT time, open, high, low, close, volume
                FROM market_ohlcv
                WHERE ticker = :ticker
                  AND interval = '1d'
                  AND time >= :start
                  AND time <= :end
                ORDER BY time ASC
            """
            )
            df = pd.read_sql(query, conn, params={"ticker": ticker, "start": start_date, "end": end_date})

            if df.empty:
                print(f"  ⚠ {ticker}: 데이터 없음 (skip)")
                continue

            df["time"] = pd.to_datetime(df["time"])
            df.set_index("time", inplace=True)
            df = df.astype(float)
            result[ticker] = df
            print(f"  ✓ {ticker}: {len(df)}일 로드")

    engine.dispose()
    return result


def generate_strategy_signals(ticker: str, ohlcv: pd.DataFrame) -> dict[str, pd.Series]:
    """
    종목의 OHLCV로 전략별 일별 시그널 시계열 생성

    Returns:
        {strategy_name: pd.Series(index=날짜, values=시그널값)}
    """
    gen = SignalGenerator()
    dates = ohlcv.index
    strategies = {
        "MEAN_REVERSION": [],
        "TREND_FOLLOWING": [],
        "RISK_PARITY": [],
        "ENSEMBLE": [],
    }

    # 최소 60일 데이터 필요 (이동평균 등 기술적 지표)
    min_window = 60

    for i in range(len(dates)):
        if i < min_window:
            for key in strategies:
                strategies[key].append(0.0)
            continue

        window = ohlcv.iloc[max(0, i - 252) : i + 1]  # 최근 1년 윈도우

        try:
            mr_sig = gen.generate_mean_reversion_signal(ticker, window)
            tf_sig = gen.generate_trend_following_signal(ticker, window)
            rp_sig = gen.generate_risk_parity_signal(ticker, window)

            strategies["MEAN_REVERSION"].append(mr_sig.value)
            strategies["TREND_FOLLOWING"].append(tf_sig.value)
            strategies["RISK_PARITY"].append(rp_sig.value)

            # 앙상블: 가중 평균 (추세추종 40%, 평균회귀 30%, 리스크패리티 30%)
            ensemble = tf_sig.value * 0.4 + mr_sig.value * 0.3 + rp_sig.value * 0.3
            strategies["ENSEMBLE"].append(round(ensemble, 4))
        except Exception:
            for key in strategies:
                strategies[key].append(0.0)

    return {name: pd.Series(values, index=dates) for name, values in strategies.items()}


def run_backtest_for_universe(
    ohlcv_data: dict[str, pd.DataFrame],
    country: Country,
    initial_capital: float = 50_000_000,
) -> dict[str, dict]:
    """
    유니버스 전체에 대해 전략별 백테스트 실행

    Returns:
        {strategy_name: {ticker: BacktestResult}}
    """
    all_results = {}

    # 1) 종목별 시그널 생성
    print("\n══ 시그널 생성 ══")
    ticker_signals = {}
    for ticker, ohlcv in ohlcv_data.items():
        print(f"  [{ticker}] 시그널 계산 중...")
        ticker_signals[ticker] = generate_strategy_signals(ticker, ohlcv)

    # 2) 전략별 백테스트
    strategy_names = ["MEAN_REVERSION", "TREND_FOLLOWING", "RISK_PARITY", "ENSEMBLE"]

    for strategy in strategy_names:
        print(f"\n══ {strategy} 백테스트 ══")

        # 종목별 시그널/가격 DataFrame 구성
        signals_df = pd.DataFrame({ticker: ticker_signals[ticker][strategy] for ticker in ohlcv_data})
        prices_df = pd.DataFrame({ticker: ohlcv_data[ticker]["close"] for ticker in ohlcv_data})

        # 인덱스 정렬
        common_idx = signals_df.index.intersection(prices_df.index)
        signals_df = signals_df.loc[common_idx]
        prices_df = prices_df.loc[common_idx]

        # NaN 처리
        signals_df = signals_df.fillna(0.0)
        prices_df = prices_df.ffill().bfill()

        if len(common_idx) < 60:
            print(f"  ⚠ 데이터 부족 ({len(common_idx)}일), skip")
            continue

        config = BacktestConfig(
            initial_capital=initial_capital,
            start_date=str(common_idx[0].date()),
            end_date=str(common_idx[-1].date()),
            country=country,
        )

        engine = BacktestEngine(config)
        result = engine.run(strategy, signals_df, prices_df)
        all_results[strategy] = result

        print(f"  수익률: {result.total_return:+.2%}")
        print(f"  CAGR:   {result.cagr:+.2%}")
        print(f"  MDD:    {result.mdd:.2%}")
        print(f"  Sharpe: {result.sharpe_ratio:.2f}")
        print(f"  거래:   {result.total_trades}건, 승률 {result.win_rate:.1%}")

    return all_results


def print_comparison_table(results: dict[str, object]):
    """전략 비교 테이블 출력"""
    print("\n" + "═" * 80)
    print("  전략 비교 요약")
    print("═" * 80)

    header = (
        f"{'전략':<20} {'수익률':>10} {'CAGR':>10} {'MDD':>10} {'Sharpe':>8} {'Sortino':>8} {'승률':>8} {'거래':>6}"
    )
    print(header)
    print("─" * 80)

    for name, r in results.items():
        row = (
            f"{name:<20} "
            f"{r.total_return:>+9.2%} "
            f"{r.cagr:>+9.2%} "
            f"{r.mdd:>9.2%} "
            f"{r.sharpe_ratio:>7.2f} "
            f"{r.sortino_ratio:>7.2f} "
            f"{r.win_rate:>7.1%} "
            f"{r.total_trades:>5d}"
        )
        print(row)

    print("─" * 80)

    # 최고 성과 전략
    if results:
        best_sharpe = max(results.items(), key=lambda x: x[1].sharpe_ratio)
        best_return = max(results.items(), key=lambda x: x[1].total_return)
        print(f"\n  📊 최고 Sharpe: {best_sharpe[0]} ({best_sharpe[1].sharpe_ratio:.2f})")
        print(f"  📈 최고 수익률: {best_return[0]} ({best_return[1].total_return:+.2%})")


def save_results_csv(results: dict[str, object], output_path: str):
    """결과를 CSV로 저장"""
    rows = []
    for name, r in results.items():
        rows.append(
            {
                "strategy": name,
                "total_return": round(r.total_return, 4),
                "cagr": round(r.cagr, 4),
                "mdd": round(r.mdd, 4),
                "sharpe_ratio": round(r.sharpe_ratio, 4),
                "sortino_ratio": round(r.sortino_ratio, 4),
                "calmar_ratio": round(r.calmar_ratio, 4),
                "win_rate": round(r.win_rate, 4),
                "profit_factor": round(r.profit_factor, 4),
                "total_trades": r.total_trades,
                "avg_trade_return": round(r.avg_trade_return, 4),
                "max_consecutive_losses": r.max_consecutive_losses,
                "initial_capital": r.initial_capital,
                "final_capital": round(r.final_capital, 2),
                "start_date": r.start_date,
                "end_date": r.end_date,
            }
        )

    df = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"\n✅ 결과 저장: {output_path}")


def build_db_url() -> str:
    """환경변수에서 DB URL 구성"""
    from urllib.parse import quote_plus

    host = os.environ.get("DB_HOST", "localhost")
    port = os.environ.get("DB_PORT", "5432")
    user = os.environ.get("DB_USER", "aqts")
    password = os.environ.get("DB_PASSWORD", "")
    name = os.environ.get("DB_NAME", "aqts")
    return f"postgresql+psycopg2://{user}:{quote_plus(password)}@{host}:{port}/{name}"


def main():
    parser = argparse.ArgumentParser(description="AQTS 백테스트 실행")
    parser.add_argument("--tickers", type=str, default=None, help="종목코드 콤마 구분 (기본: KOSPI TOP10)")
    parser.add_argument("--market", type=str, default="kr", choices=["kr", "us"], help="시장 (kr/us, 기본: kr)")
    parser.add_argument("--start", type=str, default="2024-01-02", help="시작일 (기본: 2024-01-02)")
    parser.add_argument("--end", type=str, default="2026-04-04", help="종료일 (기본: 2026-04-04)")
    parser.add_argument("--capital", type=float, default=50_000_000, help="초기 자본금 (기본: 50,000,000원)")
    parser.add_argument("--output", type=str, default=None, help="결과 CSV 저장 경로")
    parser.add_argument("--db-url", type=str, default=None, help="DB URL (미지정 시 환경변수에서 구성)")
    args = parser.parse_args()

    # 설정
    country = Country.US if args.market == "us" else Country.KR
    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",")]
    else:
        tickers = US_TOP10 if args.market == "us" else KOSPI_TOP10

    db_url = args.db_url or build_db_url()

    print("═" * 60)
    print("  AQTS 백테스트")
    print("═" * 60)
    print(f"  시장:     {args.market.upper()}")
    print(f"  종목:     {len(tickers)}개 ({', '.join(tickers[:5])}{'...' if len(tickers) > 5 else ''})")
    print(f"  기간:     {args.start} ~ {args.end}")
    print(f"  초기자본: {args.capital:,.0f}원")
    print(f"  거래비용: {TRANSACTION_COSTS[country]}")
    print()

    # 1) 데이터 로드
    print("══ OHLCV 데이터 로드 ══")
    ohlcv_data = load_ohlcv_from_db(tickers, args.start, args.end, db_url)

    if not ohlcv_data:
        print("\n❌ 로드된 데이터가 없습니다. DB 연결 및 데이터 적재를 확인하세요.")
        sys.exit(1)

    print(f"\n  총 {len(ohlcv_data)}개 종목 로드 완료")

    # 2) 백테스트 실행
    results = run_backtest_for_universe(ohlcv_data, country, args.capital)

    if not results:
        print("\n❌ 백테스트 결과가 없습니다.")
        sys.exit(1)

    # 3) 결과 비교
    print_comparison_table(results)

    # 4) CSV 저장
    if args.output:
        save_results_csv(results, args.output)

    # 5) StrategyComparator 추천
    if len(results) >= 2:
        comparator = StrategyComparator()
        result_list = list(results.values())
        weights = comparator.recommend_weights(result_list, method="sharpe")
        print("\n══ Sharpe 기반 전략 가중치 추천 ══")
        for name, w in sorted(weights.items(), key=lambda x: -x[1]):
            bar = "█" * int(w * 40)
            print(f"  {name:<20} {w:>6.1%}  {bar}")

    print("\n✅ 백테스트 완료")
    return results


if __name__ == "__main__":
    main()
