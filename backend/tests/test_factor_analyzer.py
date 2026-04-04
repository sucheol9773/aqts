"""
팩터 분석기 유닛테스트

테스트 대상: core/quant_engine/factor_analyzer.py
목표 커버리지: 85% (Quant Engine 모듈)

테스트 범위:
- 각 팩터 계산 정확성 (Value, Momentum, Quality, LowVol, Size)
- Z-Score 정규화 경계값 처리 (NaN, 0, 동일값, 극단값)
- 복합 점수 계산 (프로필별 가중치 반영)
- 빈 데이터/최소 데이터 처리
"""

import numpy as np
import pandas as pd

from config.constants import RiskProfile
from core.quant_engine.factor_analyzer import (
    FactorAnalyzer,
    _scale_to_percentile,
    _zscore_series,
)


class TestZScoreSeries:
    """Z-Score 정규화 함수 테스트"""

    def test_normal_distribution(self):
        """정상 분포 데이터"""
        values = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
        result = _zscore_series(values)
        assert len(result) == 5
        # 평균은 0에 가까워야 함
        assert abs(np.mean(result)) < 1e-10
        # 가운데 값은 0에 가까움
        assert abs(result[2]) < 0.1

    def test_all_same_values(self):
        """모든 값이 동일한 경우 전체 0 반환"""
        values = np.array([5.0, 5.0, 5.0, 5.0])
        result = _zscore_series(values)
        assert np.all(result == 0.0)

    def test_with_nan_values(self):
        """NaN이 포함된 경우 NaN 위치는 0으로 대체"""
        values = np.array([10.0, np.nan, 30.0, 40.0, np.nan])
        result = _zscore_series(values)
        assert result[1] == 0.0
        assert result[4] == 0.0
        assert not np.isnan(result).any()

    def test_single_value(self):
        """단일 값인 경우 0 반환"""
        values = np.array([42.0])
        result = _zscore_series(values)
        assert result[0] == 0.0

    def test_empty_array(self):
        """빈 배열"""
        values = np.array([])
        result = _zscore_series(values)
        assert len(result) == 0

    def test_winsorization_at_3sigma(self):
        """±3 시그마 윈저라이징"""
        values = np.array([1.0, 2.0, 3.0, 4.0, 100.0])
        result = _zscore_series(values)
        assert np.max(result) <= 3.0
        assert np.min(result) >= -3.0

    def test_all_nan(self):
        """모든 값이 NaN"""
        values = np.array([np.nan, np.nan, np.nan])
        result = _zscore_series(values)
        assert np.all(result == 0.0)


class TestScaleToPercentile:
    """백분위 스케일 변환 테스트"""

    def test_basic_ranking(self):
        """기본 순위 기반 백분위"""
        values = np.array([10.0, 30.0, 20.0, 40.0, 50.0])
        result = _scale_to_percentile(values)
        assert result.min() == 0.0
        assert result.max() == 100.0
        # 50.0이 최대값이므로 100 백분위
        assert result[4] == 100.0

    def test_single_value(self):
        """단일 값은 50"""
        values = np.array([42.0])
        result = _scale_to_percentile(values)
        assert result[0] == 50.0

    def test_empty_array(self):
        """빈 배열"""
        values = np.array([])
        result = _scale_to_percentile(values)
        assert len(result) == 0


class TestFactorAnalyzerValueFactor:
    """가치(Value) 팩터 테스트"""

    def test_low_per_gets_high_score(self):
        """낮은 PER → 높은 가치 점수"""
        df = pd.DataFrame(
            {
                "ticker": ["A", "B", "C"],
                "per": [5.0, 15.0, 30.0],
                "pbr": [0.5, 1.0, 3.0],
                "ev_ebitda": [3.0, 8.0, 20.0],
            }
        )
        result = FactorAnalyzer.calc_value_factor(df)
        # A가 가장 저평가이므로 가장 높은 점수
        assert result.iloc[0] > result.iloc[1]
        assert result.iloc[1] > result.iloc[2]

    def test_negative_per_excluded(self):
        """음수 PER (적자 기업)은 NaN 처리"""
        df = pd.DataFrame(
            {
                "ticker": ["A", "B", "C"],
                "per": [-10.0, 15.0, 30.0],
                "pbr": [1.0, 1.0, 1.0],
            }
        )
        result = FactorAnalyzer.calc_value_factor(df)
        # A의 PER은 NaN 처리되어 다른 지표(PBR)로만 계산
        assert len(result) == 3
        assert not np.isnan(result).any()

    def test_missing_columns(self):
        """일부 컬럼 누락 시 사용 가능한 컬럼으로만 계산"""
        df = pd.DataFrame(
            {
                "ticker": ["A", "B", "C"],
                "per": [5.0, 15.0, 30.0],
            }
        )
        result = FactorAnalyzer.calc_value_factor(df)
        assert len(result) == 3


