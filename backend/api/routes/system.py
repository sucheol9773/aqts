"""
시스템 API 라우터

시스템 설정, 백테스트, 리밸런싱 등 관리 엔드포인트를 제공합니다.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from api.middleware.rate_limiter import RATE_PIPELINE, limiter
from api.middleware.rbac import require_admin, require_operator, require_viewer
from api.schemas.common import APIResponse
from config.constants import Market
from config.logging import logger
from config.settings import get_settings
from core.backtest_engine.engine import BacktestConfig, BacktestEngine
from core.circuit_breaker import CircuitBreakerRegistry
from core.pipeline import InvestmentDecisionPipeline
from core.portfolio_manager.construction import PortfolioConstructionEngine
from core.portfolio_manager.profile import InvestorProfileManager
from core.portfolio_manager.rebalancing import RebalancingEngine
from db.database import RedisManager, get_db_session
from db.repositories.audit_log import AuditLogger

router = APIRouter()


@router.get("/settings", response_model=APIResponse[dict])
async def get_system_settings(current_user=Depends(require_admin)):
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
    current_user=Depends(require_operator),
    db: AsyncSession = Depends(get_db_session),
):
    """
    백테스트 실행

    지정된 종목·기간·전략으로 백테스트를 실행하고 결과를 반환합니다.
    """
    try:
        logger.info(f"Backtest started: {ticker} ({start_date} ~ {end_date})")

        strategy_name = strategy or "ENSEMBLE"

        # BacktestConfig 생성
        from config.constants import Country

        config = BacktestConfig(
            initial_capital=50_000_000,
            start_date=start_date,
            end_date=end_date,
            country=Country.KR,
        )

        # 샘플 신호 및 가격 데이터 생성 (실제로는 DataCollector에서 로드)
        # 날짜 범위 생성
        date_range = pd.date_range(start=start_date, end=end_date, freq="D")

        # 샘플 신호 DataFrame (날짜 × 종목)
        signals = pd.DataFrame(
            [[0.5]],  # 단일 종목, 단일 시그널 값
            index=[date_range[0]],
            columns=[ticker],
        )

        # 샘플 가격 DataFrame (날짜 × 종목)
        prices = pd.DataFrame(
            [[100.0 + i * 0.5 for i in range(len(date_range))]],
            index=date_range,
            columns=[ticker],
        ).T

        # BacktestEngine 실행 (동기식이므로 executor 사용)
        engine = BacktestEngine(config)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, engine.run, strategy_name, signals, prices)

        # 감사 로그 기록
        audit = AuditLogger(db)
        await audit.log(
            action_type="BACKTEST_EXECUTED",
            module="backtest_engine",
            description=f"Backtest completed: {ticker} ({start_date}~{end_date}), Strategy={strategy_name}",
            metadata={
                "ticker": ticker,
                "strategy": strategy_name,
                "total_return": float(result.total_return),
                "cagr": float(result.cagr),
                "sharpe_ratio": float(result.sharpe_ratio),
            },
        )

        return APIResponse(
            success=True,
            data={
                "ticker": ticker,
                "start_date": result.start_date,
                "end_date": result.end_date,
                "strategy": strategy_name,
                "status": "completed",
                "total_return": float(result.total_return),
                "cagr": float(result.cagr),
                "mdd": float(result.mdd),
                "sharpe_ratio": float(result.sharpe_ratio),
                "sortino_ratio": float(result.sortino_ratio),
                "calmar_ratio": float(result.calmar_ratio),
                "win_rate": float(result.win_rate),
                "profit_factor": float(result.profit_factor),
                "total_trades": result.total_trades,
                "final_capital": float(result.final_capital),
                "initial_capital": float(result.initial_capital),
            },
        )
    except Exception as e:
        logger.error(f"Backtest error: {e}")
        return APIResponse(success=False, message=f"백테스트 실행 실패: {str(e)}")


@router.post("/rebalancing", response_model=APIResponse[dict])
async def trigger_rebalancing(
    rebalancing_type: str = Query(default="MANUAL", description="리밸런싱 유형 (SCHEDULED/EMERGENCY/MANUAL)"),
    current_user=Depends(require_operator),
    db: AsyncSession = Depends(get_db_session),
):
    """
    수동 리밸런싱 트리거

    현재 포트폴리오 상태를 분석하여 리밸런싱을 실행합니다.
    """
    try:
        logger.info(f"Rebalancing triggered: {rebalancing_type}")

        # 감사 로그 기록
        audit = AuditLogger(db)
        await audit.log(
            action_type="REBALANCING_TRIGGERED",
            module="portfolio_manager",
            description=f"Manual rebalancing triggered by user {current_user}",
            metadata={
                "rebalancing_type": rebalancing_type,
                "user": current_user,
            },
        )

        triggered_at = datetime.now(timezone.utc)

        # ── 1. 사용자 프로필 조회 ──
        profile_mgr = InvestorProfileManager(db)
        profile = await profile_mgr.get_profile(current_user.id)
        if not profile:
            return APIResponse(
                success=False,
                message="투자자 프로필이 없습니다. 먼저 프로필을 생성해주세요.",
            )

        # ── 2. Redis에서 앙상블 시그널 조회 ──
        ensemble_signals: dict[str, float] = {}
        try:
            redis = RedisManager.get_client()
            keys = await redis.keys("ensemble:latest:*")
            for key in keys:
                key_str = key if isinstance(key, str) else key.decode()
                if key_str.endswith("_summary"):
                    continue
                ticker = key_str.split(":")[-1]
                raw = await redis.get(key_str)
                if raw:
                    data = json.loads(raw)
                    if "error" not in data and "ensemble_signal" in data:
                        ensemble_signals[ticker] = float(data["ensemble_signal"])
        except Exception as redis_err:
            logger.warning(f"[Rebalancing] Redis 시그널 조회 실패: {redis_err}")

        if not ensemble_signals:
            return APIResponse(
                success=False,
                message="앙상블 시그널이 없습니다. MARKET_OPEN 실행이 필요합니다.",
            )

        # ── 3. 현재 포지션 및 유니버스 정보 조회 ──
        current_portfolio: dict[str, float] = {}
        sector_info: dict[str, str] = {}
        market_info: dict[str, Market] = {}

        try:
            # 현재 포지션
            pos_query = text(
                """
                SELECT ticker,
                       SUM(CASE WHEN side = 'BUY' THEN filled_quantity * filled_price
                                ELSE -filled_quantity * filled_price END) AS position_value
                FROM orders
                WHERE status IN ('FILLED', 'PARTIAL')
                GROUP BY ticker
                HAVING SUM(CASE WHEN side = 'BUY' THEN filled_quantity
                                ELSE -filled_quantity END) > 0
                """
            )
            result = await db.execute(pos_query)
            total_pos_value = 0.0
            pos_values: dict[str, float] = {}
            for row in result.fetchall():
                ticker, pos_val = row
                pos_values[ticker] = float(pos_val)
                total_pos_value += float(pos_val)
            if total_pos_value > 0:
                for ticker, val in pos_values.items():
                    current_portfolio[ticker] = val / total_pos_value

            # 유니버스 정보
            uni_query = text("SELECT ticker, sector, market FROM universe WHERE is_active = true")
            result = await db.execute(uni_query)
            for row in result.fetchall():
                ticker, sector, market = row
                if sector:
                    sector_info[ticker] = sector
                try:
                    market_info[ticker] = Market(market)
                except ValueError:
                    pass
        except Exception as db_err:
            logger.warning(f"[Rebalancing] DB 조회 실패: {db_err}")

        # ── 4. 리밸런싱 엔진 실행 ──
        construction_engine = PortfolioConstructionEngine(
            risk_profile=profile.risk_profile,
        )
        rebalancing_engine = RebalancingEngine(profile, construction_engine)

        rebal_result = await rebalancing_engine.execute_scheduled_rebalancing(
            ensemble_signals=ensemble_signals,
            current_portfolio=current_portfolio,
            seed_capital=profile.seed_amount,
            sector_info=sector_info if sector_info else None,
            market_info=market_info if market_info else None,
        )

        logger.info(f"[Rebalancing] 완료: type={rebalancing_type}, " f"orders={len(rebal_result.orders)}건")

        return APIResponse(
            success=True,
            data={
                "type": rebalancing_type,
                "status": "completed",
                "triggered_at": triggered_at.isoformat(),
                "user": current_user,
                "result": rebal_result.to_dict(),
            },
            message=f"리밸런싱 완료: {len(rebal_result.orders)}건 주문 생성",
        )
    except Exception as e:
        logger.error(f"Rebalancing error: {e}")
        return APIResponse(success=False, message=f"리밸런싱 실패: {str(e)}")


@router.post("/pipeline", response_model=APIResponse[dict])
@limiter.limit(RATE_PIPELINE)
async def run_analysis_pipeline(
    request: Request,
    tickers: str = Query(..., description="종목코드 (콤마 구분)"),
    force_refresh: bool = Query(default=False, description="캐시 무시"),
    current_user=Depends(require_operator),
    db: AsyncSession = Depends(get_db_session),
):
    """
    투자 분석 파이프라인 실행

    뉴스 수집 → AI 감성 분석 → 투자 의견 → 앙상블 시그널 산출을
    일괄 실행합니다.
    """
    try:
        ticker_list = [t.strip() for t in tickers.split(",") if t.strip()]
        logger.info(f"Pipeline triggered: {ticker_list}")

        # InvestmentDecisionPipeline 인스턴스 생성
        pipeline = InvestmentDecisionPipeline()

        # 배치 분석 실행
        results = await pipeline.run_batch_analysis(
            tickers=ticker_list,
            force_refresh=force_refresh,
        )

        # 결과 변환 (PipelineResult를 dict로)
        result_dict = {}
        succeeded_count = 0
        blocked_count = 0

        for ticker, result in results.items():
            blocked = result.blocked
            blocked_by = result.blocked_by or ""

            if blocked:
                blocked_count += 1
                result_dict[ticker] = {
                    "ticker": ticker,
                    "status": "blocked",
                    "blocked_by": blocked_by,
                    "ensemble_signal": None,
                }
            else:
                succeeded_count += 1
                ensemble = result.ensemble_signal
                result_dict[ticker] = {
                    "ticker": ticker,
                    "status": "completed",
                    "ensemble_signal": float(ensemble.final_signal) if ensemble else None,
                    "action": ensemble.action if ensemble else None,
                    "confidence": float(ensemble.final_confidence) if ensemble else None,
                }

        # 감사 로그 기록
        audit = AuditLogger(db)
        await audit.log(
            action_type="PIPELINE_EXECUTED",
            module="pipeline",
            description=f"Investment decision pipeline executed for {len(ticker_list)} tickers",
            metadata={
                "tickers": ticker_list,
                "succeeded": succeeded_count,
                "blocked": blocked_count,
                "force_refresh": force_refresh,
                "user": current_user,
            },
        )

        return APIResponse(
            success=True,
            data={
                "tickers": ticker_list,
                "status": "completed",
                "force_refresh": force_refresh,
                "succeeded": succeeded_count,
                "blocked": blocked_count,
                "results": result_dict,
            },
            message=f"{len(ticker_list)}개 종목 분석 완료 (성공: {succeeded_count}, 차단: {blocked_count})",
        )
    except Exception as e:
        logger.error(f"Pipeline error: {e}")
        return APIResponse(success=False, message=f"파이프라인 실행 실패: {str(e)}")


@router.get("/audit-logs", response_model=APIResponse[list[dict]])
async def get_audit_logs(
    limit: int = Query(default=50, ge=1, le=200),
    module: Optional[str] = Query(default=None, description="모듈 필터"),
    current_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """
    감사 로그 조회
    """
    try:
        # 쿼리 생성
        if module:
            query = text(
                """
                SELECT id, time, action_type, module, description, before_state, after_state, metadata
                FROM audit_logs
                WHERE module = :module
                ORDER BY time DESC
                LIMIT :limit
            """
            )
            result = await db.execute(query, {"module": module, "limit": limit})
        else:
            query = text(
                """
                SELECT id, time, action_type, module, description, before_state, after_state, metadata
                FROM audit_logs
                ORDER BY time DESC
                LIMIT :limit
            """
            )
            result = await db.execute(query, {"limit": limit})

        rows = result.fetchall()

        # 결과를 딕셔너리로 변환
        logs = []
        for row in rows:
            logs.append(
                {
                    "id": row[0],
                    "time": row[1].isoformat() if row[1] else None,
                    "action_type": row[2],
                    "module": row[3],
                    "description": row[4],
                    "before_state": row[5],
                    "after_state": row[6],
                    "metadata": row[7],
                }
            )

        logger.info(f"Audit logs retrieved: {len(logs)} records (module={module})")
        return APIResponse(success=True, data=logs)
    except Exception as e:
        logger.error(f"Audit logs error: {e}")
        return APIResponse(success=False, message=f"감사 로그 조회 실패: {str(e)}")


@router.get("/circuit-breakers", response_model=APIResponse[dict])
async def get_circuit_breaker_status(
    current_user=Depends(require_viewer),
):
    """
    Circuit Breaker 상태 조회

    모든 외부 서비스 (KIS, FRED, ECOS, Anthropic)의
    서킷 브레이커 상태를 반환합니다.
    """
    return APIResponse(
        success=True,
        data=CircuitBreakerRegistry.status(),
    )
