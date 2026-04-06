"""
드라이런 API 라우터

실제 주문 없이 파이프라인을 시뮬레이션하고 결과를 조회하는 엔드포인트를 제공합니다.

엔드포인트:
  POST /api/system/dry-run/start   — 드라이런 세션 시작
  POST /api/system/dry-run/stop    — 현재 세션 종료
  GET  /api/system/dry-run/status  — 현재 세션 상태 조회
  GET  /api/system/dry-run/report  — 전체 리포트 조회
  GET  /api/system/dry-run/sessions/{session_id} — 세션 상세 조회
  DELETE /api/system/dry-run/sessions — 전체 세션 초기화
"""

from fastapi import APIRouter, Depends, HTTPException

from api.middleware.auth import get_current_user
from api.schemas.common import APIResponse
from config.logging import logger
from core.dry_run.engine import get_dry_run_engine

router = APIRouter()


@router.post("/start", response_model=APIResponse[dict])
async def start_dry_run(current_user: str = Depends(get_current_user)):
    """
    드라이런 세션 시작

    새 드라이런 세션을 시작합니다.
    이후 OrderExecutor(dry_run=True)를 통해 실행되는 모든 주문이
    이 세션에 기록됩니다.
    """
    engine = get_dry_run_engine()

    if engine.current_session is not None:
        raise HTTPException(
            status_code=409,
            detail=f"이미 진행 중인 세션이 있습니다: {engine.current_session.session_id}",
        )

    session = engine.start_session()
    logger.info(f"[API] 드라이런 세션 시작: {session.session_id}")

    return APIResponse(
        success=True,
        data={
            "session_id": session.session_id,
            "status": session.status.value,
            "started_at": session.started_at.isoformat(),
        },
        message="드라이런 세션이 시작되었습니다",
    )


@router.post("/stop", response_model=APIResponse[dict])
async def stop_dry_run(current_user: str = Depends(get_current_user)):
    """
    현재 드라이런 세션 종료

    진행 중인 세션을 정상 종료하고 요약 결과를 반환합니다.
    """
    engine = get_dry_run_engine()

    if engine.current_session is None:
        raise HTTPException(
            status_code=404,
            detail="진행 중인 드라이런 세션이 없습니다",
        )

    session = engine.end_session()
    logger.info(f"[API] 드라이런 세션 종료: {session.session_id}")

    return APIResponse(
        success=True,
        data=session.to_dict(),
        message="드라이런 세션이 종료되었습니다",
    )


@router.get("/status", response_model=APIResponse[dict])
async def get_dry_run_status(current_user: str = Depends(get_current_user)):
    """
    현재 드라이런 상태 조회

    진행 중인 세션이 있으면 실시간 상태를, 없으면 마지막 세션 요약을 반환합니다.
    """
    engine = get_dry_run_engine()

    if engine.current_session is not None:
        return APIResponse(
            success=True,
            data={
                "active": True,
                "session": engine.current_session.to_dict(),
            },
        )

    # 마지막 세션 정보
    sessions = engine.sessions
    if sessions:
        last = sessions[-1]
        return APIResponse(
            success=True,
            data={
                "active": False,
                "last_session": last.to_dict(),
                "total_sessions": len(sessions),
            },
        )

    return APIResponse(
        success=True,
        data={"active": False, "total_sessions": 0},
        message="드라이런 기록이 없습니다",
    )


@router.get("/report", response_model=APIResponse[dict])
async def get_dry_run_report(current_user: str = Depends(get_current_user)):
    """
    전체 드라이런 리포트 조회

    모든 세션의 종합 리포트를 반환합니다.
    """
    engine = get_dry_run_engine()
    report = engine.get_report()

    return APIResponse(
        success=True,
        data=report.to_dict(),
    )


@router.get("/sessions/{session_id}", response_model=APIResponse[dict])
async def get_dry_run_session(
    session_id: str,
    current_user: str = Depends(get_current_user),
):
    """
    드라이런 세션 상세 조회

    Args:
        session_id: 조회할 세션 ID
    """
    engine = get_dry_run_engine()
    session = engine.get_session(session_id)

    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"세션을 찾을 수 없습니다: {session_id}",
        )

    return APIResponse(
        success=True,
        data=session.to_dict(),
    )


@router.delete("/sessions", response_model=APIResponse[dict])
async def clear_dry_run_sessions(current_user: str = Depends(get_current_user)):
    """
    전체 드라이런 세션 초기화

    모든 세션 데이터를 삭제합니다.
    """
    engine = get_dry_run_engine()

    if engine.current_session is not None:
        raise HTTPException(
            status_code=409,
            detail="진행 중인 세션이 있습니다. 먼저 종료해주세요.",
        )

    count = engine.clear_sessions()
    logger.info(f"[API] 드라이런 세션 초기화: {count}개 삭제")

    return APIResponse(
        success=True,
        data={"deleted_sessions": count},
        message=f"{count}개 세션이 삭제되었습니다",
    )
