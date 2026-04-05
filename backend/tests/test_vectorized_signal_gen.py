"""
VectorizedSignalGenerator 유닛테스트

벡터화 시그널 생성기가 run_backtest.py의
generate_strategy_signals_vectorized()와 동일한 출력을 내는지 검증.

테스트 범위:
- 3개 전략 시그널 시계열 생성
- 시그널 값 범위 (-1 ~ +1)
- 최소 윈도우 이전 0 채움
- run_backtest.py 함수와의 일관성
"""

import numpy as np
import pandas as pd
import pytest

from core.quant_engine.vectorized_signals import VectorizedSignalGenerator


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


class TestVectorizedSignalGenerator:
    """VectorizedSignalGenerator 기본 동작 테스트"""

    def test_generate_returns_three_strategies(self, sample_ohlcv):
        """3개 전략 시그널이 모두 반환되는지"""
        gen = VectorizedSignalGenerator()
        signals = gen.generate(sample_ohlcv)

        assert set(signals.keys()) == {
            "MEAN_REVERSION",
            "TREND_FOLLOWING",
            "RISK_PARITY",
        }

    def test_signal_length_matches_input(self, sample_ohlcv):
        """시그널 길이가 OHLCV와 동일한지"""
        gen = VectorizedSignalGenerator()
        signals = gen.generate(sample_ohlcv)

        for name, sig in signals.items():
            assert len(sig) == len(sample_ohlcv), f"{name}: 길이 {len(sig)} != {len(sample_ohlcv)}"

    def test_signal_values_bounded(self, sample_ohlcv):
        """시그널 값이 [-1, 1] 범위 내인지"""
        gen = VectorizedSignalGenerator()
        signals = gen.generate(sample_ohlcv)

        for name, sig in signals.items():
            assert sig.min() >= -1.0, f"{name} min={sig.min()}"
            assert sig.max() <= 1.0, f"{name} max={sig.max()}"

    def test_min_window_zeros(self, sample_ohlcv):
        """최소 윈도우 이전 구간이 0인지"""
        min_window = 60
        gen = VectorizedSignalGenerator(min_window=min_window)
        signals = gen.generate(sample_ohlcv)

        for name, sig in signals.items():
            pre_window = sig.iloc[:min_window]
            assert (pre_window == 0.0).all(), f"{name}: min_window 이전 구간에 0이 아닌 값 존재"

    def test_no_nan_values(self, sample_ohlcv):
        """NaN이 없는지"""
        gen = VectorizedSignalGenerator()
        signals = gen.generate(sample_ohlcv)

        for name, sig in signals.items():
            assert not sig.isna().any(), f"{name}: NaN 존재"

    def test_consistency_with_backtest(self, sample_ohlcv):
        """run_backtest.py의 generate_strategy_signals_vectorized()와 동일한 출력"""
        import os
        import sys

        sys.path.insert(
            0,
            os.path.join(os.path.dirname(__file__), "..", "..", "scripts"),
        )
        from run_backtest import generate_strategy_signals_vectorized

        # 백테스트 함수 호출
        bt_signals = generate_strategy_signals_vectorized("TEST", sample_ohlcv)

        # VectorizedSignalGenerator 호출
        gen = VectorizedSignalGenerator(min_window=60)
        live_signals = gen.generate(sample_ohlcv)

        # MR, TF, RP 시그널이 동일해야 함
        for strategy in ["MEAN_REVERSION", "TREND_FOLLOWING", "RISK_PARITY"]:
            pd.testing.assert_series_equal(
                bt_signals[strategy],
                live_signals[strategy],
                check_names=False,
                atol=1e-10,
                obj=f"backtest vs live {strategy}",
            )
