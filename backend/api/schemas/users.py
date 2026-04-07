"""
사용자 관리 API 스키마
"""

from typing import Optional

from pydantic import BaseModel, Field


class UserCreateRequest(BaseModel):
    """사용자 생성 요청"""

    username: str = Field(..., min_length=1, max_length=50, description="사용자명")
    password: str = Field(..., min_length=8, description="비밀번호 (최소 8자)")
    email: Optional[str] = Field(default=None, description="이메일")
    role: str = Field(..., description="역할 (viewer/operator/admin)")


class UserUpdateRequest(BaseModel):
    """사용자 업데이트 요청"""

    email: Optional[str] = Field(default=None, description="이메일")
    role: Optional[str] = Field(default=None, description="역할 변경")
    is_active: Optional[bool] = Field(default=None, description="활성 여부")


class UserResponse(BaseModel):
    """사용자 정보 응답"""

    id: str = Field(..., description="사용자 UUID")
    username: str = Field(..., description="사용자명")
    email: Optional[str] = Field(default=None, description="이메일")
    role: str = Field(..., description="역할")
    is_active: bool = Field(..., description="활성 여부")
    is_locked: bool = Field(..., description="잠금 여부")
    totp_enabled: bool = Field(..., description="TOTP 활성화 여부")
    created_at: str = Field(..., description="생성 시각 (ISO 8601)")
    updated_at: str = Field(..., description="수정 시각 (ISO 8601)")
    last_login_at: Optional[str] = Field(default=None, description="마지막 로그인 (ISO 8601)")

    class Config:
        from_attributes = True


class PasswordResetRequest(BaseModel):
    """비밀번호 리셋 요청 (admin)"""

    new_password: str = Field(..., min_length=8, description="새 비밀번호 (최소 8자)")


class UserLockRequest(BaseModel):
    """사용자 잠금/해제 요청 (admin)"""

    is_locked: bool = Field(..., description="잠금 여부")
