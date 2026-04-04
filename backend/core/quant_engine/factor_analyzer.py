"""
팩터 분석 모듈 (Factor Analysis Module)

F-02-01 명세 구현:
- 다중 팩터 모델 기반 종목별 팩터 점수 산출
- 구현 팩터: Value, Momentum, Quality, Low Volatility, Size
- Z-Score 정규화 → 가중 평균 복합 점수 산출
- 한국/미국 시장별 별도 유니버스에서 산출 후 Cross-Market 정규화

사용 라이브러리: pandas 2.2.2, numpy 1.26.4, scipy 1.13.1
(ta-lib, vectorbt 등 빌드 이슈가 있는 패키지 미사용)
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from config.constants import RiskProfile
from config.logging import logger


@dataclass
class FactorScore:
    """단일 종목의 팩터 점수 컨테이너"""

    ticker: str
    market: str
    country: str
    value_score: float = 0.0
    momentum_score: float = 0.0
    quality_score: float = 0.0
    low_vol_score: float = 0.0
    size_score: float = 0.0
    composite_score: float = 0.0
    calculated_at: Optional[str] = None


# ══════════════════════════════════════
# 팩터 가중치 기본값 (프로필별로 조정됨)
# ══════════════════════════════════════
DEFAULT_FACTOR_WEIGHTS = {
    RiskProfile.CONSERVATIVE: {
        "value": 0.15,
        "momentum": 0.10,
        "quality": 0.25,
        "low_vol": 0.35,
        "size": 0.15,
    },
    RiskProfile.BALANCED: {
        "value": 0.25,
        "momentum": 0.20,
        "quality": 0.20,
        "low_vol": 0.20,
        "size": 0.15,
    },
    RiskProfile.AGGRESSIVE: {
        "value": 0.15,
        "momentum": 0.35,
        "quality": 0.15,
        "low_vol": 0.10,
        "size": 0.25,
    },
    RiskProfile.DIVIDEND: {
        "value": 0.30,
        "momentum": 0.05,
        "quality": 0.35,
        "low_vol": 0.20,
        "size": 0.10,
    },
}


class FactorAnalyzer:
    """
    다중 팩터 분석기

    종목별 5개 팩터 점수를 산출하고
    프로필에 따른 가중 평균 복합 점수를 계산합니다.
    """

    def __init__(self, risk_profile: RiskProfile = RiskProfile.BALANCED):
        self._risk_profile = risk_profile
        self._weights = DEFAULT_FACTOR_WEIGHTS[risk_profile]

    @property
    def weights(self) -> dict:
        return self._weights.copy()

    # ══════════════════════════════════════
    # 개별 팩터 계산
    # ══════════════════════════════════════
    @staticmethod
    def calc_value_factor(df: pd.DataFrame) -> pd.Series:
        """
        가치(Value) 팩터 계산

        낮은 PER, 낮은 PBR, 낮은 EV/EBITDA → 높은 가치 점수
        각 지표를 역수 변환 후 Z-Score 정규화, 동일 가중 평균

        Args:
            df: columns = [ticker, per, pbr, ev_ebitda]

        Returns:
            ticker를 인덱스로 한 가치 팩터 Z-Score Series
        """
        result = pd.DataFrame(index=df["ticker"])
        # 역수 변환: 낮을수록 좋은 지표를 높을수록 좋게 변환
        # 0 이하 값(적자 등)은 제외
        for col in ["per", "pbr", "ev_ebitda"]:
            if col not in df.columns:
                continue
            values = df[col].copy()
            # 0 이하 또는 극단적 음수는 NaN 처리
            values = values.where(values > 0, np.nan)
            # 역수 변환
            inverted = 1.0 / values
            # Z-Score 정규화
            result[col] = _zscore_series(inverted.values)

        # 사용 가능한 컬럼만으로 평균
        valid_cols = [c for c in ["per", "pbr", "ev_ebitda"] if c in result.columns]
        if not valid_cols:
            return pd.Series(0.0, index=df["ticker"], name="value")

        composite = result[valid_cols].mean(axis=1)
        composite.name = "value"
        composite.index = df["ticker"].values
        return composite

    @staticmethod
    def calc_momentum_factor(df: pd.DataFrame) -> pd.Series:
        """
        모멘텀(Momentum) 팩터 계산

        12개월 수익률에서 최근 1개월 수익률을 차감 (12-1 Momentum)
        단기 반전 효과를 제거한 중기 모멘텀

        Args:
            df: columns = [ticker, return_12m, return_1m]

        Returns:
            ticker를 인덱스로 한 모멘텀 팩터 Z-Score Series
        """
        if "return_12m" not in df.columns or "return_1m" not in df.columns:
            return pd.Series(0.0, index=df["ticker"], name="momentum")

        momentum_12_1 = df["return_12m"] - df["return_1m"]
        scores = _zscore_series(momentum_12_1.values)
        result = pd.Series(scores, index=df["ticker"].values, name="momentum")
        return result

    @staticmethod
    def calc_quality_factor(df: pd.DataFrame) -> pd.Series:
        """
        퀄리티(Quality) 팩터 계산

        높은 ROE, 높은 ROA, 낮은 부채비율 → 높은 퀄리티 점수

        Args:
            df: columns = [ticker, roe, roa, debt_ratio]

        Returns:
            ticker를 인덱스로 한 퀄리티 팩터 Z-Score Series
        """
        result = pd.DataFrame(index=df["ticker"])

        # ROE, ROA: 높을수록 좋음
        for col in ["roe", "roa"]:
            if col in df.columns:
                result[col] = _zscore_series(df[col].values)

        # 부채비율: 낮을수록 좋음 → 역수 변환
        if "debt_ratio" in df.columns:
            values = df["debt_ratio"].copy()
            values = values.where(values > 0, np.nan)
            inverted = 1.0 / values
            result["debt_ratio"] = _zscore_series(inverted.values)

        valid_cols = [c for c in ["roe", "roa", "debt_ratio"] if c in result.columns]
        if not valid_cols:
            return pd.Series(0.0, index=df["ticker"], name="quality")

        composite = result[valid_cols].mean(axis=1)
        composite.name = "quality"
        composite.index = df["ticker"].values
        return composite

    @staticmethod
    def calc_low_volatility_factor(df: pd.DataFrame) -> pd.Series:
        """
        저변동성(Low Volatility) 팩터 계산

        낮은 변동성, 낮은 베타 → 높은 저변동성 점수

        Args:
            df: columns = [ticker, volatility_60d, beta]

        Returns:
            ticker를 인덱스로 한 저변동성 팩터 Z-Score Series
        """
        result = pd.DataFrame(index=df["ticker"])

        # 변동성, 베타 모두 낮을수록 좋음 → 부호 반전
        for col in ["volatility_60d", "beta"]:
            if col in df.columns:
                inverted = -1.0 * df[col]
                result[col] = _zscore_series(inverted.values)

        valid_cols = [c for c in ["volatility_60d", "beta"] if c in result.columns]
        if not valid_cols:
            return pd.Series(0.0, index=df["ticker"], name="low_vol")

        composite = result[valid_cols].mean(axis=1)
        composite.name = "low_vol"
        composite.index = df["ticker"].values
        return composite

    @staticmethod
    def calc_size_factor(df: pd.DataFrame) -> pd.Series:
        """
        사이즈(Size) 팩터 계산

        소형주 프리미엄: 시가총액이 작을수록 높은 점수
        (단, 극소형주는 유니버스 필터링에서 이미 제외됨)

        Args:
            df: columns = [ticker, market_cap]

        Returns:
            ticker를 인덱스로 한 사이즈 팩터 Z-Score Series
        """
        if "market_cap" not in df.columns:
            return pd.Series(0.0, index=df["ticker"], name="size")

        # 로그 변환 후 부호 반전 (소형주 프리미엄)
        log_cap = np.log1p(df["market_cap"].values.astype(float))
        inverted = -1.0 * log_cap
        scores = _zscore_series(inverted)
        result = pd.Series(scores, index=df["ticker"].values, name="size")
        return result

    # ══════════════════════════════════════
    # Cross-Market 팩터 정규화 (F-02-01-A)
    # ══════════════════════════════════════
    def calculate_cross_market_scores(
        self,
        kr_df: pd.DataFrame,
        us_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        KR/US 시장 별도 팩터 산출 후 Cross-Market Z-Score 정규화

        각 시장 내에서 개별 팩터를 산출하고,
        합산 후 전체 유니버스 기준으로 재정규화합니다.

        정규화 절차:
        1. 시장별 개별 팩터 Z-Score 산출 (within-market)
        2. 두 시장 결합
        3. 전체 유니버스 기준 재정규화 (cross-market)
        4. 가중 평균 복합 점수 산출

        Args:
            kr_df: 한국 시장 종목 데이터 (columns: [ticker, ...])
            us_df: 미국 시장 종목 데이터 (columns: [ticker, ...])

        Returns:
            Cross-Market 정규화된 팩터 점수 DataFrame
            columns: [ticker, country, value, momentum, quality,
                      low_vol, size, composite]
        """
        factor_cols = ["value", "momentum", "quality", "low_vol", "size"]

        # Step 1: 시장별 팩터 산출
        kr_scores = self._calculate_market_factors(kr_df, "KR") if not kr_df.empty else pd.DataFrame()
        us_scores = self._calculate_market_factors(us_df, "US") if not us_df.empty else pd.DataFrame()

        # Step 2: 결합
        combined = pd.concat([kr_scores, us_scores], ignore_index=True)
        if combined.empty:
            return pd.DataFrame(columns=["ticker", "country"] + factor_cols + ["composite"])

        # Step 3: Cross-Market Z-Score 재정규화
        for col in factor_cols:
            if col in combined.columns:
                combined[col] = _zscore_series(combined[col].values)

        # NaN → 0
        combined = combined.fillna(0.0)

        # Step 4: 가중 평균 복합 점수
        w = self._weights
        combined["composite"] = (
            combined["value"] * w["value"]
            + combined["momentum"] * w["momentum"]
            + combined["quality"] * w["quality"]
            + combined["low_vol"] * w["low_vol"]
            + combined["size"] * w["size"]
        )

        # 0~100 스케일
        combined["composite"] = _scale_to_percentile(combined["composite"].values)

        logger.info(
            f"Cross-market factor scores: "
            f"KR={len(kr_scores)} + US={len(us_scores)} = {len(combined)} tickers. "
            f"Top: {combined['composite'].max():.1f}, "
            f"Bottom: {combined['composite'].min():.1f}"
        )

        return combined

    def _calculate_market_factors(
        self,
        df: pd.DataFrame,
        country: str,
    ) -> pd.DataFrame:
        """
        단일 시장 내 팩터 Z-Score 산출 (within-market)

        Args:
            df: 시장 데이터
            country: "KR" 또는 "US"

        Returns:
            시장 내 정규화된 팩터 점수 DataFrame
        """
        tickers = df["ticker"].values

        value = self.calc_value_factor(df)
        momentum = self.calc_momentum_factor(df)
        quality = self.calc_quality_factor(df)
        low_vol = self.calc_low_volatility_factor(df)
        size = self.calc_size_factor(df)

        result = pd.DataFrame(
            {
                "ticker": tickers,
                "country": country,
                "value": value.values if len(value) == len(tickers) else np.zeros(len(tickers)),
                "momentum": momentum.values if len(momentum) == len(tickers) else np.zeros(len(tickers)),
                "quality": quality.values if len(quality) == len(tickers) else np.zeros(len(tickers)),
                "low_vol": low_vol.values if len(low_vol) == len(tickers) else np.zeros(len(tickers)),
                "size": size.values if len(size) == len(tickers) else np.zeros(len(tickers)),
            }
        )

        return result.fillna(0.0)

    # ══════════════════════════════════════
    # 복합 팩터 점수 계산 (단일 시장)
    # ══════════════════════════════════════
    def calculate_composite_scores(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        전체 팩터 점수를 계산하고 가중 평균 복합 점수를 산출

        Args:
            df: 종목별 재무/시세 데이터 DataFrame
                필수 columns: [ticker]
                선택 columns: [per, pbr, ev_ebitda, return_12m, return_1m,
                              roe, roa, debt_ratio, volatility_60d, beta, market_cap]

        Returns:
            DataFrame with columns:
            [ticker, value, momentum, quality, low_vol, size, composite]
        """
        if df.empty:
            logger.warning("Empty DataFrame received for factor calculation")
            return pd.DataFrame(
                columns=[
                    "ticker",
                    "value",
                    "momentum",
                    "quality",
                    "low_vol",
                    "size",
                    "composite",
                ]
            )

        tickers = df["ticker"].values

        # 각 팩터 계산
        value = self.calc_value_factor(df)
        momentum = self.calc_momentum_factor(df)
        quality = self.calc_quality_factor(df)
        low_vol = self.calc_low_volatility_factor(df)
        size = self.calc_size_factor(df)

        # 결과 조합
        result = pd.DataFrame(
            {
                "ticker": tickers,
                "value": value.values if len(value) == len(tickers) else np.zeros(len(tickers)),
                "momentum": momentum.values if len(momentum) == len(tickers) else np.zeros(len(tickers)),
                "quality": quality.values if len(quality) == len(tickers) else np.zeros(len(tickers)),
                "low_vol": low_vol.values if len(low_vol) == len(tickers) else np.zeros(len(tickers)),
                "size": size.values if len(size) == len(tickers) else np.zeros(len(tickers)),
            }
        )

        # NaN을 0으로 채움
        result = result.fillna(0.0)

        # 가중 평균 복합 점수 계산
        w = self._weights
        result["composite"] = (
            result["value"] * w["value"]
            + result["momentum"] * w["momentum"]
            + result["quality"] * w["quality"]
            + result["low_vol"] * w["low_vol"]
            + result["size"] * w["size"]
        )

        # 복합 점수를 0~100 스케일로 변환
        result["composite"] = _scale_to_percentile(result["composite"].values)

        logger.info(
            f"Factor scores calculated for {len(result)} tickers. "
            f"Profile: {self._risk_profile.value}, "
            f"Top composite: {result['composite'].max():.1f}, "
            f"Bottom composite: {result['composite'].min():.1f}"
        )

        return result


# ══════════════════════════════════════
# 유틸리티 함수
# ══════════════════════════════════════
def _zscore_series(values: np.ndarray) -> np.ndarray:
    """
    Z-Score 정규화

    NaN을 무시하고 Z-Score 계산, NaN은 0으로 대체
    표준편차가 0인 경우 (모든 값이 동일) 전체를 0으로 반환
    """
    arr = np.array(values, dtype=float)
    mask = ~np.isnan(arr)

    if mask.sum() < 2:
        return np.zeros_like(arr)

    mean = np.nanmean(arr)
    std = np.nanstd(arr, ddof=1)

    if std < 1e-10:
        return np.zeros_like(arr)

    result = np.where(mask, (arr - mean) / std, 0.0)

    # 극단값 윈저라이징 (±3 시그마)
    result = np.clip(result, -3.0, 3.0)

    return result


def _scale_to_percentile(values: np.ndarray) -> np.ndarray:
    """
    Z-Score를 0~100 백분위 스케일로 변환

    scipy.stats.percentileofscore 대신
    rank 기반 단순 백분위 변환 (scipy 의존성 최소화)
    """
    arr = np.array(values, dtype=float)
    n = len(arr)

    if n == 0:
        return arr

    if n == 1:
        return np.array([50.0])

    # rank 기반 백분위
    ranks = arr.argsort().argsort()  # 이중 argsort = rank
    percentiles = (ranks / (n - 1)) * 100.0

    return percentiles
