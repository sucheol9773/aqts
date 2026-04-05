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
from dotenv import load_dotenv

# 프로젝트 루트의 .env 파일 로드
_project_root = os.path.join(os.path.dirname(__file__), "..")
load_dotenv(os.path.join(_project_root, ".env"))

# backend 경로 추가
sys.path.insert(0, os.path.join(_project_root, "backend"))

os.environ.setdefault("KIS_TRADING_MODE", "BACKTEST")
os.environ.setdefault("TESTING", "1")

from config.constants import TRANSACTION_COSTS, Country
from core.backtest_engine.engine import (
    BacktestConfig,
    BacktestEngine,
    StrategyComparator,
)

# ══════════════════════════════════════
# 기본 유니버스
# ══════════════════════════════════════
KOSPI_TOP10 = [
    "005930",
    "000660",
    "035420",
    "005380",
    "006400",
    "051910",
    "003670",
    "105560",
    "055550",
    "000270",
]

US_TOP10 = [
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "NVDA",
    "META",
    "TSLA",
    "TSM",
    "AVGO",
    "AMD",
]


def load_all_tickers_from_db(db_url: str, market: str = "kr") -> list[str]:
    """DB에서 시장별 전체 종목코드 조회"""
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)
    with engine.connect() as conn:
        if market == "kr":
            # 숫자로 된 종목코드 = 한국 시장
            query = text("SELECT DISTINCT ticker FROM market_ohlcv WHERE ticker ~ '^[0-9]+$' ORDER BY ticker")
        else:
            # 알파벳으로 된 종목코드 = 미국 시장
            query = text("SELECT DISTINCT ticker FROM market_ohlcv WHERE ticker ~ '^[A-Z]+$' ORDER BY ticker")
        rows = conn.execute(query).fetchall()
    return [row[0] for row in rows]


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
            df = pd.read_sql(
                query,
                conn,
                params={"ticker": ticker, "start": start_date, "end": end_date},
            )

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


