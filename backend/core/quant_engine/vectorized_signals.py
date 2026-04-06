"""
벡터화 시그널 생성기 (Vectorized Signal Generator)

run_backtest.py의 generate_strategy_signals_vectorized()를
라이브 파이프라인에서도 사용할 수 있도록 모듈화.

전체 OHLCV 기간에 대해 MR/TF/RP 시그널 시계열을
pandas 벡터 연산으로 한 번에 생성합니다 (날짜별 for-loop 대비 10~50배 빠름).

사용법:
    generator = VectorizedSignalGenerator()
    signals = generator.generate(ohlcv_df)
    # signals == {"MEAN_REVERSION": pd.Series, "TREND_FOLLOWING": pd.Series, "RISK_PARITY": pd.Series}

TF v2 개선 (2026-04-06):
- MACD 제거 → 모멘텀(ROC 20일) + ADX(14) 기반 추세 감지
- 거래량 확인: 20일 평균 이하 거래량 시 시그널 감쇄
- 적응적 MA 기간: ATR 기반 변동성에 따라 MA 기간 조정
- 시그널 강도 개선: 초기 추세(5>20, 20 방향 일치)에서 0.3→0.5
"""

import numpy as np
import pandas as pd

from core.quant_engine.signal_generator import TechnicalIndicators


