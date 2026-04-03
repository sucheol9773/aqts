"""
포트폴리오 관련 스키마

포지션, 포트폴리오 요약, 성과 정보 등 포트폴리오 관련 응답 모델을 정의합니다.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class PositionResponse(BaseModel):
    """
    포지션 정보

    현재 보유 중인 개별 포지션의 상세 정보입니다.
    """

    model_config = ConfigDict(from_attributes=True)

    ticker: str = Field(
        ...,
        description="종목 코드"
    )
    market: str = Field(
        ...,
        description="시장 (KRX, NYSE, NASDAQ 등)"
    )
    quantity: int = Field(
        ...,
        description="보유 수량"
    )
    avg_price: float = Field(
        ...,
        description="평균 단가"
    )
    current_price: float = Field(
        ...,
        description="현재가"
    )
    unrealized_pnl: float = Field(
        ...,
        description="미실현 손익"
    )
    weight: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="포트폴리오 내 비중 (0.0 ~ 1.0)"
    )


class PortfolioSummaryResponse(BaseModel):
    """
    포트폴리오 요약

    전체 포트폴리오의 종합 정보입니다.
    """

    model_config = ConfigDict(from_attributes=True)

    total_value: float = Field(
        ...,
        description="총 자산 가치"
    )
    cash_krw: float = Field(
        ...,
        description="KRW 현금 잔액"
    )
    cash_usd: float = Field(
        ...,
        description="USD 현금 잔액"
    )
    daily_return: float = Field(
        ...,
        description="당일 수익률"
    )
    unrealized_pnl: float = Field(
        ...,
        description="미실현 손익"
    )
    realized_pnl: float = Field(
        ...,
        description="실현 손익"
    )
    position_count: int = Field(
        ...,
        ge=0,
        description="보유 중인 포지션 수"
    )
    positions: list[PositionResponse] = Field(
        ...,
        description="포지션 목록"
    )


class PerformanceResponse(BaseModel):
    """
    성과 정보

    특정 기간의 포트폴리오 성과 지표입니다.
    """

    model_config = ConfigDict(from_attributes=True)

    period: str = Field(
        ...,
        description="기간 (e.g., 'D' (일간), 'W' (주간), 'M' (월간), 'Y' (연간))"
    )
    return_pct: float = Field(
        ...,
        description="수익률 (%)"
    )
    mdd: float = Field(
        ...,
        description="최대낙폭 (Maximum Drawdown, %)"
    )
    sharpe: float = Field(
        ...,
        description="샤프지수"
    )
    volatility: float = Field(
        ...,
        description="변동성 (%)"
    )
    win_rate: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="승률 (0.0 ~ 1.0)"
    )
