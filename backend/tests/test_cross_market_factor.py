"""
Cross-Market 팩터 정규화 테스트 (F-02-01-A)

FactorAnalyzer.calculate_cross_market_scores()의 종합 단위 테스트

테스트 범위:
- KR/US 합산 후 재정규화
- Cross-Market Z-Score 속성 검증
- 단일 시장만 제공 시 폴백
- 빈 데이터 처리
- 복합 점수 0~100 스케일
- Country 태깅
"""

import numpy as np
import pandas as pd

from config.constants import RiskProfile
from core.quant_engine.factor_analyzer import FactorAnalyzer


# ══════════════════════════════════════
# 테스트 픽스처
# ══════════════════════════════════════
def _make_kr_data(n: int = 5) -> pd.DataFrame:
    """한국 시장 테스트 데이터"""
    np.random.seed(42)
    return pd.DataFrame(
        {
            "ticker": [f"00{i:04d}" for i in range(n)],
            "per": np.random.uniform(5, 30, n),
            "pbr": np.random.uniform(0.5, 3.0, n),
            "ev_ebitda": np.random.uniform(3, 15, n),
            "return_12m": np.random.uniform(-0.2, 0.5, n),
            "return_1m": np.random.uniform(-0.1, 0.1, n),
            "roe": np.random.uniform(5, 30, n),
            "roa": np.random.uniform(2, 15, n),
            "debt_ratio": np.random.uniform(30, 200, n),
            "volatility_60d": np.random.uniform(0.1, 0.5, n),
            "beta": np.random.uniform(0.5, 1.5, n),
            "market_cap": np.random.uniform(1e11, 1e13, n),
        }
    )


def _make_us_data(n: int = 5) -> pd.DataFrame:
    """미국 시장 테스트 데이터"""
    np.random.seed(123)
    return pd.DataFrame(
        {
            "ticker": ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA"][:n],
            "per": np.random.uniform(10, 50, n),
            "pbr": np.random.uniform(2, 15, n),
            "ev_ebitda": np.random.uniform(8, 30, n),
            "return_12m": np.random.uniform(-0.1, 0.6, n),
            "return_1m": np.random.uniform(-0.05, 0.15, n),
            "roe": np.random.uniform(10, 50, n),
            "roa": np.random.uniform(5, 25, n),
            "debt_ratio": np.random.uniform(20, 150, n),
            "volatility_60d": np.random.uniform(0.15, 0.6, n),
            "beta": np.random.uniform(0.8, 1.8, n),
            "market_cap": np.random.uniform(1e11, 3e12, n),
        }
    )


# ══════════════════════════════════════
# Cross-Market 정규화 테스트
# ══════════════════════════════════════
class TestCrossMarketScores:
    """Cross-Market 팩터 점수 테스트"""

    def test_combined_count(self):
        """KR + US 합산 개수"""
        analyzer = FactorAnalyzer(RiskProfile.BALANCED)
        kr = _make_kr_data(5)
        us = _make_us_data(5)
        result = analyzer.calculate_cross_market_scores(kr, us)

        assert len(result) == 10

    def test_country_tag(self):
        """Country 태그 정확성"""
        analyzer = FactorAnalyzer()
        kr = _make_kr_data(3)
        us = _make_us_data(3)
        result = analyzer.calculate_cross_market_scores(kr, us)

        assert set(result["country"]) == {"KR", "US"}
        assert (result["country"] == "KR").sum() == 3
        assert (result["country"] == "US").sum() == 3

    def test_zscore_mean_near_zero(self):
        """재정규화 후 Z-Score 평균 ≈ 0"""
        analyzer = FactorAnalyzer()
        kr = _make_kr_data(10)
        us = _make_us_data(5)
        result = analyzer.calculate_cross_market_scores(kr, us)

        for col in ["value", "momentum", "quality", "low_vol", "size"]:
            mean = result[col].mean()
            assert abs(mean) < 0.5, f"{col} mean = {mean}"

    def test_zscore_bounded(self):
        """Z-Score가 ±3 이내 (윈저라이징)"""
        analyzer = FactorAnalyzer()
        kr = _make_kr_data(10)
        us = _make_us_data(5)
        result = analyzer.calculate_cross_market_scores(kr, us)

        for col in ["value", "momentum", "quality", "low_vol", "size"]:
            assert result[col].max() <= 3.0
            assert result[col].min() >= -3.0

    def test_composite_0_100(self):
        """복합 점수가 0~100 범위"""
        analyzer = FactorAnalyzer()
        kr = _make_kr_data(10)
        us = _make_us_data(5)
        result = analyzer.calculate_cross_market_scores(kr, us)

        assert result["composite"].min() >= 0.0
        assert result["composite"].max() <= 100.0

    def test_has_all_columns(self):
        """필수 컬럼 존재"""
        analyzer = FactorAnalyzer()
        kr = _make_kr_data()
        us = _make_us_data()
        result = analyzer.calculate_cross_market_scores(kr, us)

        expected = {"ticker", "country", "value", "momentum", "quality", "low_vol", "size", "composite"}
        assert expected.issubset(set(result.columns))


