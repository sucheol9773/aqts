"""
AQTS Phase 7 - 모의투자 자동화 스케줄러

KRX 장 시간에 맞춰 DEMO 모드 파이프라인을 자동 실행합니다.

스케줄:
  08:30 — 장 전 준비 (건전성 검사, 일일 리셋, 뉴스 수집)
  09:00 — 장 시작 (분석 파이프라인 실행, 주문 생성)
  11:30 — 중간 점검 (포지션 모니터링, 리밸런싱 검토)
  15:30 — 장 마감 처리 (최종 잔고 기록, 일일 리포트 생성)
  16:00 — 장 마감 후 (리포트 발송, 다음 거래일 준비)

거래일 판별:
  - 주말(토/일) 제외
  - 한국 공휴일 제외 (하드코딩 + 연 1회 갱신)
"""

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Awaitable, Callable, Optional

from loguru import logger

from config.settings import TradingMode, get_settings

# ══════════════════════════════════════
# 한국 시간대 및 거래일 관리
# ══════════════════════════════════════

KST = timezone(timedelta(hours=9))

# 2026년 한국 공휴일 (매년 갱신 필요)
KR_HOLIDAYS_2026 = {
    date(2026, 1, 1),  # 신정
    date(2026, 2, 16),  # 설날 연휴
    date(2026, 2, 17),  # 설날
    date(2026, 2, 18),  # 설날 연휴
    date(2026, 3, 1),  # 삼일절
    date(2026, 5, 5),  # 어린이날
    date(2026, 5, 24),  # 부처님 오신 날
    date(2026, 6, 6),  # 현충일
    date(2026, 8, 15),  # 광복절
    date(2026, 9, 24),  # 추석 연휴
    date(2026, 9, 25),  # 추석
    date(2026, 9, 26),  # 추석 연휴
    date(2026, 10, 3),  # 개천절
    date(2026, 10, 9),  # 한글날
    date(2026, 12, 25),  # 크리스마스
}

# 2025년 한국 공휴일 (백업)
KR_HOLIDAYS_2025 = {
    date(2025, 1, 1),
    date(2025, 1, 28),
    date(2025, 1, 29),
    date(2025, 1, 30),
    date(2025, 3, 1),
    date(2025, 5, 5),
    date(2025, 5, 6),
    date(2025, 6, 6),
    date(2025, 8, 15),
    date(2025, 10, 3),
    date(2025, 10, 6),
    date(2025, 10, 7),
    date(2025, 10, 8),
    date(2025, 10, 9),
    date(2025, 12, 25),
}

ALL_HOLIDAYS = KR_HOLIDAYS_2025 | KR_HOLIDAYS_2026


def is_trading_day(d: date) -> bool:
    """한국 거래일 여부 확인 (주말/공휴일 제외)"""
    if d.weekday() >= 5:  # 토(5), 일(6)
        return False
    if d in ALL_HOLIDAYS:
        return False
    return True


def next_trading_day(d: date) -> date:
    """다음 거래일 반환"""
    candidate = d + timedelta(days=1)
    while not is_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def now_kst() -> datetime:
    """현재 한국 시간 반환"""
    return datetime.now(KST)


# ══════════════════════════════════════
# 스케줄 이벤트 정의
# ══════════════════════════════════════


class ScheduleEventType(str, Enum):
    """스케줄 이벤트 유형"""

    PRE_MARKET = "PRE_MARKET"  # 08:30 장 전 준비
    MARKET_OPEN = "MARKET_OPEN"  # 09:00 장 시작
    MIDDAY_CHECK = "MIDDAY_CHECK"  # 11:30 중간 점검
    MARKET_CLOSE = "MARKET_CLOSE"  # 15:30 장 마감
    POST_MARKET = "POST_MARKET"  # 16:00 마감 후 처리


