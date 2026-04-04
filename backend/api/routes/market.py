"""
시장 데이터 API 라우터

환율, 시세, 경제지표 조회 엔드포인트를 제공합니다.
ExchangeRateManager, EconomicCollectorService, UniverseManager와 직접 연동합니다.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.middleware.auth import get_current_user
from api.schemas.common import APIResponse
from config.logging import logger
from core.portfolio_manager.exchange_rate import ExchangeRateManager
from core.data_collector.economic_collector import EconomicCollectorService
from core.portfolio_manager.universe import UniverseManager
from core.portfolio_manager.profile import InvestorProfileManager

router = APIRouter()


@router.get("/exchange-rate", response_model=APIResponse[dict])
async def get_exchange_rate(current_user: str = Depends(get_current_user)):
    """
    현재 환율 조회 (USD/KRW)

    KIS API → FRED Fallback → Redis 캐시 순으로 조회합니다.
    """
    try:
        manager = ExchangeRateManager()
        rate_data = await manager.get_current_rate()

        return APIResponse(
            success=True,
            data={
                "currency_pair": rate_data.pair,
                "rate": rate_data.rate,
                "source": rate_data.source,
                "fetched_at": rate_data.fetched_at.isoformat(),
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
    KIS API를 통해 실시간 지수를 조회합니다.
    """
    try:
        from core.data_collector.kis_client import KISClient

        kis = KISClient()
        indices = []

        # 국내 주요 지수 ETF로 간접 조회 (KODEX 200 → KOSPI 근사, KODEX 코스닥150)
        kr_index_proxies = [
            ("KOSPI", "069500"),   # KODEX 200
            ("KOSDAQ", "229200"),  # KODEX 코스닥150
        ]

        for name, proxy_ticker in kr_index_proxies:
            try:
                data = await kis.get_kr_stock_price(proxy_ticker)
                indices.append({
                    "name": name,
                    "value": float(data.get("stck_prpr", 0)),
                    "change": float(data.get("prdy_vrss", 0)),
                    "change_pct": float(data.get("prdy_ctrt", 0)),
                })
            except Exception:
                indices.append({
                    "name": name, "value": 0,
                    "change": 0.0, "change_pct": 0.0,
                })

        # 미국 지수 ETF 근사 (SPY → S&P500, QQQ → NASDAQ)
        us_index_proxies = [
            ("S&P 500", "SPY", "NYS"),
            ("NASDAQ", "QQQ", "NAS"),
        ]

        for name, proxy_ticker, exchange in us_index_proxies:
            try:
                data = await kis.get_us_stock_price(proxy_ticker, exchange)
                indices.append({
                    "name": name,
                    "value": float(data.get("last", 0)),
                    "change": float(data.get("diff", 0)),
                    "change_pct": float(data.get("rate", 0)),
                })
            except Exception:
                indices.append({
                    "name": name, "value": 0,
                    "change": 0.0, "change_pct": 0.0,
                })

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
        service = EconomicCollectorService()

        indicators: list[dict] = []

        if source is None or source.upper() == "FRED":
            try:
                fred_data = await service._fred.collect_all()
                for item in fred_data:
                    indicators.append({
                        "indicator": item.indicator_name,
                        "value": item.value,
                        "date": item.date.isoformat() if hasattr(item, "date") and item.date else None,
                        "source": "FRED",
                        "country": "US",
                    })
            except Exception as fred_err:
                logger.warning(f"FRED data collection failed: {fred_err}")

        if source is None or source.upper() == "ECOS":
            try:
                ecos_data = await service._ecos.collect_all()
                for item in ecos_data:
                    indicators.append({
                        "indicator": item.indicator_name,
                        "value": item.value,
                        "date": item.date.isoformat() if hasattr(item, "date") and item.date else None,
                        "source": "ECOS",
                        "country": "KR",
                    })
            except Exception as ecos_err:
                logger.warning(f"ECOS data collection failed: {ecos_err}")

        return APIResponse(success=True, data=indicators)
    except Exception as e:
        logger.error(f"Economic indicators error: {e}")
        return APIResponse(success=False, message=f"경제지표 조회 실패: {str(e)}")


@router.get("/universe", response_model=APIResponse[list[dict]])
async def get_universe(current_user: str = Depends(get_current_user)):
    """
    투자 유니버스 조회

    현재 활성화된 투자 유니버스 종목 목록을 반환합니다.
    사용자 프로필 기반 섹터 필터 및 자동 필터링을 적용합니다.
    """
    try:
        # 사용자 프로필 조회
        profile_manager = InvestorProfileManager()
        profile = await profile_manager.get_profile(current_user)

        if profile is None:
            # 프로필 미존재 시 기본 프로필로 유니버스 생성
            from core.portfolio_manager.profile import InvestorProfile
            from config.constants import RiskProfile, InvestmentStyle, RebalancingFrequency

            profile = InvestorProfile(
                user_id=current_user,
                risk_profile=RiskProfile.BALANCED,
                seed_capital=50_000_000,
                investment_purpose="WEALTH_GROWTH",
                investment_style=InvestmentStyle.ADVISORY,
                loss_tolerance=0.10,
            )

        universe_manager = UniverseManager(profile)
        items = await universe_manager.build_universe()

        universe: list[dict] = [item.to_dict() for item in items]
        return APIResponse(success=True, data=universe)
    except Exception as e:
        logger.error(f"Universe query error: {e}")
        return APIResponse(success=False, message=f"유니버스 조회 실패: {str(e)}")
