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

from config.logging import logger, setup_logging
from config.settings import get_settings
from db.database import MongoDBManager, RedisManager, engine


# ══════════════════════════════════════
# 그레이스풀 셧다운 핸들러
# ══════════════════════════════════════
shutdown_event = asyncio.Event()


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

        # TODO: Phase 4에서 스케줄러 시작 로직 추가
        # TODO: Phase 1에서 한투 API 토큰 초기화 추가

        logger.info("AQTS startup complete. System ready.")

    except Exception as e:
        logger.error(f"Startup failed: {e}")
        raise

    yield

    # ── Shutdown ──
    logger.info("AQTS shutting down...")

    # TODO: 진행 중인 주문 처리 완료 대기

    await MongoDBManager.disconnect()
    logger.info("MongoDB disconnected")

    await RedisManager.disconnect()
    logger.info("Redis disconnected")

    await engine.dispose()
    logger.info("PostgreSQL engine disposed")

    logger.info("AQTS shutdown complete.")


# ══════════════════════════════════════
# FastAPI 앱 생성
# ══════════════════════════════════════
app = FastAPI(
    title="AQTS - AI Quant Trade System",
    description="AI 기반 정량·정성적 분석 통합 퀀트 트레이딩 시스템",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS 설정 (단일 사용자, 개발 환경)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 운영 시 특정 도메인으로 제한
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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

    return health


# ══════════════════════════════════════
# 루트 엔드포인트
# ══════════════════════════════════════
@app.get("/", tags=["Root"])
async def root():
    return {
        "name": "AQTS - AI Quant Trade System",
        "version": "0.1.0",
        "status": "running",
    }


# ══════════════════════════════════════
# API 라우터 등록
# ══════════════════════════════════════
# TODO: Phase별로 라우터 추가
# from api.routes import auth, portfolio, market, orders, profile, rebalancing, settings_router, alerts
# app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
# app.include_router(portfolio.router, prefix="/api/portfolio", tags=["Portfolio"])
# ...
