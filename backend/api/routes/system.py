"""
시스템 API 라우터

시스템 설정, 백테스트, 리밸런싱 등 관리 엔드포인트를 제공합니다.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.middleware.auth import get_current_user
from api.schemas.common import APIResponse
from config.logging import logger
from config.settings import get_settings

router = APIRouter()


@router.get("/settings", response_model=APIResponse[dict])
async def get_system_settings(current_user: str = Depends(get_current_user)):
    """
    시스템 설정 조회

    현재 활성화된 거래 모드, 리스크 관리 설정 등을 반환합니다.
    민감 정보(API 키, 비밀번호)는 마스킹 처리됩니다.
    """
    try:
        settings = get_settings()
        return APIResponse(
            success=True,
            data={
                "environment": settings.environment,
                "trading_mode": settings.kis.trading_mode.value,
                "risk_management": {
                    "initial_capital_krw": settings.risk.initial_capital_krw,
                    "daily_loss_limit_krw": settings.risk.daily_loss_limit_krw,
                    "max_order_amount_krw": settings.risk.max_order_amount_krw,
                    "max_positions": settings.risk.max_positions,
                    "max_position_weight": settings.risk.max_position_weight,
                    "max_sector_weight": settings.risk.max_sector_weight,
                    "max_drawdown": settings.risk.max_drawdown,
                    "stop_loss_percent": settings.risk.stop_loss_percent,
                },
                "telegram": {
                    "alert_level": settings.telegram.alert_level,
                    "chat_id": settings.telegram.chat_id[:4] + "****",
                },
            },
        )
    except Exception as e:
        logger.error(f"Settings query error: {e}")
        return APIResponse(success=False, message=f"조회 실패: {str(e)}")


@router.post("/backtest", response_model=APIResponse[dict])
async def run_backtest(
    ticker: str = Query(..., description="종목코드"),
    start_date: str = Query(..., description="시작일 (YYYY-MM-DD)"),
    end_date: str = Query(..., description="종료일 (YYYY-MM-DD)"),
    strategy: Optional[str] = Query(default=None, description="전략 유형"),
    current_user: str = Depends(get_current_user),
):
    """
    백테스트 실행

    지정된 종목·기간·전략으로 백테스트를 실행하고 결과를 반환합니다.
    """
    try:
        # TODO: BacktestEngine 연동
        logger.info(f"Backtest started: {ticker} ({start_date} ~ {end_date})")
        return APIResponse(
            success=True,
            data={
                "ticker": ticker,
                "start_date": start_date,
                "end_date": end_date,
                "strategy": strategy or "ENSEMBLE",
                "status": "queued",
                "message": "백테스트가 큐에 등록되었습니다.",
            },
        )
    except Exception as e:
        logger.error(f"Backtest error: {e}")
        return APIResponse(success=False, message=f"백테스트 실행 실패: {str(e)}")


@router.post("/rebalancing", response_model=APIResponse[dict])
async def trigger_rebalancing(
    rebalancing_type: str = Query(
        default="MANUAL", description="리밸런싱 유형 (SCHEDULED/EMERGENCY/MANUAL)"
    ),
    current_user: str = Depends(get_current_user),
):
    """
    수동 리밸런싱 트리거

    현재 포트폴리오 상태를 분석하여 리밸런싱을 실행합니다.
    """
    try:
        # TODO: RebalancingEngine 연동
        logger.info(f"Rebalancing triggered: {rebalancing_type}")
        return APIResponse(
            success=True,
            data={
                "type": rebalancing_type,
                "status": "queued",
                "triggered_at": datetime.now(timezone.utc).isoformat(),
            },
            message="리밸런싱이 요청되었습니다.",
        )
    except Exception as e:
        logger.error(f"Rebalancing error: {e}")
        return APIResponse(success=False, message=f"리밸런싱 실패: {str(e)}")


@router.post("/pipeline", response_model=APIResponse[dict])
async def run_analysis_pipeline(
    tickers: str = Query(..., description="종목코드 (콤마 구분)"),
    force_refresh: bool = Query(default=False, description="캐시 무시"),
    current_user: str = Depends(get_current_user),
):
    """
    투자 분석 파이프라인 실행

    뉴스 수집 → AI 감성 분석 → 투자 의견 → 앙상블 시그널 산출을
    일괄 실행합니다.
    """
    try:
        ticker_list = [t.strip() for t in tickers.split(",") if t.strip()]
        # TODO: InvestmentDecisionPipeline 연동
        logger.info(f"Pipeline triggered: {ticker_list}")
        return APIResponse(
            success=True,
            data={
                "tickers": ticker_list,
                "status": "queued",
                "force_refresh": force_refresh,
            },
            message=f"{len(ticker_list)}개 종목 분석이 시작되었습니다.",
        )
    except Exception as e:
        logger.error(f"Pipeline error: {e}")
        return APIResponse(success=False, message=f"파이프라인 실행 실패: {str(e)}")


@router.get("/audit-logs", response_model=APIResponse[list[dict]])
async def get_audit_logs(
    limit: int = Query(default=50, ge=1, le=200),
    module: Optional[str] = Query(default=None, description="모듈 필터"),
    current_user: str = Depends(get_current_user),
):
    """
    감사 로그 조회
    """
    try:
        # TODO: AuditLogger 연동
        logs: list[dict] = []
        return APIResponse(success=True, data=logs)
    except Exception as e:
        logger.error(f"Audit logs error: {e}")
        return APIResponse(success=False, message=f"감사 로그 조회 실패: {str(e)}")
