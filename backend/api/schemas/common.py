"""
공통 응답 모델

전체 API 응답에서 사용되는 통일된 래퍼 클래스들을 정의합니다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):
    """
    API 응답 래퍼

    모든 API 엔드포인트의 기본 응답 구조입니다.
    """

    success: bool = Field(
        ...,
        description="요청 성공 여부"
    )
    data: Optional[T] = Field(
        default=None,
        description="응답 데이터"
    )
    message: Optional[str] = Field(
        default=None,
        description="추가 메시지"
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="응답 생성 시간 (UTC)"
    )


class PaginatedResponse(BaseModel, Generic[T]):
    """
    페이지네이션 응답

    리스트 형태의 데이터를 페이지 단위로 반환할 때 사용합니다.
    """

    items: list[T] = Field(
        ...,
        description="현재 페이지의 아이템 목록"
    )
    total: int = Field(
        ...,
        description="전체 아이템 수"
    )
    page: int = Field(
        ...,
        description="현재 페이지 번호 (1부터 시작)"
    )
    page_size: int = Field(
        ...,
        description="페이지당 아이템 수"
    )


class ErrorResponse(BaseModel):
    """
    오류 응답

    API 오류 상황에서 반환됩니다.
    """

    error_code: str = Field(
        ...,
        description="오류 코드 (e.g., 'VALIDATION_ERROR', 'UNAUTHORIZED')"
    )
    detail: str = Field(
        ...,
        description="오류 상세 메시지"
    )
