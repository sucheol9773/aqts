"""
시장 레짐 감지 + 동적 임계값 + 신뢰도 캘리브레이션 테스트

4개 핵심 컴포넌트:
1. MarketRegimeDetector: 레짐 분류 정확성
2. DynamicThreshold: 레짐별 임계값 산출
3. ConfidenceCalibrator: 과신 방지 보정
4. RegimeWeightRouter: 레짐별 가중치 라우팅
"""

import numpy as np
import pandas as pd

from core.strategy_ensemble.regime import (
    ConfidenceCalibrator,
    DynamicThreshold,
    MarketRegime,
    MarketRegimeDetector,
    RegimeInfo,
    RegimeWeightRouter,
)


# ══════════════════════════════════════
# 테스트 데이터 생성 헬퍼
# ══════════════════════════════════════
def _make_ohlcv(
    n: int = 200,
    trend: float = 0.0,
    volatility: float = 0.02,
    base_price: float = 50000,
) -> pd.DataFrame:
    """
    테스트용 OHLCV 생성

    Args:
        n: 데이터 길이
        trend: 일일 평균 수익률 (양수=상승, 음수=하락, 0=횡보)
        volatility: 일일 수익률 표준편차
        base_price: 시작 가격
    """
    np.random.seed(42)
    returns = np.random.normal(trend, volatility, n)
    prices = [base_price]
    for r in returns:
        prices.append(prices[-1] * (1 + r))
    prices = np.array(prices[1:])

    high = prices * (1 + np.abs(np.random.normal(0, 0.005, n)))
    low = prices * (1 - np.abs(np.random.normal(0, 0.005, n)))
    volume = np.random.randint(1_000_000, 20_000_000, n)

    return pd.DataFrame(
        {
            "open": prices * (1 + np.random.normal(0, 0.002, n)),
            "high": high,
            "low": low,
            "close": prices,
            "volume": volume,
        }
    )


# ══════════════════════════════════════
# 1. MarketRegimeDetector 테스트
# ══════════════════════════════════════
class TestMarketRegimeDetector:
    """레짐 감지 정확성 테스트"""

    def setup_method(self):
        self.detector = MarketRegimeDetector()

    def test_insufficient_data(self):
        """데이터 부족 시 SIDEWAYS 반환"""
        short_df = _make_ohlcv(n=30)
        result = self.detector.detect(short_df)
        assert result.regime == MarketRegime.SIDEWAYS
        assert result.confidence == 0.3

    def test_uptrend_detection(self):
        """강한 상승 추세 감지"""
        uptrend = _make_ohlcv(n=200, trend=0.003, volatility=0.01)
        result = self.detector.detect(uptrend)
        assert result.regime in (MarketRegime.TRENDING_UP, MarketRegime.SIDEWAYS)
        assert result.trend_strength > 0

    def test_downtrend_detection(self):
        """강한 하락 추세 감지"""
        downtrend = _make_ohlcv(n=200, trend=-0.003, volatility=0.01)
        result = self.detector.detect(downtrend)
        assert result.regime in (MarketRegime.TRENDING_DOWN, MarketRegime.SIDEWAYS)
        assert result.trend_strength < 0

    def test_sideways_detection(self):
        """횡보 시장 감지"""
        sideways = _make_ohlcv(n=200, trend=0.0, volatility=0.01)
        result = self.detector.detect(sideways)
        # 낮은 추세 강도
        assert abs(result.trend_strength) < 0.5

    def test_high_volatility_detection(self):
        """고변동 전환 시장 감지 (후반부 변동성 급등)"""
        # 전반부는 저변동, 후반부는 고변동 → 백분위가 높아짐
        np.random.seed(42)
        n = 200
        prices = [50000.0]
        for i in range(n):
            vol = 0.005 if i < 150 else 0.05  # 후반 50일 변동성 급등
            ret = np.random.normal(0, vol)
            prices.append(prices[-1] * (1 + ret))
        prices = np.array(prices[1:])
        ohlcv = pd.DataFrame(
            {
                "open": prices,
                "high": prices * 1.005,
                "low": prices * 0.995,
                "close": prices,
                "volume": np.random.randint(1_000_000, 20_000_000, n),
            }
        )
        result = self.detector.detect(ohlcv)
        # 후반부 변동성 급등 → 변동성 백분위 높아야 함
        assert result.volatility_percentile > 0.5

    def test_regime_info_structure(self):
        """RegimeInfo 구조 검증"""
        ohlcv = _make_ohlcv(n=200)
        result = self.detector.detect(ohlcv)

        assert isinstance(result.regime, MarketRegime)
        assert 0 <= result.confidence <= 1
        assert 0 <= result.volatility_percentile <= 1
        assert -1 <= result.trend_strength <= 1
        assert isinstance(result.details, dict)
        assert "adx" in result.details
        assert "vol_20d" in result.details

    def test_adx_computation(self):
        """ADX 계산 기본 검증"""
        ohlcv = _make_ohlcv(n=200, trend=0.002)
        close = ohlcv["close"].astype(float)
        high = ohlcv["high"].astype(float)
        low = ohlcv["low"].astype(float)

        adx = self.detector._compute_adx(high, low, close)
        assert 0 <= adx <= 100

    def test_adx_insufficient_data(self):
        """ADX 데이터 부족 시 0 반환"""
        short = _make_ohlcv(n=10)
        adx = self.detector._compute_adx(short["high"], short["low"], short["close"])
        assert adx == 0.0


