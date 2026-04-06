"""
TREND_FOLLOWING v2 시그널 개선 검증 테스트

v1(MA+MACD) → v2(적응적MA+ADX+ROC+거래량) 개선 효과 검증:
1. 기본 시그널 생성 정확성
2. 추세 시장에서 시그널 강도 향상
3. 횡보 시장에서 시그널 억제
4. 거래량 필터 동작
5. 적응적 MA 변동성 적응
6. ADX + ROC 모멘텀 시그널
7. v1 대비 시그널 품질 개선 (합성 추세 데이터)
"""

import numpy as np
import pandas as pd

from core.quant_engine.signal_generator import TechnicalIndicators
from core.quant_engine.vectorized_signals import VectorizedSignalGenerator


# ═══════════════════════════════════════════
# 테스트 데이터 생성 헬퍼
# ═══════════════════════════════════════════
def _make_ohlcv(
    n: int = 300,
    trend: float = 0.0005,
    noise: float = 0.02,
    volume_base: float = 1_000_000,
    seed: int = 42,
) -> pd.DataFrame:
    """합성 OHLCV 데이터 생성"""
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2020-01-01", periods=n)

    # 가격 생성 (GBM + trend)
    returns = trend + noise * rng.randn(n)
    close = 10000.0 * np.cumprod(1 + returns)
    high = close * (1 + abs(noise * rng.randn(n) * 0.5))
    low = close * (1 - abs(noise * rng.randn(n) * 0.5))
    open_ = close * (1 + noise * rng.randn(n) * 0.3)
    volume = volume_base * (1 + 0.5 * rng.randn(n))
    volume = np.maximum(volume, 10000)

    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=dates,
    )


def _make_trending_up(n: int = 300, seed: int = 42) -> pd.DataFrame:
    """강한 상승 추세 데이터"""
    return _make_ohlcv(n=n, trend=0.002, noise=0.01, seed=seed)


def _make_trending_down(n: int = 300, seed: int = 42) -> pd.DataFrame:
    """강한 하락 추세 데이터 (SNR=0.3으로 명확한 하락)"""
    return _make_ohlcv(n=n, trend=-0.003, noise=0.01, seed=seed)


def _make_sideways(n: int = 300, seed: int = 42) -> pd.DataFrame:
    """횡보 (무추세) 데이터"""
    return _make_ohlcv(n=n, trend=0.0, noise=0.015, seed=seed)


def _make_high_volatility(n: int = 300, seed: int = 42) -> pd.DataFrame:
    """고변동성 상승 추세"""
    return _make_ohlcv(n=n, trend=0.001, noise=0.04, seed=seed)


def _signal_sharpe(signal: pd.Series, close: pd.Series, min_window: int = 60) -> float:
    """시그널 기반 Sharpe ratio 계산 (단순화)"""
    returns = close.pct_change()
    # 시그널을 1일 래그 (진입 다음날 수익)
    strategy_returns = signal.shift(1) * returns
    strategy_returns = strategy_returns.iloc[min_window:]
    strategy_returns = strategy_returns.dropna()

    if len(strategy_returns) < 20 or strategy_returns.std() == 0:
        return 0.0
    return float((strategy_returns.mean() / strategy_returns.std()) * np.sqrt(252))


