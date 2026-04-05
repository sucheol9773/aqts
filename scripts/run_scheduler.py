#!/usr/bin/env python3
"""
AQTS 모의투자 스케줄러 실행 스크립트

서버 터미널에서 직접 실행:
    cd ~/aqts && source .venv/bin/activate
    python scripts/run_scheduler.py

또는 특정 이벤트만 즉시 실행:
    python scripts/run_scheduler.py --event PRE_MARKET
    python scripts/run_scheduler.py --event MARKET_OPEN

핸들러만 직접 호출 (스케줄러 없이):
    python scripts/run_scheduler.py --handler pre_market
    python scripts/run_scheduler.py --handler market_open

환경변수:
    KIS_TRADING_MODE=DEMO  (필수: DEMO 모드에서만 스케줄러 시작 가능)
"""

import argparse
import asyncio
import signal
import sys
from pathlib import Path

# backend를 Python 경로에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from config.logging import logger
from core.scheduler_handlers import (
    handle_market_close,
    handle_market_open,
    handle_midday_check,
    handle_post_market,
    handle_pre_market,
    register_pipeline_handlers,
)
from core.trading_scheduler import ScheduleEventType, TradingScheduler

# ── 핸들러 직접 호출 매핑 ──
HANDLER_MAP = {
    "pre_market": handle_pre_market,
    "market_open": handle_market_open,
    "midday_check": handle_midday_check,
    "market_close": handle_market_close,
    "post_market": handle_post_market,
}

# ── 이벤트 타입 매핑 ──
EVENT_MAP = {
    "PRE_MARKET": ScheduleEventType.PRE_MARKET,
    "MARKET_OPEN": ScheduleEventType.MARKET_OPEN,
    "MIDDAY_CHECK": ScheduleEventType.MIDDAY_CHECK,
    "MARKET_CLOSE": ScheduleEventType.MARKET_CLOSE,
    "POST_MARKET": ScheduleEventType.POST_MARKET,
}


async def run_full_scheduler():
    """스케줄러 전체 루프 실행 (Ctrl+C로 종료)"""
    scheduler = TradingScheduler()
    register_pipeline_handlers(scheduler)

    # 시그널 핸들러: 정상 종료
    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("종료 시그널 수신 — 스케줄러 중지 중...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    logger.info("=" * 60)
    logger.info("AQTS 모의투자 스케줄러 시작")
    logger.info("종료: Ctrl+C")
    logger.info("=" * 60)

    await scheduler.start()

    # 종료 시그널 대기
    await stop_event.wait()
    await scheduler.stop()

    logger.info("스케줄러 정상 종료")


async def run_single_event(event_name: str):
    """특정 이벤트를 즉시 실행 (스케줄러 경유)"""
    event_type = EVENT_MAP.get(event_name.upper())
    if not event_type:
        print(f"알 수 없는 이벤트: {event_name}")
        print(f"가능한 값: {', '.join(EVENT_MAP.keys())}")
        sys.exit(1)

    scheduler = TradingScheduler()
    register_pipeline_handlers(scheduler)

    logger.info(f"이벤트 즉시 실행: {event_name}")
    result = await scheduler.run_event_now(event_type)

    import json

    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


async def run_handler_directly(handler_name: str):
    """핸들러를 직접 호출 (스케줄러 없이)"""
    handler = HANDLER_MAP.get(handler_name.lower())
    if not handler:
        print(f"알 수 없는 핸들러: {handler_name}")
        print(f"가능한 값: {', '.join(HANDLER_MAP.keys())}")
        sys.exit(1)

    logger.info(f"핸들러 직접 호출: {handler_name}")
    result = await handler()

    import json

    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


def main():
    parser = argparse.ArgumentParser(
        description="AQTS 모의투자 스케줄러",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python scripts/run_scheduler.py                    # 전체 스케줄러 실행
  python scripts/run_scheduler.py --event PRE_MARKET # 장 전 이벤트 즉시 실행
  python scripts/run_scheduler.py --handler market_open  # 장 시작 핸들러 직접 호출
        """,
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--event",
        type=str,
        choices=list(EVENT_MAP.keys()),
        help="특정 이벤트 즉시 실행 (스케줄러 경유)",
    )
    group.add_argument(
        "--handler",
        type=str,
        choices=list(HANDLER_MAP.keys()),
        help="핸들러 직접 호출 (스케줄러 없이)",
    )

    args = parser.parse_args()

    if args.event:
        asyncio.run(run_single_event(args.event))
    elif args.handler:
        asyncio.run(run_handler_directly(args.handler))
    else:
        asyncio.run(run_full_scheduler())


if __name__ == "__main__":
    main()
