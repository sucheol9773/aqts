"""
사용자 프로필 관련 스키마

투자자 프로필 정보의 생성, 수정, 조회에 필요한 요청/응답 모델을 정의합니다.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ProfileUpdateRequest(BaseModel):
    """
    프로필 업데이트 요청

    사용자 프로필 정보를 수정할 때 사용됩니다.
    """

    risk_profile: str = Field(..., description="위험 성향 (CONSERVATIVE, BALANCED, AGGRESSIVE, DIVIDEND)")
    investment_style: str = Field(..., description="투자 스타일 (DISCRETIONARY, ADVISORY)")
    investment_goal: str = Field(..., description="투자 목적 (WEALTH_GROWTH, RETIREMENT, EDUCATION, INCOME)")
    initial_capital: float = Field(..., gt=0, description="초기 자본 (원)")
    max_loss_tolerance: Optional[float] = Field(default=None, ge=0, le=1.0, description="최대 손실 허용도 (0.0 ~ 1.0)")


class ProfileResponse(BaseModel):
    """
    프로필 응답

    사용자 프로필 조회 시 반환되는 프로필 정보입니다.
    """

    model_config = ConfigDict(from_attributes=True)

    risk_profile: str = Field(..., description="위험 성향 (CONSERVATIVE, BALANCED, AGGRESSIVE, DIVIDEND)")
    investment_style: str = Field(..., description="투자 스타일 (DISCRETIONARY, ADVISORY)")
    investment_goal: str = Field(..., description="투자 목적 (WEALTH_GROWTH, RETIREMENT, EDUCATION, INCOME)")
    initial_capital: float = Field(..., description="초기 자본 (원)")
    max_loss_tolerance: Optional[float] = Field(default=None, description="최대 손실 허용도 (0.0 ~ 1.0)")
    created_at: Optional[str] = Field(default=None, description="프로필 생성 시간 (ISO 8601)")
    updated_at: Optional[str] = Field(default=None, description="프로필 수정 시간 (ISO 8601)")
