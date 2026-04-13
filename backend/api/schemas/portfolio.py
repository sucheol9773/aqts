"""
포트폴리오 관련 스키마

포지션, 포트폴리오 요약, 성과 정보 등 포트폴리오 관련 응답 모델을 정의합니다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class PositionResponse(BaseModel):
    """
    포지션 정보

    현재 보유 중인 개별 포지션의 상세 정보입니다.
    """

    model_config = ConfigDict(from_attributes=True)

    ticker: str = Field(..., description="종목 코드")
    market: str = Field(..., description="시장 (KRX, NYSE, NASDAQ 등)")
    quantity: int = Field(..., description="보유 수량")
    avg_price: float = Field(..., description="평균 단가")
    current_price: float = Field(..., description="현재가")
    unrealized_pnl: float = Field(..., description="미실현 손익")
    weight: float = Field(..., ge=0.0, le=1.0, description="포트폴리오 내 비중 (0.0 ~ 1.0)")


class PortfolioSummaryResponse(BaseModel):
    """
    포트폴리오 요약

    전체 포트폴리오의 종합 정보입니다.
    """

    model_config = ConfigDict(from_attributes=True)

    total_value: float = Field(..., description="총 자산 가치")
    cash_krw: float = Field(..., description="KRW 현금 잔액")
    cash_usd: float = Field(..., description="USD 현금 잔액")
    daily_return: float = Field(..., description="당일 수익률")
    unrealized_pnl: float = Field(..., description="미실현 손익")
    realized_pnl: float = Field(..., description="실현 손익")
    position_count: int = Field(..., ge=0, description="보유 중인 포지션 수")
    positions: list[PositionResponse] = Field(..., description="포지션 목록")


class PerformanceResponse(BaseModel):
    """
    성과 정보

    특정 기간의 포트폴리오 성과 지표입니다.
    """

    model_config = ConfigDict(from_attributes=True)

    period: str = Field(..., description="기간 (e.g., 'D' (일간), 'W' (주간), 'M' (월간), 'Y' (연간))")
    return_pct: float = Field(..., description="수익률 (%)")
    mdd: float = Field(..., description="최대낙폭 (Maximum Drawdown, %)")
    sharpe: float = Field(..., description="샤프지수")
    volatility: float = Field(..., description="변동성 (%)")
    win_rate: float = Field(..., ge=0.0, le=1.0, description="승률 (0.0 ~ 1.0)")


# ══════════════════════════════════════
# 포트폴리오 구성 요청/응답 스키마
# ══════════════════════════════════════


class ConstructionRequest(BaseModel):
    """포트폴리오 구성 요청"""

    method: str = Field(
        default="mean_variance",
        description="최적화 방법 (mean_variance, risk_parity, black_litterman)",
    )
    risk_profile: str = Field(
        default="BALANCED",
        description="위험 성향 (CONSERVATIVE, BALANCED, AGGRESSIVE, DIVIDEND)",
    )
    seed_capital: Optional[float] = Field(
        default=None,
        description="초기 자본 (원). None이면 설정값 사용",
    )


class TargetAllocationResponse(BaseModel):
    """목표 포트폴리오 종목별 할당"""

    model_config = ConfigDict(from_attributes=True)

    ticker: str = Field(..., description="종목 코드")
    market: str = Field(..., description="시장 (KRX, NYSE, NASDAQ, AMEX)")
    target_weight: float = Field(..., description="목표 비중 (0.0 ~ 1.0)")
    current_weight: float = Field(..., description="현재 비중 (0.0 ~ 1.0)")
    signal_score: float = Field(..., description="앙상블 시그널 점수 (-1.0 ~ 1.0)")
    sector: str = Field(default="", description="섹터")


class ConstructionResponse(BaseModel):
    """포트폴리오 구성 결과"""

    model_config = ConfigDict(from_attributes=True)

    allocations: list[TargetAllocationResponse] = Field(..., description="종목별 목표 할당")
    total_value: float = Field(..., description="포트폴리오 총 자산 (원)")
    cash_ratio: float = Field(..., description="현금 비중 (0.0 ~ 1.0)")
    stock_count: int = Field(..., description="보유 종목 수")
    optimization_method: str = Field(..., description="사용된 최적화 방법")
    generated_at: datetime = Field(..., description="생성 시각")
    sector_weights: dict[str, float] = Field(default_factory=dict, description="섹터별 가중치")
    market_weights: dict[str, float] = Field(default_factory=dict, description="시장별 가중치")
