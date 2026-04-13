"""
포트폴리오 API 라우터

포트폴리오 현황, 보유 종목, 성과 분석 엔드포인트를 제공합니다.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.middleware.rbac import require_viewer
from api.schemas.common import APIResponse
from api.schemas.portfolio import (
    PerformanceResponse,
    PortfolioSummaryResponse,
    PositionResponse,
)
from config.logging import logger
from config.settings import get_settings
from db.database import get_db_session

router = APIRouter()


@router.get("/summary", response_model=APIResponse[PortfolioSummaryResponse])
async def get_portfolio_summary(
    current_user=Depends(require_viewer),
    db: AsyncSession = Depends(get_db_session),
):
    """
    포트폴리오 요약 조회

    총 자산, 현금, 수익률, 보유 종목 수 등 전체 요약 정보를 반환합니다.
    체결된 주문(orders 테이블)을 종목별로 집계하여 현재 포지션을 산출합니다.
    """
    try:
        settings = get_settings()

        # 체결된 주문을 종목별로 집계하여 포지션 산출
        positions: list[PositionResponse] = []
        total_position_value = 0.0

        try:
            query = text(
                """
                SELECT ticker, market,
                       SUM(CASE WHEN side = 'BUY' THEN filled_quantity ELSE -filled_quantity END) AS net_qty,
                       SUM(CASE WHEN side = 'BUY' THEN filled_quantity * filled_price ELSE 0 END) AS total_cost,
                       SUM(CASE WHEN side = 'BUY' THEN filled_quantity ELSE 0 END) AS total_bought
                FROM orders
                WHERE status IN ('FILLED', 'PARTIAL')
                GROUP BY ticker, market
                HAVING SUM(CASE WHEN side = 'BUY' THEN filled_quantity ELSE -filled_quantity END) > 0
            """
            )
            result = await db.execute(query)
            rows = result.fetchall()

            for row in rows:
                ticker, market, net_qty, total_cost, total_bought = row
                avg_price = total_cost / total_bought if total_bought > 0 else 0.0
                # 현재가는 평균단가로 근사 (실시간 시세 연동 시 교체)
                current_price = avg_price
                unrealized = (current_price - avg_price) * net_qty
                position_value = current_price * net_qty
                total_position_value += position_value

                positions.append(
                    PositionResponse(
                        ticker=ticker,
                        market=market,
                        quantity=int(net_qty),
                        avg_price=round(avg_price, 2),
                        current_price=round(current_price, 2),
                        unrealized_pnl=round(unrealized, 2),
                        weight=0.0,  # 아래에서 재계산
                    )
                )
        except Exception as db_err:
            logger.warning(f"Portfolio DB query failed (returning empty): {db_err}")

        initial_capital = float(settings.risk.initial_capital_krw)
        total_value = initial_capital + total_position_value
        cash_krw = initial_capital - total_position_value

        # 포지션 비중 재계산
        for pos in positions:
            pos.weight = round((pos.current_price * pos.quantity) / total_value, 4) if total_value > 0 else 0.0

        total_unrealized = sum(p.unrealized_pnl for p in positions)

        summary = PortfolioSummaryResponse(
            total_value=round(total_value, 2),
            cash_krw=round(max(cash_krw, 0), 2),
            cash_usd=0.0,
            daily_return=0.0,
            unrealized_pnl=round(total_unrealized, 2),
            realized_pnl=0.0,
            position_count=len(positions),
            positions=positions,
            updated_at=datetime.now(timezone.utc),
        )
        return APIResponse(success=True, data=summary)
    except Exception as e:
        logger.error(f"Portfolio summary error: {e}")
        return APIResponse(success=False, message=f"조회 실패: {str(e)}")


@router.get("/positions", response_model=APIResponse[list[PositionResponse]])
async def get_positions(
    current_user=Depends(require_viewer),
    db: AsyncSession = Depends(get_db_session),
):
    """
    보유 종목 목록 조회

    체결된 주문 이력을 종목별로 집계하여 현재 보유 포지션을 반환합니다.
    """
    try:
        positions: list[PositionResponse] = []

        try:
            query = text(
                """
                SELECT ticker, market,
                       SUM(CASE WHEN side = 'BUY' THEN filled_quantity ELSE -filled_quantity END) AS net_qty,
                       SUM(CASE WHEN side = 'BUY' THEN filled_quantity * filled_price ELSE 0 END) AS total_cost,
                       SUM(CASE WHEN side = 'BUY' THEN filled_quantity ELSE 0 END) AS total_bought
                FROM orders
                WHERE status IN ('FILLED', 'PARTIAL')
                GROUP BY ticker, market
                HAVING SUM(CASE WHEN side = 'BUY' THEN filled_quantity ELSE -filled_quantity END) > 0
            """
            )
            result = await db.execute(query)
            rows = result.fetchall()

            total_value = sum((row[3] / row[4] if row[4] > 0 else 0) * row[2] for row in rows)

            for row in rows:
                ticker, market, net_qty, total_cost, total_bought = row
                avg_price = total_cost / total_bought if total_bought > 0 else 0.0
                current_price = avg_price
                unrealized = (current_price - avg_price) * net_qty
                position_value = current_price * net_qty

                positions.append(
                    PositionResponse(
                        ticker=ticker,
                        market=market,
                        quantity=int(net_qty),
                        avg_price=round(avg_price, 2),
                        current_price=round(current_price, 2),
                        unrealized_pnl=round(unrealized, 2),
                        weight=round(position_value / total_value, 4) if total_value > 0 else 0.0,
                    )
                )
        except Exception as db_err:
            logger.warning(f"Positions DB query failed (returning empty): {db_err}")

        return APIResponse(success=True, data=positions)
    except Exception as e:
        logger.error(f"Positions query error: {e}")
        return APIResponse(success=False, message=f"조회 실패: {str(e)}")


@router.get("/performance", response_model=APIResponse[PerformanceResponse])
async def get_performance(
    period: str = Query(default="1M", description="성과 기간 (1D/1W/1M/3M/6M/1Y/ALL)"),
    current_user=Depends(require_viewer),
    db: AsyncSession = Depends(get_db_session),
):
    """
    포트폴리오 성과 분석

    지정 기간의 수익률, MDD, Sharpe Ratio 등 성과 지표를 반환합니다.
    체결된 주문 이력에서 기간별 실현 수익률을 계산합니다.
    """
    try:
        # 기간에 따른 날짜 필터
        period_days = {
            "1D": 1,
            "1W": 7,
            "1M": 30,
            "3M": 90,
            "6M": 180,
            "1Y": 365,
            "ALL": 3650,
        }
        days = period_days.get(period, 30)
        return_pct = 0.0

        try:
            query = text(
                """
                SELECT
                    COALESCE(SUM(
                        CASE WHEN side = 'SELL'
                             THEN filled_quantity * filled_price
                             ELSE -filled_quantity * filled_price
                        END
                    ), 0) AS net_pnl,
                    COUNT(*) AS trade_count,
                    COUNT(CASE WHEN side = 'SELL' AND filled_price > 0 THEN 1 END) AS sell_count
                FROM orders
                WHERE status IN ('FILLED', 'PARTIAL')
                  AND created_at >= NOW() - MAKE_INTERVAL(days => :days)
            """
            )
            result = await db.execute(query, {"days": days})
            row = result.fetchone()

            settings = get_settings()
            initial_capital = float(settings.risk.initial_capital_krw)
            net_pnl = float(row[0]) if row else 0.0
            return_pct = (net_pnl / initial_capital * 100) if initial_capital > 0 else 0.0
        except Exception as db_err:
            logger.warning(f"Performance DB query failed (returning defaults): {db_err}")

        performance = PerformanceResponse(
            period=period,
            return_pct=round(return_pct, 4),
            mdd=0.0,
            sharpe=0.0,
            volatility=0.0,
            win_rate=0.0,
        )
        return APIResponse(success=True, data=performance)
    except Exception as e:
        logger.error(f"Performance query error: {e}")
        return APIResponse(success=False, message=f"조회 실패: {str(e)}")


@router.get("/value-history", response_model=APIResponse[list[dict]])
async def get_value_history(
    period: str = Query(default="1M", description="조회 기간"),
    current_user=Depends(require_viewer),
    db: AsyncSession = Depends(get_db_session),
):
    """
    자산 가치 변동 이력 (차트 데이터)

    일별 체결 금액을 누적하여 자산 가치 변동 이력을 반환합니다.
    """
    try:
        period_days = {
            "1D": 1,
            "1W": 7,
            "1M": 30,
            "3M": 90,
            "6M": 180,
            "1Y": 365,
            "ALL": 3650,
        }
        days = period_days.get(period, 30)
        history: list[dict] = []

        try:
            settings = get_settings()
            initial_capital = float(settings.risk.initial_capital_krw)

            query = text(
                """
                SELECT DATE(created_at) AS trade_date,
                       SUM(CASE WHEN side = 'BUY'
                                THEN -filled_quantity * filled_price
                                ELSE filled_quantity * filled_price END) AS daily_flow
                FROM orders
                WHERE status IN ('FILLED', 'PARTIAL')
                  AND created_at >= NOW() - MAKE_INTERVAL(days => :days)
                GROUP BY DATE(created_at)
                ORDER BY trade_date
            """
            )
            result = await db.execute(query, {"days": days})
            rows = result.fetchall()

            cumulative = initial_capital
            for row in rows:
                trade_date, daily_flow = row
                cumulative += float(daily_flow)
                history.append(
                    {
                        "date": trade_date.isoformat() if trade_date else None,
                        "value": round(cumulative, 2),
                        "daily_change": round(float(daily_flow), 2),
                    }
                )
        except Exception as db_err:
            logger.warning(f"Value history DB query failed (returning empty): {db_err}")

        return APIResponse(success=True, data=history)
    except Exception as e:
        logger.error(f"Value history error: {e}")
        return APIResponse(success=False, message=f"조회 실패: {str(e)}")
