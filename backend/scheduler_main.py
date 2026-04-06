"""
AQTS 스케줄러 전용 엔트리포인트

API 서버와 분리된 별도 컨테이너에서 스케줄러만 실행합니다.
장애 격리: 스케줄러 크래시가 API 서버에 영향을 주지 않습니다.

실행:
    python scheduler_main.py

환경변수:
    KIS_TRADING_MODE=DEMO  (필수)
"""

import asyncio
import signal

from config.logging import logger, setup_logging
from config.settings import get_settings
from core.data_collector.kis_client import KISClient
from core.scheduler_handlers import register_pipeline_handlers
from core.trading_scheduler import TradingScheduler
from db.database import MongoDBManager, RedisManager, engine


async def main():
    """스케줄러 전용 메인 루프"""
    setup_logging()
    settings = get_settings()

    logger.info("=" * 60)
    logger.info("AQTS Scheduler Process Starting...")
    logger.info(f"Environment: {settings.environment}")
    logger.info(f"KIS Trading Mode: {settings.kis.trading_mode.value}")
    logger.info("=" * 60)

    # 종료 시그널 설정
    stop_event = asyncio.Event()

    def _signal_handler(sig, frame):
        logger.warning(f"Received signal {sig}. Initiating scheduler shutdown...")
        stop_event.set()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # DB 연결 (스케줄러 핸들러에서 DB 접근 필요)
    try:
        await MongoDBManager.connect()
        logger.info("MongoDB connected")

        await RedisManager.connect()
        logger.info("Redis connected")

        logger.info("PostgreSQL engine ready")
    except Exception as e:
        logger.error(f"DB 연결 실패: {e}")
        raise

    # KIS API 토큰 초기화
    kis_client = None
    try:
        kis_client = KISClient()
        if not settings.kis.is_backtest:
            await kis_client._token_manager.get_access_token()
            logger.info("KIS API 토큰 초기화 완료")
        else:
            logger.info("KIS BACKTEST 모드 — 토큰 발급 건너뜀")
    except Exception as e:
        logger.warning(f"KIS 토큰 초기화 실패 (degraded): {e}")

    # 스케줄러 시작
    scheduler = TradingScheduler()
    register_pipeline_handlers(scheduler)
    await scheduler.start()
    logger.info("TradingScheduler started successfully")

    # 종료 시그널 대기
    await stop_event.wait()

    # 정리
    logger.info("Scheduler shutting down...")
    await scheduler.stop()
    logger.info("TradingScheduler stopped")

    await MongoDBManager.disconnect()
    await RedisManager.disconnect()
    await engine.dispose()
    logger.info("DB connections closed")

    logger.info("AQTS Scheduler shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
