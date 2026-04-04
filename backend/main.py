"""
AQTS - AI Quant Trade System
FastAPI 메인 애플리케이션 엔트리포인트

Lifecycle:
  startup  → DB 연결, 스케줄러 시작
  shutdown → 그레이스풀 셧다운, DB 연결 해제
"""

import signal
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

from config.logging import logger, setup_logging
from config.settings import get_settings
from db.database import MongoDBManager, RedisManager, engine
from core.graceful_shutdown import GracefulShutdownManager
from core.trading_scheduler import TradingScheduler
from core.data_collector.kis_client import KISClient

# Phase 5: API 라우터 & 미들웨어
from api.routes import auth, portfolio, orders, profile, market, alerts, system, audit, oos
from api.middleware.request_logger import RequestLoggingMiddleware
from api.middleware.rate_limiter import limiter, rate_limit_exceeded_handler


# ══════════════════════════════════════
# 그레이스풀 셧다운 매니저 (NFR-06)
# ══════════════════════════════════════
shutdown_manager = GracefulShutdownManager()
shutdown_event = asyncio.Event()

# 스케줄러 & KIS 클라이언트 (startup에서 초기화)
trading_scheduler: TradingScheduler | None = None
kis_client: KISClient | None = None


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
    setup_logging()
    settings = get_settings()

    logger.info("=" * 60)
    logger.info("AQTS - AI Quant Trade System Starting...")
    logger.info(f"Environment: {settings.environment}")
    logger.info(f"KIS Trading Mode: {settings.kis.trading_mode.value}")
    logger.info("=" * 60)

    # 시그널 핸들러 등록
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # DB 연결
    try:
        await MongoDBManager.connect()
        logger.info("MongoDB connected successfully")

        await RedisManager.connect()
        logger.info("Redis connected successfully")

        logger.info("PostgreSQL (TimescaleDB) engine ready")

        # ── 스케줄러 시작 ──
        global trading_scheduler
        try:
            trading_scheduler = TradingScheduler()
            await trading_scheduler.start()
            logger.info("TradingScheduler started successfully")
        except Exception as e:
            logger.warning(f"TradingScheduler 시작 실패 (degraded): {e}")
            trading_scheduler = None
            app.state.scheduler_degraded = True

        # ── KIS API 토큰 초기화 ──
        global kis_client
        try:
            kis_client = KISClient()
            if not settings.kis.is_backtest:
                await kis_client._token_manager.get_access_token()
                logger.info("KIS API 토큰 초기화 완료")
            else:
                logger.info("KIS BACKTEST 모드 — 토큰 발급 건너뜀")
        except Exception as e:
            logger.warning(f"KIS 토큰 초기화 실패 (degraded): {e}")
            kis_client = None
            app.state.kis_degraded = True

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

# ── 미들웨어 등록 ──
# CORS 설정: 환경변수 CORS_ALLOWED_ORIGINS에서 허용 Origin 목록 로드
_settings = get_settings()
_cors_origins = [
    origin.strip()
    for origin in _settings.cors_allowed_origins.split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 요청 로깅 미들웨어
app.add_middleware(RequestLoggingMiddleware)


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
    if getattr(app.state, "scheduler_degraded", False):
        health["components"]["scheduler"] = "degraded"
        health["status"] = "degraded"
    elif trading_scheduler and trading_scheduler.is_running:
        health["components"]["scheduler"] = "healthy"
    else:
        health["components"]["scheduler"] = "stopped"

    # KIS API 상태 (degraded 허용)
    if getattr(app.state, "kis_degraded", False):
        health["components"]["kis_api"] = "degraded"
        health["status"] = "degraded"
    elif kis_client:
        health["components"]["kis_api"] = "healthy"
    else:
        health["components"]["kis_api"] = "not_initialized"

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

    frontend_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "frontend", "index.html"
    )
    if os.path.exists(frontend_path):
        return FileResponse(frontend_path, media_type="text/html")
    return {"message": "AQTS Dashboard - frontend/index.html not found"}


# ══════════════════════════════════════
# API 라우터 등록 (Phase 5, Stage 4)
# ══════════════════════════════════════
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["Portfolio"])
app.include_router(orders.router, prefix="/api/orders", tags=["Orders"])
app.include_router(profile.router, prefix="/api/profile", tags=["Profile"])
app.include_router(market.router, prefix="/api/market", tags=["Market"])
app.include_router(alerts.router, prefix="/api/alerts", tags=["Alerts"])
app.include_router(system.router, prefix="/api/system", tags=["System"])
app.include_router(oos.router, prefix="/api/system/oos", tags=["OOS Validation"])
app.include_router(audit.router)
