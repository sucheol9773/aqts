"""
Contract 3: NewsData — 뉴스/공시 데이터 계약
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from config.constants import NewsSource


class NewsData(BaseModel):
    """뉴스 데이터 계약.

    뉴스 수집 시점에 제목/본문/출처/발행 시각이 유효한지 검증합니다.
    sentiment_label이 있으면 [-1.0, +1.0] 범위 강제.
    """

    ticker: str = Field(..., min_length=1, max_length=20, description="관련 종목 코드")
    title: str = Field(..., min_length=1, max_length=500, description="뉴스 제목")
    content: str = Field(..., min_length=1, description="뉴스 본문 또는 요약")
    source: NewsSource = Field(..., description="뉴스 소스")
    published_at: datetime = Field(..., description="뉴스 발행 시각")
    url: Optional[str] = Field(None, max_length=2048, description="원문 URL")

    sentiment_score: Optional[float] = Field(None, ge=-1.0, le=1.0, description="감성 점수 (-1.0 ~ +1.0)")
    sentiment_label: Optional[str] = Field(None, description="감성 레이블 (POSITIVE/NEUTRAL/NEGATIVE)")

    collected_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="수집 시각 (UTC)",
    )

    @field_validator("ticker")
    @classmethod
    def validate_ticker(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("ticker는 비어 있을 수 없습니다")
        return stripped

    @field_validator("sentiment_label")
    @classmethod
    def validate_sentiment_label(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            allowed = {"POSITIVE", "NEUTRAL", "NEGATIVE"}
            if v.upper() not in allowed:
                raise ValueError(f"sentiment_label은 {allowed} 중 하나여야 합니다: {v}")
            return v.upper()
        return v

    @field_validator("content")
    @classmethod
    def validate_content_length(cls, v: str) -> str:
        if len(v) > 100_000:
            raise ValueError(f"content 길이가 100,000자를 초과합니다: {len(v)}")
        return v

    model_config = {"frozen": True, "extra": "forbid"}
