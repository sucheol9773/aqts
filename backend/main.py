"""
AQTS - AI Quant Trade System
FastAPI 메인 애플리케이션 엔트리포인트

Lifecycle:
  startup  → DB 연결, 스케줄러 시작
  shutdown → 그레이스풀 셧다운, DB 연결 해제
"""

import asyncio
import os
import signal
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from limits.errors import StorageError
from slowapi.errors import RateLimitExceeded

from api.errors import normalize_error_body
from api.middleware.rate_limiter import (
    limiter,
    rate_limit_exceeded_handler,
    rate_limit_storage_unavailable_handler,
)
from api.middleware.request_logger import RequestLoggingMiddleware

# Phase 5: API 라우터 & 미들웨어
from api.routes import (
    alerts,
    audit,
    auth,
    dry_run,
    ensemble,
    market,
    oos,
    orders,
    param_sensitivity,
    portfolio,
    profile,
    realtime,
    system,
    users,
)
from config.logging import logger, setup_logging
from config.settings import get_settings
from core.data_collector.kis_client import KISClient
from core.data_collector.kis_recovery import (
    DEFAULT_ALERT_THRESHOLD as KIS_RECOVERY_DEFAULT_ALERT_THRESHOLD,
)
from core.data_collector.kis_recovery import (
    DEFAULT_COOLDOWN_SECONDS as KIS_RECOVERY_DEFAULT_COOLDOWN,
)
from core.data_collector.kis_recovery import (
    KISRecoveryState,
    try_recover_kis,
)
from core.data_collector.kis_startup import (
    DEFAULT_JITTER_MAX_SECONDS as KIS_STARTUP_DEFAULT_JITTER,
)
from core.data_collector.kis_startup import (
    jittered_token_issue,
)
from core.graceful_shutdown import GracefulShutdownManager
from core.monitoring.metrics import COMPONENT_HEALTH, SYSTEM_STATUS, setup_prometheus
from core.monitoring.tracing import setup_tracing
from core.portfolio_ledger import configure_portfolio_ledger
from core.trading_scheduler import TradingScheduler
from db.database import MongoDBManager, RedisManager, async_session_factory, engine
from db.repositories.portfolio_positions import SqlPortfolioLedgerRepository

# ══════════════════════════════════════
# 그레이스풀 셧다운 매니저 (NFR-06)
# ══════════════════════════════════════
shutdown_manager = GracefulShutdownManager()
shutdown_event = asyncio.Event()

# 스케줄러 & KIS 클라이언트 (startup에서 초기화)
trading_scheduler: TradingScheduler | None = None
kis_client: KISClient | None = None
kis_recovery_state: KISRecoveryState | None = None

# 알림 재시도 루프 태스크 (Commit 3).
# Router 주입 성공 시 startup 에서 create_task 로 붙이고, shutdown 에서
# cancel 한다. TradingScheduler 의 긴 sleep 과 독립적으로 동작하도록
# `asyncio.create_task` 로 직접 관리한다 (2026-04-09 heartbeat 회귀 교훈).
_alert_retry_task: asyncio.Task | None = None

# 알림 재시도 루프 주기(초). 환경변수로 override 가능.
# 기본값 60s 는 RETRY_BACKOFF_SECONDS[1] 의 해상도와 일치 — 첫 번째
# 백오프(60s) 만기 직후 다음 iteration 에서 픽업되도록 설계.
ALERT_RETRY_LOOP_INTERVAL_SECONDS = 60


def _signal_handler(sig, frame):
    """SIGTERM/SIGINT 시그널 핸들러"""
    logger.warning(f"Received signal {sig}. Initiating graceful shutdown...")
    shutdown_event.set()


