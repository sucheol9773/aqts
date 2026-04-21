"""
시스템 API 라우터

시스템 설정, 백테스트, 리밸런싱 등 관리 엔드포인트를 제공합니다.
"""

import asyncio
import json
import uuid
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
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
from core.notification.telegram_transport import create_transport as create_telegram_transport
from core.order_executor.executor import OrderExecutor
from core.order_executor.quote_provider_kis import KISQuoteProvider
from core.pipeline import InvestmentDecisionPipeline
from core.portfolio_ledger import get_portfolio_ledger
from core.portfolio_manager.construction import PortfolioConstructionEngine
from core.portfolio_manager.profile import InvestorProfile, InvestorProfileManager
from core.portfolio_manager.rebalancing import RebalancingEngine
from core.scheduler_idempotency import is_executed, mark_executed
from core.trading_guard import get_trading_guard
from core.utils.timezone import now_kst, to_kst_iso
from db.database import RedisManager, async_session_factory, get_db_session
from db.repositories.audit_log import AuditLogger, AuditWriteFailure

# ── 리밸런싱 분산 락 설정 ──
REBALANCING_LOCK_KEY = "rebalancing:lock"
REBALANCING_LOCK_TTL = 300  # 5분 — 리밸런싱 최대 소요 시간
REBALANCING_IDEM_EVENT = "API_REBALANCING"  # scheduler_idempotency 이벤트 타입

# ── 리밸런싱 백그라운드 태스크 상태 키 ──
REBALANCING_STATUS_PREFIX = "rebalancing:status:"
REBALANCING_STATUS_TTL = 86400  # 24시간 보존

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


async def _update_rebalancing_status(
    task_id: str,
    status: str,
    **kwargs: object,
) -> None:
    """Redis에 리밸런싱 백그라운드 태스크 상태 저장"""
    try:
        redis = RedisManager.get_client()
        payload = {
            "task_id": task_id,
            "status": status,
            "updated_at": now_kst().isoformat(),
            **kwargs,
        }
        await redis.set(
            f"{REBALANCING_STATUS_PREFIX}{task_id}",
            json.dumps(payload, default=str),
            ex=REBALANCING_STATUS_TTL,
        )
    except Exception as e:
        logger.error(f"[Rebalancing] Redis 상태 업데이트 실패: {e}")


async def _run_rebalancing_background(
    task_id: str,
    rebalancing_type: str,
    user_id: str,
    username: str,
    profile: InvestorProfile,
    ensemble_signals: dict[str, float],
    current_portfolio: dict[str, float],
    sector_info: Optional[dict[str, str]],
    market_info: Optional[dict[str, Market]],
) -> None:
    """리밸런싱 주문 실행 백그라운드 태스크

    API 핸들러에서 검증(멱등성, 락, 프로필, 시그널) 완료 후
    asyncio.create_task로 호출된다.
    분산 락 해제와 멱등성 기록은 이 함수 내에서 처리한다.
    """
    try:
        await _update_rebalancing_status(task_id, "running", order_count=0)

        # 리밸런싱 엔진 생성
        construction_engine = PortfolioConstructionEngine(
            risk_profile=profile.risk_profile,
        )
        quote_provider = KISQuoteProvider()
        order_executor = OrderExecutor(quote_provider=quote_provider)
        telegram_notifier = create_telegram_transport()
        rebalancing_engine = RebalancingEngine(
            profile,
            construction_engine,
            telegram_notifier=telegram_notifier,
            order_executor=order_executor,
        )

        rebal_result = await rebalancing_engine.execute_scheduled_rebalancing(
            ensemble_signals=ensemble_signals,
            current_portfolio=current_portfolio,
            seed_capital=profile.seed_amount,
            sector_info=sector_info,
            market_info=market_info,
        )

        # 멱등성 키 기록
        await mark_executed(REBALANCING_IDEM_EVENT)

        # 감사 로그 (백그라운드 세션)
        try:
            async with async_session_factory() as db_session:
                audit = AuditLogger(db_session)
                await audit.log(
                    action_type="REBALANCING_COMPLETED",
                    module="portfolio_manager",
                    description=(f"Rebalancing completed: {len(rebal_result.orders)}건 주문"),
                    metadata={
                        "task_id": task_id,
                        "rebalancing_type": rebalancing_type,
                        "user": user_id,
                        "order_count": len(rebal_result.orders),
                    },
                )
        except Exception as audit_err:
            logger.warning(f"[Rebalancing] 완료 감사 로그 실패: {audit_err}")

        await _update_rebalancing_status(
            task_id,
            "completed",
            order_count=len(rebal_result.orders),
            result_summary=rebal_result.to_dict(),
        )

        logger.info(
            f"[Rebalancing] 백그라운드 완료: task_id={task_id}, "
            f"type={rebalancing_type}, orders={len(rebal_result.orders)}건"
        )

    except Exception as e:
        logger.error(f"[Rebalancing] 백그라운드 실패: task_id={task_id}, error={e}")
        await _update_rebalancing_status(task_id, "failed", error=str(e))
    finally:
        # 분산 락 해제 (성공/실패 무관하게 항상 해제)
        try:
            redis = RedisManager.get_client()
            await redis.delete(REBALANCING_LOCK_KEY)
        except Exception:
            pass  # 락 해제 실패는 TTL 만료로 자동 해소