class TestFactorAnalyzerMomentumFactor:
    """모멘텀(Momentum) 팩터 테스트"""

    def test_high_12m_low_1m_gets_high_score(self):
        """12M 수익률 높고 1M 수익률 낮은 종목 → 높은 모멘텀"""
        df = pd.DataFrame(
            {
                "ticker": ["A", "B", "C"],
                "return_12m": [0.50, 0.20, -0.10],
                "return_1m": [0.02, 0.05, 0.10],
            }
        )
        result = FactorAnalyzer.calc_momentum_factor(df)
        # A: 0.50-0.02=0.48, B: 0.20-0.05=0.15, C: -0.10-0.10=-0.20
        assert result.iloc[0] > result.iloc[1]
        assert result.iloc[1] > result.iloc[2]

    def test_missing_return_columns(self):
        """수익률 컬럼 누락 시 0 반환"""
        df = pd.DataFrame({"ticker": ["A", "B"]})
        result = FactorAnalyzer.calc_momentum_factor(df)
        assert np.all(result == 0.0)


class TestFactorAnalyzerCompositeScore:
    """복합 점수 계산 테스트"""

    def _make_test_df(self) -> pd.DataFrame:
        """테스트용 데이터프레임 생성"""
        np.random.seed(42)
        n = 20
        return pd.DataFrame(
            {
                "ticker": [f"STOCK_{i:03d}" for i in range(n)],
                "per": np.random.uniform(5, 50, n),
                "pbr": np.random.uniform(0.3, 5.0, n),
                "ev_ebitda": np.random.uniform(3, 30, n),
                "return_12m": np.random.uniform(-0.3, 0.6, n),
                "return_1m": np.random.uniform(-0.1, 0.15, n),
                "roe": np.random.uniform(0.02, 0.30, n),
                "roa": np.random.uniform(0.01, 0.15, n),
                "debt_ratio": np.random.uniform(0.1, 3.0, n),
                "volatility_60d": np.random.uniform(0.1, 0.5, n),
                "beta": np.random.uniform(0.5, 2.0, n),
                "market_cap": np.random.uniform(1e9, 1e12, n),
            }
        )

    def test_composite_score_range(self):
        """복합 점수는 0~100 범위"""
        df = self._make_test_df()
        analyzer = FactorAnalyzer(RiskProfile.BALANCED)
        result = analyzer.calculate_composite_scores(df)

        assert result["composite"].min() >= 0.0
        assert result["composite"].max() <= 100.0

    def test_all_factors_present(self):
        """모든 팩터 컬럼이 결과에 존재"""
        df = self._make_test_df()
        analyzer = FactorAnalyzer(RiskProfile.BALANCED)
        result = analyzer.calculate_composite_scores(df)

        expected_cols = ["ticker", "value", "momentum", "quality", "low_vol", "size", "composite"]
        for col in expected_cols:
            assert col in result.columns

    def test_different_profiles_different_scores(self):
        """프로필에 따라 복합 점수 순위가 달라짐"""
        df = self._make_test_df()

        conservative = FactorAnalyzer(RiskProfile.CONSERVATIVE).calculate_composite_scores(df)
        aggressive = FactorAnalyzer(RiskProfile.AGGRESSIVE).calculate_composite_scores(df)

        # 최고점 종목이 다를 수 있음 (항상은 아니므로 점수 분포가 다른지 확인)
        correlation = np.corrcoef(
            conservative["composite"].values,
            aggressive["composite"].values,
        )[0, 1]
        # 상관계수가 1.0이 아님 (프로필별로 다른 가중치 적용)
        assert correlation < 0.99

    def test_empty_dataframe(self):
        """빈 데이터프레임 처리"""
        df = pd.DataFrame()
        analyzer = FactorAnalyzer(RiskProfile.BALANCED)
        result = analyzer.calculate_composite_scores(df)
        assert len(result) == 0

    def test_no_nan_in_result(self):
        """결과에 NaN이 없어야 함"""
        df = self._make_test_df()
        # 일부 값에 NaN 추가
        df.loc[0, "per"] = np.nan
        df.loc[5, "roe"] = np.nan

        analyzer = FactorAnalyzer(RiskProfile.BALANCED)
        result = analyzer.calculate_composite_scores(df)

        assert not result.isna().any().any()

    def test_weights_sum_to_one(self):
        """가중치 합이 1.0"""
        for profile in RiskProfile:
            analyzer = FactorAnalyzer(profile)
            total = sum(analyzer.weights.values())
            assert abs(total - 1.0) < 1e-10, f"{profile}: weights sum = {total}"