# ══════════════════════════════════════
# 애플리케이션 생명주기
# ══════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan 이벤트 핸들러"""
    global _alert_retry_task
    setup_logging()
    settings = get_settings()

    logger.info("=" * 60)
    logger.info("AQTS - AI Quant Trade System Starting...")
    logger.info(f"Environment: {settings.environment}")
    logger.info(f"KIS Trading Mode: {settings.kis.trading_mode.value}")
    logger.info("=" * 60)

    # ── WebSocket 보안 검증 (운영+LIVE에서 ws:// 차단) ──
    settings.kis.validate_websocket_security(settings.environment)

    # 시그널 핸들러 등록
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # DB 연결
    try:
        await MongoDBManager.connect()
        logger.info("MongoDB connected successfully")

        # AlertManager 싱글톤에 MongoDB 컬렉션 주입 (영속화 활성화)
        try:
            from api.routes.alerts import _alert_manager

            _alert_manager.set_collection(MongoDBManager.get_collection("alerts"))
        except Exception as e:
            logger.warning(f"AlertManager MongoDB 주입 실패 (in-memory 폴백): {e}")

        # AlertManager 싱글톤에 NotificationRouter 주입 (Commit 2 wiring)
        # Telegram → File → Console 캐스케이드. 어느 단계가 실패해도
        # 알림 전송이 완전히 중단되지 않도록 다층 방어.
        # set_collection 이후에 실행되어야 하며, router 주입에 실패해도
        # 원인 이벤트 처리 경로에 영향을 주지 않기 위해 swallow.
        try:
            from api.routes.alerts import _alert_manager as _am_for_router
            from core.notification.fallback_notifier import (
                ConsoleNotifier,
                FileNotifier,
                NotificationRouter,
            )
            from core.notification.telegram_adapter import TelegramChannelAdapter

            notification_router = NotificationRouter()
            notification_router.add_channel(TelegramChannelAdapter())
            notification_router.add_channel(FileNotifier())
            notification_router.add_channel(ConsoleNotifier())
            _am_for_router.set_router(notification_router)
            logger.info("NotificationRouter wired: telegram → file → console cascade")

            # Commit 3: 주기 재시도 루프 등록.
            # Router 주입 성공 경로에서만 루프를 시작한다 — Router 가
            # 없으면 dispatch_retriable_alerts 가 noop 이므로 태스크를
            # 붙여봤자 빈 루프가 된다. 환경변수 ALERT_RETRY_LOOP_ENABLED
            # 로 강제 비활성화 가능 (테스트/운영 셧다운 시 사용).
            from core.utils.env import env_bool

            retry_loop_enabled = env_bool("ALERT_RETRY_LOOP_ENABLED", default=True)
            if retry_loop_enabled:

                async def _alert_retry_loop() -> None:
                    """FAILED 알림 주기 재픽업 루프 (Commit 3).

                    ALERT_RETRY_LOOP_INTERVAL_SECONDS 마다 한 번씩
                    dispatch_retriable_alerts 를 호출한다. 개별 alert
                    처리의 예외는 이미 _dispatch_via_router 에서 swallow
                    되지만, 루프 전체의 방어적 try/except 도 둔다 — DB
                    조회 실패 등 루프 레벨 예외가 다음 iteration 을
                    막아서는 안 된다.
                    """
                    logger.info("AlertRetryLoop started " f"(interval={ALERT_RETRY_LOOP_INTERVAL_SECONDS}s)")
                    while True:
                        try:
                            await asyncio.sleep(ALERT_RETRY_LOOP_INTERVAL_SECONDS)
                            stats = await _am_for_router.dispatch_retriable_alerts()
                            if stats.get("dispatched", 0) > 0 or stats.get("dead", 0) > 0:
                                logger.info(f"AlertRetryLoop iteration: {stats}")
                        except asyncio.CancelledError:
                            logger.info("AlertRetryLoop cancelled")
                            raise
                        except Exception as exc:  # noqa: BLE001 — 루프 보호
                            logger.warning(f"AlertRetryLoop iteration error (continuing): {exc}")

                _alert_retry_task = asyncio.create_task(_alert_retry_loop())
            else:
                logger.info("AlertRetryLoop disabled via ALERT_RETRY_LOOP_ENABLED=false")
        except Exception as e:
            logger.warning(f"NotificationRouter wiring 실패 (즉시 디스패치 비활성): {e}")

        await RedisManager.connect()
        logger.info("Redis connected successfully")

        logger.info("PostgreSQL (TimescaleDB) engine ready")

        # P1-정합성: PortfolioLedger 영속 계층 구성 + cache hydrate (embedded mode).
        try:
            portfolio_ledger = configure_portfolio_ledger(SqlPortfolioLedgerRepository(async_session_factory))
            await portfolio_ledger.hydrate()
            # loguru 는 stdlib logging 의 % 포맷 posarg 를 해석하지 않으므로
            # f-string 으로 직접 포맷한다. 회고: phase1-demo-verification-2026-04-11 §10.15.
            logger.info(f"PortfolioLedger hydrated from DB (positions={len(portfolio_ledger.get_positions())})")
        except Exception as e:
            logger.error(f"PortfolioLedger hydrate 실패: {e}")
            raise

        # ── 스케줄러 시작 ──
        # SCHEDULER_ENABLED=false 설정 시 API 서버에서 스케줄러를 시작하지 않음
        # (별도 scheduler 컨테이너에서 실행하는 경우)
        from core.utils.env import env_bool

        scheduler_enabled = env_bool("SCHEDULER_ENABLED", default=True)
        global trading_scheduler
        if scheduler_enabled:
            try:
                trading_scheduler = TradingScheduler()
                # P1-정합성: ReconciliationRunner 는 KIS 토큰 초기화 이후에
                # wiring 한다 (아래 KIS 초기화 블록 직후 _wire_reconciliation
                # 호출). 여기서는 스케줄러만 시작.
                await trading_scheduler.start()
                logger.info("TradingScheduler started successfully (embedded mode)")
            except Exception as e:
                logger.warning(f"TradingScheduler 시작 실패 (degraded): {e}")
                trading_scheduler = None
                app.state.scheduler_degraded = True
        else:
            trading_scheduler = None
            logger.info("TradingScheduler disabled (SCHEDULER_ENABLED=false, 별도 컨테이너 실행)")

        # ── KIS API 토큰 초기화 ──
        # 실패해도 lifespan 은 계속 진행하고 health 엔드포인트에서 자동 복원을 시도한다.
        # 쿨다운(KIS_RECOVERY_COOLDOWN_SECONDS, 기본 75s)은 EGW00133(1분 1회 제한) 회피용.
        global kis_client, kis_recovery_state
        try:
            cooldown = int(os.environ.get("KIS_RECOVERY_COOLDOWN_SECONDS", str(KIS_RECOVERY_DEFAULT_COOLDOWN)))
        except ValueError:
            logger.warning(
                "KIS_RECOVERY_COOLDOWN_SECONDS 파싱 실패 — 기본값 사용 " f"({KIS_RECOVERY_DEFAULT_COOLDOWN}s)"
            )
            cooldown = KIS_RECOVERY_DEFAULT_COOLDOWN
        try:
            alert_threshold = int(
                os.environ.get(
                    "KIS_RECOVERY_ALERT_THRESHOLD",
                    str(KIS_RECOVERY_DEFAULT_ALERT_THRESHOLD),
                )
            )
        except ValueError:
            logger.warning(
                "KIS_RECOVERY_ALERT_THRESHOLD 파싱 실패 — 기본값 사용 " f"({KIS_RECOVERY_DEFAULT_ALERT_THRESHOLD})"
            )
            alert_threshold = KIS_RECOVERY_DEFAULT_ALERT_THRESHOLD
        kis_recovery_state = KISRecoveryState(
            cooldown_seconds=cooldown,
            alert_threshold=alert_threshold,
        )
        app.state.kis_recovery_state = kis_recovery_state

        # KIS_STARTUP_JITTER_MAX_SECONDS: 동시 부팅 컨테이너 간 EGW00133 1차 충돌
        # 빈도를 줄이기 위한 균등분포 jitter 상한 (기본 15s, 0 이하면 비활성).
        try:
            jitter_max = float(os.environ.get("KIS_STARTUP_JITTER_MAX_SECONDS", str(KIS_STARTUP_DEFAULT_JITTER)))
        except ValueError:
            logger.warning("KIS_STARTUP_JITTER_MAX_SECONDS 파싱 실패 — 기본값 사용 " f"({KIS_STARTUP_DEFAULT_JITTER}s)")
            jitter_max = KIS_STARTUP_DEFAULT_JITTER

        try:
            if not settings.kis.is_backtest:
                kis_client = await jittered_token_issue(
                    client_factory=KISClient,
                    jitter_max_seconds=jitter_max,
                )
                logger.info("KIS API 토큰 초기화 완료")
            else:
                kis_client = KISClient()
                logger.info("KIS BACKTEST 모드 — 토큰 발급 건너뜀")
        except Exception as e:
            logger.warning(f"KIS 토큰 초기화 실패 (degraded, 자동 복원 대기): {e}")
            kis_client = None
            app.state.kis_degraded = True
            kis_recovery_state.mark_degraded(str(e))

        # P1-정합성: ReconciliationRunner 를 임베디드 스케줄러에 주입.
        # KIS 토큰 초기화 이후에만 wiring 한다 (degraded 모드에서는 등록 생략).
        if (
            scheduler_enabled
            and trading_scheduler is not None
            and kis_client is not None
            and not settings.kis.is_backtest
        ):
            try:
                from core.reconciliation import ReconciliationEngine
                from core.reconciliation_providers import (
                    KISBrokerPositionProvider,
                    LedgerPositionProvider,
                )
                from core.reconciliation_runner import ReconciliationRunner

                runner = ReconciliationRunner(
                    engine=ReconciliationEngine(),
                    broker_provider=KISBrokerPositionProvider(kis_client=kis_client),
                    internal_provider=LedgerPositionProvider(),
                )
                trading_scheduler.register_reconciliation_runner(runner)
                logger.info("ReconciliationRunner wired (embedded mode)")
            except Exception as e:
                logger.warning(f"ReconciliationRunner wiring 실패 (degraded): {e}")

        logger.info("AQTS startup complete. System ready.")

    except Exception as e:
        logger.error(f"Startup failed: {e}")
        raise

    yield

    # ── Shutdown (NFR-06: 그레이스풀 셧다운) ──
    logger.info("AQTS shutting down...")

    # 스케줄러 중지
    if trading_scheduler and trading_scheduler.is_running:
        try:
            await trading_scheduler.stop()
            logger.info("TradingScheduler stopped")
        except Exception as e:
            logger.error(f"TradingScheduler stop failed: {e}")

    # 알림 재시도 루프 중지 (Commit 3).
    # shutdown_manager 의 cleanup 체인보다 먼저 cancel 하여, DB 정리
    # 시점에 retry loop 가 Mongo 쿼리를 계속 발사하는 상황을 방지한다.
    if _alert_retry_task is not None and not _alert_retry_task.done():
        _alert_retry_task.cancel()
        try:
            await _alert_retry_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"AlertRetryLoop cancel 중 예외 무시: {e}")
        _alert_retry_task = None
        logger.info("AlertRetryLoop stopped")

    # DB 정리 콜백 등록
    shutdown_manager.register_cleanup(MongoDBManager.disconnect)
    shutdown_manager.register_cleanup(RedisManager.disconnect)

    async def _dispose_pg():
        await engine.dispose()
        logger.info("PostgreSQL engine disposed")

    shutdown_manager.register_cleanup(_dispose_pg)

    # 그레이스풀 셧다운 실행 (주문 대기 → 서비스 종료 → DB 정리)
    result = await shutdown_manager.shutdown(timeout=60)

    logger.info(f"AQTS shutdown complete. Result: {result}")