@dataclass
class ScheduleEvent:
    """스케줄 이벤트"""

    event_type: ScheduleEventType
    scheduled_time: time  # KST 기준
    description: str
    handler: Optional[str] = None  # 핸들러 메서드 이름


# 기본 스케줄
DEFAULT_SCHEDULE = [
    ScheduleEvent(
        event_type=ScheduleEventType.PRE_MARKET,
        scheduled_time=time(8, 30),
        description="장 전 준비: 건전성 검사, 일일 리셋, 뉴스 수집",
        handler="handle_pre_market",
    ),
    ScheduleEvent(
        event_type=ScheduleEventType.MARKET_OPEN,
        scheduled_time=time(9, 0),
        description="장 시작: 분석 파이프라인 실행, 주문 생성",
        handler="handle_market_open",
    ),
    ScheduleEvent(
        event_type=ScheduleEventType.MIDDAY_CHECK,
        scheduled_time=time(11, 30),
        description="중간 점검: 포지션 모니터링, 리밸런싱 검토",
        handler="handle_midday_check",
    ),
    ScheduleEvent(
        event_type=ScheduleEventType.MARKET_CLOSE,
        scheduled_time=time(15, 30),
        description="장 마감: 최종 잔고 기록, 성과 계산",
        handler="handle_market_close",
    ),
    ScheduleEvent(
        event_type=ScheduleEventType.POST_MARKET,
        scheduled_time=time(16, 0),
        description="마감 후: 일일 리포트 생성 및 발송",
        handler="handle_post_market",
    ),
]


# ══════════════════════════════════════
# 스케줄러 상태
# ══════════════════════════════════════


class SchedulerStatus(str, Enum):
    """스케줄러 상태"""

    IDLE = "IDLE"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


@dataclass
class SchedulerState:
    """스케줄러 런타임 상태"""

    status: SchedulerStatus = SchedulerStatus.IDLE
    current_date: Optional[date] = None
    last_event: Optional[ScheduleEventType] = None
    last_event_at: Optional[datetime] = None
    next_event: Optional[ScheduleEventType] = None
    next_event_at: Optional[datetime] = None
    events_executed_today: list[dict] = field(default_factory=list)
    errors_today: list[dict] = field(default_factory=list)
    total_trading_days: int = 0
    started_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "current_date": self.current_date.isoformat() if self.current_date else None,
            "last_event": self.last_event.value if self.last_event else None,
            "last_event_at": self.last_event_at.isoformat() if self.last_event_at else None,
            "next_event": self.next_event.value if self.next_event else None,
            "next_event_at": self.next_event_at.isoformat() if self.next_event_at else None,
            "events_executed_today": len(self.events_executed_today),
            "errors_today": len(self.errors_today),
            "total_trading_days": self.total_trading_days,
            "started_at": self.started_at.isoformat() if self.started_at else None,
        }


# ══════════════════════════════════════
# 모의투자 자동화 스케줄러
# ══════════════════════════════════════


