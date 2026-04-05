"""
스케줄러 이벤트 핸들러 (Scheduler Event Handlers)

TradingScheduler에 등록할 이벤트 핸들러 모음.
각 핸들러는 장 전/장 시작/중간점검/장 마감/마감 후 이벤트에 대응합니다.

핸들러 흐름:
  08:30 PRE_MARKET   → OHLCV 수집 (DailyOHLCVCollector)
  09:00 MARKET_OPEN  → 동적 앙상블 배치 실행 (DynamicEnsembleRunner)
  11:30 MIDDAY_CHECK → 포지션 모니터링 (향후 확장)
  15:30 MARKET_CLOSE → 일일 성과 기록 (향후 확장)
  16:00 POST_MARKET  → 리포트 생성 (향후 확장)

사용법:
    scheduler = TradingScheduler()
    register_pipeline_handlers(scheduler)
    await scheduler.start()
"""

import json
from datetime import datetime, timezone

from config.logging import logger
from core.data_collector.daily_collector import (
    DailyOHLCVCollector,
)
from core.strategy_ensemble.runner import DynamicEnsembleRunner
from db.database import RedisManager, async_session_factory


async def handle_pre_market() -> dict:
    """
    장 전 준비 핸들러 (08:30 KST)

    1. 유니버스 전 종목 OHLCV 일봉 수집 (KIS API)
    2. 건전성 검사 (기존 로직 유지)
    3. TradingGuard 일일 리셋 (기존 로직 유지)
    """
    result = {}

    # ── 1. OHLCV 일봉 수집 ──
    try:
        async with async_session_factory() as session:
            collector = DailyOHLCVCollector(session)
            report = await collector.collect_all()
            result["ohlcv_collection"] = report.to_dict()

            if report.errors:
                result["collection_errors"] = report.errors[:10]  # 최대 10개

    except Exception as e:
        logger.error(f"[PreMarket] OHLCV 수집 실패: {e}")
        result["ohlcv_collection_error"] = str(e)

    # ── 2. 건전성 검사 ──
    try:
        from core.health_checker import HealthChecker

        checker = HealthChecker()
        health = await checker.run_full_check()
        result["health_status"] = health.overall_status.value
        result["ready_for_trading"] = health.ready_for_trading
    except Exception as e:
        result["health_check_error"] = str(e)

    # ── 3. TradingGuard 일일 리셋 ──
    try:
        from core.trading_guard import TradingGuard

        guard = TradingGuard()
        guard.reset_daily_state()
        result["daily_reset"] = True
    except Exception as e:
        result["daily_reset_error"] = str(e)

    return result


async def handle_market_open() -> dict:
    """
    장 시작 핸들러 (09:00 KST)

    1. DB에서 활성 유니버스 종목 조회
    2. 종목별 동적 앙상블 시그널 생성
    3. 결과를 Redis에 캐시 (API 조회용)
    """
    result = {
        "message": "장 시작 — 동적 앙상블 분석 실행",
        "market_open_time": datetime.now(timezone.utc).isoformat(),
    }

    try:
        async with async_session_factory() as session:
            # 활성 종목 조회
            tickers_by_country = await _load_universe_grouped(session)
            total_tickers = sum(len(tks) for tks in tickers_by_country.values())

            if total_tickers == 0:
                result["warning"] = "활성 종목이 없습니다"
                return result

            logger.info(f"[MarketOpen] 동적 앙상블 배치 시작: " f"{total_tickers}개 종목")

            # 국가별 동적 앙상블 실행
            ensemble_results: dict[str, dict] = {}
            succeeded = 0
            failed = 0

            for country, tickers in tickers_by_country.items():
                for ticker_info in tickers:
                    ticker = ticker_info["ticker"]
                    try:
                        runner = DynamicEnsembleRunner(db_session=session)
                        runner_result = await runner.run(
                            ticker=ticker,
                            country=country,
                            lookback_days=300,
                        )
                        ensemble_results[ticker] = runner_result.to_summary_dict()
                        succeeded += 1

                    except Exception as e:
                        failed += 1
                        logger.warning(f"[MarketOpen] {ticker} 앙상블 실패: {e}")
                        ensemble_results[ticker] = {"error": str(e)}

            result["total_tickers"] = total_tickers
            result["succeeded"] = succeeded
            result["failed"] = failed

            # Redis에 앙상블 결과 캐시
            await _cache_ensemble_results(ensemble_results)

            logger.info(f"[MarketOpen] 동적 앙상블 완료: " f"{succeeded}/{total_tickers} 성공")

    except Exception as e:
        logger.error(f"[MarketOpen] 동적 앙상블 배치 실패: {e}")
        result["error"] = str(e)

    return result