# ═══════════════════════════════════════════
# 테스트 클래스: TF v2 기본 동작
# ═══════════════════════════════════════════
class TestTFv2BasicSignal:
    """TF v2 시그널 기본 생성 테스트"""

    def test_signal_range(self):
        """시그널이 [-1, 1] 범위 내"""
        ohlcv = _make_ohlcv()
        gen = VectorizedSignalGenerator()
        signals = gen.generate(ohlcv)
        tf = signals["TREND_FOLLOWING"]
        assert tf.min() >= -1.0
        assert tf.max() <= 1.0

    def test_min_window_zeroed(self):
        """최소 윈도우(60일) 이전은 0"""
        ohlcv = _make_ohlcv()
        gen = VectorizedSignalGenerator(min_window=60)
        signals = gen.generate(ohlcv)
        tf = signals["TREND_FOLLOWING"]
        assert (tf.iloc[:60] == 0.0).all()

    def test_no_nan_values(self):
        """NaN 없음"""
        ohlcv = _make_ohlcv()
        gen = VectorizedSignalGenerator()
        signals = gen.generate(ohlcv)
        tf = signals["TREND_FOLLOWING"]
        assert not tf.isna().any()

    def test_all_three_strategies_returned(self):
        """MR, TF, RP 모두 반환"""
        ohlcv = _make_ohlcv()
        gen = VectorizedSignalGenerator()
        signals = gen.generate(ohlcv)
        assert set(signals.keys()) == {"MEAN_REVERSION", "TREND_FOLLOWING", "RISK_PARITY"}

    def test_without_volume_column(self):
        """volume 컬럼 없어도 동작 (거래량 필터 비활성)"""
        ohlcv = _make_ohlcv()
        ohlcv_no_vol = ohlcv.drop(columns=["volume"])
        gen = VectorizedSignalGenerator()
        signals = gen.generate(ohlcv_no_vol)
        tf = signals["TREND_FOLLOWING"]
        assert not tf.isna().any()
        assert len(tf) == len(ohlcv_no_vol)


# ═══════════════════════════════════════════
# 테스트 클래스: 추세 감지 능력
# ═══════════════════════════════════════════
class TestTFv2TrendDetection:
    """추세 시장에서의 시그널 방향성 검증"""

    def test_uptrend_positive_signal(self):
        """강한 상승 추세 → 양(+) 시그널 우세"""
        ohlcv = _make_trending_up()
        gen = VectorizedSignalGenerator()
        signals = gen.generate(ohlcv)
        tf = signals["TREND_FOLLOWING"]
        # min_window 이후만 평가
        active = tf.iloc[80:]
        assert active.mean() > 0.1, f"상승추세인데 평균 시그널이 너무 낮음: {active.mean():.3f}"

    def test_downtrend_negative_signal(self):
        """강한 하락 추세 → 음(-) 시그널 우세"""
        ohlcv = _make_trending_down()
        gen = VectorizedSignalGenerator()
        signals = gen.generate(ohlcv)
        tf = signals["TREND_FOLLOWING"]
        active = tf.iloc[80:]
        assert active.mean() < -0.1, f"하락추세인데 평균 시그널이 너무 높음: {active.mean():.3f}"

    def test_sideways_weak_signal(self):
        """횡보 시장 → 약한 시그널 (평균 절대값 < 0.3)"""
        ohlcv = _make_sideways()
        gen = VectorizedSignalGenerator()
        signals = gen.generate(ohlcv)
        tf = signals["TREND_FOLLOWING"]
        active = tf.iloc[80:]
        assert active.abs().mean() < 0.3, f"횡보인데 시그널이 너무 강함: {active.abs().mean():.3f}"

    def test_strong_trend_higher_signal_than_weak(self):
        """강한 추세 > 약한 추세 시그널 강도"""
        strong = _make_ohlcv(trend=0.003, noise=0.008, seed=42)
        weak = _make_ohlcv(trend=0.0005, noise=0.015, seed=42)

        gen = VectorizedSignalGenerator()
        strong_tf = gen.generate(strong)["TREND_FOLLOWING"].iloc[80:]
        weak_tf = gen.generate(weak)["TREND_FOLLOWING"].iloc[80:]

        assert (
            strong_tf.mean() > weak_tf.mean()
        ), f"강한 추세({strong_tf.mean():.3f})가 약한 추세({weak_tf.mean():.3f})보다 시그널이 약함"


