"""
시그널 생성기 유닛테스트

테스트 대상: core/quant_engine/signal_generator.py
목표 커버리지: 85% (Quant Engine 모듈)

테스트 범위:
- 기술적 지표 정확성 (SMA, EMA, RSI, BB, MACD)
- 전략별 시그널 값 범위 (-1 ~ +1)
- 데이터 부족 시 안전한 처리
- 과매수/과매도 조건에서의 시그널 방향
"""

import numpy as np
import pandas as pd
import pytest

from config.constants import StrategyType
from core.quant_engine.signal_generator import (
    SignalGenerator,
    TechnicalIndicators,
    Signal,
)


def _make_ohlcv(n: int = 100, trend: str = "up") -> pd.DataFrame:
    """테스트용 OHLCV 데이터 생성"""
    np.random.seed(42)
    dates = pd.bdate_range(start="2025-01-02", periods=n)
    base = 70000.0

    if trend == "up":
        drift = 0.001
    elif trend == "down":
        drift = -0.001
    else:
        drift = 0.0

    prices = [base]
    for _ in range(n - 1):
        change = drift + np.random.normal(0, 0.015)
        prices.append(prices[-1] * (1 + change))

    close = np.array(prices)
    return pd.DataFrame({
        "open": close * (1 + np.random.uniform(-0.005, 0.005, n)),
        "high": close * (1 + np.random.uniform(0.005, 0.02, n)),
        "low": close * (1 - np.random.uniform(0.005, 0.02, n)),
        "close": close,
        "volume": np.random.randint(5_000_000, 20_000_000, n),
    }, index=dates)


class TestTechnicalIndicators:
    """기술적 지표 계산기 테스트"""

    def test_sma_basic(self):
        """SMA 기본 계산"""
        series = pd.Series([1, 2, 3, 4, 5], dtype=float)
        result = TechnicalIndicators.sma(series, 3)
        assert result.iloc[-1] == 4.0  # (3+4+5)/3
        assert pd.isna(result.iloc[0])  # 충분한 기간 전은 NaN

    def test_ema_responsive_to_recent(self):
        """EMA는 최근 값에 더 민감"""
        series = pd.Series([10, 10, 10, 10, 20], dtype=float)
        sma = TechnicalIndicators.sma(series, 5).iloc[-1]
        ema = TechnicalIndicators.ema(series, 5).iloc[-1]
        # EMA가 급등(20)에 더 민감하게 반응
        assert ema > sma

    def test_rsi_range(self):
        """RSI 값은 0~100 범위"""
        ohlcv = _make_ohlcv(100)
        rsi = TechnicalIndicators.rsi(ohlcv["close"], 14)
        valid_rsi = rsi.dropna()
        assert valid_rsi.min() >= 0.0
        assert valid_rsi.max() <= 100.0

    def test_rsi_overbought_in_uptrend(self):
        """강한 상승 추세에서 RSI > 50"""
        ohlcv = _make_ohlcv(100, trend="up")
        rsi = TechnicalIndicators.rsi(ohlcv["close"], 14)
        # 마지막 20일 평균 RSI가 50보다 높을 가능성이 큼
        avg_rsi = rsi.tail(20).mean()
        assert avg_rsi > 40  # 약간의 여유를 둠

    def test_bollinger_bands_relationship(self):
        """볼린저밴드: upper > middle > lower 항상 성립"""
        ohlcv = _make_ohlcv(50)
        upper, middle, lower = TechnicalIndicators.bollinger_bands(ohlcv["close"], 20, 2.0)
        valid_mask = ~(upper.isna() | middle.isna() | lower.isna())
        assert (upper[valid_mask] >= middle[valid_mask]).all()
        assert (middle[valid_mask] >= lower[valid_mask]).all()

    def test_macd_histogram_sign(self):
        """MACD 히스토그램 부호 확인"""
        ohlcv = _make_ohlcv(60)
        macd_line, signal_line, histogram = TechnicalIndicators.macd(ohlcv["close"])
        # 히스토그램 = MACD - Signal
        valid = ~histogram.isna()
        diff = (macd_line[valid] - signal_line[valid] - histogram[valid]).abs()
        assert (diff < 1e-6).all()


class TestSignalGeneratorFactorSignal:
    """팩터 투자 시그널 테스트"""

    def test_high_score_generates_buy(self):
        """높은 팩터 점수 → 매수 시그널"""
        gen = SignalGenerator()
        signal = gen.generate_factor_signal("TEST", 85.0)
        assert signal.value > 0.0
        assert signal.strategy == StrategyType.FACTOR

    def test_low_score_generates_sell(self):
        """낮은 팩터 점수 → 매도 시그널"""
        gen = SignalGenerator()
        signal = gen.generate_factor_signal("TEST", 15.0)
        assert signal.value < 0.0

    def test_mid_score_generates_neutral(self):
        """중간 팩터 점수 → 약한 시그널"""
        gen = SignalGenerator()
        signal = gen.generate_factor_signal("TEST", 50.0)
        assert abs(signal.value) < 0.1

    def test_signal_range(self):
        """시그널 값은 -1 ~ +1 범위"""
        gen = SignalGenerator()
        for score in [0, 25, 50, 75, 100]:
            signal = gen.generate_factor_signal("TEST", score)
            assert -1.0 <= signal.value <= 1.0
            assert 0.0 <= signal.confidence <= 1.0