@router.post("/rebalancing")
async def trigger_rebalancing(
    rebalancing_type: str = Query(
        default="MANUAL",
        description="리밸런싱 유형 (SCHEDULED/EMERGENCY/MANUAL)",
    ),
    force: bool = Query(
        default=False,
        description="같은 거래일 중복 실행 허용 (기본: False)",
    ),
    current_user=Depends(require_operator),
    db: AsyncSession = Depends(get_db_session),
):
    """
    수동 리밸런싱 트리거 (비동기)

    검증 후 즉시 202 Accepted를 반환하고, 주문 실행은 백그라운드에서 처리합니다.
    진행 상태는 GET /api/system/rebalancing/status/{task_id}로 조회합니다.

    중복 실행 방지:
    - 같은 거래일(KST)에는 1회만 실행 (force=true로 강제 가능)
    - 동시 실행 시 Redis 분산 락으로 두 번째 요청 차단 (409)
    """
    try:
        logger.info(f"Rebalancing triggered: {rebalancing_type}")

        # ── 0-a. 거래일 멱등성 체크 (같은 날 중복 실행 방지) ──
        if not force:
            already_ran = await is_executed(REBALANCING_IDEM_EVENT)
            if already_ran:
                logger.info(f"[Rebalancing] 같은 거래일 중복 요청 차단 (user={current_user.username})")
                return APIResponse(
                    success=True,
                    data={"type": rebalancing_type, "status": "already_executed"},
                    message="오늘 이미 리밸런싱이 실행되었습니다. force=true로 강제 실행할 수 있습니다.",
                )

        # ── 0-b. 분산 락 획득 (동시 실행 방지) ──
        redis = RedisManager.get_client()
        lock_acquired = await redis.set(
            REBALANCING_LOCK_KEY,
            f"{current_user.username}:{now_kst().isoformat()}",
            nx=True,
            ex=REBALANCING_LOCK_TTL,
        )
        if not lock_acquired:
            logger.warning(f"[Rebalancing] 분산 락 획득 실패 — 이미 실행 중 (user={current_user.username})")
            return JSONResponse(
                status_code=409,
                content={
                    "success": False,
                    "message": "리밸런싱이 이미 실행 중입니다. 완료 후 다시 시도해주세요.",
                },
            )

        # ── 이후 실패 시 분산 락을 해제하기 위한 플래그 ──
        # 백그라운드 태스크에 진입하면 태스크가 락을 관리한다.
        background_started = False

        try:
            # 감사 로그 기록
            audit = AuditLogger(db)
            await audit.log(
                action_type="REBALANCING_TRIGGERED",
                module="portfolio_manager",
                description=f"Manual rebalancing triggered by user {current_user.username}",
                metadata={
                    "rebalancing_type": rebalancing_type,
                    "user": current_user.id,
                },
            )

            # ── 1. 사용자 프로필 조회 ──
            profile_mgr = InvestorProfileManager()
            profile = await profile_mgr.get_profile(current_user.id)
            if not profile:
                return APIResponse(
                    success=False,
                    message="투자자 프로필이 없습니다. 먼저 프로필을 생성해주세요.",
                )

            # ── 2. Redis에서 앙상블 시그널 조회 ──
            ensemble_signals: dict[str, float] = {}
            try:
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
                    ticker_val, pos_val = row
                    pos_values[ticker_val] = float(pos_val)
                    total_pos_value += float(pos_val)
                if total_pos_value > 0:
                    for ticker_val, val in pos_values.items():
                        current_portfolio[ticker_val] = val / total_pos_value

                uni_query = text("SELECT ticker, sector, market FROM universe WHERE is_active = true")
                result = await db.execute(uni_query)
                for row in result.fetchall():
                    ticker_val, sector, market = row
                    if sector:
                        sector_info[ticker_val] = sector
                    try:
                        market_info[ticker_val] = Market(market)
                    except ValueError:
                        pass
            except Exception as db_err:
                logger.warning(f"[Rebalancing] DB 조회 실패: {db_err}")

            # ── 4. 백그라운드 태스크 생성 → 즉시 202 반환 ──
            kst_now = now_kst()
            task_id = f"{kst_now.strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"

            await _update_rebalancing_status(
                task_id,
                "accepted",
                rebalancing_type=rebalancing_type,
                user=current_user.username,
                signal_count=len(ensemble_signals),
            )

            asyncio.create_task(
                _run_rebalancing_background(
                    task_id=task_id,
                    rebalancing_type=rebalancing_type,
                    user_id=current_user.id,
                    username=current_user.username,
                    profile=profile,
                    ensemble_signals=ensemble_signals,
                    current_portfolio=current_portfolio,
                    sector_info=sector_info if sector_info else None,
                    market_info=market_info if market_info else None,
                )
            )
            background_started = True

            logger.info(
                f"[Rebalancing] 백그라운드 태스크 시작: task_id={task_id}, " f"signals={len(ensemble_signals)}건"
            )

            return JSONResponse(
                status_code=202,
                content={
                    "success": True,
                    "data": {
                        "type": rebalancing_type,
                        "status": "accepted",
                        "task_id": task_id,
                        "signal_count": len(ensemble_signals),
                    },
                    "message": (
                        f"리밸런싱 요청 수락됨 (task_id={task_id}). "
                        f"GET /api/system/rebalancing/status/{task_id}로 진행 상태를 조회하세요."
                    ),
                },
            )
        finally:
            # 백그라운드 태스크가 시작되지 않았으면 여기서 락 해제
            if not background_started:
                try:
                    await redis.delete(REBALANCING_LOCK_KEY)
                except Exception:
                    pass

    except Exception as e:
        logger.error(f"Rebalancing error: {e}")
        # 검증 단계에서 예외 발생 시 락 해제
        try:
            redis = RedisManager.get_client()
            await redis.delete(REBALANCING_LOCK_KEY)
        except Exception:
            pass
        return APIResponse(success=False, message=f"리밸런싱 실패: {str(e)}")


