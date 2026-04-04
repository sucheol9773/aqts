"""
인증 관련 스키마

로그인, 토큰 갱신 등 인증 처리에 필요한 요청/응답 모델을 정의합니다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """
    로그인 요청

    대시보드 접근을 위한 비밀번호 인증에 사용됩니다.
    """

    password: str = Field(..., min_length=1, description="대시보드 로그인 비밀번호")


class TokenResponse(BaseModel):
    """
    토큰 응답

    로그인 성공 시 반환되는 토큰 정보입니다.
    """

    access_token: str = Field(..., description="JWT 액세스 토큰")
    refresh_token: str = Field(..., description="JWT 리프레시 토큰")
    token_type: str = Field(default="bearer", description="토큰 유형")
    expires_in: int = Field(..., description="액세스 토큰 만료 시간 (초)")


class RefreshTokenRequest(BaseModel):
    """
    토큰 갱신 요청

    만료된 액세스 토큰을 갱신할 때 사용됩니다.
    """

    refresh_token: str = Field(..., description="갱신할 리프레시 토큰")