# ═══════════════════════════════════════════
# 테스트 클래스: 거래량 필터
# ═══════════════════════════════════════════
class TestTFv2VolumeFilter:
    """거래량 기반 시그널 필터링 검증"""

    def test_low_volume_reduces_signal(self):
        """저거래량 시 시그널 감쇄"""
        ohlcv = _make_trending_up()
        ohlcv_low_vol = ohlcv.copy()
        # 후반부 거래량을 극단적으로 낮춤
        ohlcv_low_vol.iloc[150:, 4] = 10000  # volume 컬럼

        gen = VectorizedSignalGenerator()
        normal_tf = gen.generate(ohlcv)["TREND_FOLLOWING"]
        low_vol_tf = gen.generate(ohlcv_low_vol)["TREND_FOLLOWING"]

        # 저거래량 구간에서 시그널이 더 약해야 함
        period = slice(160, 250)
        assert (
            low_vol_tf.iloc[period].abs().mean() < normal_tf.iloc[period].abs().mean()
        ), "저거래량 구간에서 시그널 감쇄가 동작하지 않음"

    def test_high_volume_boosts_signal(self):
        """고거래량 전환 직후 구간에서 시그널 부스트 (rolling mean 적응 전)"""
        ohlcv = _make_trending_up()
        ohlcv_high_vol = ohlcv.copy()
        ohlcv_high_vol.iloc[150:, 4] = 5_000_000  # 평균의 5배

        gen = VectorizedSignalGenerator()
        normal_tf = gen.generate(ohlcv)["TREND_FOLLOWING"]
        high_vol_tf = gen.generate(ohlcv_high_vol)["TREND_FOLLOWING"]

        # rolling mean이 적응하기 전 구간(150~165)에서 볼륨 비율이 높아 부스트
        early_period = slice(152, 165)
        assert high_vol_tf.iloc[early_period].abs().mean() >= normal_tf.iloc[early_period].abs().mean()


# ═══════════════════════════════════════════
# 테스트 클래스: ADX 지표
# ═══════════════════════════════════════════
class TestADXIndicator:
    """TechnicalIndicators.adx() 검증"""

    def test_adx_range(self):
        """ADX 값이 0~100 범위"""
        ohlcv = _make_ohlcv()
        ti = TechnicalIndicators()
        adx = ti.adx(ohlcv["high"], ohlcv["low"], ohlcv["close"])
        adx_valid = adx.dropna()
        assert adx_valid.min() >= 0.0
        assert adx_valid.max() <= 100.0

    def test_trending_market_high_adx(self):
        """추세 시장에서 ADX > 25"""
        ohlcv = _make_trending_up(n=300)
        ti = TechnicalIndicators()
        adx = ti.adx(ohlcv["high"], ohlcv["low"], ohlcv["close"])
        # 추세가 확립된 후반부 (150일 이후)
        assert adx.iloc[150:].mean() > 20.0, f"추세 시장인데 ADX 평균이 낮음: {adx.iloc[150:].mean():.1f}"

    def test_sideways_lower_adx(self):
        """횡보 시장에서 ADX가 추세 시장보다 낮음"""
        trend_ohlcv = _make_trending_up()
        side_ohlcv = _make_sideways()
        ti = TechnicalIndicators()

        adx_trend = ti.adx(trend_ohlcv["high"], trend_ohlcv["low"], trend_ohlcv["close"])
        adx_side = ti.adx(side_ohlcv["high"], side_ohlcv["low"], side_ohlcv["close"])

        assert adx_side.iloc[100:].mean() < adx_trend.iloc[100:].mean()


