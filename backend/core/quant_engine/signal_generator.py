"""
시그널 생성 모듈 (Signal Generator)

F-02-02 명세 구현:
- 전략군별 매수/매도/보유 시그널 생성
- 구현 전략: 팩터투자, 평균회귀(RSI/볼린저), 추세추종(이동평균 크로스/듀얼모멘텀), 리스크패리티
- 각 시그널은 -1.0(강한 매도) ~ +1.0(강한 매수) 범위
- 신뢰도 점수 (0.0 ~ 1.0) 함께 산출

사용 라이브러리: pandas 2.2.2, numpy 1.26.4
(ta 라이브러리는 사용하지 않고 직접 구현하여 의존성 문제 방지)
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from config.constants import Market, StrategyType
from config.logging import logger
from contracts.converters import internal_signal_to_contract


@dataclass
class Signal:
    """단일 종목의 시그널"""

    ticker: str
    strategy: StrategyType
    value: float  # -1.0 (강한 매도) ~ +1.0 (강한 매수)
    confidence: float  # 0.0 ~ 1.0
    reason: str = ""


# ══════════════════════════════════════
# 기술적 지표 직접 구현
# (ta-lib/ta 패키지 의존성 제거)
# ══════════════════════════════════════
class TechnicalIndicators:
    """기술적 지표 계산기 (순수 pandas/numpy 구현)"""

    @staticmethod
    def sma(series: pd.Series, period: int) -> pd.Series:
        """단순 이동평균"""
        return series.rolling(window=period, min_periods=period).mean()

    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        """지수 이동평균"""
        return series.ewm(span=period, adjust=False, min_periods=period).mean()

    @staticmethod
    def rsi(series: pd.Series, period: int = 14) -> pd.Series:
        """RSI (Relative Strength Index)"""
        delta = series.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)

        avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi

    @staticmethod
    def bollinger_bands(
        series: pd.Series, period: int = 20, num_std: float = 2.0
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        """볼린저 밴드 (upper, middle, lower)"""
        middle = series.rolling(window=period, min_periods=period).mean()
        std = series.rolling(window=period, min_periods=period).std()
        upper = middle + num_std * std
        lower = middle - num_std * std
        return upper, middle, lower

    @staticmethod
    def macd(
        series: pd.Series,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        """MACD (macd_line, signal_line, histogram)"""
        ema_fast = series.ewm(span=fast, adjust=False, min_periods=fast).mean()
        ema_slow = series.ewm(span=slow, adjust=False, min_periods=slow).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    @staticmethod
    def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """ATR (Average True Range)"""
        prev_close = close.shift(1)
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return true_range.rolling(window=period, min_periods=period).mean()


# ══════════════════════════════════════
# 전략별 시그널 생성기
# ══════════════════════════════════════
class SignalGenerator:
    """전략군별 시그널 생성기"""

    def __init__(self):
        self._ti = TechnicalIndicators()

    def generate_factor_signal(self, ticker: str, composite_score: float) -> Signal:
        """
        팩터 투자 시그널

        팩터 복합 점수(0~100)를 시그널(-1~+1)로 변환
        상위 20%: 매수, 하위 20%: 매도, 중간: 보유
        """
        # 0~100 → -1~+1 선형 변환
        signal_value = (composite_score - 50.0) / 50.0
        signal_value = np.clip(signal_value, -1.0, 1.0)

        # 신뢰도: 점수가 극단일수록 높음
        confidence = min(abs(signal_value) * 1.2, 1.0)

        reason = f"Factor composite={composite_score:.1f}/100"

        return Signal(
            ticker=ticker,
            strategy=StrategyType.FACTOR,
            value=round(signal_value, 4),
            confidence=round(confidence, 4),
            reason=reason,
        )

    def generate_mean_reversion_signal(self, ticker: str, ohlcv: pd.DataFrame) -> Signal:
        """
        평균회귀 시그널 (RSI + 볼린저밴드 결합)

        RSI 과매도(<30) + 볼린저 하단 이탈 → 매수
        RSI 과매수(>70) + 볼린저 상단 이탈 → 매도
        """
        if len(ohlcv) < 30:
            return Signal(
                ticker=ticker,
                strategy=StrategyType.MEAN_REVERSION,
                value=0.0,
                confidence=0.0,
                reason="Insufficient data",
            )

        close = ohlcv["close"].astype(float)
        current_price = close.iloc[-1]

        # RSI
        rsi = self._ti.rsi(close, period=14)
        current_rsi = rsi.iloc[-1]

        # 볼린저밴드
        upper, middle, lower = self._ti.bollinger_bands(close, period=20, num_std=2.0)
        bb_upper = upper.iloc[-1]
        bb_lower = lower.iloc[-1]
        bb_middle = middle.iloc[-1]

        if pd.isna(current_rsi) or pd.isna(bb_upper):
            return Signal(
                ticker=ticker, strategy=StrategyType.MEAN_REVERSION, value=0.0, confidence=0.0, reason="Indicator NaN"
            )

        # RSI 시그널: -1 ~ +1
        rsi_signal = 0.0
        if current_rsi < 30:
            rsi_signal = (30 - current_rsi) / 30.0  # 0~1 (과매도)
        elif current_rsi > 70:
            rsi_signal = -(current_rsi - 70) / 30.0  # -1~0 (과매수)

        # 볼린저 시그널
        bb_range = bb_upper - bb_lower
        bb_signal = 0.0
        if bb_range > 0:
            bb_position = (current_price - bb_middle) / (bb_range / 2)
            bb_signal = -np.clip(bb_position, -1.0, 1.0)  # 상단이면 매도, 하단이면 매수

        # 결합 (동일 가중)
        combined = (rsi_signal + bb_signal) / 2.0
        combined = np.clip(combined, -1.0, 1.0)

        # 신뢰도: 두 지표가 같은 방향이면 높음
        if rsi_signal * bb_signal > 0:
            confidence = min(abs(combined) * 1.5, 1.0)
        else:
            confidence = abs(combined) * 0.5

        reason = f"RSI={current_rsi:.1f}, BB_pos={bb_signal:.2f}"

        return Signal(
            ticker=ticker,
            strategy=StrategyType.MEAN_REVERSION,
            value=round(combined, 4),
            confidence=round(confidence, 4),
            reason=reason,
        )

    def generate_trend_following_signal(self, ticker: str, ohlcv: pd.DataFrame) -> Signal:
        """
        추세추종 시그널 (이동평균 크로스 + MACD + 듀얼모멘텀)

        골든크로스(5MA > 20MA > 60MA) → 매수
        데드크로스(5MA < 20MA < 60MA) → 매도
        MACD 히스토그램 양수 → 매수 보강
        """
        if len(ohlcv) < 65:
            return Signal(
                ticker=ticker,
                strategy=StrategyType.TREND_FOLLOWING,
                value=0.0,
                confidence=0.0,
                reason="Insufficient data",
            )

        close = ohlcv["close"].astype(float)

        # 이동평균
        ma5 = self._ti.sma(close, 5).iloc[-1]
        ma20 = self._ti.sma(close, 20).iloc[-1]
        ma60 = self._ti.sma(close, 60).iloc[-1]

        # MACD
        macd_line, signal_line, histogram = self._ti.macd(close)
        current_hist = histogram.iloc[-1]
        prev_hist = histogram.iloc[-2] if len(histogram) > 1 else 0.0

        if pd.isna(ma60) or pd.isna(current_hist):
            return Signal(
                ticker=ticker, strategy=StrategyType.TREND_FOLLOWING, value=0.0, confidence=0.0, reason="Indicator NaN"
            )

        # 이동평균 정배열/역배열 점수
        ma_signal = 0.0
        if ma5 > ma20 > ma60:
            # 정배열: 강한 매수
            spread = (ma5 - ma60) / ma60
            ma_signal = min(spread * 10.0, 1.0)
        elif ma5 < ma20 < ma60:
            # 역배열: 강한 매도
            spread = (ma60 - ma5) / ma60
            ma_signal = -min(spread * 10.0, 1.0)
        else:
            # 혼합: 약한 시그널
            if ma5 > ma20:
                ma_signal = 0.3
            elif ma5 < ma20:
                ma_signal = -0.3

        # MACD 시그널 보강
        macd_signal = 0.0
        if current_hist > 0 and current_hist > prev_hist:
            macd_signal = 0.3  # 히스토그램 양수 + 증가
        elif current_hist < 0 and current_hist < prev_hist:
            macd_signal = -0.3  # 히스토그램 음수 + 감소

        combined = np.clip(ma_signal + macd_signal, -1.0, 1.0)

        # 신뢰도: MA 정배열/역배열이 깨끗할수록 높음
        confidence = abs(ma_signal) * 0.7 + abs(macd_signal) * 0.3

        reason = f"MA5={ma5:.0f},MA20={ma20:.0f},MA60={ma60:.0f},MACD_H={current_hist:.2f}"

        return Signal(
            ticker=ticker,
            strategy=StrategyType.TREND_FOLLOWING,
            value=round(combined, 4),
            confidence=round(min(confidence, 1.0), 4),
            reason=reason,
        )

    def generate_risk_parity_signal(self, ticker: str, ohlcv: pd.DataFrame, market_volatility: float = 0.0) -> Signal:
        """
        리스크 패리티 시그널

        종목 변동성 대비 시장 변동성 비율로 포지션 크기 결정
        변동성이 낮은 종목에 더 큰 비중 부여
        """
        if len(ohlcv) < 60:
            return Signal(
                ticker=ticker, strategy=StrategyType.RISK_PARITY, value=0.0, confidence=0.0, reason="Insufficient data"
            )

        close = ohlcv["close"].astype(float)
        returns = close.pct_change().dropna()

        if len(returns) < 20:
            return Signal(
                ticker=ticker,
                strategy=StrategyType.RISK_PARITY,
                value=0.0,
                confidence=0.0,
                reason="Insufficient returns",
            )

        # 60일 연환산 변동성
        vol_60d = returns.tail(60).std() * np.sqrt(252)

        if vol_60d < 1e-10:
            return Signal(
                ticker=ticker, strategy=StrategyType.RISK_PARITY, value=0.0, confidence=0.0, reason="Zero volatility"
            )

        # 목표 변동성 (예: 15%) 대비 역비례 포지션
        target_vol = 0.15
        vol_ratio = target_vol / vol_60d

        # 시그널: 변동성이 낮으면 양수(비중 확대), 높으면 음수(비중 축소)
        signal_value = np.clip(vol_ratio - 1.0, -1.0, 1.0)

        # 신뢰도: 충분한 데이터 + 안정적 변동성
        vol_stability = 1.0 - min(returns.tail(20).std() / returns.tail(60).std(), 2.0) / 2.0
        confidence = max(vol_stability, 0.1)

        reason = f"Vol_60d={vol_60d:.4f}, VolRatio={vol_ratio:.2f}"

        return Signal(
            ticker=ticker,
            strategy=StrategyType.RISK_PARITY,
            value=round(signal_value, 4),
            confidence=round(confidence, 4),
            reason=reason,
        )

    def generate_all_signals(
        self,
        ticker: str,
        ohlcv: pd.DataFrame,
        composite_score: float = 50.0,
        market: Market = Market.KRX,
        validate_contract: bool = True,
    ) -> list[Signal]:
        """
        단일 종목에 대해 모든 전략의 시그널을 생성

        Args:
            ticker: 종목코드
            ohlcv: OHLCV DataFrame
            composite_score: 팩터 복합 점수 (0~100)
            market: 거래소 (계약 검증용)
            validate_contract: True면 출력 시그널을 contracts.Signal로 검증

        Returns:
            Signal 리스트 (전략별 1개씩)
        """
        signals = []

        signals.append(self.generate_factor_signal(ticker, composite_score))
        signals.append(self.generate_mean_reversion_signal(ticker, ohlcv))
        signals.append(self.generate_trend_following_signal(ticker, ohlcv))
        signals.append(self.generate_risk_parity_signal(ticker, ohlcv))

        # 계약 검증: Pydantic validation을 통해 데이터 무결성 강제
        if validate_contract:
            for sig in signals:
                try:
                    internal_signal_to_contract(sig, market=market)
                except Exception as e:
                    logger.warning(f"[Contract] Signal 계약 위반: {ticker}/{sig.strategy.value} — {e}")

        return signals