# ══════════════════════════════════════
# 단일 시장 폴백 테스트
# ══════════════════════════════════════
class TestSingleMarketFallback:
    """단일 시장만 제공 시 폴백 테스트"""

    def test_kr_only(self):
        """KR만 제공"""
        analyzer = FactorAnalyzer()
        kr = _make_kr_data(5)
        result = analyzer.calculate_cross_market_scores(kr, pd.DataFrame())

        assert len(result) == 5
        assert all(result["country"] == "KR")

    def test_us_only(self):
        """US만 제공"""
        analyzer = FactorAnalyzer()
        us = _make_us_data(5)
        result = analyzer.calculate_cross_market_scores(pd.DataFrame(), us)

        assert len(result) == 5
        assert all(result["country"] == "US")

    def test_both_empty(self):
        """둘 다 빈 경우"""
        analyzer = FactorAnalyzer()
        result = analyzer.calculate_cross_market_scores(pd.DataFrame(), pd.DataFrame())
        assert len(result) == 0


# ══════════════════════════════════════
# 프로필별 가중치 반영 테스트
# ══════════════════════════════════════
class TestProfileWeights:
    """프로필에 따른 가중치 반영 테스트"""

    def test_aggressive_favors_momentum(self):
        """공격적 프로필은 모멘텀 비중 높음"""
        analyzer = FactorAnalyzer(RiskProfile.AGGRESSIVE)
        assert analyzer.weights["momentum"] > analyzer.weights["low_vol"]

    def test_conservative_favors_low_vol(self):
        """보수적 프로필은 저변동성 비중 높음"""
        analyzer = FactorAnalyzer(RiskProfile.CONSERVATIVE)
        assert analyzer.weights["low_vol"] > analyzer.weights["momentum"]

    def test_different_profiles_different_scores(self):
        """프로필에 따라 복합 점수 순위가 달라짐"""
        kr = _make_kr_data(10)
        us = _make_us_data(5)

        agg = FactorAnalyzer(RiskProfile.AGGRESSIVE)
        con = FactorAnalyzer(RiskProfile.CONSERVATIVE)

        agg_result = agg.calculate_cross_market_scores(kr, us)
        con_result = con.calculate_cross_market_scores(kr, us)

        # 두 프로필의 1위가 다를 수 있음 (반드시는 아니지만 점수는 다름)
        agg_top = agg_result.nlargest(1, "composite")["ticker"].values[0]
        con_top = con_result.nlargest(1, "composite")["ticker"].values[0]

        # 최소한 점수 자체는 다름
        agg_scores = agg_result.set_index("ticker")["composite"]
        con_scores = con_result.set_index("ticker")["composite"]
        diff = (agg_scores - con_scores).abs().sum()
        assert diff > 0  # 프로필이 다르면 점수도 다름


# ══════════════════════════════════════
# _calculate_market_factors 테스트
# ══════════════════════════════════════
class TestMarketFactors:
    """단일 시장 팩터 산출 테스트"""

    def test_kr_factors(self):
        """KR 시장 팩터 산출"""
        analyzer = FactorAnalyzer()
        kr = _make_kr_data(5)
        result = analyzer._calculate_market_factors(kr, "KR")

        assert len(result) == 5
        assert all(result["country"] == "KR")
        assert "value" in result.columns
        assert "composite" not in result.columns  # 복합점수는 cross-market에서

    def test_no_nan(self):
        """NaN이 없음"""
        analyzer = FactorAnalyzer()
        kr = _make_kr_data(5)
        result = analyzer._calculate_market_factors(kr, "KR")

        assert result.isna().sum().sum() == 0
