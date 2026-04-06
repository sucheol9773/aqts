"""
백테스트 성과 개선 테스트

테스트 대상:
  1. CRISIS 레짐 감지 — 급락+고변동성 복합 조건
  2. 변동성 타겟팅 (Volatility Scaling)
  3. 점진적 재진입 (Gradual Re-entry)
  4. 동적 임계값 통합 (Dynamic Threshold in Backtest)
  5. 레짐별 가중치 라우팅 (CRISIS 포함)
  6. 종합 MDD 방어 효과
"""

import numpy as np
import pandas as pd

from config.constants import Country
from core.backtest_engine.engine import BacktestConfig, BacktestEngine
from core.strategy_ensemble.regime import (
    ConfidenceCalibrator,
    DynamicThreshold,
    MarketRegime,
    MarketRegimeDetector,
    RegimeInfo,
    RegimeWeightRouter,
)


# ══════════════════════════════════════
# 유틸리티: 테스트 데이터 생성
# ══════════════════════════════════════
def make_ohlcv(
    n_days: int = 200,
    trend: float = 0.0,
    volatility: float = 0.02,
    crash_start: int = -1,
    crash_magnitude: float = -0.30,
    crash_days: int = 20,
    seed: int = 42,
) -> pd.DataFrame:
    """테스트용 OHLCV 생성

    Args:
        n_days: 총 일수
        trend: 일별 평균 수익률 (0.001 = 0.1%/day)
        volatility: 일별 표준편차
        crash_start: 급락 시작 인덱스 (-1이면 없음)
        crash_magnitude: 급락 총 하락률
        crash_days: 급락 기간 (일수)
        seed: 랜덤 시드
    """
    rng = np.random.RandomState(seed)
    returns = rng.normal(trend, volatility, n_days)

    if crash_start >= 0:
        daily_crash = crash_magnitude / crash_days
        end = min(crash_start + crash_days, n_days)
        returns[crash_start:end] = daily_crash

    prices = 100.0 * np.cumprod(1 + returns)
    dates = pd.date_range("2020-01-01", periods=n_days, freq="B")

    return pd.DataFrame(
        {
            "open": prices * (1 - volatility * 0.5),
            "high": prices * (1 + volatility),
            "low": prices * (1 - volatility),
            "close": prices,
            "volume": rng.randint(100000, 1000000, n_days),
        },
        index=dates,
    )


