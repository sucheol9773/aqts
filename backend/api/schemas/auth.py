"""
인증 관련 스키마

로그인, 토큰 갱신 등 인증 처리에 필요한 요청/응답 모델을 정의합니다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """
    로그인 요청

    사용자명/비밀번호 인증 + 선택적 TOTP 코드.
    TOTP 활성화 시 totp_code 필수.
    """

    username: str = Field(..., min_length=1, max_length=50, description="사용자명")
    password: str = Field(..., min_length=1, description="비밀번호")
    totp_code: str = Field(default=None, description="TOTP 6자리 코드 (TOTP 활성 시 필수)")


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


class MFAEnrollResponse(BaseModel):
    """
    MFA 등록 응답

    TOTP 시크릿 + QR 코드 프로비저닝 URI.
    """

    secret: str = Field(..., description="Base32 인코딩된 TOTP 시크릿")
    provisioning_uri: str = Field(
        ...,
        description="QR 코드 생성용 provisioning URI (otpauth://)",
    )


class MFAVerifyRequest(BaseModel):
    """
    MFA 검증 요청

    TOTP 코드로 MFA 등록 완료.
    """

    totp_code: str = Field(..., description="6자리 TOTP 코드")


class MFADisableRequest(BaseModel):
    """
    MFA 비활성화 요청

    현재 비밀번호로 인증하고 MFA 비활성.
    """

    password: str = Field(..., description="현재 비밀번호 (확인용)")
