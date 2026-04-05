"""
DynamicEnsembleRunner 유닛테스트

벡터화 시그널 생성 → 동적 앙상블 계산까지의 전체 흐름을 검증.

테스트 범위:
- run_with_ohlcv() 정상 동작
- RunnerResult 구조 검증
- 데이터 부족 시 에러
- DB 미설정 시 에러
- 배치 실행 (pipeline 통합)
"""

import numpy as np
import pandas as pd
import pytest

from core.strategy_ensemble.dynamic_ensemble import DynamicRegime
from core.strategy_ensemble.runner import DynamicEnsembleRunner, RunnerResult


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


class TestDynamicEnsembleRunner:
    """DynamicEnsembleRunner 기본 동작 테스트"""

    def test_run_with_ohlcv_returns_result(self, sample_ohlcv):
        """run_with_ohlcv()가 RunnerResult를 반환하는지"""
        runner = DynamicEnsembleRunner()
        result = runner.run_with_ohlcv(sample_ohlcv, ticker="TEST")

        assert isinstance(result, RunnerResult)
        assert result.ticker == "TEST"
        assert result.country == "KR"
        assert result.ohlcv_days == len(sample_ohlcv)

    def test_result_has_ensemble_data(self, sample_ohlcv):
        """결과에 앙상블 데이터가 포함되어 있는지"""
        runner = DynamicEnsembleRunner()
        result = runner.run_with_ohlcv(sample_ohlcv, ticker="TEST")

        assert isinstance(result.ensemble_signal, float)
        assert result.regime in DynamicRegime
        assert set(result.weights.keys()) == {"MR", "TF", "RP"}

    def test_result_has_signal_series(self, sample_ohlcv):
        """결과에 시그널 시계열이 포함되어 있는지"""
        runner = DynamicEnsembleRunner()
        result = runner.run_with_ohlcv(sample_ohlcv, ticker="TEST")

        assert set(result.signals.keys()) == {
            "MEAN_REVERSION",
            "TREND_FOLLOWING",
            "RISK_PARITY",
        }
        for name, sig in result.signals.items():
            assert len(sig) == len(sample_ohlcv), f"{name} 길이 불일치"

    def test_to_summary_dict(self, sample_ohlcv):
        """to_summary_dict()가 올바른 딕셔너리를 반환하는지"""
        runner = DynamicEnsembleRunner()
        result = runner.run_with_ohlcv(sample_ohlcv, ticker="005930", country="KR")
        summary = result.to_summary_dict()

        assert summary["ticker"] == "005930"
        assert summary["country"] == "KR"
        assert "ensemble_signal" in summary
        assert "regime" in summary
        assert "weights" in summary
        assert "adx" in summary
        assert "vol_scalar" in summary

    def test_insufficient_data_raises(self):
        """데이터 부족 시 ValueError 발생"""
        np.random.seed(42)
        n = 50  # 최소 200일 미만
        dates = pd.date_range("2023-01-01", periods=n, freq="B")
        close = 50000 + np.cumsum(np.random.randn(n) * 500)
        ohlcv = pd.DataFrame(
            {
                "open": close - 100,
                "high": close + 100,
                "low": close - 100,
                "close": close,
                "volume": np.full(n, 500000.0),
            },
            index=dates,
        )

        runner = DynamicEnsembleRunner()
        with pytest.raises(ValueError, match="데이터 부족"):
            runner.run_with_ohlcv(ohlcv, ticker="SHORT")

    @pytest.mark.asyncio
    async def test_run_without_db_raises(self):
        """DB 세션 없이 run() 호출 시 ValueError 발생"""
        runner = DynamicEnsembleRunner(db_session=None)
        with pytest.raises(ValueError, match="DB session"):
            await runner.run("005930", country="KR")

    def test_weights_sum_to_one(self, sample_ohlcv):
        """가중치 합이 1인지"""
        runner = DynamicEnsembleRunner()
        result = runner.run_with_ohlcv(sample_ohlcv, ticker="TEST")

        weight_sum = sum(result.weights.values())
        assert abs(weight_sum - 1.0) < 1e-6

    def test_vol_scalar_bounded(self, sample_ohlcv):
        """변동성 스칼라가 1.0 이하인지"""
        runner = DynamicEnsembleRunner()
        result = runner.run_with_ohlcv(sample_ohlcv, ticker="TEST")

        assert result.ensemble.vol_scalar <= 1.0

    def test_end_to_end_consistency_with_backtest(self, sample_ohlcv):
        """
        Runner의 전체 파이프라인이 backtest와 근사한 결과를 내는지
        (VectorizedSignalGenerator → DynamicEnsembleService 경로)

        NOTE: 정확히 동일하지 않은 이유:
        - 백테스트: round(ensemble(raw_signals), 4)  ← 앙상블 결과를 반올림
        - Runner: ensemble(round(signals, 4))  ← 시그널을 먼저 반올림 후 앙상블
        반올림 순서 차이로 1e-3 수준의 수치 오차 발생.
        알고리즘 자체는 동일하며, 개별 단계 일치는 별도 테스트로 검증됨:
        - test_vectorized_signal_gen.py: 시그널 시계열 일치 확인
        - test_dynamic_ensemble.py: 앙상블 알고리즘 일치 확인
        """
        import os
        import sys

        sys.path.insert(
            0,
            os.path.join(os.path.dirname(__file__), "..", "..", "scripts"),
        )
        from run_backtest import generate_strategy_signals_vectorized

        # 백테스트 경로: generate_strategy_signals_vectorized (ENSEMBLE 포함)
        bt_signals = generate_strategy_signals_vectorized("TEST", sample_ohlcv)
        bt_ensemble = bt_signals["ENSEMBLE"]

        # Runner 경로: VectorizedSignalGenerator → DynamicEnsembleService
        runner = DynamicEnsembleRunner()
        result = runner.run_with_ohlcv(sample_ohlcv, ticker="TEST")

        # min_window(60) 이후 구간에서 앙상블 값이 근사해야 함
        active_bt = bt_ensemble.iloc[60:]
        active_runner = result.ensemble.ensemble_series.iloc[60:]

        # 반올림 순서 차이로 인한 수치 오차 허용 (atol=2e-3)
        pd.testing.assert_series_equal(
            active_bt,
            active_runner,
            check_names=False,
            atol=2e-3,
            obj="backtest vs runner ensemble",
        )
