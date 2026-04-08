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
from core.portfolio_ledger import configure_portfolio_ledger
from core.reconciliation import ReconciliationEngine
from core.reconciliation_providers import (
    KISBrokerPositionProvider,
    LedgerPositionProvider,
)
from core.reconciliation_runner import ReconciliationRunner
from core.scheduler_handlers import register_pipeline_handlers
from core.trading_scheduler import TradingScheduler
from db.database import MongoDBManager, RedisManager, async_session_factory, engine
from db.repositories.portfolio_positions import SqlPortfolioLedgerRepository


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

    # P1-정합성: PortfolioLedger 영속 계층 구성 + cache hydrate.
    # DB engine 이 준비된 직후에 ledger singleton 을 SQL repository 로 (재)구성
    # 하고, 부팅 시 1회 hydrate 하여 cache 에 기존 잔량을 채운다. 이후
    # OrderExecutor 의 record_fill 은 매 호출마다 DB 트랜잭션을 통해 누적되며,
    # 프로세스 재시작 후에도 broker 잔고와의 mismatch 회귀가 발생하지 않는다.
    try:
        portfolio_ledger = configure_portfolio_ledger(SqlPortfolioLedgerRepository(async_session_factory))
        await portfolio_ledger.hydrate()
        logger.info(
            "PortfolioLedger hydrated from DB (positions=%d)",
            len(portfolio_ledger.get_positions()),
        )
    except Exception as e:
        logger.error(f"PortfolioLedger hydrate 실패: {e}")
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

    # P1-정합성: ReconciliationRunner 를 운영 스케줄러에 실제로 주입한다.
    # KIS 토큰 초기화에 실패한 degraded 모드(kis_client=None)에서는 reconcile
    # 자체가 무의미하므로 등록을 건너뛰고 경고만 남긴다 — fail-closed 원칙은
    # provider 호출 단에서 별도로 작동한다 (아래 _run_reconciliation_if_wired
    # 가 예외를 result="error" 로 카운트하여 관측 가능).
    if kis_client is not None and not settings.kis.is_backtest:
        runner = ReconciliationRunner(
            engine=ReconciliationEngine(),
            broker_provider=KISBrokerPositionProvider(kis_client=kis_client),
            internal_provider=LedgerPositionProvider(),
        )
        scheduler.register_reconciliation_runner(runner)
        logger.info("ReconciliationRunner wired (KIS broker ↔ PortfolioLedger)")
    else:
        logger.warning(
            "ReconciliationRunner 미등록 — kis_client=%s backtest=%s",
            kis_client is not None,
            settings.kis.is_backtest,
        )

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