class TradingScheduler:
    """
    KRX 장 시간 기반 DEMO 모드 자동화 스케줄러

    Usage:
        scheduler = TradingScheduler()
        await scheduler.start()  # 스케줄러 루프 시작
        await scheduler.stop()   # 스케줄러 중지
    """

    def __init__(
        self,
        schedule: Optional[list[ScheduleEvent]] = None,
    ):
        self._settings = get_settings()
        self._schedule = schedule or DEFAULT_SCHEDULE
        self._state = SchedulerState()
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # 핸들러 콜백 (외부 주입 가능)
        self._handlers: dict[str, Callable[..., Awaitable]] = {}

    # ── 프로퍼티 ──

    @property
    def state(self) -> SchedulerState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._running and self._state.status == SchedulerStatus.RUNNING

    # ── 핸들러 등록 ──

    def register_handler(self, event_type: str, handler: Callable[..., Awaitable]) -> None:
        """이벤트 핸들러 등록"""
        self._handlers[event_type] = handler
        logger.debug(f"핸들러 등록: {event_type}")

    # ── 스케줄러 제어 ──

    async def start(self) -> None:
        """스케줄러 시작"""
        if self._running:
            logger.warning("스케줄러가 이미 실행 중입니다")
            return

        # DEMO 모드 검증
        if self._settings.kis.trading_mode != TradingMode.DEMO:
            raise RuntimeError(
                f"DEMO 모드에서만 스케줄러를 시작할 수 있습니다 " f"(현재: {self._settings.kis.trading_mode.value})"
            )

        self._running = True
        self._state.status = SchedulerStatus.RUNNING
        self._state.started_at = now_kst()

        logger.info("━" * 50)
        logger.info("AQTS 모의투자 스케줄러 시작")
        logger.info(f"거래 모드: {self._settings.kis.trading_mode.value}")
        logger.info(f"시작 시간: {self._state.started_at}")
        logger.info("━" * 50)

        # 재시작 시 Redis 에 기록된 오늘의 실행 이력을 복원하여
        # 같은 거래일에 동일 이벤트가 두 번 실행되는 것을 방지한다.
        try:
            from core.scheduler_idempotency import load_executed_for_date

            today = self._state.started_at.date()
            executed_types = await load_executed_for_date(today)
            if executed_types:
                self._state.current_date = today
                self._state.events_executed_today = [{"event_type": et, "restored": True} for et in executed_types]
                logger.info(
                    f"[Scheduler] 멱등성 복원: {today} 에 이미 실행된 이벤트 "
                    f"{len(executed_types)}건 — {sorted(executed_types)}"
                )
        except Exception as exc:
            logger.warning(f"[Scheduler] 멱등성 복원 실패 (인메모리만 사용): {exc}")

        self._task = asyncio.create_task(self._scheduler_loop())

    async def stop(self) -> None:
        """스케줄러 중지"""
        self._running = False
        self._state.status = SchedulerStatus.STOPPED

        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info("AQTS 모의투자 스케줄러 중지")

    async def pause(self) -> None:
        """스케줄러 일시 정지"""
        self._state.status = SchedulerStatus.PAUSED
        logger.info("스케줄러 일시 정지")

    async def resume(self) -> None:
        """스케줄러 재개"""
        if self._running:
            self._state.status = SchedulerStatus.RUNNING
            logger.info("스케줄러 재개")

    # ── 즉시 실행 ──

    async def run_event_now(self, event_type: ScheduleEventType) -> dict:
        """특정 이벤트를 즉시 실행 (수동 트리거)"""
        event = next(
            (e for e in self._schedule if e.event_type == event_type),
            None,
        )
        if not event:
            return {"success": False, "error": f"이벤트 없음: {event_type.value}"}

        return await self._execute_event(event)

    # ── 스케줄러 메인 루프 ──

    async def _scheduler_loop(self) -> None:
        """메인 스케줄러 루프"""
        while self._running:
            try:
                now = now_kst()
                today = now.date()

                # 거래일 아닌 경우 다음 거래일까지 대기
                if not is_trading_day(today):
                    next_td = next_trading_day(today)
                    wake_time = datetime.combine(next_td, time(8, 0), tzinfo=KST)
                    wait_seconds = (wake_time - now).total_seconds()
                    if wait_seconds > 0:
                        logger.info(f"비거래일 ({today}). 다음 거래일 {next_td}까지 대기")
                        self._state.next_event_at = wake_time
                        await asyncio.sleep(min(wait_seconds, 3600))  # 최대 1시간 단위 체크
                    continue

                # 새로운 거래일 시작
                if self._state.current_date != today:
                    self._state.current_date = today
                    self._state.events_executed_today = []
                    self._state.errors_today = []
                    self._state.total_trading_days += 1
                    logger.info(f"=== 거래일 {today} (#{self._state.total_trading_days}) ===")

                # 일시 정지 상태면 대기
                if self._state.status == SchedulerStatus.PAUSED:
                    await asyncio.sleep(60)
                    continue

                # 다음 실행할 이벤트 찾기
                next_event = self._find_next_event(now)

                if next_event:
                    event_time = datetime.combine(today, next_event.scheduled_time, tzinfo=KST)
                    wait_seconds = (event_time - now).total_seconds()

                    self._state.next_event = next_event.event_type
                    self._state.next_event_at = event_time

                    if wait_seconds > 0:
                        # 대기 (1분 단위 체크로 중지 신호 반응)
                        logger.debug(
                            f"다음 이벤트: {next_event.event_type.value} "
                            f"({next_event.scheduled_time}) - {wait_seconds:.0f}초 후"
                        )
                        await asyncio.sleep(min(wait_seconds, 60))

                        # 대기 후 시간 재확인
                        now = now_kst()
                        if now < event_time:
                            continue

                    # 이벤트 실행
                    await self._execute_event(next_event)
                else:
                    # 오늘 모든 이벤트 완료 → 다음 거래일까지 대기
                    next_td = next_trading_day(today)
                    wake_time = datetime.combine(next_td, time(8, 0), tzinfo=KST)
                    wait_seconds = (wake_time - now).total_seconds()
                    logger.info(f"오늘 스케줄 완료. 다음 거래일 {next_td}까지 대기")
                    self._state.next_event = None
                    self._state.next_event_at = wake_time
                    await asyncio.sleep(min(wait_seconds, 3600))

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"스케줄러 루프 오류: {e}")
                self._state.errors_today.append(
                    {
                        "time": now_kst().isoformat(),
                        "error": str(e),
                    }
                )
                await asyncio.sleep(60)  # 오류 후 1분 대기

    def _find_next_event(self, now: datetime) -> Optional[ScheduleEvent]:
        """현재 시간 이후 실행할 다음 이벤트 반환.

        events_executed_today (인메모리) + Redis idempotency (영속) 를
        모두 확인하여 같은 거래일에 동일 이벤트가 두 번 트리거되는 것을
        방지한다. Redis 조회는 동기 함수에서 불가능하므로 인메모리에는
        부팅 시 load_executed_for_date 로 복원된 값이 들어있고,
        실행 직전 _execute_event 에서 한 번 더 await 으로 확인한다.
        """
        current_time = now.time()
        executed_types = {e["event_type"] for e in self._state.events_executed_today}

        for event in self._schedule:
            if event.event_type.value in executed_types:
                continue
            if event.scheduled_time >= current_time:
                return event
            # 시간이 지났지만 아직 실행 안 됨 (지연 실행)
            if event.event_type.value not in executed_types:
                return event

        return None

    async def _execute_event(self, event: ScheduleEvent) -> dict:
        """이벤트 실행.

        실행 직전에 Redis 멱등성 키를 await 으로 한 번 더 확인하여,
        부팅 시 load 와 _find_next_event 사이의 race condition 도 방어한다.
        """
        start_time = now_kst()
        logger.info(f"▶ [{event.event_type.value}] {event.description}")

        result = {
            "event_type": event.event_type.value,
            "started_at": start_time.isoformat(),
            "success": False,
            "details": {},
        }

        # 실행 전 Redis 멱등성 final-check
        try:
            from core.scheduler_idempotency import is_executed

            if await is_executed(event.event_type.value, start_time.date()):
                logger.info(
                    f"⚠ [{event.event_type.value}] 멱등성 키 존재 — 같은 거래일 "
                    f"({start_time.date()}) 에 이미 실행됨. 스킵."
                )
                result["success"] = True
                result["skipped"] = True
                result["skip_reason"] = "idempotency_key_exists"
                # 인메모리에도 반영하여 같은 부팅 세션 내에서 또 잡지 않게 한다
                if event.event_type.value not in {e["event_type"] for e in self._state.events_executed_today}:
                    self._state.events_executed_today.append(result)
                return result
        except Exception as exc:
            logger.warning(f"[Scheduler] 멱등성 final-check 실패 (인메모리만 사용): {exc}")

        try:
            # 등록된 핸들러 호출
            handler_name = event.handler or event.event_type.value.lower()
            handler = self._handlers.get(handler_name)

            if handler:
                handler_result = await handler()
                result["details"] = handler_result if isinstance(handler_result, dict) else {}
                result["success"] = True
            else:
                # 기본 핸들러 (내장)
                builtin = getattr(self, f"_default_{handler_name}", None)
                if builtin:
                    handler_result = await builtin()
                    result["details"] = handler_result if isinstance(handler_result, dict) else {}
                    result["success"] = True
                else:
                    logger.warning(f"핸들러 미등록: {handler_name}")
                    result["details"] = {"message": "핸들러 미등록 (건너뜀)"}
                    result["success"] = True  # 핸들러 없어도 진행

            elapsed = (now_kst() - start_time).total_seconds()
            result["elapsed_seconds"] = round(elapsed, 1)

            self._state.last_event = event.event_type
            self._state.last_event_at = now_kst()
            self._state.events_executed_today.append(result)

            # Redis 에 멱등성 키 기록 — 컨테이너 재시작 후에도 같은 거래일에
            # 동일 이벤트가 다시 실행되지 않도록 한다.
            try:
                from core.scheduler_idempotency import mark_executed

                await mark_executed(event.event_type.value, start_time.date())
            except Exception as exc:
                logger.warning(f"[Scheduler] 멱등성 키 기록 실패 (인메모리만 보존): {exc}")

            logger.info(f"✓ [{event.event_type.value}] 완료 ({elapsed:.1f}초)")

        except Exception as e:
            elapsed = (now_kst() - start_time).total_seconds()
            result["error"] = f"{type(e).__name__}: {str(e)}"
            result["elapsed_seconds"] = round(elapsed, 1)

            self._state.errors_today.append(
                {
                    "event_type": event.event_type.value,
                    "time": now_kst().isoformat(),
                    "error": str(e),
                }
            )

            logger.error(f"✗ [{event.event_type.value}] 실패: {e}")

        return result

    # ── 기본 내장 핸들러 ──

    async def _default_handle_pre_market(self) -> dict:
        """장 전 준비 기본 핸들러"""
        result = {}

        # 1. 건전성 검사
        try:
            from core.health_checker import HealthChecker

            checker = HealthChecker()
            health = await checker.run_full_check()
            result["health_status"] = health.overall_status.value
            result["ready_for_trading"] = health.ready_for_trading
        except Exception as e:
            result["health_check_error"] = str(e)

        # 2. TradingGuard 일일 리셋
        try:
            from core.trading_guard import TradingGuard

            guard = TradingGuard()
            guard.reset_daily_state()
            result["daily_reset"] = True
        except Exception as e:
            result["daily_reset_error"] = str(e)

        return result

    async def _default_handle_market_open(self) -> dict:
        """장 시작 기본 핸들러"""
        result = {"message": "장 시작 — 분석 파이프라인 실행 대기"}

        # 파이프라인 실행은 외부 핸들러에서 처리
        # 기본 핸들러는 상태 기록만 수행
        result["market_open_time"] = now_kst().isoformat()

        return result

    async def _default_handle_midday_check(self) -> dict:
        """중간 점검 기본 핸들러"""
        result = {"message": "중간 점검 — 포지션 모니터링"}
        result["check_time"] = now_kst().isoformat()
        return result

    async def _default_handle_market_close(self) -> dict:
        """장 마감 기본 핸들러"""
        result = {"message": "장 마감 처리"}
        result["close_time"] = now_kst().isoformat()
        return result

    async def _default_handle_post_market(self) -> dict:
        """장 마감 후 기본 핸들러"""
        result = {"message": "마감 후 처리 — 일일 리포트 생성 대기"}
        result["post_market_time"] = now_kst().isoformat()
        return result
