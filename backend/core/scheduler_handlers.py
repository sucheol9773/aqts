"""
스케줄러 이벤트 핸들러 (Scheduler Event Handlers)

TradingScheduler에 등록할 이벤트 핸들러 모음.
각 핸들러는 장 전/장 시작/중간점검/장 마감/마감 후 이벤트에 대응합니다.

핸들러 흐름:
  08:30 PRE_MARKET   → OHLCV 수집 (DailyOHLCVCollector)
  09:00 MARKET_OPEN  → 동적 앙상블 배치 실행 (DynamicEnsembleRunner)
  11:30 MIDDAY_CHECK → 포지션 모니터링 + 손실 경보 + DD 추적
  15:30 MARKET_CLOSE → 일일 성과 기록 + 포트폴리오 스냅샷 + 감사 로그
  16:00 POST_MARKET  → 일일 리포트 생성 + Telegram 발송 + Redis 스냅샷

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

    1. KIS API로 현재 포지션 조회
    2. 포트폴리오 가치 갱신 → TradingGuard DD 추적
    3. 손실 한도 초과 여부 점검
    4. 앙상블 시그널 대비 포지션 괴리 경고
    """
    result = {
        "message": "중간 점검 — 포지션 모니터링",
        "check_time": datetime.now(timezone.utc).isoformat(),
    }

    # ── 1. 현재 포지션 조회 (KIS API) ──
    try:
        from core.data_collector.kis_client import KISClient

        kis = KISClient()
        balance = await kis.get_kr_balance()

        positions_raw = balance.get("output1", [])
        summary_raw = balance.get("output2", [])

        positions_count = len([p for p in positions_raw if int(p.get("hldg_qty", 0)) > 0])

        total_eval = 0.0
        cash = 0.0
        if summary_raw:
            total_eval = float(summary_raw[0].get("tot_evlu_amt", 0))
            cash = float(summary_raw[0].get("dnca_tot_amt", 0))

        result["positions_count"] = positions_count
        result["total_eval"] = total_eval
        result["cash"] = cash

        # 종목별 손익 요약
        loss_tickers = []
        for p in positions_raw:
            qty = int(p.get("hldg_qty", 0))
            if qty <= 0:
                continue
            pnl_amt = float(p.get("evlu_pfls_amt", 0))
            pnl_pct = float(p.get("evlu_pfls_rt", 0))
            if pnl_pct < -5.0:
                loss_tickers.append(
                    {
                        "ticker": p.get("pdno", ""),
                        "name": p.get("prdt_name", ""),
                        "pnl_pct": round(pnl_pct, 2),
                        "pnl_amt": pnl_amt,
                    }
                )

        if loss_tickers:
            result["loss_alert"] = loss_tickers
            logger.warning(
                f"[MiddayCheck] 5%+ 손실 종목 {len(loss_tickers)}개: " f"{[t['ticker'] for t in loss_tickers]}"
            )

    except Exception as e:
        logger.warning(f"[MiddayCheck] KIS 잔고 조회 실패: {e}")
        result["kis_error"] = str(e)

    # ── 2. TradingGuard 포트폴리오 가치 갱신 ──
    try:
        from core.trading_guard import TradingGuard

        guard = TradingGuard()
        if total_eval > 0:
            guard.state.current_portfolio_value = total_eval
            if guard.state.peak_portfolio_value == 0:
                guard.state.peak_portfolio_value = total_eval
            guard.check_max_drawdown()
            result["drawdown"] = round(guard.state.current_drawdown, 4)

            # DD 한도 경고
            if guard.state.current_drawdown > 0.15:
                result["dd_warning"] = f"드로다운 {guard.state.current_drawdown:.1%} — 한도 접근 중"
                logger.warning(f"[MiddayCheck] DD 경고: {guard.state.current_drawdown:.1%}")
    except Exception as e:
        result["guard_error"] = str(e)

    # ── 3. 캐시된 앙상블 시그널과 포지션 비교 ──
    try:
        redis = RedisManager.get_client()
        ensemble_summary_raw = await redis.get("ensemble:latest:_summary")

        if ensemble_summary_raw:
            ensemble_summary = json.loads(ensemble_summary_raw)
            result["ensemble_cached_tickers"] = ensemble_summary.get("total_tickers", 0)
            result["ensemble_updated_at"] = ensemble_summary.get("updated_at")
    except Exception:
        pass  # Redis 실패는 무시

    return result