@router.get("/rebalancing/status/{task_id}")
async def get_rebalancing_status(
    task_id: str,
    current_user=Depends(require_viewer),
):
    """
    리밸런싱 백그라운드 태스크 진행 상태 조회

    Returns:
        - accepted: 요청 수락, 실행 대기 중
        - running: 주문 실행 중
        - completed: 완료
        - failed: 실패
    """
    try:
        redis = RedisManager.get_client()
        raw = await redis.get(f"{REBALANCING_STATUS_PREFIX}{task_id}")
        if not raw:
            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "message": f"task_id '{task_id}'를 찾을 수 없습니다.",
                },
            )
        status_data = json.loads(raw)
        return APIResponse(success=True, data=status_data)
    except Exception as e:
        logger.error(f"Rebalancing status error: {e}")
        return APIResponse(success=False, message=f"상태 조회 실패: {str(e)}")


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
                "user": current_user.id,
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
                    "time": to_kst_iso(row[1]),
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


# ═════════════════════════════════════════════════════════════════════════
# TradingGuard Kill Switch — 상태 조회 + 수동 해제
# -------------------------------------------------------------------------
# RBAC Wiring Rule 준수: 상태는 require_viewer, 해제는 require_admin.
# 감사 fail-closed (audit log_strict 실패 시 해제하지 않고 503 AUDIT_UNAVAILABLE).
# 해제 성공 시 PortfolioLedger 를 재hydrate 하여 DB ↔ cache 동기화.
#
# 배경: docs/operations/trading-halt-policy.md v1.1 §4 에 정의된 수동 해제 경로를
# 실제 HTTP API 로 구현. TradingGuard.deactivate_kill_switch() 는 이전까지
# 메서드로만 존재했으며(정의 ≠ 적용), 외부에서 해제할 수 있는 경로가 없었다.
# 회고: docs/operations/phase1-demo-verification-2026-04-11.md §10.17.
# ═════════════════════════════════════════════════════════════════════════