# ══════════════════════════════════════
# FastAPI 앱 생성
# ══════════════════════════════════════
app = FastAPI(
    title="AQTS - AI Quant Trade System",
    description="AI 기반 정량·정성적 분석 통합 퀀트 트레이딩 시스템",
    version="0.5.0",
    lifespan=lifespan,
)

# ── Rate Limiting 등록 ──
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
# P0-2b: storage 장애는 fail-closed (503)
app.add_exception_handler(StorageError, rate_limit_storage_unavailable_handler)


# ── P1-에러 메시지 표준화: 글로벌 HTTPException → ErrorResponse ──
async def _standard_http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """모든 HTTPException 을 공통 ErrorResponse 본문으로 직렬화한다.

    - 라우트가 `raise_api_error(...)` 또는 dict detail 을 사용한 경우
      `error_code` / `message` / `context` 가 그대로 전달된다.
    - 문자열 detail 은 상태 코드에 기반한 기본 `error_code` 로 보완된다.
    - HTTPException 에 지정된 headers (예: `Retry-After`, `WWW-Authenticate`)
      는 그대로 응답에 첨부된다.
    """
    body = normalize_error_body(exc.status_code, exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=body,
        headers=dict(exc.headers) if exc.headers else None,
    )


app.add_exception_handler(HTTPException, _standard_http_exception_handler)