# ══════════════════════════════════════
# 2. DynamicThreshold 테스트
# ══════════════════════════════════════
class TestDynamicThreshold:
    """동적 임계값 산출 테스트"""

    def setup_method(self):
        self.threshold = DynamicThreshold()

    def test_trending_up_lower_threshold(self):
        """추세장에서는 임계값이 낮아야 함"""
        regime = RegimeInfo(
            regime=MarketRegime.TRENDING_UP,
            confidence=0.8,
            volatility_percentile=0.5,
            trend_strength=0.6,
            details={},
        )
        buy_t, sell_t = self.threshold.compute(regime)
        assert buy_t < 0.30  # 기본값보다 낮아야 함

    def test_high_vol_higher_threshold(self):
        """고변동장에서는 임계값이 높아야 함"""
        regime = RegimeInfo(
            regime=MarketRegime.HIGH_VOLATILITY,
            confidence=0.8,
            volatility_percentile=0.85,
            trend_strength=0.0,
            details={},
        )
        buy_t, sell_t = self.threshold.compute(regime)
        assert buy_t > 0.30  # 기본값보다 높아야 함

    def test_sideways_near_default(self):
        """횡보장에서는 기본값 근처"""
        regime = RegimeInfo(
            regime=MarketRegime.SIDEWAYS,
            confidence=0.8,
            volatility_percentile=0.5,
            trend_strength=0.0,
            details={},
        )
        buy_t, sell_t = self.threshold.compute(regime)
        assert 0.20 <= buy_t <= 0.40

    def test_threshold_bounds(self):
        """임계값은 0.10~0.50 범위 내"""
        for regime_val in MarketRegime:
            regime = RegimeInfo(
                regime=regime_val,
                confidence=1.0,
                volatility_percentile=0.99,
                trend_strength=0.5,
                details={},
            )
            buy_t, sell_t = self.threshold.compute(regime)
            assert 0.10 <= buy_t <= 0.50
            assert 0.10 <= sell_t <= 0.50

    def test_low_confidence_blends_to_default(self):
        """레짐 확신이 낮으면 기본값(0.3)에 수렴"""
        regime = RegimeInfo(
            regime=MarketRegime.TRENDING_UP,
            confidence=0.1,  # 매우 낮은 확신
            volatility_percentile=0.5,
            trend_strength=0.0,
            details={},
        )
        buy_t, _ = self.threshold.compute(regime)
        # 확신 낮으면 0.30에 가까워야 함
        assert abs(buy_t - 0.30) < 0.10

    def test_classify_action(self):
        """액션 분류 테스트"""
        regime = RegimeInfo(
            regime=MarketRegime.SIDEWAYS,
            confidence=0.8,
            volatility_percentile=0.5,
            trend_strength=0.0,
            details={},
        )
        assert self.threshold.classify_action(0.5, regime) == "BUY"
        assert self.threshold.classify_action(-0.5, regime) == "SELL"
        assert self.threshold.classify_action(0.0, regime) == "HOLD"


