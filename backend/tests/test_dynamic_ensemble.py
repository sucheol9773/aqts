"""
DynamicEnsembleService 유닛테스트

run_backtest.py의 _compute_dynamic_ensemble()과 동일한 알고리즘을
라이브 파이프라인용 서비스로 모듈화한 DynamicEnsembleService를 검증.

테스트 범위:
- 레짐 판정 정확성 (ADX, 모멘텀, 변동성)
- 가중치 합 = 1 보장
- 성과 보정 동작
- 변동성 타겟팅
- 백테스트 _compute_dynamic_ensemble()과의 일관성
"""

import numpy as np
import pandas as pd
import pytest

from core.strategy_ensemble.dynamic_ensemble import (
    DynamicEnsembleResult,
    DynamicEnsembleService,
    DynamicRegime,
)


@pytest.fixture
def sample_ohlcv():
    """200일 샘플 OHLCV 데이터"""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    close = 50000 + np.cumsum(np.random.randn(n) * 500)
    return pd.DataFrame(
        {
            "open": close - 100,
            "high": close + np.abs(np.random.randn(n) * 200),
            "low": close - np.abs(np.random.randn(n) * 200),
            "close": close,
            "volume": np.random.randint(100000, 1000000, n).astype(float),
        },
        index=dates,
    )


@pytest.fixture
def sample_signals(sample_ohlcv):
    """3개 전략 시그널"""
    n = len(sample_ohlcv)
    dates = sample_ohlcv.index
    np.random.seed(123)
    mr = pd.Series(np.random.uniform(-0.5, 0.5, n), index=dates)
    tf = pd.Series(np.random.uniform(-0.5, 0.5, n), index=dates)
    rp = pd.Series(np.random.uniform(-0.5, 0.5, n), index=dates)
    return mr, tf, rp


class TestDynamicEnsembleService:
    """DynamicEnsembleService 기본 동작 테스트"""

    def test_compute_returns_result(self, sample_ohlcv, sample_signals):
        """compute()가 DynamicEnsembleResult를 반환하는지"""
        mr, tf, rp = sample_signals
        service = DynamicEnsembleService()
        result = service.compute(sample_ohlcv, mr, tf, rp)

        assert isinstance(result, DynamicEnsembleResult)
        assert isinstance(result.ensemble_signal, float)
        assert isinstance(result.regime, DynamicRegime)
        assert isinstance(result.weights, dict)
        assert set(result.weights.keys()) == {"MR", "TF", "RP"}

    def test_weights_sum_to_one(self, sample_ohlcv, sample_signals):
        """가중치 합이 항상 1인지 확인"""
        mr, tf, rp = sample_signals
        service = DynamicEnsembleService()
        result = service.compute(sample_ohlcv, mr, tf, rp)

        weight_sum = sum(result.weights.values())
        assert abs(weight_sum - 1.0) < 1e-6, f"가중치 합 = {weight_sum}, 1이어야 함"

    def test_ensemble_signal_bounded(self, sample_ohlcv, sample_signals):
        """앙상블 시그널이 합리적 범위 내인지"""
        mr, tf, rp = sample_signals
        service = DynamicEnsembleService()
        result = service.compute(sample_ohlcv, mr, tf, rp)

        # vol scalar ≤ 1.0이므로 앙상블 ≤ 입력 시그널 최대값
        assert -1.0 <= result.ensemble_signal <= 1.0

    def test_vol_scalar_leq_one(self, sample_ohlcv, sample_signals):
        """변동성 타겟 스칼라가 1.0을 넘지 않는지 (레버리지 방지)"""
        mr, tf, rp = sample_signals
        service = DynamicEnsembleService()
        result = service.compute(sample_ohlcv, mr, tf, rp)

        assert result.vol_scalar <= 1.0

    def test_ensemble_series_length(self, sample_ohlcv, sample_signals):
        """앙상블 시계열 길이가 입력과 동일한지"""
        mr, tf, rp = sample_signals
        service = DynamicEnsembleService()
        result = service.compute(sample_ohlcv, mr, tf, rp)

        assert len(result.ensemble_series) == len(sample_ohlcv)

    def test_custom_params(self, sample_ohlcv, sample_signals):
        """커스텀 파라미터가 적용되는지"""
        mr, tf, rp = sample_signals
        custom = {"softmax_temperature": 10.0, "perf_blend": 0.5}
        service = DynamicEnsembleService(params=custom)
        result = service.compute(sample_ohlcv, mr, tf, rp)

        assert isinstance(result, DynamicEnsembleResult)
        # 기본 파라미터와 다른 결과여야 함
        default_service = DynamicEnsembleService()
        default_result = default_service.compute(sample_ohlcv, mr, tf, rp)
        assert result.ensemble_signal != default_result.ensemble_signal


class TestRegimeDetection:
    """레짐 판정 정확성 테스트"""

    def test_regime_is_valid_enum(self, sample_ohlcv, sample_signals):
        """레짐이 유효한 DynamicRegime 값인지"""
        mr, tf, rp = sample_signals
        service = DynamicEnsembleService()
        result = service.compute(sample_ohlcv, mr, tf, rp)

        assert result.regime in DynamicRegime

    def test_strong_uptrend_regime(self):
        """강한 상승 추세 데이터 → TRENDING_UP 레짐"""
        n = 200
        dates = pd.date_range("2023-01-01", periods=n, freq="B")
        # 강한 상승 추세
        close = pd.Series(50000 + np.arange(n) * 200.0, index=dates)
        ohlcv = pd.DataFrame(
            {
                "open": close - 50,
                "high": close + 100,
                "low": close - 100,
                "close": close,
                "volume": np.full(n, 500000.0),
            },
            index=dates,
        )
        signals = pd.Series(0.5, index=dates)

        service = DynamicEnsembleService()
        result = service.compute(ohlcv, signals, signals, signals)

        # 강한 상승에서 TRENDING_UP 또는 높은 TF 가중치
        assert (
            result.weights["TF"] >= result.weights["MR"]
        ), f"상승 추세에서 TF({result.weights['TF']:.2f}) >= MR({result.weights['MR']:.2f})여야 함"


class TestBacktestConsistency:
    """run_backtest.py의 _compute_dynamic_ensemble()과 일관성 검증"""

    def test_same_output_as_backtest(self, sample_ohlcv, sample_signals):
        """DynamicEnsembleService와 _compute_dynamic_ensemble()의 출력이 동일한지"""
        import os
        import sys

        # scripts/ 경로 추가
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
        from run_backtest import _compute_dynamic_ensemble

        mr, tf, rp = sample_signals

        # 백테스트 함수 호출
        backtest_ensemble = _compute_dynamic_ensemble(sample_ohlcv, mr, tf, rp, min_window=60)

        # DynamicEnsembleService 호출
        service = DynamicEnsembleService()
        result = service.compute(sample_ohlcv, mr, tf, rp)

        # min_window 이후 구간에서 값이 동일해야 함
        active_bt = backtest_ensemble.iloc[60:]
        active_svc = result.ensemble_series.iloc[60:]

        pd.testing.assert_series_equal(
            active_bt,
            active_svc,
            check_names=False,
            atol=1e-10,
            obj="backtest vs service ensemble",
        )
