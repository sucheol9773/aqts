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
        dates = ohlcv.index

        mr_signal = self._generate_mean_reversion(close, dates)
        tf_signal = self._generate_trend_following(close, dates)
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

    def _generate_trend_following(self, close: pd.Series, dates: pd.DatetimeIndex) -> pd.Series:
        """추세추종 시그널: MA 크로스 + MACD"""
        ma5 = self._ti.sma(close, 5)
        ma20 = self._ti.sma(close, 20)
        ma60 = self._ti.sma(close, 60)

        macd_line, signal_line, histogram = self._ti.macd(close)
        prev_hist = histogram.shift(1)

        # MA 시그널
        ma_signal = pd.Series(0.0, index=dates)
        bull_mask = (ma5 > ma20) & (ma20 > ma60)
        spread_bull = ((ma5 - ma60) / ma60 * 10.0).clip(0.0, 1.0)
        ma_signal = ma_signal.where(~bull_mask, spread_bull)

        bear_mask = (ma5 < ma20) & (ma20 < ma60)
        spread_bear = -((ma60 - ma5) / ma60 * 10.0).clip(0.0, 1.0)
        ma_signal = ma_signal.where(~bear_mask, spread_bear)

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

        return (ma_signal + macd_signal).clip(-1.0, 1.0)

    def _generate_risk_parity(self, close: pd.Series, dates: pd.DatetimeIndex) -> pd.Series:
        """리스크 패리티 시그널: 변동성 추세 + 절대 수준"""
        returns = close.pct_change()
        vol_20d = returns.rolling(20).std() * np.sqrt(252)
        vol_60d = returns.rolling(60).std() * np.sqrt(252)

        vol_trend = ((vol_60d - vol_20d) / vol_60d).fillna(0.0)
        vol_median = 0.30
        vol_level = ((vol_median - vol_60d) / vol_median).fillna(0.0)

        return (vol_trend * 0.6 + vol_level * 0.4).clip(-1.0, 1.0)