# ══════════════════════════════════════
# 3. ConfidenceCalibrator 테스트
# ══════════════════════════════════════
class TestConfidenceCalibrator:
    """신뢰도 캘리브레이션 테스트"""

    def setup_method(self):
        self.calibrator = ConfidenceCalibrator()

    def test_empty_signals_halved(self):
        """시그널 없으면 confidence 절반"""
        result = self.calibrator.calibrate(0.8, {})
        assert result <= 0.4

    def test_agreement_preserves_confidence(self):
        """전략 방향 일치 시 confidence 유지 (상대적으로)"""
        signals_agree = {"FACTOR": 0.6, "TREND": 0.5, "SENTIMENT": 0.4}
        signals_disagree = {"FACTOR": 0.6, "TREND": -0.5, "SENTIMENT": 0.4}

        conf_agree = self.calibrator.calibrate(0.8, signals_agree)
        conf_disagree = self.calibrator.calibrate(0.8, signals_disagree)

        assert conf_agree > conf_disagree

    def test_high_volatility_penalty(self):
        """고변동 레짐에서 confidence 감소"""
        signals = {"FACTOR": 0.5, "TREND": 0.4, "RISK_PARITY": 0.3}

        normal_regime = RegimeInfo(
            regime=MarketRegime.SIDEWAYS,
            confidence=0.7,
            volatility_percentile=0.3,
            trend_strength=0.0,
            details={},
        )
        high_vol_regime = RegimeInfo(
            regime=MarketRegime.HIGH_VOLATILITY,
            confidence=0.8,
            volatility_percentile=0.9,
            trend_strength=0.0,
            details={},
        )

        conf_normal = self.calibrator.calibrate(0.8, signals, normal_regime)
        conf_high_vol = self.calibrator.calibrate(0.8, signals, high_vol_regime)

        assert conf_high_vol < conf_normal

    def test_extreme_confidence_capped(self):
        """극단 confidence는 0.95 이하로 캡"""
        signals = {"FACTOR": 0.9, "TREND": 0.8, "RISK_PARITY": 0.7}
        result = self.calibrator.calibrate(0.99, signals)
        assert result <= 0.95

    def test_few_strategies_penalty(self):
        """전략 수가 적으면 confidence 하향"""
        one_signal = {"FACTOR": 0.7}
        three_signals = {"FACTOR": 0.7, "TREND": 0.6, "RISK_PARITY": 0.5}

        conf_one = self.calibrator.calibrate(0.8, one_signal)
        conf_three = self.calibrator.calibrate(0.8, three_signals)

        assert conf_one < conf_three

    def test_calibrated_always_in_range(self):
        """보정 결과는 항상 0~1 범위"""
        for raw in [0.0, 0.3, 0.5, 0.8, 1.0]:
            result = self.calibrator.calibrate(raw, {"A": 0.5, "B": -0.3, "C": 0.1})
            assert 0.0 <= result <= 1.0

    def test_disagreement_computation(self):
        """불일치도 계산 검증"""
        # 완전 일치
        assert self.calibrator._compute_disagreement([0.5, 0.3, 0.7]) == 0.0
        # 부분 불일치
        d = self.calibrator._compute_disagreement([0.5, -0.3, 0.7])
        assert 0 < d < 1
        # 완전 불일치
        d_half = self.calibrator._compute_disagreement([0.5, -0.5])
        assert d_half == 1.0  # 50/50 → minority=1/2 → *2 = 1.0