class VectorizedSignalGenerator:
    """
    벡터화 전략 시그널 생성기

    OHLCV DataFrame을 입력받아 3개 전략(MR, TF, RP)의
    일별 시그널 시계열을 벡터 연산으로 생성합니다.

    run_backtest.py의 generate_strategy_signals_vectorized()와
    동일한 알고리즘이지만, 모듈화하여 재사용 가능하게 함.
    """

    def __init__(self, min_window: int = 60):
        self._ti = TechnicalIndicators()
        self._min_window = min_window

    def generate(self, ohlcv: pd.DataFrame) -> dict[str, pd.Series]:
        """
        전략별 시그널 시계열 생성 (벡터화)

        Args:
            ohlcv: OHLCV DataFrame (columns: open, high, low, close, volume)
                   index는 DatetimeIndex

        Returns:
            {"MEAN_REVERSION": pd.Series, "TREND_FOLLOWING": pd.Series, "RISK_PARITY": pd.Series}
        """
        close = ohlcv["close"].astype(float)
        high = ohlcv["high"].astype(float) if "high" in ohlcv.columns else close
        low = ohlcv["low"].astype(float) if "low" in ohlcv.columns else close
        volume = ohlcv["volume"].astype(float) if "volume" in ohlcv.columns else None
        dates = ohlcv.index

        mr_signal = self._generate_mean_reversion(close, dates)
        tf_signal = self._generate_trend_following(close, high, low, volume, dates)
        rp_signal = self._generate_risk_parity(close, dates)

        # 최소 윈도우 이전은 0으로
        for sig in [mr_signal, tf_signal, rp_signal]:
            sig.iloc[: self._min_window] = 0.0

        # NaN → 0, 반올림
        mr_signal = mr_signal.fillna(0.0).round(4)
        tf_signal = tf_signal.fillna(0.0).round(4)
        rp_signal = rp_signal.fillna(0.0).round(4)

        return {
            "MEAN_REVERSION": mr_signal,
            "TREND_FOLLOWING": tf_signal,
            "RISK_PARITY": rp_signal,
        }

    def _generate_mean_reversion(self, close: pd.Series, dates: pd.DatetimeIndex) -> pd.Series:
        """평균회귀 시그널: RSI + 볼린저밴드"""
        rsi = self._ti.rsi(close, period=14)
        bb_upper, bb_middle, bb_lower = self._ti.bollinger_bands(close, period=20, num_std=2.0)

        # RSI 시그널
        rsi_signal = pd.Series(0.0, index=dates)
        rsi_signal = rsi_signal.where(~(rsi < 30), (30 - rsi) / 30.0)
        rsi_signal = rsi_signal.where(~(rsi > 70), -(rsi - 70) / 30.0)

        # 볼린저 시그널
        bb_range = bb_upper - bb_lower
        bb_position = (close - bb_middle) / (bb_range / 2)
        bb_signal = -bb_position.clip(-1.0, 1.0)
        bb_signal = bb_signal.where(bb_range > 0, 0.0)

        return ((rsi_signal + bb_signal) / 2.0).clip(-1.0, 1.0)

    def _generate_trend_following(
        self,
        close: pd.Series,
        high: pd.Series,
        low: pd.Series,
        volume: pd.Series | None,
        dates: pd.DatetimeIndex,
    ) -> pd.Series:
        """
        추세추종 시그널 v2: MA 크로스 + 모멘텀(ROC) + ADX + 거래량 확인

        v1 대비 개선:
        - MACD(3중 스무딩, 늦은 진입) → ROC(즉각 모멘텀) + ADX(추세 강도)
        - 거래량 필터: 20일 평균 미만 거래량 시 시그널 50% 감쇄
        - 적응적 MA: ATR 기반 변동성 높으면 장기 MA, 낮으면 단기 MA
        - 초기 추세(5>20, 20 방향=60 방향) 시그널 0.3→0.5 강화
        """
        # ── 적응적 MA 기간 (ATR 기반) ──
        atr_14 = self._ti.atr(high, low, close, period=14)
        atr_pct = (atr_14 / close).fillna(0.0)
        atr_median = atr_pct.rolling(60, min_periods=20).median().fillna(atr_pct.median())

        # 변동성 비율: > 1이면 고변동성, < 1이면 저변동성
        vol_ratio = (atr_pct / atr_median.replace(0, np.nan)).fillna(1.0).clip(0.5, 2.0)

        # MA 기간 조정: 고변동성 → 장기(노이즈 필터), 저변동성 → 단기(빠른 진입)
        # 기본 5/20/60 기준, vol_ratio로 스케일
        fast_period = (5 * vol_ratio).clip(3, 10).round().astype(int)
        mid_period = (20 * vol_ratio).clip(10, 40).round().astype(int)
        slow_period = (60 * vol_ratio).clip(30, 120).round().astype(int)

        # 적응적 MA 계산 (대표 3구간: 저/중/고 변동성)
        ma_fast = self._adaptive_sma(close, fast_period)
        ma_mid = self._adaptive_sma(close, mid_period)
        ma_slow = self._adaptive_sma(close, slow_period)

        # ── ADX: 추세 강도 ──
        adx = self._ti.adx(high, low, close, period=14)

        # ── 모멘텀 (ROC 20일) ──
        roc_20 = close.pct_change(20).fillna(0.0)

        # ── MA 시그널 (3중 정렬 + 초기 추세) ──
        ma_signal = pd.Series(0.0, index=dates)

        # 완전 정렬: fast > mid > slow (강한 상승 추세)
        bull_aligned = (ma_fast > ma_mid) & (ma_mid > ma_slow)
        spread_bull = ((ma_fast - ma_slow) / ma_slow.replace(0, np.nan) * 10.0).clip(0.0, 1.0)
        ma_signal = ma_signal.where(~bull_aligned, spread_bull)

        # 완전 정렬: fast < mid < slow (강한 하락 추세)
        bear_aligned = (ma_fast < ma_mid) & (ma_mid < ma_slow)
        spread_bear = -((ma_slow - ma_fast) / ma_slow.replace(0, np.nan) * 10.0).clip(-1.0, 0.0)
        ma_signal = ma_signal.where(~bear_aligned, spread_bear)

        # 초기 추세: fast>mid이고 mid가 slow 방향으로 이동 중 (v1: 0.3 → v2: 0.5)
        mid_rising = ma_mid > ma_mid.shift(5)
        mid_falling = ma_mid < ma_mid.shift(5)
        early_bull = (~bull_aligned) & (~bear_aligned) & (ma_fast > ma_mid) & mid_rising
        early_bear = (~bull_aligned) & (~bear_aligned) & (ma_fast < ma_mid) & mid_falling
        ma_signal = ma_signal.where(~early_bull, 0.5)
        ma_signal = ma_signal.where(~early_bear, -0.5)

        # 약한 혼합 상태 (방향 불일치)
        weak_bull = (~bull_aligned) & (~bear_aligned) & (~early_bull) & (~early_bear) & (ma_fast > ma_mid)
        weak_bear = (~bull_aligned) & (~bear_aligned) & (~early_bull) & (~early_bear) & (ma_fast < ma_mid)
        ma_signal = ma_signal.where(~weak_bull, 0.15)
        ma_signal = ma_signal.where(~weak_bear, -0.15)

        # ── 모멘텀 시그널: ADX + ROC ──
        momentum_signal = pd.Series(0.0, index=dates)

        # ADX > 20 = 추세 존재, ROC 방향으로 시그널
        trending = adx > 20
        strong_trend = adx > 30

        # ROC 정규화: ATR 대비 모멘텀 크기
        roc_normalized = (roc_20 / atr_pct.replace(0, np.nan)).fillna(0.0).clip(-3.0, 3.0) / 3.0

        # 추세 + 모멘텀 일치 시 시그널
        mom_bull = trending & (roc_20 > 0)
        mom_bear = trending & (roc_20 < 0)
        momentum_signal = momentum_signal.where(~mom_bull, roc_normalized.clip(0.0, 1.0) * 0.4)
        momentum_signal = momentum_signal.where(~mom_bear, roc_normalized.clip(-1.0, 0.0) * 0.4)

        # 강한 추세(ADX>30) 시 부스트
        strong_bull = strong_trend & (roc_20 > 0)
        strong_bear = strong_trend & (roc_20 < 0)
        momentum_signal = momentum_signal.where(~strong_bull, roc_normalized.clip(0.0, 1.0) * 0.5)
        momentum_signal = momentum_signal.where(~strong_bear, roc_normalized.clip(-1.0, 0.0) * 0.5)

        # ── 시그널 결합: MA 60% + 모멘텀 40% ──
        combined = (ma_signal * 0.6 + momentum_signal * 0.4).clip(-1.0, 1.0)

        # ── 거래량 필터 ──
        if volume is not None:
            vol_ma20 = volume.rolling(20, min_periods=10).mean()
            vol_ratio_filter = (volume / vol_ma20.replace(0, np.nan)).fillna(1.0)
            # 거래량 < 평균 50%: 시그널 50% 감쇄 / 거래량 > 평균 150%: 시그널 20% 부스트
            vol_multiplier = vol_ratio_filter.clip(0.5, 1.5)
            vol_multiplier = vol_multiplier.where(vol_multiplier >= 1.0, 0.5 + vol_multiplier)
            combined = (combined * vol_multiplier).clip(-1.0, 1.0)

        return combined

    def _adaptive_sma(self, series: pd.Series, periods: pd.Series) -> pd.Series:
        """
        적응적 SMA: 각 시점마다 다른 윈도우 길이로 이동평균 계산.

        성능을 위해 대표 3구간(저/중/고 변동성)으로 근사.
        """
        # 대표 기간 3개로 근사 (정확한 per-row rolling은 너무 느림)
        unique_periods = periods.unique()
        if len(unique_periods) <= 1:
            p = int(unique_periods[0]) if len(unique_periods) == 1 else 20
            return self._ti.sma(series, max(p, 2))

        p_low = int(np.percentile(unique_periods, 25))
        p_mid = int(np.percentile(unique_periods, 50))
        p_high = int(np.percentile(unique_periods, 75))

        ma_low = self._ti.sma(series, max(p_low, 2))
        ma_mid = self._ti.sma(series, max(p_mid, 2))
        ma_high = self._ti.sma(series, max(p_high, 2))

        # 각 시점의 기간이 어느 구간에 속하는지에 따라 선택
        result = ma_mid.copy()
        result = result.where(~(periods <= p_low), ma_low)
        result = result.where(~(periods >= p_high), ma_high)
        return result

    def _generate_risk_parity(self, close: pd.Series, dates: pd.DatetimeIndex) -> pd.Series:
        """리스크 패리티 시그널: 변동성 추세 + 절대 수준"""
        returns = close.pct_change()
        vol_20d = returns.rolling(20).std() * np.sqrt(252)
        vol_60d = returns.rolling(60).std() * np.sqrt(252)

        vol_trend = ((vol_60d - vol_20d) / vol_60d).fillna(0.0)
        vol_median = 0.30
        vol_level = ((vol_median - vol_60d) / vol_median).fillna(0.0)

        return (vol_trend * 0.6 + vol_level * 0.4).clip(-1.0, 1.0)
