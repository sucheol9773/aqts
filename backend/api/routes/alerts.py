"""
알림 API 라우터

알림 이력 조회, 확인 처리, 통계 엔드포인트를 제공합니다.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.middleware.rbac import require_operator, require_viewer
from api.schemas.alerts import AlertListResponse, AlertResponse, AlertStatsResponse
from api.schemas.common import APIResponse
from config.constants import AlertType
from config.logging import logger
from core.notification.alert_manager import AlertLevel, AlertManager

router = APIRouter()

# 모듈 레벨 AlertManager 인스턴스 (startup 시 MongoDB 컬렉션 주입 가능)
_alert_manager = AlertManager()


def get_alert_manager() -> AlertManager:
    """AlertManager 의존성"""
    return _alert_manager


@router.get("/", response_model=APIResponse[AlertListResponse])
async def get_alerts(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    alert_type: Optional[str] = Query(default=None, description="알림 유형 필터"),
    level: Optional[str] = Query(default=None, description="심각도 필터"),
    current_user=Depends(require_viewer),
    manager: AlertManager = Depends(get_alert_manager),
):
    """
    알림 이력 조회

    필터 조건에 맞는 알림 목록과 미확인 알림 수를 반환합니다.
    """
    try:
        at = AlertType(alert_type) if alert_type else None
        lv = AlertLevel(level) if level else None

        alerts = await manager.get_alerts(
            limit=limit,
            offset=offset,
            alert_type=at,
            level=lv,
        )
        unread = await manager.get_unread_count()

        response = AlertListResponse(
            alerts=[AlertResponse(**a) if isinstance(a, dict) else a for a in alerts],
            unread_count=unread,
        )
        return APIResponse(success=True, data=response)
    except Exception as e:
        logger.error(f"Alerts query error: {e}")
        return APIResponse(success=False, message=f"알림 조회 실패: {str(e)}")


@router.get("/stats", response_model=APIResponse[AlertStatsResponse])
async def get_alert_stats(
    current_user=Depends(require_viewer),
    manager: AlertManager = Depends(get_alert_manager),
):
    """
    알림 통계 조회
    """
    try:
        stats = await manager.get_alert_stats()
        return APIResponse(
            success=True,
            data=AlertStatsResponse(**stats),
        )
    except Exception as e:
        logger.error(f"Alert stats error: {e}")
        return APIResponse(success=False, message=f"통계 조회 실패: {str(e)}")


@router.put("/{alert_id}/read", response_model=APIResponse[dict])
async def mark_alert_read(
    alert_id: str,
    current_user=Depends(require_operator),
    manager: AlertManager = Depends(get_alert_manager),
):
    """
    알림 확인 처리
    """
    try:
        result = await manager.mark_alert_read(alert_id)
        if result:
            return APIResponse(success=True, data={"alert_id": alert_id}, message="확인 처리됨")
        return APIResponse(success=False, message="알림을 찾을 수 없습니다.")
    except Exception as e:
        logger.error(f"Mark alert read error: {e}")
        return APIResponse(success=False, message=f"처리 실패: {str(e)}")


@router.put("/read-all", response_model=APIResponse[dict])
async def mark_all_alerts_read(
    current_user=Depends(require_operator),
    manager: AlertManager = Depends(get_alert_manager),
):
    """
    모든 알림 일괄 확인 처리
    """
    try:
        count = await manager.mark_all_read()
        return APIResponse(
            success=True,
            data={"marked_count": count},
            message=f"{count}건의 알림이 확인 처리되었습니다.",
        )
    except Exception as e:
        logger.error(f"Mark all read error: {e}")
        return APIResponse(success=False, message=f"처리 실패: {str(e)}")