async def handle_market_close() -> dict:
    """
    장 마감 핸들러 (15:30 KST)

    1. 최종 포지션 및 포트폴리오 가치 조회
    2. 금일 체결 주문 조회 → 일일 거래 통계
    3. 포트폴리오 스냅샷 Redis 저장
    4. 감사 로그 기록
    """
    result = {
        "message": "장 마감 처리",
        "close_time": datetime.now(timezone.utc).isoformat(),
    }

    portfolio_value_end = 0.0
    cash_balance = 0.0
    positions_data = []

    # ── 1. 최종 포지션 조회 ──
    try:
        from core.data_collector.kis_client import KISClient

        kis = KISClient()
        balance = await kis.get_kr_balance()

        positions_raw = balance.get("output1", [])
        summary_raw = balance.get("output2", [])

        if summary_raw:
            portfolio_value_end = float(summary_raw[0].get("tot_evlu_amt", 0))
            cash_balance = float(summary_raw[0].get("dnca_tot_amt", 0))

        for p in positions_raw:
            qty = int(p.get("hldg_qty", 0))
            if qty <= 0:
                continue
            positions_data.append(
                {
                    "ticker": p.get("pdno", ""),
                    "name": p.get("prdt_name", ""),
                    "quantity": qty,
                    "avg_price": float(p.get("pchs_avg_pric", 0)),
                    "current_price": float(p.get("prpr", 0)),
                    "eval_amount": float(p.get("evlu_amt", 0)),
                    "pnl_amount": float(p.get("evlu_pfls_amt", 0)),
                    "pnl_percent": float(p.get("evlu_pfls_rt", 0)),
                }
            )

        result["portfolio_value"] = portfolio_value_end
        result["cash_balance"] = cash_balance
        result["positions_count"] = len(positions_data)

    except Exception as e:
        logger.warning(f"[MarketClose] KIS 잔고 조회 실패: {e}")
        result["kis_error"] = str(e)

    # ── 2. 금일 거래 통계 조회 ──
    try:
        async with async_session_factory() as session:
            from sqlalchemy import text

            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            query = text(
                """
                SELECT side,
                       COUNT(*) AS cnt,
                       COALESCE(SUM(filled_qty * avg_price), 0) AS total_amount
                FROM orders
                WHERE DATE(created_at) = :today
                  AND status IN ('FILLED', 'PARTIAL')
                GROUP BY side
            """
            )
            rows = await session.execute(query, {"today": today_str})
            trade_stats = {}
            for side, cnt, amount in rows.fetchall():
                trade_stats[side] = {"count": cnt, "amount": float(amount)}
            result["trade_stats"] = trade_stats

    except Exception as e:
        logger.warning(f"[MarketClose] 거래 통계 조회 실패: {e}")
        result["trade_stats_error"] = str(e)

    # ── 3. 포트폴리오 스냅샷 Redis 저장 ──
    try:
        redis = RedisManager.get_client()
        today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        snapshot = {
            "date": today_key,
            "portfolio_value": portfolio_value_end,
            "cash_balance": cash_balance,
            "positions_count": len(positions_data),
            "positions": positions_data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        await redis.set(
            f"portfolio:snapshot:{today_key}",
            json.dumps(snapshot),
            ex=86400 * 30,  # 30일 보관
        )
        result["snapshot_saved"] = True

    except Exception as e:
        logger.warning(f"[MarketClose] 스냅샷 저장 실패: {e}")
        result["snapshot_error"] = str(e)

    # ── 4. 감사 로그 기록 ──
    try:
        async with async_session_factory() as session:
            from db.repositories.audit_log import AuditLogger

            audit = AuditLogger(session)
            await audit.log(
                action_type="MARKET_CLOSE",
                module="scheduler_handler",
                description=(
                    f"장 마감 처리 완료: "
                    f"포트폴리오={portfolio_value_end:,.0f}원, "
                    f"포지션={len(positions_data)}개"
                ),
                metadata={
                    "portfolio_value": portfolio_value_end,
                    "cash_balance": cash_balance,
                    "positions_count": len(positions_data),
                    "trade_stats": result.get("trade_stats", {}),
                },
            )
            await session.commit()

    except Exception as e:
        logger.warning(f"[MarketClose] 감사 로그 기록 실패: {e}")
        result["audit_error"] = str(e)

    logger.info(f"[MarketClose] 완료: " f"포트폴리오={portfolio_value_end:,.0f}원, " f"포지션={len(positions_data)}개")

    return result


async def handle_post_market() -> dict:
    """
    마감 후 핸들러 (16:00 KST)

    1. 장 시작 시점 스냅샷 조회 (Redis)
    2. 일일 리포트 생성 (DailyReporter)
    3. Telegram 발송
    4. 리포트 Redis 저장
    """
    result = {
        "message": "마감 후 처리 — 일일 리포트",
        "post_market_time": datetime.now(timezone.utc).isoformat(),
    }

    # ── 1. 금일 스냅샷에서 종가 데이터 조회 ──
    portfolio_value_start = 0.0
    portfolio_value_end = 0.0
    cash_balance = 0.0
    positions_data = []

    try:
        redis = RedisManager.get_client()
        today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # 금일 마감 스냅샷
        snapshot_raw = await redis.get(f"portfolio:snapshot:{today_key}")

        if snapshot_raw:
            snapshot = json.loads(snapshot_raw)
            portfolio_value_end = snapshot.get("portfolio_value", 0)
            cash_balance = snapshot.get("cash_balance", 0)
            positions_data = snapshot.get("positions", [])

        # 전일 스냅샷 (시작 가치)
        from datetime import timedelta

        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        prev_raw = await redis.get(f"portfolio:snapshot:{yesterday}")

        if prev_raw:
            prev_snapshot = json.loads(prev_raw)
            portfolio_value_start = prev_snapshot.get("portfolio_value", 0)
        else:
            # 전일 스냅샷 없으면 초기자본 사용
            from config.settings import get_settings

            portfolio_value_start = get_settings().risk.initial_capital_krw

    except Exception as e:
        logger.warning(f"[PostMarket] 스냅샷 조회 실패: {e}")
        result["snapshot_error"] = str(e)

    # ── 2. 금일 체결 내역 조회 ──
    trades = []
    try:
        async with async_session_factory() as session:
            from sqlalchemy import text

            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            query = text(
                """
                SELECT ticker, side, filled_qty, avg_price, status, created_at
                FROM orders
                WHERE DATE(created_at) = :today
                  AND status IN ('FILLED', 'PARTIAL')
                ORDER BY created_at
            """
            )
            rows = await session.execute(query, {"today": today_str})
            for ticker, side, qty, price, status, created_at in rows.fetchall():
                trades.append(
                    {
                        "ticker": ticker,
                        "side": side,
                        "quantity": int(qty) if qty else 0,
                        "price": float(price) if price else 0.0,
                        "amount": (float(qty or 0) * float(price or 0)),
                        "executed_at": (created_at.isoformat() if created_at else None),
                    }
                )
    except Exception as e:
        logger.warning(f"[PostMarket] 거래 내역 조회 실패: {e}")
        result["trades_error"] = str(e)

    # ── 3. 일일 리포트 생성 ──
    try:
        from core.daily_reporter import (
            DailyReporter,
            PositionSnapshot,
            TradeRecord,
        )

        reporter = DailyReporter()

        # 포지션 변환
        position_snapshots = []
        for p in positions_data:
            eval_amt = p.get("eval_amount", 0)
            weight = eval_amt / portfolio_value_end if portfolio_value_end > 0 else 0
            position_snapshots.append(
                PositionSnapshot(
                    ticker=p.get("ticker", ""),
                    name=p.get("name", ""),
                    quantity=p.get("quantity", 0),
                    avg_price=p.get("avg_price", 0),
                    current_price=p.get("current_price", 0),
                    market_value=eval_amt,
                    pnl=p.get("pnl_amount", 0),
                    pnl_percent=p.get("pnl_percent", 0),
                    weight=round(weight, 4),
                )
            )

        # 거래 변환
        trade_records = [
            TradeRecord(
                ticker=t["ticker"],
                name="",
                side=t["side"],
                quantity=t["quantity"],
                price=t["price"],
                amount=t["amount"],
            )
            for t in trades
        ]

        # TradingGuard 상태
        dd_today = 0.0
        consecutive_losses = 0
        try:
            from core.trading_guard import TradingGuard

            guard = TradingGuard()
            dd_today = guard.state.current_drawdown
            consecutive_losses = guard.state.consecutive_losses
        except Exception:
            pass

        report = await reporter.generate_report(
            portfolio_value_start=portfolio_value_start,
            portfolio_value_end=portfolio_value_end,
            trades=trade_records,
            positions=position_snapshots,
            cash_balance=cash_balance,
            max_drawdown_today=dd_today,
            consecutive_losses=consecutive_losses,
        )

        result["daily_pnl"] = report.daily_pnl
        result["daily_return_pct"] = report.daily_return_pct
        result["total_trades"] = report.total_trades
        result["total_positions"] = report.total_positions

        # ── 4. Telegram 발송 ──
        try:
            sent = await reporter.send_telegram_report(report)
            result["telegram_sent"] = sent
        except Exception as e:
            logger.warning(f"[PostMarket] Telegram 발송 실패: {e}")
            result["telegram_error"] = str(e)

        # ── 5. 리포트 Redis 저장 ──
        try:
            redis = RedisManager.get_client()
            today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            await redis.set(
                f"report:daily:{today_key}",
                json.dumps(report.to_dict()),
                ex=86400 * 90,  # 90일 보관
            )
            result["report_saved"] = True
        except Exception as e:
            result["report_save_error"] = str(e)

    except Exception as e:
        logger.error(f"[PostMarket] 리포트 생성 실패: {e}")
        result["report_error"] = str(e)

    logger.info(
        f"[PostMarket] 완료: "
        f"PnL={result.get('daily_pnl', 0):+,.0f}원 "
        f"({result.get('daily_return_pct', 0):+.2f}%)"
    )

    return result


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