def generate_strategy_signals_vectorized(ticker: str, ohlcv: pd.DataFrame) -> dict[str, pd.Series]:
    """
    종목의 OHLCV로 전략별 일별 시그널 시계열 생성 (벡터화 버전)

    기존 날짜별 for-loop 대신 pandas 벡터 연산으로 전체 기간을 한번에 계산.
    동일한 시그널 로직이지만 10~50배 빠름.

    Returns:
        {strategy_name: pd.Series(index=날짜, values=시그널값)}
    """
    import numpy as np

    from core.quant_engine.signal_generator import TechnicalIndicators

    ti = TechnicalIndicators()
    close = ohlcv["close"].astype(float)
    dates = ohlcv.index
    min_window = 60

    # ── MEAN_REVERSION: RSI + 볼린저밴드 ──
    rsi = ti.rsi(close, period=14)
    bb_upper, bb_middle, bb_lower = ti.bollinger_bands(close, period=20, num_std=2.0)

    # RSI 시그널
    rsi_signal = pd.Series(0.0, index=dates)
    rsi_signal = rsi_signal.where(~((rsi < 30)), (30 - rsi) / 30.0)
    rsi_signal = rsi_signal.where(~((rsi > 70)), -(rsi - 70) / 30.0)

    # 볼린저 시그널
    bb_range = bb_upper - bb_lower
    bb_position = (close - bb_middle) / (bb_range / 2)
    bb_signal = -bb_position.clip(-1.0, 1.0)
    bb_signal = bb_signal.where(bb_range > 0, 0.0)

    mr_signal = ((rsi_signal + bb_signal) / 2.0).clip(-1.0, 1.0)

    # ── TREND_FOLLOWING: MA 크로스 + MACD ──
    ma5 = ti.sma(close, 5)
    ma20 = ti.sma(close, 20)
    ma60 = ti.sma(close, 60)

    macd_line, signal_line, histogram = ti.macd(close)
    prev_hist = histogram.shift(1)

    # MA 시그널
    ma_signal = pd.Series(0.0, index=dates)
    # 정배열
    bull_mask = (ma5 > ma20) & (ma20 > ma60)
    spread_bull = ((ma5 - ma60) / ma60 * 10.0).clip(0.0, 1.0)
    ma_signal = ma_signal.where(~bull_mask, spread_bull)
    # 역배열
    bear_mask = (ma5 < ma20) & (ma20 < ma60)
    spread_bear = -((ma60 - ma5) / ma60 * 10.0).clip(0.0, 1.0)
    ma_signal = ma_signal.where(~bear_mask, spread_bear)
    # 혼합
    mixed_bull = (~bull_mask) & (~bear_mask) & (ma5 > ma20)
    mixed_bear = (~bull_mask) & (~bear_mask) & (ma5 < ma20)
    ma_signal = ma_signal.where(~mixed_bull, 0.3)
    ma_signal = ma_signal.where(~mixed_bear, -0.3)

    # MACD 시그널
    macd_signal = pd.Series(0.0, index=dates)
    macd_bull = (histogram > 0) & (histogram > prev_hist)
    macd_bear = (histogram < 0) & (histogram < prev_hist)
    macd_signal = macd_signal.where(~macd_bull, 0.3)
    macd_signal = macd_signal.where(~macd_bear, -0.3)

    tf_signal = (ma_signal + macd_signal).clip(-1.0, 1.0)

    # ── RISK_PARITY: 변동성 추세 + 절대 수준 ──
    returns = close.pct_change()
    vol_20d = returns.rolling(20).std() * np.sqrt(252)
    vol_60d = returns.rolling(60).std() * np.sqrt(252)

    vol_trend = ((vol_60d - vol_20d) / vol_60d).fillna(0.0)
    vol_median = 0.30
    vol_level = ((vol_median - vol_60d) / vol_median).fillna(0.0)

    rp_signal = (vol_trend * 0.6 + vol_level * 0.4).clip(-1.0, 1.0)

    # ── ENSEMBLE: 동적 레짐 기반 가중치 ──
    ensemble_signal = _compute_dynamic_ensemble(ohlcv, mr_signal, tf_signal, rp_signal, min_window)

    # 최소 윈도우 이전은 0으로
    for sig in [mr_signal, tf_signal, rp_signal, ensemble_signal]:
        sig.iloc[:min_window] = 0.0

    # NaN → 0
    mr_signal = mr_signal.fillna(0.0).round(4)
    tf_signal = tf_signal.fillna(0.0).round(4)
    rp_signal = rp_signal.fillna(0.0).round(4)
    ensemble_signal = ensemble_signal.fillna(0.0).round(4)

    return {
        "MEAN_REVERSION": mr_signal,
        "TREND_FOLLOWING": tf_signal,
        "RISK_PARITY": rp_signal,
        "ENSEMBLE": ensemble_signal,
    }