async def handle_midday_check() -> dict:
    """
    중간 점검 핸들러 (11:30 KST)

    포지션 모니터링 및 리밸런싱 검토 (향후 확장)
    """
    return {
        "message": "중간 점검 — 포지션 모니터링",
        "check_time": datetime.now(timezone.utc).isoformat(),
    }


async def handle_market_close() -> dict:
    """
    장 마감 핸들러 (15:30 KST)

    일일 성과 기록 (향후 확장)
    """
    return {
        "message": "장 마감 처리",
        "close_time": datetime.now(timezone.utc).isoformat(),
    }


async def handle_post_market() -> dict:
    """
    마감 후 핸들러 (16:00 KST)

    일일 리포트 생성 (향후 확장)
    """
    return {
        "message": "마감 후 처리 — 일일 리포트 생성 대기",
        "post_market_time": datetime.now(timezone.utc).isoformat(),
    }


# ══════════════════════════════════════
# 핸들러 등록 유틸리티
# ══════════════════════════════════════
def register_pipeline_handlers(scheduler) -> None:
    """
    TradingScheduler에 파이프라인 핸들러를 등록합니다.

    Args:
        scheduler: TradingScheduler 인스턴스
    """
    scheduler.register_handler("handle_pre_market", handle_pre_market)
    scheduler.register_handler("handle_market_open", handle_market_open)
    scheduler.register_handler("handle_midday_check", handle_midday_check)
    scheduler.register_handler("handle_market_close", handle_market_close)
    scheduler.register_handler("handle_post_market", handle_post_market)

    logger.info("[Scheduler] 파이프라인 핸들러 등록 완료 (5개)")


# ══════════════════════════════════════
# 내부 유틸리티
# ══════════════════════════════════════
async def _load_universe_grouped(
    session,
) -> dict[str, list[dict]]:
    """국가별로 그룹화된 활성 종목 조회"""
    from sqlalchemy import text

    query = text(
        """
        SELECT ticker, market, country
        FROM universe
        WHERE is_active = TRUE
        ORDER BY country, market, ticker
    """
    )
    rows = await session.execute(query)
    items = rows.fetchall()

    grouped: dict[str, list[dict]] = {}
    for ticker, market, country in items:
        grouped.setdefault(country, []).append({"ticker": ticker, "market": market})

    return grouped


async def _cache_ensemble_results(
    results: dict[str, dict],
    ttl_seconds: int = 86400,
) -> None:
    """앙상블 결과를 Redis에 캐시 (24시간 TTL)"""
    try:
        redis = RedisManager.get_client()
        pipe = redis.pipeline()

        for ticker, data in results.items():
            key = f"ensemble:latest:{ticker}"
            pipe.set(key, json.dumps(data), ex=ttl_seconds)

        # 전체 요약도 저장
        summary_key = "ensemble:latest:_summary"
        summary = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "total_tickers": len(results),
            "tickers": list(results.keys()),
        }
        pipe.set(summary_key, json.dumps(summary), ex=ttl_seconds)

        await pipe.execute()
        logger.debug(f"[Redis] 앙상블 결과 {len(results)}건 캐시 완료")

    except Exception as e:
        # Redis 실패는 치명적이지 않음 (캐시일 뿐)
        logger.warning(f"[Redis] 앙상블 결과 캐시 실패 (무시): {e}")