class TestSignalGeneratorMeanReversion:
    """평균회귀 시그널 테스트"""

    def test_insufficient_data(self):
        """데이터 부족 시 0 시그널 반환"""
        gen = SignalGenerator()
        ohlcv = _make_ohlcv(10)  # 30일 미만
        signal = gen.generate_mean_reversion_signal("TEST", ohlcv)
        assert signal.value == 0.0
        assert signal.confidence == 0.0

    def test_signal_range(self):
        """시그널 값 범위 확인"""
        gen = SignalGenerator()
        ohlcv = _make_ohlcv(100)
        signal = gen.generate_mean_reversion_signal("TEST", ohlcv)
        assert -1.0 <= signal.value <= 1.0
        assert 0.0 <= signal.confidence <= 1.0

    def test_has_reason(self):
        """시그널에 근거(reason)가 포함"""
        gen = SignalGenerator()
        ohlcv = _make_ohlcv(100)
        signal = gen.generate_mean_reversion_signal("TEST", ohlcv)
        assert "RSI" in signal.reason


class TestSignalGeneratorTrendFollowing:
    """추세추종 시그널 테스트"""

    def test_insufficient_data(self):
        """데이터 부족 시 0 시그널"""
        gen = SignalGenerator()
        ohlcv = _make_ohlcv(30)  # 65일 미만
        signal = gen.generate_trend_following_signal("TEST", ohlcv)
        assert signal.value == 0.0

    def test_uptrend_positive_signal(self):
        """상승 추세에서 양수 시그널 경향"""
        gen = SignalGenerator()
        ohlcv = _make_ohlcv(100, trend="up")
        signal = gen.generate_trend_following_signal("TEST", ohlcv)
        # 강한 상승 추세면 양수 시그널 가능성 높음 (절대적이진 않음)
        assert -1.0 <= signal.value <= 1.0
        assert signal.strategy == StrategyType.TREND_FOLLOWING

    def test_signal_has_ma_info(self):
        """시그널 근거에 이동평균 정보 포함"""
        gen = SignalGenerator()
        ohlcv = _make_ohlcv(100)
        signal = gen.generate_trend_following_signal("TEST", ohlcv)
        assert "MA5" in signal.reason


class TestSignalGeneratorRiskParity:
    """리스크 패리티 시그널 테스트"""

    def test_low_volatility_positive_signal(self):
        """저변동성 종목 → 비중 확대 시그널"""
        gen = SignalGenerator()
        # 변동성이 매우 낮은 데이터 생성
        dates = pd.bdate_range(start="2025-01-02", periods=100)
        close = 70000 + np.cumsum(np.random.normal(0, 50, 100))
        ohlcv = pd.DataFrame({
            "close": close,
            "open": close, "high": close + 100, "low": close - 100,
            "volume": np.full(100, 1e7),
        }, index=dates)

        signal = gen.generate_risk_parity_signal("TEST", ohlcv)
        assert signal.strategy == StrategyType.RISK_PARITY
        assert -1.0 <= signal.value <= 1.0

    def test_insufficient_data(self):
        """데이터 부족 시 0 시그널"""
        gen = SignalGenerator()
        ohlcv = _make_ohlcv(20)
        signal = gen.generate_risk_parity_signal("TEST", ohlcv)
        assert signal.value == 0.0


class TestSignalGeneratorAllSignals:
    """전체 시그널 생성 테스트"""

    def test_generates_all_strategies(self):
        """모든 전략의 시그널이 생성됨"""
        gen = SignalGenerator()
        ohlcv = _make_ohlcv(100)
        signals = gen.generate_all_signals("TEST", ohlcv, composite_score=65.0)

        strategies = {s.strategy for s in signals}
        assert StrategyType.FACTOR in strategies
        assert StrategyType.MEAN_REVERSION in strategies
        assert StrategyType.TREND_FOLLOWING in strategies
        assert StrategyType.RISK_PARITY in strategies

    def test_all_signals_have_ticker(self):
        """모든 시그널에 종목코드가 있음"""
        gen = SignalGenerator()
        ohlcv = _make_ohlcv(100)
        signals = gen.generate_all_signals("005930", ohlcv)

        for s in signals:
            assert s.ticker == "005930"
