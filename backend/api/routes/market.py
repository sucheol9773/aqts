"""
시장 데이터 API 라우터

환율, 시세, 경제지표 조회 엔드포인트를 제공합니다.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.middleware.auth import get_current_user
from api.schemas.common import APIResponse
from config.logging import logger

router = APIRouter()


@router.get("/exchange-rate", response_model=APIResponse[dict])
async def get_exchange_rate(current_user: str = Depends(get_current_user)):
    """
    현재 환율 조회 (USD/KRW)

    KIS API → FRED Fallback → Redis 캐시 순으로 조회합니다.
    """
    try:
        # TODO: ExchangeRateManager 연동
        return APIResponse(
            success=True,
            data={
                "currency_pair": "USD/KRW",
                "rate": 1350.0,
                "source": "cache",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as e:
        logger.error(f"Exchange rate error: {e}")
        return APIResponse(success=False, message=f"환율 조회 실패: {str(e)}")


@router.get("/indices", response_model=APIResponse[list[dict]])
async def get_market_indices(current_user: str = Depends(get_current_user)):
    """
    주요 시장 지수 조회

    KOSPI, KOSDAQ, S&P500, NASDAQ 지수 현황을 반환합니다.
    """
    try:
        # TODO: MarketDataCollector 연동
        indices = [
            {"name": "KOSPI", "value": 0, "change": 0.0, "change_pct": 0.0},
            {"name": "KOSDAQ", "value": 0, "change": 0.0, "change_pct": 0.0},
            {"name": "S&P 500", "value": 0, "change": 0.0, "change_pct": 0.0},
            {"name": "NASDAQ", "value": 0, "change": 0.0, "change_pct": 0.0},
        ]
        return APIResponse(success=True, data=indices)
    except Exception as e:
        logger.error(f"Market indices error: {e}")
        return APIResponse(success=False, message=f"지수 조회 실패: {str(e)}")


@router.get("/economic-indicators", response_model=APIResponse[list[dict]])
async def get_economic_indicators(
    source: Optional[str] = Query(
        default=None, description="데이터 소스 (FRED / ECOS)"
    ),
    current_user: str = Depends(get_current_user),
):
    """
    경제지표 조회

    FRED(미국) 및 ECOS(한국) 주요 경제지표를 반환합니다.
    """
    try:
        # TODO: EconomicCollector 연동
        indicators: list[dict] = []
        return APIResponse(success=True, data=indicators)
    except Exception as e:
        logger.error(f"Economic indicators error: {e}")
        return APIResponse(success=False, message=f"경제지표 조회 실패: {str(e)}")


@router.get("/universe", response_model=APIResponse[list[dict]])
async def get_universe(current_user: str = Depends(get_current_user)):
    """
    투자 유니버스 조회

    현재 활성화된 투자 유니버스 종목 목록을 반환합니다.
    """
    try:
        # TODO: UniverseManager 연동
        universe: list[dict] = []
        return APIResponse(success=True, data=universe)
    except Exception as e:
        logger.error(f"Universe query error: {e}")
        return APIResponse(success=False, message=f"유니버스 조회 실패: {str(e)}")