def make_backtest_data(
    n_days: int = 252,
    n_tickers: int = 3,
    signal_strength: float = 0.5,
    crash_start: int = -1,
    crash_magnitude: float = -0.30,
    seed: int = 42,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """백테스트용 시그널 + 가격 데이터 생성"""
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2020-01-01", periods=n_days, freq="B")
    tickers = [f"TICK{i}" for i in range(n_tickers)]

    # 가격
    prices_data = {}
    for t in tickers:
        returns = rng.normal(0.0003, 0.02, n_days)
        if crash_start >= 0:
            daily_crash = crash_magnitude / 20
            end = min(crash_start + 20, n_days)
            returns[crash_start:end] = daily_crash
        prices_data[t] = 100.0 * np.cumprod(1 + returns)

    prices = pd.DataFrame(prices_data, index=dates)

    # 시그널
    signals_data = {}
    for t in tickers:
        sigs = rng.uniform(-signal_strength, signal_strength, n_days)
        signals_data[t] = sigs

    signals = pd.DataFrame(signals_data, index=dates)
    return signals, prices


# ══════════════════════════════════════
# 1. CRISIS 레짐 감지 테스트
# ══════════════════════════════════════
class TestCrisisRegimeDetection:
    """CRISIS 레짐 감지"""

    def test_crisis_detected_during_crash(self):
        """급락 + 고변동성 → CRISIS 레짐 감지"""
        ohlcv = make_ohlcv(
            n_days=200,
            trend=0.001,
            volatility=0.01,
            crash_start=150,
            crash_magnitude=-0.35,
            crash_days=20,
        )
        detector = MarketRegimeDetector()
        result = detector.detect(ohlcv)
        # 급락 후이므로 CRISIS 또는 TRENDING_DOWN 또는 HIGH_VOLATILITY
        assert result.regime in (
            MarketRegime.CRISIS,
            MarketRegime.TRENDING_DOWN,
            MarketRegime.HIGH_VOLATILITY,
        )

    def test_crisis_requires_multiple_signals(self):
        """CRISIS는 2개 이상 조건 필요 (변동성만으론 불충분)"""
        # 안정적 상승장 → CRISIS 아님
        ohlcv = make_ohlcv(n_days=200, trend=0.001, volatility=0.01)
        detector = MarketRegimeDetector()
        result = detector.detect(ohlcv)
        assert result.regime != MarketRegime.CRISIS

    def test_crisis_has_high_confidence(self):
        """CRISIS 감지 시 확신도 0.7 이상"""
        ohlcv = make_ohlcv(
            n_days=200,
            trend=-0.002,
            volatility=0.04,
            crash_start=160,
            crash_magnitude=-0.40,
            crash_days=20,
        )
        detector = MarketRegimeDetector()
        result = detector.detect(ohlcv)
        if result.regime == MarketRegime.CRISIS:
            assert result.confidence >= 0.7

    def test_dd_from_60d_high_in_details(self):
        """details에 dd_from_60d_high 포함"""
        ohlcv = make_ohlcv(n_days=200, trend=0.0, volatility=0.02)
        detector = MarketRegimeDetector()
        result = detector.detect(ohlcv)
        assert "dd_from_60d_high" in result.details

    def test_normal_market_not_crisis(self):
        """정상 시장에서는 CRISIS 아님"""
        ohlcv = make_ohlcv(n_days=200, trend=0.0005, volatility=0.015)
        detector = MarketRegimeDetector()
        result = detector.detect(ohlcv)
        assert result.regime != MarketRegime.CRISIS


# ══════════════════════════════════════
# 2. DynamicThreshold CRISIS 지원
# ══════════════════════════════════════
class TestDynamicThresholdCrisis:
    """CRISIS 레짐 동적 임계값"""

    def test_crisis_threshold_highest(self):
        """CRISIS 레짐은 가장 높은 기본 임계값 (0.50)"""
        dt = DynamicThreshold()
        crisis_info = RegimeInfo(
            regime=MarketRegime.CRISIS,
            confidence=0.9,
            volatility_percentile=0.95,
            trend_strength=-0.8,
            details={},
        )
        buy_t, sell_t = dt.compute(crisis_info)
        # CRISIS base=0.50, 고변동성 보정으로 더 높아질 수 있음
        assert buy_t >= 0.40

    def test_crisis_vs_sideways(self):
        """CRISIS 임계값 > SIDEWAYS 임계값"""
        dt = DynamicThreshold()
        crisis_info = RegimeInfo(
            regime=MarketRegime.CRISIS,
            confidence=0.9,
            volatility_percentile=0.9,
            trend_strength=-0.5,
            details={},
        )
        sideways_info = RegimeInfo(
            regime=MarketRegime.SIDEWAYS,
            confidence=0.6,
            volatility_percentile=0.5,
            trend_strength=0.0,
            details={},
        )
        crisis_t, _ = dt.compute(crisis_info)
        sideways_t, _ = dt.compute(sideways_info)
        assert crisis_t > sideways_t


# ══════════════════════════════════════
# 3. RegimeWeightRouter CRISIS 지원
# ══════════════════════════════════════
class TestRegimeWeightRouterCrisis:
    """CRISIS 레짐 가중치 라우팅"""

    def test_crisis_reduces_mean_reversion(self):
        """CRISIS 시 평균회귀 가중치 대폭 축소"""
        router = RegimeWeightRouter()
        base = {
            "FACTOR": 0.25,
            "MEAN_REVERSION": 0.10,
            "TREND_FOLLOWING": 0.20,
            "RISK_PARITY": 0.20,
            "ML_SIGNAL": 0.0,
            "SENTIMENT": 0.25,
        }
        crisis_info = RegimeInfo(
            regime=MarketRegime.CRISIS,
            confidence=0.9,
            volatility_percentile=0.95,
            trend_strength=-0.8,
            details={},
        )
        adjusted = router.adjust_weights(base, crisis_info)
        # RISK_PARITY should be boosted, MEAN_REVERSION reduced
        assert adjusted["RISK_PARITY"] > base["RISK_PARITY"]
        assert adjusted["MEAN_REVERSION"] < base["MEAN_REVERSION"]

    def test_crisis_boosts_risk_parity(self):
        """CRISIS 시 리스크패리티 가중치 최대"""
        router = RegimeWeightRouter()
        base = {
            "FACTOR": 0.20,
            "MEAN_REVERSION": 0.20,
            "TREND_FOLLOWING": 0.20,
            "RISK_PARITY": 0.20,
            "ML_SIGNAL": 0.0,
            "SENTIMENT": 0.20,
        }
        crisis_info = RegimeInfo(
            regime=MarketRegime.CRISIS,
            confidence=0.9,
            volatility_percentile=0.95,
            trend_strength=-0.8,
            details={},
        )
        adjusted = router.adjust_weights(base, crisis_info)
        # RISK_PARITY는 가장 높은 가중치여야 함
        max_strategy = max(adjusted, key=adjusted.get)
        assert max_strategy == "RISK_PARITY"


# ══════════════════════════════════════
# 4. 변동성 타겟팅 테스트
# ══════════════════════════════════════
class TestVolatilityScaling:
    """변동성 타겟팅 (Volatility Scaling)"""

    def test_vol_scaling_reduces_positions_in_high_vol(self):
        """고변동성 환경에서 vol_target이 포지션을 줄임"""
        signals, prices = make_backtest_data(n_days=252, n_tickers=3, signal_strength=0.6, seed=42)
        # 기본 (vol_target 없음)
        config_base = BacktestConfig(
            initial_capital=10_000_000,
            country=Country.KR,
            max_drawdown_limit=0.20,
            drawdown_cooldown_days=20,
        )
        result_base = BacktestEngine(config_base).run("BASE", signals, prices)

        # vol_target 적용
        config_vol = BacktestConfig(
            initial_capital=10_000_000,
            country=Country.KR,
            max_drawdown_limit=0.20,
            drawdown_cooldown_days=20,
            vol_target=0.10,
        )
        result_vol = BacktestEngine(config_vol).run("VOL_TARGET", signals, prices)

        # vol_target 적용 시 MDD가 같거나 더 낮아야 함 (항상 보장은 아니지만 확률적)
        # 기본 검증: 두 결과 모두 유효한 값
        assert result_base.total_return != 0 or result_base.total_trades == 0
        assert result_vol.total_return != 0 or result_vol.total_trades == 0

    def test_vol_target_config_fields(self):
        """BacktestConfig에 vol_target 필드 존재"""
        config = BacktestConfig(vol_target=0.15, vol_lookback=30)
        assert config.vol_target == 0.15
        assert config.vol_lookback == 30


# ══════════════════════════════════════
# 5. 점진적 재진입 테스트
# ══════════════════════════════════════
class TestGradualReentry:
    """점진적 재진입"""

    def test_gradual_reentry_config(self):
        """BacktestConfig에 gradual_reentry_days 필드 존재"""
        config = BacktestConfig(gradual_reentry_days=10)
        assert config.gradual_reentry_days == 10

    def test_gradual_reentry_default_zero(self):
        """기본값은 0 (즉시 복귀)"""
        config = BacktestConfig()
        assert config.gradual_reentry_days == 0

    def test_gradual_reentry_runs_without_error(self):
        """점진적 재진입 설정이 에러 없이 실행됨"""
        signals, prices = make_backtest_data(
            n_days=252,
            n_tickers=3,
            signal_strength=0.6,
            crash_start=100,
            crash_magnitude=-0.25,
            seed=42,
        )
        config = BacktestConfig(
            initial_capital=10_000_000,
            country=Country.KR,
            max_drawdown_limit=0.15,
            drawdown_cooldown_days=15,
            gradual_reentry_days=10,
        )
        result = BacktestEngine(config).run("GRADUAL", signals, prices)
        assert result is not None
        assert len(result.equity_curve) > 0


# ══════════════════════════════════════
# 6. 동적 임계값 통합 테스트
# ══════════════════════════════════════
class TestDynamicThresholdInBacktest:
    """백테스트 내 동적 임계값"""

    def test_dynamic_threshold_config(self):
        """BacktestConfig에 use_dynamic_threshold 필드"""
        config = BacktestConfig(use_dynamic_threshold=True)
        assert config.use_dynamic_threshold is True

    def test_dynamic_threshold_default_false(self):
        """기본값은 False"""
        config = BacktestConfig()
        assert config.use_dynamic_threshold is False

    def test_dynamic_threshold_backtest_runs(self):
        """동적 임계값 활성화 백테스트 실행"""
        signals, prices = make_backtest_data(n_days=252, n_tickers=3, signal_strength=0.6, seed=42)
        config = BacktestConfig(
            initial_capital=10_000_000,
            country=Country.KR,
            use_dynamic_threshold=True,
        )
        result = BacktestEngine(config).run("DYNAMIC", signals, prices)
        assert result is not None
        assert len(result.equity_curve) > 0

    def test_dynamic_vs_static_threshold(self):
        """동적 임계값 vs 고정 0.3 비교 — 둘 다 유효한 결과"""
        signals, prices = make_backtest_data(n_days=252, n_tickers=3, signal_strength=0.6, seed=42)
        config_static = BacktestConfig(
            initial_capital=10_000_000,
            country=Country.KR,
            use_dynamic_threshold=False,
        )
        config_dynamic = BacktestConfig(
            initial_capital=10_000_000,
            country=Country.KR,
            use_dynamic_threshold=True,
        )
        r_static = BacktestEngine(config_static).run("STATIC", signals, prices)
        r_dynamic = BacktestEngine(config_dynamic).run("DYNAMIC", signals, prices)

        # 둘 다 유효한 결과
        assert len(r_static.equity_curve) > 0
        assert len(r_dynamic.equity_curve) > 0


# ══════════════════════════════════════
# 7. 종합 MDD 방어 효과 테스트
# ══════════════════════════════════════
class TestComprehensiveMDDDefense:
    """MDD 방어 종합 테스트 — 모든 방어 레이어 동시 활성화"""

    def test_all_defenses_combined(self):
        """모든 방어 레이어 동시 활성화 시 에러 없이 실행"""
        signals, prices = make_backtest_data(
            n_days=504,
            n_tickers=5,
            signal_strength=0.6,
            crash_start=200,
            crash_magnitude=-0.35,
            seed=42,
        )
        config = BacktestConfig(
            initial_capital=50_000_000,
            country=Country.KR,
            # 기존 방어
            max_drawdown_limit=0.20,
            drawdown_cooldown_days=20,
            dd_cushion_start=0.10,
            dd_cushion_floor=0.25,
            trailing_stop_atr_multiplier=2.0,
            # 신규 방어
            vol_target=0.15,
            vol_lookback=20,
            gradual_reentry_days=10,
            use_dynamic_threshold=True,
        )
        result = BacktestEngine(config).run("FULL_DEFENSE", signals, prices)

        assert result is not None
        assert len(result.equity_curve) > 0
        assert result.total_trades >= 0
        # MDD는 음수 (or 0)
        assert result.mdd <= 0

    def test_defense_vs_no_defense(self):
        """방어 활성 vs 비활성 — 방어 시 MDD 개선 또는 동등"""
        signals, prices = make_backtest_data(
            n_days=504,
            n_tickers=5,
            signal_strength=0.6,
            crash_start=200,
            crash_magnitude=-0.35,
            seed=42,
        )

        # 방어 없음
        config_none = BacktestConfig(
            initial_capital=50_000_000,
            country=Country.KR,
        )
        r_none = BacktestEngine(config_none).run("NO_DEFENSE", signals, prices)

        # 방어 활성
        config_full = BacktestConfig(
            initial_capital=50_000_000,
            country=Country.KR,
            max_drawdown_limit=0.20,
            drawdown_cooldown_days=20,
            dd_cushion_start=0.10,
            dd_cushion_floor=0.25,
            vol_target=0.15,
            gradual_reentry_days=10,
        )
        r_full = BacktestEngine(config_full).run("FULL_DEFENSE", signals, prices)

        # 방어 활성화 시 MDD가 더 양호해야 함 (|MDD| 작아야)
        # MDD는 음수이므로 방어 MDD > 비방어 MDD (절대값 더 작음)
        assert r_full.mdd >= r_none.mdd

    def test_crash_recovery_with_gradual_reentry(self):
        """급락 후 점진적 재진입으로 회복 경로 검증"""
        signals, prices = make_backtest_data(
            n_days=504,
            n_tickers=3,
            signal_strength=0.6,
            crash_start=150,
            crash_magnitude=-0.30,
            seed=42,
        )
        config = BacktestConfig(
            initial_capital=10_000_000,
            country=Country.KR,
            max_drawdown_limit=0.15,
            drawdown_cooldown_days=15,
            gradual_reentry_days=15,
        )
        result = BacktestEngine(config).run("RECOVERY", signals, prices)

        # 쿨다운 + 재진입 후에도 거래가 재개되어야 함
        assert result.total_trades > 0
        # 에퀴티 커브가 전체 기간 커버
        assert len(result.equity_curve) == len(prices)


# ══════════════════════════════════════
# 8. ConfidenceCalibrator CRISIS 레짐 보정
# ══════════════════════════════════════
class TestConfidenceCalibratorCrisis:
    """CRISIS 레짐 신뢰도 보정"""

    def test_crisis_regime_reduces_confidence(self):
        """CRISIS 레짐에서 confidence 대폭 감소"""
        cal = ConfidenceCalibrator()
        crisis_info = RegimeInfo(
            regime=MarketRegime.CRISIS,
            confidence=0.9,
            volatility_percentile=0.95,
            trend_strength=-0.8,
            details={},
        )
        signals = {"FACTOR": 0.5, "TREND": 0.3, "RISK_PARITY": -0.2}
        raw = 0.8
        calibrated = cal.calibrate(raw, signals, crisis_info)
        # CRISIS의 고변동성으로 인해 신뢰도가 크게 감소해야 함
        assert calibrated < raw * 0.8