class KillSwitchDeactivateRequest(BaseModel):
    """Kill switch 수동 해제 요청 본문.

    실수로 인한 해제를 방지하기 위해 사유와 이중 확인 플래그를 모두 요구한다.
    사유는 감사 로그에 영구 기록되어 사후 감사의 근거가 된다.
    """

    reason: str = Field(
        ...,
        min_length=10,
        max_length=500,
        description="해제 사유 (감사 로그에 영구 기록됨, 최소 10자).",
    )
    confirm: bool = Field(
        ...,
        description="실수 방지 이중 확인 — 반드시 true 여야 해제가 진행된다.",
    )


class KillSwitchStatus(BaseModel):
    """TradingGuard kill switch 현재 상태 snapshot."""

    kill_switch_on: bool = Field(..., description="kill switch 활성 여부")
    kill_switch_reason: str = Field(..., description="활성화 사유 (비활성 시 빈 문자열)")
    daily_realized_pnl: float = Field(..., description="오늘 실현 손익 (원)")
    daily_order_count: int = Field(..., description="오늘 주문 횟수")
    consecutive_losses: int = Field(..., description="연속 손실 횟수")
    current_drawdown: float = Field(..., description="현재 drawdown (0.0~1.0)")
    peak_portfolio_value: float = Field(..., description="고점 포트폴리오 가치 (원)")
    current_portfolio_value: float = Field(..., description="현재 포트폴리오 가치 (원)")
    last_updated: str = Field(..., description="마지막 상태 갱신 시각 (ISO 8601 UTC)")


class KillSwitchDeactivateResponse(BaseModel):
    """Kill switch 해제 결과."""

    was_on: bool = Field(..., description="해제 직전 활성화 여부")
    previous_reason: str = Field(..., description="해제 직전 활성화 사유")
    deactivated_at: str = Field(..., description="해제 시각 (ISO 8601 KST)")
    ledger_rehydrated: bool = Field(..., description="PortfolioLedger 재hydrate 성공 여부")
    ledger_positions_count: int = Field(..., description="재hydrate 후 in-memory 포지션 개수")
    operator: str = Field(..., description="해제를 수행한 관리자 username")


@router.get(
    "/kill-switch/status",
    response_model=APIResponse[KillSwitchStatus],
)
async def get_kill_switch_status(
    current_user=Depends(require_viewer),
):
    """TradingGuard kill switch 현재 상태 조회.

    어떤 운영자든 (viewer 이상) 상태를 확인할 수 있다. 해제 권한은 별도.
    """
    state = get_trading_guard().state
    snapshot = KillSwitchStatus(
        kill_switch_on=state.kill_switch_on,
        kill_switch_reason=state.kill_switch_reason,
        daily_realized_pnl=state.daily_realized_pnl,
        daily_order_count=state.daily_order_count,
        consecutive_losses=state.consecutive_losses,
        current_drawdown=round(state.current_drawdown, 4),
        peak_portfolio_value=state.peak_portfolio_value,
        current_portfolio_value=state.current_portfolio_value,
        last_updated=state.last_updated.isoformat(),
    )
    return APIResponse(success=True, data=snapshot)


