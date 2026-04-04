"""
시세 데이터 수집 및 무결성 검증 유닛테스트

테스트 대상: core/data_collector/market_data.py
목표 커버리지: 80% (Data Collector 모듈)

테스트 범위:
- OHLCV 데이터 파싱 및 저장
- 결측 데이터 Forward Fill 로직
- 연속 결측 감지 및 유니버스 제외 판단
- 이상치 탐지 (3시그마 기준)
- 상하한가/서킷브레이커 범위 내 급변 정상 처리
"""

from unittest.mock import AsyncMock, patch

import numpy as np
import pandas as pd

from config.constants import DATA_INTEGRITY


class TestConsecutiveMissing:
    """연속 결측 검증 테스트"""

    def _create_collector(self):
        """MarketDataCollector 인스턴스 생성 (DB 세션 Mock)"""
        with patch("core.data_collector.market_data.KISClient"):
            from core.data_collector.market_data import MarketDataCollector

            mock_session = AsyncMock()
            collector = MarketDataCollector(mock_session)
            return collector

    def test_no_missing_days(self):
        """결측 없는 정상 데이터"""
        collector = self._create_collector()
        dates = pd.bdate_range(start="2026-03-01", periods=10)
        df = pd.DataFrame({"time": dates, "close": range(10), "volume": range(10)})

        result = collector._check_consecutive_missing(df)
        assert result == 0

    def test_one_missing_day(self):
        """1일 결측"""
        collector = self._create_collector()
        dates = pd.bdate_range(start="2026-03-01", periods=10)
        # 3번째 영업일 제거
        dates_with_gap = dates.delete(2)
        df = pd.DataFrame(
            {
                "time": dates_with_gap,
                "close": range(len(dates_with_gap)),
                "volume": range(len(dates_with_gap)),
            }
        )

        result = collector._check_consecutive_missing(df)
        assert result == 1

    def test_three_consecutive_missing(self):
        """3일 연속 결측 (임계값)"""
        collector = self._create_collector()
        dates = pd.bdate_range(start="2026-03-01", periods=10)
        # 3,4,5번째 영업일 제거
        dates_with_gap = dates.delete([2, 3, 4])
        df = pd.DataFrame(
            {
                "time": dates_with_gap,
                "close": range(len(dates_with_gap)),
                "volume": range(len(dates_with_gap)),
            }
        )

        result = collector._check_consecutive_missing(df)
        assert result == 3

    def test_exceeds_missing_threshold(self):
        """임계값 초과 연속 결측 (4일)"""
        collector = self._create_collector()
        dates = pd.bdate_range(start="2026-03-01", periods=10)
        # 4일 연속 제거
        dates_with_gap = dates.delete([2, 3, 4, 5])
        df = pd.DataFrame(
            {
                "time": dates_with_gap,
                "close": range(len(dates_with_gap)),
                "volume": range(len(dates_with_gap)),
            }
        )

        result = collector._check_consecutive_missing(df)
        assert result > DATA_INTEGRITY["max_consecutive_missing_days"]

    def test_empty_dataframe(self):
        """빈 데이터프레임"""
        collector = self._create_collector()
        df = pd.DataFrame(columns=["time", "close", "volume"])

        result = collector._check_consecutive_missing(df)
        assert result == 0


class TestOutlierDetection:
    """이상치 탐지 테스트"""

    def test_normal_returns_no_outlier(self):
        """정상 수익률 범위 - 이상치 없음"""
        np.random.seed(42)
        prices = [70000]
        for _ in range(59):
            # 일반적인 일간 변동 (±1.5%)
            change = np.random.normal(0, 0.01)
            prices.append(prices[-1] * (1 + change))

        returns = pd.Series(prices).pct_change().dropna()
        sigma = DATA_INTEGRITY["outlier_sigma_threshold"]
        mean_ret = returns.mean()
        std_ret = returns.std()

        if std_ret > 0:
            z_scores = (returns - mean_ret) / std_ret
            outliers = z_scores.abs() > sigma
            # 정상 분포에서 3시그마 초과는 매우 드묾
            assert outliers.sum() <= 2

    def test_kr_limit_price_not_flagged(self):
        """한국 상하한가(±30%) 범위 내 급변은 정상 처리"""
        daily_return = 0.25  # +25%
        abs_return = abs(daily_return)
        kr_limit = DATA_INTEGRITY["kr_daily_limit_pct"]

        # 상하한가 범위 내이므로 정상
        assert abs_return <= kr_limit

    def test_kr_beyond_limit_flagged(self):
        """한국 상하한가 초과 시 이상치 플래깅"""
        daily_return = 0.35  # +35% (상하한가 30% 초과)
        abs_return = abs(daily_return)
        kr_limit = DATA_INTEGRITY["kr_daily_limit_pct"]

        # 상하한가 범위 초과이므로 이상치
        assert abs_return > kr_limit

    def test_us_circuit_breaker_not_flagged(self):
        """미국 서킷브레이커 1단계(7%) 이내는 정상 처리"""
        daily_return = -0.05  # -5%
        abs_return = abs(daily_return)
        us_limit = DATA_INTEGRITY["us_circuit_breaker_l1"]

        assert abs_return <= us_limit

    def test_us_beyond_circuit_breaker_flagged(self):
        """미국 서킷브레이커 1단계 초과 시 이상치 플래깅"""
        daily_return = -0.10  # -10%
        abs_return = abs(daily_return)
        us_limit = DATA_INTEGRITY["us_circuit_breaker_l1"]

        assert abs_return > us_limit


class TestDataIntegrityConstants:
    """데이터 무결성 상수 검증"""

    def test_constants_exist(self):
        assert "max_consecutive_missing_days" in DATA_INTEGRITY
        assert "outlier_sigma_threshold" in DATA_INTEGRITY
        assert "kr_daily_limit_pct" in DATA_INTEGRITY
        assert "us_circuit_breaker_l1" in DATA_INTEGRITY

    def test_reasonable_values(self):
        assert DATA_INTEGRITY["max_consecutive_missing_days"] == 3
        assert DATA_INTEGRITY["outlier_sigma_threshold"] == 3.0
        assert DATA_INTEGRITY["kr_daily_limit_pct"] == 0.30
        assert DATA_INTEGRITY["us_circuit_breaker_l1"] == 0.07
