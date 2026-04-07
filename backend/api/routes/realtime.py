"""
실시간 시세 API 라우터

KIS WebSocket으로 수신 중인 실시간 시세를 조회합니다.

엔드포인트:
    GET  /realtime/quotes          - 전 종목 최신 시세
    GET  /realtime/quotes/{ticker} - 단일 종목 시세
    GET  /realtime/status          - 실시간 수신 상태
"""

from fastapi import APIRouter, Depends

from api.middleware.rbac import require_viewer
from api.schemas.common import APIResponse
from config.logging import logger

router = APIRouter()


@router.get("/quotes", response_model=APIResponse[dict])
async def get_all_quotes(current_user=Depends(require_viewer)):
    """전 종목 실시간 시세 조회"""
    try:
        from core.scheduler_handlers import get_realtime_manager

        manager = get_realtime_manager()
        if manager is None or not manager.is_running:
            return APIResponse(
                success=False,
                message="실시간 시세 수신이 비활성 상태입니다",
            )

        snapshots = manager.get_all_snapshots()
        data = {ticker: snap.to_dict() for ticker, snap in snapshots.items() if snap.price > 0}

        return APIResponse(
            success=True,
            data={
                "count": len(data),
                "quotes": data,
            },
        )
    except Exception as e:
        logger.error(f"Realtime quotes error: {e}")
        return APIResponse(success=False, message=str(e))


@router.get("/quotes/{ticker}", response_model=APIResponse[dict])
async def get_ticker_quote(
    ticker: str,
    current_user=Depends(require_viewer),
):
    """단일 종목 실시간 시세 조회"""
    try:
        from core.scheduler_handlers import get_realtime_manager

        manager = get_realtime_manager()
        if manager is None or not manager.is_running:
            return APIResponse(
                success=False,
                message="실시간 시세 수신이 비활성 상태입니다",
            )

        snapshot = manager.get_snapshot(ticker)
        if snapshot is None:
            return APIResponse(
                success=False,
                message=f"{ticker}: 구독되지 않은 종목입니다",
            )

        return APIResponse(success=True, data=snapshot.to_dict())
    except Exception as e:
        logger.error(f"Realtime quote {ticker} error: {e}")
        return APIResponse(success=False, message=str(e))


@router.get("/status", response_model=APIResponse[dict])
async def get_realtime_status(current_user=Depends(require_viewer)):
    """실시간 수신 상태 조회"""
    try:
        from core.scheduler_handlers import get_realtime_manager

        manager = get_realtime_manager()
        if manager is None:
            return APIResponse(
                success=True,
                data={"running": False, "reason": "not_started"},
            )

        return APIResponse(success=True, data=manager.stats)
    except Exception as e:
        logger.error(f"Realtime status error: {e}")
        return APIResponse(success=False, message=str(e))