@router.post(
    "/kill-switch/deactivate",
    response_model=APIResponse[KillSwitchDeactivateResponse],
)
async def deactivate_kill_switch(
    body: KillSwitchDeactivateRequest,
    current_user=Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """TradingGuard kill switch 수동 해제.

    절차:
      1. ``confirm=true`` 확인 — false 면 400.
      2. ``log_strict`` 로 감사 기록 선행 — 실패 시 503 AUDIT_UNAVAILABLE
         (**해제하지 않고 반환**; fail-closed).
      3. 감사 성공 후 ``TradingGuard.deactivate_kill_switch()`` 호출.
      4. ``PortfolioLedger.hydrate()`` 재호출로 DB ↔ cache 동기화
         (실패해도 해제 자체는 유효; warning 로그만 기록).

    보안:
      - require_admin (RBAC Wiring Rule).
      - reason + confirm 이중 확인으로 실수 방지.
      - before/after 상태가 감사 로그에 영구 기록.
    """
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error_code": "CONFIRM_REQUIRED",
                "message": "confirm 필드가 true 여야 해제가 진행됩니다.",
            },
        )

    guard = get_trading_guard()
    was_on = guard.state.kill_switch_on
    previous_reason = guard.state.kill_switch_reason

    # 1) 감사 fail-closed: audit 실패 시 해제하지 않는다.
    audit = AuditLogger(db)
    try:
        await audit.log_strict(
            action_type="KILL_SWITCH_DEACTIVATE",
            module="trading_guard",
            description=(
                f"Kill switch manual deactivation (was_on={was_on}) " f"by {current_user.username}: {body.reason}"
            ),
            before_state={
                "kill_switch_on": was_on,
                "kill_switch_reason": previous_reason,
            },
            after_state={
                "kill_switch_on": False,
                "kill_switch_reason": "",
            },
            metadata={
                "user_id": current_user.id,
                "username": current_user.username,
                "release_reason": body.reason,
            },
        )
    except AuditWriteFailure:
        logger.critical(
            f"Kill switch deactivation refused — audit fail-closed " f"(was_on={was_on}, user={current_user.username})"
        )
        raise HTTPException(
            status_code=503,
            detail={
                "success": False,
                "error_code": "AUDIT_UNAVAILABLE",
                "message": "감사 시스템 일시 장애로 해제가 차단되었습니다",
                "retry_after_seconds": 30,
            },
        )

    # 2) 감사 통과 후에만 해제.
    guard.deactivate_kill_switch()

    # 3) Ledger 재hydrate — cache 가 stale 하면 다음 reconcile 에서 재차단되므로
    #    해제 직후 DB 에서 실제 포지션을 다시 읽어 cache 를 덮어쓴다.
    ledger = get_portfolio_ledger()
    ledger_rehydrated = False
    positions_count = 0
    try:
        if ledger.repository is not None:
            await ledger.hydrate()
            ledger_rehydrated = True
        positions_count = len(ledger.get_positions())
    except Exception as e:  # noqa: BLE001 — ledger 재hydrate 실패는 해제 자체를 무효화하지 않는다.
        logger.warning(f"Kill switch deactivated but ledger rehydrate failed (user={current_user.username}): {e}")

    deactivated_at = now_kst().isoformat()
    logger.warning(
        f"Kill switch deactivated manually: was_on={was_on} "
        f"user={current_user.username} reason={body.reason!r} "
        f"ledger_rehydrated={ledger_rehydrated} positions={positions_count}"
    )

    return APIResponse(
        success=True,
        data=KillSwitchDeactivateResponse(
            was_on=was_on,
            previous_reason=previous_reason,
            deactivated_at=deactivated_at,
            ledger_rehydrated=ledger_rehydrated,
            ledger_positions_count=positions_count,
            operator=current_user.username,
        ),
    )
