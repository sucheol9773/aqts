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
        # regime은 문자열로 직렬화되어야 함 (DynamicRegime.value)
        assert isinstance(summary["regime"], str)
        assert summary["regime"] in ["TRENDING_UP", "TRENDING_DOWN", "HIGH_VOLATILITY", "SIDEWAYS"]
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

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_sql_uses_any_not_in(self):
        """
        _fetch_ohlcv SQL 쿼리가 asyncpg 호환 문법(= ANY)을 사용하는지 검증.

        회귀 방지: market IN :markets → asyncpg에서 syntax error 발생 (2026-04-11 발견).
        수정: market = ANY(:markets) + list 바인딩.
        """
        from unittest.mock import AsyncMock, MagicMock

        from sqlalchemy.ext.asyncio import AsyncSession

        mock_session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            (
                pd.Timestamp("2025-01-02"),
                50000.0,
                51000.0,
                49000.0,
                50500.0,
                1000000.0,
            ),
        ] * 200

        mock_session.execute = AsyncMock(return_value=mock_result)

        runner = DynamicEnsembleRunner(db_session=mock_session)

        # _fetch_ohlcv 호출 — SQL 구문이 asyncpg 호환인지 확인
        await runner._fetch_ohlcv("005930", "KR", 300)

        # execute에 전달된 SQL 확인
        call_args = mock_session.execute.call_args
        sql_text = str(call_args[0][0])
        params = call_args[0][1]

        # IN :markets 가 아니라 = ANY(:markets) 여야 함
        assert "IN :markets" not in sql_text, (
            "asyncpg 비호환: 'IN :markets' 는 tuple 바인딩 불가. " "'= ANY(:markets)' 를 사용해야 합니다."
        )
        assert "= ANY(:markets)" in sql_text

        # markets 파라미터가 list 타입이어야 asyncpg에서 ARRAY로 변환됨
        assert isinstance(
            params["markets"], list
        ), f"markets 파라미터는 list여야 합니다 (실제: {type(params['markets']).__name__})"
        assert params["markets"] == ["KRX"]

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_us_market_filter(self):
        """US 종목의 market 필터가 NASDAQ/NYSE/AMEX를 포함하는지 확인"""
        from unittest.mock import AsyncMock, MagicMock

        from sqlalchemy.ext.asyncio import AsyncSession

        mock_session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            (
                pd.Timestamp("2025-01-02"),
                150.0,
                155.0,
                148.0,
                152.0,
                5000000.0,
            ),
        ] * 200

        mock_session.execute = AsyncMock(return_value=mock_result)

        runner = DynamicEnsembleRunner(db_session=mock_session)
        await runner._fetch_ohlcv("AAPL", "US", 300)

        params = mock_session.execute.call_args[0][1]
        assert params["markets"] == ["NASDAQ", "NYSE", "AMEX"]

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