# ── 미들웨어 등록 ──
# CORS 설정: 환경변수 CORS_ALLOWED_ORIGINS에서 허용 Origin 목록 로드
_settings = get_settings()
_cors_origins = [origin.strip() for origin in _settings.cors_allowed_origins.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 요청 로깅 미들웨어
app.add_middleware(RequestLoggingMiddleware)

# Prometheus 메트릭
setup_prometheus(app)

# OpenTelemetry 분산 추적
setup_tracing(app)


# ══════════════════════════════════════
# 헬스체크 엔드포인트
# ══════════════════════════════════════
@app.get("/api/system/health", tags=["System"])
async def health_check():
    """시스템 헬스체크 엔드포인트"""
    health = {
        "status": "healthy",
        "components": {},
    }

    # PostgreSQL 체크
    try:
        from sqlalchemy import text

        from db.database import async_session_factory

        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        health["components"]["postgresql"] = "healthy"
    except Exception as e:
        health["components"]["postgresql"] = f"unhealthy: {str(e)}"
        health["status"] = "degraded"

    # MongoDB 체크
    try:
        db = MongoDBManager.get_db()
        await db.command("ping")
        health["components"]["mongodb"] = "healthy"
    except Exception as e:
        health["components"]["mongodb"] = f"unhealthy: {str(e)}"
        health["status"] = "degraded"

    # Redis 체크
    try:
        client = RedisManager.get_client()
        await client.ping()
        health["components"]["redis"] = "healthy"
    except Exception as e:
        health["components"]["redis"] = f"unhealthy: {str(e)}"
        health["status"] = "degraded"

    # 스케줄러 상태 (degraded 허용)
    from core.utils.env import env_bool

    scheduler_enabled = env_bool("SCHEDULER_ENABLED", default=True)
    if not scheduler_enabled:
        health["components"]["scheduler"] = "external"  # 별도 컨테이너에서 실행
    elif getattr(app.state, "scheduler_degraded", False):
        health["components"]["scheduler"] = "degraded"
        health["status"] = "degraded"
    elif trading_scheduler and trading_scheduler.is_running:
        health["components"]["scheduler"] = "healthy"
    else:
        health["components"]["scheduler"] = "stopped"

    # KIS API 상태 (degraded → 자동 복원 시도)
    # 쿨다운이 만료되지 않았거나 backtest 모드면 try_recover_kis 가 즉시 None 을 반환.
    settings_local = get_settings()
    state_obj: KISRecoveryState | None = getattr(app.state, "kis_recovery_state", None)
    if getattr(app.state, "kis_degraded", False) and state_obj is not None and not settings_local.kis.is_backtest:

        async def _kis_client_factory() -> KISClient:
            client = KISClient()
            await client._token_manager.get_access_token()
            return client

        async def _kis_alert_callback(state: KISRecoveryState) -> None:
            """연속 실패 임계값 도달 시 운영자 알림 1회 발송.

            AlertManager 는 lazy import 로 가져와 순환 의존성을 회피한다.
            """
            from api.routes.alerts import _alert_manager
            from config.constants import AlertType
            from core.notification.alert_manager import AlertLevel

            await _alert_manager.create_and_persist_alert(
                alert_type=AlertType.SYSTEM_ERROR,
                level=AlertLevel.ERROR,
                title="KIS API 자동 복원 연속 실패",
                message=(
                    f"KIS 토큰 재발급이 {state.consecutive_failures}회 연속 실패했습니다. "
                    f"마지막 오류: {state.last_error}"
                ),
                metadata={
                    "consecutive_failures": state.consecutive_failures,
                    "attempt_count": state.attempt_count,
                    "last_error": state.last_error,
                    "alert_threshold": state.alert_threshold,
                },
            )

        recovered = await try_recover_kis(
            state_obj,
            _kis_client_factory,
            alert_callback=_kis_alert_callback,
        )
        if recovered is not None:
            global kis_client
            kis_client = recovered
            app.state.kis_degraded = False

    if getattr(app.state, "kis_degraded", False):
        health["components"]["kis_api"] = "degraded"
        health["status"] = "degraded"
    elif kis_client:
        health["components"]["kis_api"] = "healthy"
    else:
        health["components"]["kis_api"] = "not_initialized"

    # Prometheus 게이지 업데이트
    status_map = {"healthy": 1.0, "degraded": 0.5, "unhealthy": 0.0}
    SYSTEM_STATUS.set(status_map.get(health["status"], 0.0))
    for comp, comp_status in health["components"].items():
        if comp_status == "healthy":
            COMPONENT_HEALTH.labels(component=comp).set(1.0)
        elif comp_status in ("degraded", "stopped", "not_initialized", "external"):
            COMPONENT_HEALTH.labels(component=comp).set(0.5)
        else:
            COMPONENT_HEALTH.labels(component=comp).set(0.0)

    return health


# ══════════════════════════════════════
# 루트 엔드포인트 (대시보드 / API 정보)
# ══════════════════════════════════════
@app.get("/api/info", tags=["Root"])
async def api_info():
    """API 정보 엔드포인트"""
    return {
        "name": "AQTS - AI Quant Trade System",
        "version": "0.5.0",
        "status": "running",
    }


# 대시보드 HTML 서빙
@app.get("/", tags=["Root"])
async def dashboard():
    """웹 대시보드 (Frontend SPA)"""
    import os

    from fastapi.responses import FileResponse

    frontend_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "index.html")
    if os.path.exists(frontend_path):
        return FileResponse(frontend_path, media_type="text/html")
    return {"message": "AQTS Dashboard - frontend/index.html not found"}


# ══════════════════════════════════════
# API 라우터 등록 (Phase 5, Stage 4)
# ══════════════════════════════════════
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(users.router, prefix="/api", tags=["Users"])
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["Portfolio"])
app.include_router(orders.router, prefix="/api/orders", tags=["Orders"])
app.include_router(profile.router, prefix="/api/profile", tags=["Profile"])
app.include_router(market.router, prefix="/api/market", tags=["Market"])
app.include_router(alerts.router, prefix="/api/alerts", tags=["Alerts"])
app.include_router(system.router, prefix="/api/system", tags=["System"])
app.include_router(oos.router, prefix="/api/system/oos", tags=["OOS Validation"])
app.include_router(
    param_sensitivity.router,
    prefix="/api/system/param-sensitivity",
    tags=["Parameter Sensitivity"],
)
app.include_router(audit.router)
app.include_router(ensemble.router, prefix="/api/ensemble", tags=["Ensemble"])
app.include_router(realtime.router, prefix="/api/realtime", tags=["Realtime"])
app.include_router(
    dry_run.router,
    prefix="/api/system/dry-run",
    tags=["Dry Run"],
)
