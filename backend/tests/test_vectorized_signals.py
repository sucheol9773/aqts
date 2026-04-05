"""
벡터화 시그널 생성 + multiprocessing 병렬화 테스트

generate_strategy_signals_vectorized() 및 _generate_signals_worker()의
정합성과 multiprocessing 호환성을 검증한다.
"""

import multiprocessing as mp
import os
import sys

import numpy as np
import pandas as pd
import pytest

# scripts/ 경로 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

from run_backtest import (
    _generate_signals_worker,
    generate_strategy_signals_vectorized,
)


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """100일짜리 샘플 OHLCV 데이터 생성"""
    np.random.seed(42)
    n = 100
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = 50000 + np.cumsum(np.random.randn(n) * 500)
    high = close + np.abs(np.random.randn(n) * 300)
    low = close - np.abs(np.random.randn(n) * 300)
    open_ = close + np.random.randn(n) * 100
    volume = np.random.randint(100000, 1000000, n).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


@pytest.fixture
def large_ohlcv() -> pd.DataFrame:
    """500일짜리 대형 OHLCV 데이터 (벡터화 성능 검증)"""
    np.random.seed(123)
    n = 500
    dates = pd.date_range("2022-01-01", periods=n, freq="B")
    close = 70000 + np.cumsum(np.random.randn(n) * 800)
    high = close + np.abs(np.random.randn(n) * 400)
    low = close - np.abs(np.random.randn(n) * 400)
    open_ = close + np.random.randn(n) * 200
    volume = np.random.randint(50000, 500000, n).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


class TestVectorizedSignals:
    """벡터화 시그널 생성 함수 테스트"""

    def test_returns_all_strategies(self, sample_ohlcv):
        """4개 전략 시그널이 모두 반환되는지 확인"""
        result = generate_strategy_signals_vectorized("005930", sample_ohlcv)
        assert set(result.keys()) == {
            "MEAN_REVERSION",
            "TREND_FOLLOWING",
            "RISK_PARITY",
            "ENSEMBLE",
        }

    def test_signal_length_matches_input(self, sample_ohlcv):
        """시그널 길이가 입력 데이터와 동일한지 확인"""
        result = generate_strategy_signals_vectorized("005930", sample_ohlcv)
        for name, sig in result.items():
            assert len(sig) == len(sample_ohlcv), f"{name} 길이 불일치"

    def test_signal_range(self, large_ohlcv):
        """시그널 값이 [-1, 1] 범위 내인지 확인"""
        result = generate_strategy_signals_vectorized("005930", large_ohlcv)
        for name, sig in result.items():
            assert sig.min() >= -1.0, f"{name} min={sig.min():.4f} < -1.0"
            assert sig.max() <= 1.0, f"{name} max={sig.max():.4f} > 1.0"

    def test_no_nan_values(self, large_ohlcv):
        """NaN이 없는지 확인"""
        result = generate_strategy_signals_vectorized("005930", large_ohlcv)
        for name, sig in result.items():
            assert not sig.isna().any(), f"{name}에 NaN 존재"

    def test_min_window_zeros(self, sample_ohlcv):
        """최소 윈도우(60일) 이전은 0인지 확인"""
        result = generate_strategy_signals_vectorized("005930", sample_ohlcv)
        for name, sig in result.items():
            assert (sig.iloc[:60] == 0.0).all(), f"{name} 처음 60일이 0이 아님"

    def test_ensemble_is_weighted_average(self, large_ohlcv):
        """ENSEMBLE = TF*0.4 + MR*0.3 + RP*0.3 인지 확인"""
        result = generate_strategy_signals_vectorized("005930", large_ohlcv)
        expected = (
            result["TREND_FOLLOWING"] * 0.4 + result["MEAN_REVERSION"] * 0.3 + result["RISK_PARITY"] * 0.3
        ).round(4)
        # 처음 60일은 모두 0이므로 그 이후만 비교
        pd.testing.assert_series_equal(
            result["ENSEMBLE"].iloc[60:],
            expected.iloc[60:],
            check_names=False,
            atol=1e-3,
        )

    def test_short_data_returns_zeros(self):
        """30일 미만 데이터에서도 에러 없이 0을 반환하는지 확인"""
        np.random.seed(99)
        n = 20
        dates = pd.date_range("2024-01-01", periods=n, freq="B")
        ohlcv = pd.DataFrame(
            {
                "open": np.random.randn(n) + 100,
                "high": np.random.randn(n) + 101,
                "low": np.random.randn(n) + 99,
                "close": np.random.randn(n) + 100,
                "volume": np.random.randint(1000, 10000, n).astype(float),
            },
            index=dates,
        )
        result = generate_strategy_signals_vectorized("TEST", ohlcv)
        for name, sig in result.items():
            assert (sig == 0.0).all(), f"{name}이 0이 아님 (짧은 데이터)"


class TestMultiprocessingWorker:
    """multiprocessing 워커 호환성 테스트"""

    def test_worker_returns_correct_format(self, sample_ohlcv):
        """워커가 (ticker, dict) 튜플을 반환하는지 확인"""
        ticker, signals = _generate_signals_worker(("005930", sample_ohlcv))
        assert ticker == "005930"
        assert isinstance(signals, dict)
        assert len(signals) == 4

    def test_pool_map_execution(self, sample_ohlcv):
        """실제 multiprocessing.Pool.map()으로 실행 시 에러 없는지 확인

        이 테스트는 별도 프로세스에서 import/pickle 문제를 잡는다.
        """
        items = [
            ("005930", sample_ohlcv.copy()),
            ("000660", sample_ohlcv.copy()),
        ]
        with mp.Pool(2) as pool:
            results = pool.map(_generate_signals_worker, items)

        assert len(results) == 2
        for ticker, signals in results:
            assert ticker in ("005930", "000660")
            assert set(signals.keys()) == {
                "MEAN_REVERSION",
                "TREND_FOLLOWING",
                "RISK_PARITY",
                "ENSEMBLE",
            }
            for name, sig in signals.items():
                assert len(sig) == len(sample_ohlcv)
                assert not sig.isna().any(), f"{ticker}/{name}에 NaN"

    def test_pool_map_multiple_tickers(self, large_ohlcv):
        """여러 종목 병렬 처리 결과가 순차 처리와 동일한지 확인"""
        tickers = ["A", "B", "C", "D"]
        items = [(t, large_ohlcv.copy()) for t in tickers]

        # 순차 처리
        sequential = {}
        for t, ohlcv in items:
            sequential[t] = generate_strategy_signals_vectorized(t, ohlcv)

        # 병렬 처리
        with mp.Pool(2) as pool:
            parallel_results = pool.map(_generate_signals_worker, items)
        parallel = {t: s for t, s in parallel_results}

        # 비교
        for t in tickers:
            for strategy in ["MEAN_REVERSION", "TREND_FOLLOWING", "RISK_PARITY", "ENSEMBLE"]:
                pd.testing.assert_series_equal(
                    sequential[t][strategy],
                    parallel[t][strategy],
                    check_names=False,
                    obj=f"{t}/{strategy}",
                )
