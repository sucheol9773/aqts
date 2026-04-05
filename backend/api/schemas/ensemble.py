"""
동적 앙상블 API 스키마

동적 앙상블 분석 결과를 API 응답으로 변환하기 위한 Pydantic 모델 정의.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class EnsembleWeights(BaseModel):
    """전략별 가중치"""

    MR: float = Field(..., description="Mean Reversion 가중치")
    TF: float = Field(..., description="Trend Following 가중치")
    RP: float = Field(..., description="Risk Parity 가중치")


class EnsembleSignalResponse(BaseModel):
    """단일 종목 동적 앙상블 결과"""

    ticker: str = Field(..., description="종목코드")
    country: str = Field(..., description="국가 코드 (KR/US)")
    ensemble_signal: float = Field(..., description="앙상블 시그널 값 (-1 ~ +1)")
    regime: str = Field(..., description="현재 레짐 (TRENDING_UP/TRENDING_DOWN/SIDEWAYS)")
    weights: EnsembleWeights = Field(..., description="전략별 가중치")
    adx: float = Field(..., description="현재 ADX 값")
    vol_percentile: float = Field(..., description="변동성 백분위 (0~1)")
    vol_scalar: float = Field(..., description="변동성 타겟 스칼라 (0~1)")
    ohlcv_days: int = Field(..., description="사용된 OHLCV 일수")


class EnsembleBatchResponse(BaseModel):
    """배치 앙상블 결과"""

    total_tickers: int = Field(..., description="전체 종목 수")
    succeeded: int = Field(..., description="성공 종목 수")
    failed: int = Field(..., description="실패 종목 수")
    results: dict[str, EnsembleSignalResponse] = Field(
        default_factory=dict,
        description="종목별 앙상블 결과",
    )
    errors: dict[str, str] = Field(
        default_factory=dict,
        description="실패 종목별 에러 메시지",
    )


class EnsembleCachedResponse(BaseModel):
    """Redis 캐시에서 조회한 앙상블 결과"""

    ticker: str = Field(..., description="종목코드")
    ensemble_signal: float = Field(..., description="앙상블 시그널 값")
    regime: str = Field(..., description="현재 레짐")
    weights: Optional[dict[str, float]] = Field(default=None, description="전략별 가중치")
    adx: Optional[float] = Field(default=None, description="ADX 값")
    vol_percentile: Optional[float] = Field(default=None, description="변동성 백분위")
    vol_scalar: Optional[float] = Field(default=None, description="변동성 타겟 스칼라")
    ohlcv_days: Optional[int] = Field(default=None, description="사용된 OHLCV 일수")
    cached: bool = Field(default=True, description="캐시에서 조회 여부")


class EnsembleCacheSummary(BaseModel):
    """캐시된 앙상블 결과 요약"""

    updated_at: Optional[str] = Field(default=None, description="캐시 갱신 시간 (UTC)")
    total_tickers: int = Field(default=0, description="캐시된 종목 수")
    tickers: list[str] = Field(default_factory=list, description="캐시된 종목 목록")