# ══════════════════════════════════════
# 4. RegimeWeightRouter 테스트
# ══════════════════════════════════════
class TestRegimeWeightRouter:
    """레짐별 가중치 라우팅 테스트"""

    def setup_method(self):
        self.router = RegimeWeightRouter()
        self.base_weights = {
            "FACTOR": 0.25,
            "MEAN_REVERSION": 0.10,
            "TREND_FOLLOWING": 0.20,
            "RISK_PARITY": 0.20,
            "ML_SIGNAL": 0.00,
            "SENTIMENT": 0.25,
        }

    def test_weights_sum_to_one(self):
        """조정 후 가중치 합계 ≈ 1.0"""
        for regime_val in MarketRegime:
            regime = RegimeInfo(
                regime=regime_val,
                confidence=0.8,
                volatility_percentile=0.5,
                trend_strength=0.0,
                details={},
            )
            adjusted = self.router.adjust_weights(self.base_weights, regime)
            active_sum = sum(v for v in adjusted.values() if v > 0)
            assert abs(active_sum - 1.0) < 0.02

    def test_inactive_stays_zero(self):
        """비활성 전략(ML_SIGNAL)은 0 유지"""
        regime = RegimeInfo(
            regime=MarketRegime.TRENDING_UP,
            confidence=0.9,
            volatility_percentile=0.5,
            trend_strength=0.5,
            details={},
        )
        adjusted = self.router.adjust_weights(self.base_weights, regime)
        assert adjusted.get("ML_SIGNAL", 0) == 0.0

    def test_trending_boosts_trend_following(self):
        """추세장에서 TREND_FOLLOWING 가중치 증가"""
        regime = RegimeInfo(
            regime=MarketRegime.TRENDING_UP,
            confidence=0.9,
            volatility_percentile=0.5,
            trend_strength=0.6,
            details={},
        )
        adjusted = self.router.adjust_weights(self.base_weights, regime)

        # TREND_FOLLOWING 비중이 원본보다 커야 함
        assert adjusted["TREND_FOLLOWING"] > self.base_weights["TREND_FOLLOWING"]

    def test_sideways_boosts_mean_reversion(self):
        """횡보장에서 MEAN_REVERSION 가중치 증가"""
        regime = RegimeInfo(
            regime=MarketRegime.SIDEWAYS,
            confidence=0.9,
            volatility_percentile=0.5,
            trend_strength=0.0,
            details={},
        )
        adjusted = self.router.adjust_weights(self.base_weights, regime)
        assert adjusted["MEAN_REVERSION"] > self.base_weights["MEAN_REVERSION"]

    def test_high_vol_boosts_risk_parity(self):
        """고변동장에서 RISK_PARITY 가중치 증가"""
        regime = RegimeInfo(
            regime=MarketRegime.HIGH_VOLATILITY,
            confidence=0.9,
            volatility_percentile=0.9,
            trend_strength=0.0,
            details={},
        )
        adjusted = self.router.adjust_weights(self.base_weights, regime)
        assert adjusted["RISK_PARITY"] > self.base_weights["RISK_PARITY"]

    def test_low_confidence_minimal_change(self):
        """레짐 확신 낮으면 가중치 변화 최소"""
        regime = RegimeInfo(
            regime=MarketRegime.TRENDING_UP,
            confidence=0.1,  # 거의 확신 없음
            volatility_percentile=0.5,
            trend_strength=0.0,
            details={},
        )
        adjusted = self.router.adjust_weights(self.base_weights, regime)

        # 변화가 거의 없어야 함
        for key in self.base_weights:
            if self.base_weights[key] > 0:
                diff = abs(adjusted.get(key, 0) - self.base_weights[key])
                assert diff < 0.05  # 5%p 이내

    def test_minimum_weight_guarantee(self):
        """활성 전략은 최소 3% 보장"""
        regime = RegimeInfo(
            regime=MarketRegime.HIGH_VOLATILITY,
            confidence=1.0,
            volatility_percentile=0.95,
            trend_strength=0.0,
            details={},
        )
        adjusted = self.router.adjust_weights(self.base_weights, regime)

        for key, val in adjusted.items():
            if self.base_weights.get(key, 0) > 0:
                assert val >= 0.03  # 최소 3%


# ══════════════════════════════════════
# 5. 통합 시나리오 테스트
# ══════════════════════════════════════
class TestIntegrationScenarios:
    """레짐 감지 → 임계값 → 캘리브레이션 통합 흐름"""

    def test_full_pipeline_uptrend(self):
        """상승 추세: 감지 → 낮은 임계값 → 적절한 confidence"""
        detector = MarketRegimeDetector()
        threshold = DynamicThreshold()
        calibrator = ConfidenceCalibrator()

        ohlcv = _make_ohlcv(n=200, trend=0.003, volatility=0.01)
        regime = detector.detect(ohlcv)

        buy_t, sell_t = threshold.compute(regime)
        # 추세장이면 임계값이 낮거나 적당해야
        assert buy_t <= 0.35

        signals = {"FACTOR": 0.6, "TREND": 0.7, "SENTIMENT": 0.5}
        calibrated = calibrator.calibrate(0.8, signals, regime)
        assert 0 < calibrated < 1

    def test_full_pipeline_high_vol(self):
        """고변동: 높은 임계값 + 낮은 confidence"""
        detector = MarketRegimeDetector()
        threshold = DynamicThreshold()
        calibrator = ConfidenceCalibrator()

        ohlcv = _make_ohlcv(n=200, trend=0.0, volatility=0.05)
        regime = detector.detect(ohlcv)

        buy_t, _ = threshold.compute(regime)

        signals = {"FACTOR": 0.3, "TREND": -0.2, "SENTIMENT": 0.4}
        calibrated = calibrator.calibrate(0.8, signals, regime)

        # 고변동 + 불일치 시그널 → confidence 크게 하락
        assert calibrated < 0.8

    def test_regime_action_vs_fixed(self):
        """동적 vs 고정 임계값 비교: 추세장에서 동적이 더 민감해야"""
        threshold = DynamicThreshold()

        trending_regime = RegimeInfo(
            regime=MarketRegime.TRENDING_UP,
            confidence=0.9,
            volatility_percentile=0.4,
            trend_strength=0.7,
            details={},
        )

        # 시그널 0.25: 고정 0.3으로는 HOLD, 동적이면 BUY 가능
        signal = 0.25
        dynamic_action = threshold.classify_action(signal, trending_regime)
        fixed_action = "BUY" if signal > 0.3 else "HOLD"

        # 동적이 BUY이고 고정이 HOLD → 동적이 더 민감
        assert fixed_action == "HOLD"
        assert dynamic_action == "BUY"