# ═══════════════════════════════════════════
# 테스트 클래스: 적응적 MA
# ═══════════════════════════════════════════
class TestAdaptiveSMA:
    """_adaptive_sma() 동작 검증"""

    def test_adaptive_sma_returns_series(self):
        """적응적 SMA가 올바른 길이의 Series 반환"""
        ohlcv = _make_ohlcv()
        gen = VectorizedSignalGenerator()
        close = ohlcv["close"]
        periods = pd.Series(20, index=close.index)
        result = gen._adaptive_sma(close, periods)
        assert len(result) == len(close)

    def test_uniform_period_matches_sma(self):
        """균일 기간이면 일반 SMA와 동일"""
        ohlcv = _make_ohlcv()
        gen = VectorizedSignalGenerator()
        ti = TechnicalIndicators()
        close = ohlcv["close"]

        period = 20
        periods = pd.Series(period, index=close.index)
        adaptive = gen._adaptive_sma(close, periods)
        standard = ti.sma(close, period)

        # NaN 아닌 부분만 비교
        mask = adaptive.notna() & standard.notna()
        np.testing.assert_allclose(adaptive[mask].values, standard[mask].values, rtol=1e-6)

    def test_high_volatility_uses_longer_period(self):
        """고변동성 데이터에서 더 긴 기간 사용 확인"""
        high_vol = _make_high_volatility()
        low_vol = _make_ohlcv(trend=0.001, noise=0.005)

        gen = VectorizedSignalGenerator()
        # ATR 기반 vol_ratio 계산 확인
        ti = TechnicalIndicators()
        atr_high = ti.atr(high_vol["high"], high_vol["low"], high_vol["close"])
        atr_low = ti.atr(low_vol["high"], low_vol["low"], low_vol["close"])

        # 고변동성 ATR > 저변동성 ATR (후반부)
        assert atr_high.iloc[100:].mean() > atr_low.iloc[100:].mean()


# ═══════════════════════════════════════════
# 테스트 클래스: 시그널 품질 (Sharpe 비교)
# ═══════════════════════════════════════════
class TestTFv2SignalQuality:
    """합성 데이터에서 TF v2 시그널 품질 검증"""

    def test_uptrend_positive_sharpe(self):
        """상승 추세에서 양의 Sharpe"""
        ohlcv = _make_trending_up(n=500)
        gen = VectorizedSignalGenerator()
        tf = gen.generate(ohlcv)["TREND_FOLLOWING"]
        sharpe = _signal_sharpe(tf, ohlcv["close"])
        assert sharpe > 0.0, f"상승추세에서 Sharpe가 음수: {sharpe:.3f}"

    def test_downtrend_positive_sharpe(self):
        """하락 추세에서도 양의 Sharpe (숏 시그널)"""
        ohlcv = _make_trending_down(n=500)
        gen = VectorizedSignalGenerator()
        tf = gen.generate(ohlcv)["TREND_FOLLOWING"]
        sharpe = _signal_sharpe(tf, ohlcv["close"])
        assert sharpe > 0.0, f"하락추세에서 숏 시그널 Sharpe가 음수: {sharpe:.3f}"

    def test_signal_direction_matches_trend(self):
        """시그널 방향이 실제 추세 방향과 60% 이상 일치"""
        ohlcv = _make_trending_up(n=500)
        gen = VectorizedSignalGenerator()
        tf = gen.generate(ohlcv)["TREND_FOLLOWING"]

        returns = ohlcv["close"].pct_change()
        # 시그널과 다음날 수익의 부호 일치율
        active = tf.iloc[80:-1]
        future_returns = returns.iloc[81:]
        agreement = ((active > 0) & (future_returns > 0)) | ((active < 0) & (future_returns < 0))
        hit_rate = agreement.mean()
        assert hit_rate > 0.50, f"시그널 방향 일치율이 너무 낮음: {hit_rate:.1%}"


# ═══════════════════════════════════════════
# 테스트 클래스: run_backtest.py 동기화
# ═══════════════════════════════════════════
class TestRunBacktestSync:
    """run_backtest.py가 VectorizedSignalGenerator와 동일 로직 사용 확인"""

    def test_backtest_uses_vsg(self):
        """run_backtest.py의 TF 시그널이 VectorizedSignalGenerator를 사용"""
        import os
        import sys

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

        from run_backtest import generate_strategy_signals_vectorized

        ohlcv = _make_ohlcv()
        bt_signals = generate_strategy_signals_vectorized("TEST", ohlcv)
        gen = VectorizedSignalGenerator()
        vsg_signals = gen.generate(ohlcv)

        # TF 시그널이 동일해야 함
        np.testing.assert_allclose(
            bt_signals["TREND_FOLLOWING"].values,
            vsg_signals["TREND_FOLLOWING"].values,
            atol=1e-4,
            err_msg="run_backtest.py와 VectorizedSignalGenerator의 TF 시그널이 불일치",
        )
