"""
알림 관련 스키마

시스템 알림, 거래 알림 등 알림 관련 응답 모델을 정의합니다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class AlertResponse(BaseModel):
    """
    알림 정보

    시스템에서 발생한 개별 알림의 상세 정보입니다.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="알림 ID")
    alert_type: str = Field(
        ..., description="알림 유형 (DAILY_REPORT, WEEKLY_REPORT, MONTHLY_REPORT, EMERGENCY_REBALANCING, SYSTEM_ERROR)"
    )
    level: str = Field(..., description="알림 레벨 (INFO, WARNING, ERROR, CRITICAL)")
    title: str = Field(..., description="알림 제목")
    message: str = Field(..., description="알림 메시지")
    status: str = Field(..., description="알림 상태 (UNREAD, READ, ARCHIVED)")
    created_at: datetime = Field(..., description="알림 생성 시간 (UTC)")
    read_at: Optional[datetime] = Field(default=None, description="알림 읽음 시간 (UTC)")


class AlertStatsResponse(BaseModel):
    """
    알림 통계

    알림의 종합 통계 정보입니다.
    """

    total: int = Field(..., ge=0, description="전체 알림 수")
    unread: int = Field(..., ge=0, description="읽지 않은 알림 수")
    by_level: dict[str, int] = Field(
        ..., description="레벨별 알림 수 (예: {'INFO': 10, 'WARNING': 5, 'ERROR': 2, 'CRITICAL': 1})"
    )


class AlertListResponse(BaseModel):
    """
    알림 목록 응답

    알림 목록과 통계 정보를 함께 반환합니다.
    """

    model_config = ConfigDict(from_attributes=True)

    alerts: list[AlertResponse] = Field(..., description="알림 목록")
    unread_count: int = Field(..., ge=0, description="읽지 않은 알림 수")