def _compute_dynamic_ensemble(
    ohlcv: pd.DataFrame,
    mr_signal: pd.Series,
    tf_signal: pd.Series,
    rp_signal: pd.Series,
    min_window: int = 60,
) -> pd.Series:
    """
    레짐 기반 동적 앙상블 가중치 계산 (벡터화)

    매일 최근 데이터로 시장 레짐(추세/횡보/고변동)을 판단하고,
    레짐에 따라 전략 가중치를 자동 조절합니다.

    레짐 판정 기준:
        - ADX > 25 + 양(+) 모멘텀 → TRENDING_UP   (추세추종 강화)
        - ADX > 25 + 음(-) 모멘텀 → TRENDING_DOWN  (리스크패리티 강화)
        - vol_pct > 0.75 + ADX < 25 → HIGH_VOL     (리스크패리티 대폭 강화)
        - 그 외 → SIDEWAYS                          (평균회귀 강화)

    Returns:
        pd.Series: 동적 가중 앙상블 시그널
    """
    import numpy as np

    close = ohlcv["close"].astype(float)
    high = ohlcv["high"].astype(float)
    low = ohlcv["low"].astype(float)
    dates = ohlcv.index

    # ── 1) 롤링 ADX 계산 (벡터화) ──
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    plus_dm = high - prev_high
    minus_dm = prev_low - low
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    alpha = 1.0 / 14
    atr = tr.ewm(alpha=alpha, min_periods=14, adjust=False).mean()
    safe_atr = atr.replace(0, np.nan)
    plus_di = 100.0 * plus_dm.ewm(alpha=alpha, min_periods=14, adjust=False).mean() / safe_atr
    minus_di = 100.0 * minus_dm.ewm(alpha=alpha, min_periods=14, adjust=False).mean() / safe_atr

    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    adx = dx.ewm(alpha=alpha, min_periods=14, adjust=False).mean().fillna(0.0)

    # ── 2) 롤링 변동성 백분위 ──
    returns = close.pct_change()
    rolling_vol = returns.rolling(20).std() * np.sqrt(252)
    # 확장 윈도우 백분위: 현재 vol이 과거 대비 몇 % 위치인지
    vol_percentile = rolling_vol.expanding(min_periods=60).rank(pct=True).fillna(0.5)

    # ── 3) 모멘텀 (20일 수익률) ──
    momentum = close.pct_change(20).fillna(0.0)

    # ── 4) 레짐별 가중치 매핑 (벡터화) ──
    # 기본 가중치: TF=0.4, MR=0.3, RP=0.3
    w_tf = pd.Series(0.40, index=dates)
    w_mr = pd.Series(0.30, index=dates)
    w_rp = pd.Series(0.30, index=dates)

    # TRENDING_UP: ADX > 25, 모멘텀 > 0
    trend_up = (adx > 25) & (momentum > 0)
    w_tf = w_tf.where(~trend_up, 0.55)  # 추세추종 강화
    w_mr = w_mr.where(~trend_up, 0.15)  # 평균회귀 약화
    w_rp = w_rp.where(~trend_up, 0.30)

    # TRENDING_DOWN: ADX > 25, 모멘텀 < 0
    trend_down = (adx > 25) & (momentum < 0) & (~trend_up)
    w_tf = w_tf.where(~trend_down, 0.40)
    w_mr = w_mr.where(~trend_down, 0.15)
    w_rp = w_rp.where(~trend_down, 0.45)  # 리스크 관리 강화

    # HIGH_VOLATILITY: vol_pct > 0.75, ADX < 25
    high_vol = (vol_percentile > 0.75) & (adx <= 25) & (~trend_up) & (~trend_down)
    w_tf = w_tf.where(~high_vol, 0.20)  # 추세추종 약화
    w_mr = w_mr.where(~high_vol, 0.20)
    w_rp = w_rp.where(~high_vol, 0.60)  # 리스크패리티 대폭 강화

    # SIDEWAYS: 나머지 (기본 가중치 유지하되 평균회귀 약간 강화)
    sideways = (~trend_up) & (~trend_down) & (~high_vol)
    w_tf = w_tf.where(~sideways, 0.25)
    w_mr = w_mr.where(~sideways, 0.45)  # 횡보장 = 평균회귀
    w_rp = w_rp.where(~sideways, 0.30)

    # ── 5) 롤링 성과 기반 미세조정 ──
    # 최근 60일 각 전략 시그널의 누적 수익률로 가중치 보정
    # 잘 나가는 전략 가중치를 최대 ±20% 보정
    perf_window = 60
    mr_perf = (mr_signal * returns).rolling(perf_window).sum().fillna(0.0)
    tf_perf = (tf_signal * returns).rolling(perf_window).sum().fillna(0.0)
    rp_perf = (rp_signal * returns).rolling(perf_window).sum().fillna(0.0)

    # softmax 스타일 보정 계수 (온도 파라미터로 과도한 쏠림 방지)
    temperature = 5.0
    exp_mr = np.exp(mr_perf / temperature)
    exp_tf = np.exp(tf_perf / temperature)
    exp_rp = np.exp(rp_perf / temperature)
    exp_sum = exp_mr + exp_tf + exp_rp

    # 보정 비율 (1/3 기준 대비 얼마나 벗어나는지)
    perf_adj_mr = exp_mr / exp_sum
    perf_adj_tf = exp_tf / exp_sum
    perf_adj_rp = exp_rp / exp_sum

    # 레짐 가중치에 성과 보정 블렌딩 (70% 레짐 + 30% 성과)
    blend = 0.3
    w_mr = w_mr * (1 - blend) + perf_adj_mr * blend
    w_tf = w_tf * (1 - blend) + perf_adj_tf * blend
    w_rp = w_rp * (1 - blend) + perf_adj_rp * blend

    # 재정규화 (합 = 1)
    w_total = w_mr + w_tf + w_rp
    w_mr = w_mr / w_total
    w_tf = w_tf / w_total
    w_rp = w_rp / w_total

    # ── 6) 동적 가중 합산 ──
    ensemble = w_tf * tf_signal + w_mr * mr_signal + w_rp * rp_signal

    # ── 7) 변동성 타겟팅: 고변동 시 시그널 축소 ──
    # 연환산 변동성이 target_vol을 넘으면 시그널을 비례 축소
    # 레버리지는 사용하지 않음 (scalar ≤ 1.0)
    target_vol = 0.15  # 연 15% 목표 변동성
    current_vol = returns.rolling(20).std() * np.sqrt(252)
    current_vol = current_vol.fillna(target_vol)  # 초기 구간은 목표값 사용
    vol_scalar = (target_vol / current_vol.replace(0, target_vol)).clip(upper=1.0)
    ensemble = ensemble * vol_scalar

    return ensemble


def _generate_signals_worker(args: tuple) -> tuple[str, dict[str, pd.Series]]:
    """multiprocessing용 워커 함수"""
    ticker, ohlcv = args
    signals = generate_strategy_signals_vectorized(ticker, ohlcv)
    return ticker, signals


def generate_strategy_signals(ticker: str, ohlcv: pd.DataFrame) -> dict[str, pd.Series]:
    """하위 호환용 래퍼 — 벡터화 버전 호출"""
    return generate_strategy_signals_vectorized(ticker, ohlcv)


# ══════════════════════════════════════
# 전략별 리스크 프리셋
# ══════════════════════════════════════
# 각 전략의 특성에 맞는 기본 리스크 파라미터
# CLI에서 --risk-preset custom 으로 커스텀 값을 사용할 수 있음
STRATEGY_RISK_PRESETS: dict[str, dict] = {
    "MEAN_REVERSION": {
        # 평균회귀는 역추세 전략이므로 손절을 느슨하게 (단기 역행 허용)
        "stop_loss_pct": None,
        "stop_loss_atr_multiplier": None,
        "max_drawdown_limit": 0.25,
        "drawdown_cooldown_days": 10,
        "dd_cushion_start": 0.10,  # -10%부터 포지션 축소 시작
    },
    "TREND_FOLLOWING": {
        # 추세추종은 ATR 기반 트레일링 손절이 적합
        "stop_loss_pct": None,
        "stop_loss_atr_multiplier": 2.0,
        "max_drawdown_limit": 0.20,
        "drawdown_cooldown_days": 20,
        "dd_cushion_start": 0.08,  # -8%부터 포지션 축소 시작
    },
    "RISK_PARITY": {
        # 리스크패리티는 변동성 기반이므로 넓은 ATR 배수
        "stop_loss_pct": None,
        "stop_loss_atr_multiplier": 2.5,
        "max_drawdown_limit": 0.20,
        "drawdown_cooldown_days": 15,
        "dd_cushion_start": 0.08,  # -8%부터 포지션 축소 시작
    },
    "ENSEMBLE": {
        # 앙상블은 중간 수준
        "stop_loss_pct": None,
        "stop_loss_atr_multiplier": 2.0,
        "max_drawdown_limit": 0.20,
        "drawdown_cooldown_days": 20,
        "dd_cushion_start": 0.08,  # -8%부터 포지션 축소 시작
    },
}


def run_backtest_for_universe(
    ohlcv_data: dict[str, pd.DataFrame],
    country: Country,
    initial_capital: float = 50_000_000,
    stop_loss_pct: float | None = None,
    stop_loss_atr_multiplier: float | None = None,
    max_drawdown_limit: float | None = None,
    drawdown_cooldown_days: int = 20,
    risk_preset: str = "strategy",
) -> dict[str, dict]:
    """
    유니버스 전체에 대해 전략별 백테스트 실행

    Args:
        risk_preset:
            "strategy" — 전략별 프리셋 적용 (STRATEGY_RISK_PRESETS)
            "custom"   — CLI에서 전달된 값을 전 전략에 동일 적용
            "none"     — 리스크 보호 없음

    Returns:
        {strategy_name: {ticker: BacktestResult}}
    """
    all_results = {}

    # 1) 종목별 시그널 생성 (병렬 + 벡터화)
    print("\n══ 시그널 생성 ══")
    ticker_signals = {}
    n_tickers = len(ohlcv_data)

    if n_tickers >= 4:
        # 4종목 이상이면 multiprocessing 병렬 처리
        import multiprocessing as mp

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

        # 전략별 리스크 파라미터 결정
        if risk_preset == "strategy":
            preset = STRATEGY_RISK_PRESETS.get(strategy, {})
            s_stop_loss = preset.get("stop_loss_pct")
            s_atr_mult = preset.get("stop_loss_atr_multiplier")
            s_max_dd = preset.get("max_drawdown_limit")
            s_cooldown = preset.get("drawdown_cooldown_days", 20)
            print(f"  리스크: stop_loss={s_stop_loss}, " f"ATR×{s_atr_mult}, DD한도={s_max_dd}, 쿨다운={s_cooldown}일")
        elif risk_preset == "none":
            s_stop_loss = None
            s_atr_mult = None
            s_max_dd = None
            s_cooldown = 20
        else:  # custom
            s_stop_loss = stop_loss_pct
            s_atr_mult = stop_loss_atr_multiplier
            s_max_dd = max_drawdown_limit
            s_cooldown = drawdown_cooldown_days

        config = BacktestConfig(
            initial_capital=initial_capital,
            start_date=str(common_idx[0].date()),
            end_date=str(common_idx[-1].date()),
            country=country,
            stop_loss_pct=s_stop_loss,
            stop_loss_atr_multiplier=s_atr_mult,
            max_drawdown_limit=s_max_dd,
            drawdown_cooldown_days=s_cooldown,
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
    """환경변수에서 DB URL 구성 (.env의 TIMESCALE_* 변수 사용)"""
    from urllib.parse import quote_plus

    host = os.environ.get("TIMESCALE_HOST", os.environ.get("DB_HOST", "localhost"))
    port = os.environ.get("TIMESCALE_PORT", os.environ.get("DB_PORT", "5432"))
    user = os.environ.get("TIMESCALE_USER", os.environ.get("DB_USER", "aqts"))
    password = os.environ.get("TIMESCALE_PASSWORD", os.environ.get("DB_PASSWORD", ""))
    name = os.environ.get("TIMESCALE_DB", os.environ.get("DB_NAME", "aqts"))

    # Docker 내부 호스트명 → 호스트 머신에서는 localhost로 변환
    if host in ("postgres", "timescaledb", "timescale"):
        host = "localhost"

    return f"postgresql+psycopg2://{user}:{quote_plus(password)}@{host}:{port}/{name}"


def main():
    parser = argparse.ArgumentParser(description="AQTS 백테스트 실행")
    parser.add_argument("--tickers", type=str, default=None, help="종목코드 콤마 구분 (기본: TOP10)")
    parser.add_argument(
        "--market",
        type=str,
        default="kr",
        choices=["kr", "us"],
        help="시장 (kr/us, 기본: kr)",
    )
    parser.add_argument("--all", action="store_true", help="DB의 해당 시장 전체 종목으로 백테스트")
    parser.add_argument("--start", type=str, default="2000-01-02", help="시작일 (기본: 2000-01-02)")
    parser.add_argument("--end", type=str, default="2026-04-04", help="종료일 (기본: 2026-04-04)")
    parser.add_argument(
        "--capital",
        type=float,
        default=50_000_000,
        help="초기 자본금 (기본: 50,000,000원)",
    )
    parser.add_argument("--output", type=str, default=None, help="결과 CSV 저장 경로")
    parser.add_argument("--db-url", type=str, default=None, help="DB URL (미지정 시 환경변수에서 구성)")
    parser.add_argument(
        "--stop-loss",
        type=float,
        default=None,
        help="종목별 고정 손절 비율 (예: 0.15 = -15%%)",
    )
    parser.add_argument(
        "--stop-loss-atr",
        type=float,
        default=None,
        help="ATR 기반 동적 손절 배수 (예: 2.0 = 2×ATR)",
    )
    parser.add_argument(
        "--max-dd",
        type=float,
        default=None,
        help="포트폴리오 DD 한도 (예: 0.20 = -20%%)",
    )
    parser.add_argument(
        "--cooldown",
        type=int,
        default=20,
        help="DD 발동 후 거래 재개 대기 영업일 (기본: 20)",
    )
    parser.add_argument(
        "--risk-preset",
        type=str,
        default="strategy",
        choices=["strategy", "custom", "none"],
        help="리스크 프리셋: strategy(전략별 차등), custom(CLI값 전 전략 적용), none(보호 없음)",
    )
    args = parser.parse_args()

    db_url = args.db_url or build_db_url()

    # 설정
    country = Country.US if args.market == "us" else Country.KR
    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",")]
    elif getattr(args, "all"):
        tickers = load_all_tickers_from_db(db_url, args.market)
    else:
        tickers = US_TOP10 if args.market == "us" else KOSPI_TOP10

    print("═" * 60)
    print("  AQTS 백테스트")
    print("═" * 60)
    print(f"  시장:     {args.market.upper()}")
    print(f"  종목:     {len(tickers)}개 ({', '.join(tickers[:5])}{'...' if len(tickers) > 5 else ''})")
    print(f"  기간:     {args.start} ~ {args.end}")
    print(f"  초기자본: {args.capital:,.0f}원")
    print(f"  거래비용: {TRANSACTION_COSTS[country]}")
    print(f"  리스크:   {args.risk_preset} 모드")
    if args.risk_preset == "custom":
        if args.stop_loss:
            print(f"  손절기준: 종목별 고정 -{args.stop_loss:.0%}")
        if args.stop_loss_atr:
            print(f"  손절기준: ATR×{args.stop_loss_atr:.1f} (동적)")
        if args.max_dd:
            print(f"  DD한도:   포트폴리오 -{args.max_dd:.0%} (쿨다운 {args.cooldown}일)")
    elif args.risk_preset == "strategy":
        print("            (전략별 차등 파라미터 — 각 전략 실행 시 표시)")
    print()

    # 1) 데이터 로드
    print("══ OHLCV 데이터 로드 ══")
    ohlcv_data = load_ohlcv_from_db(tickers, args.start, args.end, db_url)

    if not ohlcv_data:
        print("\n❌ 로드된 데이터가 없습니다. DB 연결 및 데이터 적재를 확인하세요.")
        sys.exit(1)

    print(f"\n  총 {len(ohlcv_data)}개 종목 로드 완료")

    # 2) 백테스트 실행
    results = run_backtest_for_universe(
        ohlcv_data,
        country,
        args.capital,
        stop_loss_pct=args.stop_loss,
        stop_loss_atr_multiplier=args.stop_loss_atr,
        max_drawdown_limit=args.max_dd,
        drawdown_cooldown_days=args.cooldown,
        risk_preset=args.risk_preset,
    )

    if not results:
        print("\n❌ 백테스트 결과가 없습니다.")
        sys.exit(1)

    # 3) 결과 비교
    print_comparison_table(results)

    # 4) CSV 저장 (항상 저장, --output으로 경로 변경 가능)
    if args.output:
        output_path = args.output
    else:
        from datetime import datetime

        os.makedirs(os.path.join(_project_root, "results", "backtest"), exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(_project_root, "results", "backtest", f"backtest_{timestamp}.csv")
    save_results_csv(results, output_path)

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
