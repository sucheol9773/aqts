"""
Scheduler Idempotency — 같은 거래일에 동일 이벤트가 두 번 실행되는 것을 방지.

배경:
    TradingScheduler 는 events_executed_today 를 인메모리 상태로 관리한다.
    컨테이너가 재시작되면 이 상태가 비워지고, _find_next_event 는
    "지나간 시각이지만 아직 실행되지 않은 이벤트" 를 즉시 다시 트리거한다.
    그 결과 CD 배포 등으로 backend/scheduler 가 재시작될 때마다
    POST_MARKET (일일 리포트 발송) 가 다시 실행되어 텔레그램에 중복
    리포트가 발사되는 회귀가 발생했다.

설계:
    Redis 키 `scheduler:executed:{KST date}:{event_type}` 로 실행 여부를
    영속화한다. TTL 은 다음 KST 자정까지로 설정해 자동 만료된다.
    이를 통해 인메모리 상태와 영속 상태를 결합하여 재시작 후에도
    멱등성을 보장한다.

키 설계:
    - 키: `scheduler:executed:2026-04-07:POST_MARKET`
    - 값: 실행 시각 ISO8601 (디버깅/관찰용)
    - TTL: 다음 KST 자정 - now (보통 < 24h)

호출 경로:
    - mark_executed: TradingScheduler._execute_event 성공 직후
    - is_executed: TradingScheduler._find_next_event 에서 인메모리와 함께 조회
    - load_executed_for_today: 스케줄러 시작 시 인메모리 상태 복원
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from loguru import logger

from db.database import RedisManager

# KST = UTC+9, 한국 거래일 기준
KST = timezone(timedelta(hours=9))

# Redis 키 prefix — 다른 scheduler 키와 구분
KEY_PREFIX = "scheduler:executed"


def _now_kst() -> datetime:
    """현재 KST 시각."""
    return datetime.now(KST)


def _today_kst() -> date:
    """현재 KST 날짜."""
    return _now_kst().date()


def _build_key(event_type: str, on_date: date) -> str:
    """Redis 키 생성. event_type 은 ScheduleEventType.value 문자열."""
    return f"{KEY_PREFIX}:{on_date.isoformat()}:{event_type}"


def _ttl_until_next_midnight_kst(now: Optional[datetime] = None) -> int:
    """현재 시각부터 다음 KST 자정까지 남은 초.

    같은 KST 거래일 동안만 멱등성 키가 유효하도록 만료를 설정한다.
    최소 60초로 clamp 하여 자정 직전 race condition 을 회피한다.
    """
    now = now or _now_kst()
    next_midnight = datetime.combine(
        now.date() + timedelta(days=1),
        time(0, 0),
        tzinfo=KST,
    )
    seconds = int((next_midnight - now).total_seconds())
    return max(seconds, 60)


async def mark_executed(event_type: str, on_date: Optional[date] = None) -> bool:
    """이벤트 실행 완료를 Redis 에 기록.

    Args:
        event_type: ScheduleEventType.value (예: "POST_MARKET")
        on_date: 기록 대상 KST 날짜. 기본값은 오늘.

    Returns:
        True 면 정상 기록, False 면 Redis 오류로 기록 실패.
        기록 실패는 caller 가 인메모리 fallback 을 사용해야 함을 의미한다.
    """
    on_date = on_date or _today_kst()
    key = _build_key(event_type, on_date)
    ttl = _ttl_until_next_midnight_kst()

    try:
        client = RedisManager.get_client()
        await client.set(key, _now_kst().isoformat(), ex=ttl)
        logger.debug(f"[SchedulerIdempotency] mark_executed: {key} (ttl={ttl}s)")
        return True
    except Exception as exc:
        logger.warning(f"[SchedulerIdempotency] mark_executed 실패: {key} | {exc}")
        return False


async def is_executed(event_type: str, on_date: Optional[date] = None) -> bool:
    """해당 이벤트가 오늘 이미 실행되었는지 Redis 에서 확인.

    Args:
        event_type: ScheduleEventType.value
        on_date: 조회 대상 KST 날짜. 기본값은 오늘.

    Returns:
        True 면 이미 실행됨 (skip 해야 함).
        False 면 미실행 또는 Redis 오류 (Redis 오류 시에도 False 를
        반환해 caller 가 인메모리 상태로 fallback 하게 한다 —
        false negative 가 false positive 보다 안전하지는 않지만,
        Redis 장애가 모든 스케줄을 멈추는 단일 장애점이 되는 것을 막는다.
        대신 caller 는 인메모리 events_executed_today 와 결합하여
        이중 방어한다).
    """
    on_date = on_date or _today_kst()
    key = _build_key(event_type, on_date)

    try:
        client = RedisManager.get_client()
        exists = await client.exists(key)
        return bool(exists)
    except Exception as exc:
        logger.warning(f"[SchedulerIdempotency] is_executed 조회 실패: {key} | {exc}")
        return False


async def load_executed_for_date(on_date: Optional[date] = None) -> set[str]:
    """주어진 날짜에 이미 실행된 이벤트 타입 집합을 Redis 에서 로드.

    스케줄러가 부팅할 때 호출하여 인메모리 events_executed_today 를
    복원하는 데 사용한다.

    Args:
        on_date: 조회 대상 KST 날짜. 기본값은 오늘.

    Returns:
        이미 실행된 event_type 문자열 집합.
        Redis 오류 시 빈 집합 (인메모리에서 처음부터 시작).
    """
    on_date = on_date or _today_kst()
    pattern = f"{KEY_PREFIX}:{on_date.isoformat()}:*"
    executed: set[str] = set()

    try:
        client = RedisManager.get_client()
        # SCAN 으로 패턴 매칭 (KEYS 는 운영 환경에서 금지)
        cursor = 0
        while True:
            cursor, keys = await client.scan(cursor=cursor, match=pattern, count=100)
            for key in keys:
                # key 형식: scheduler:executed:2026-04-07:POST_MARKET
                parts = key.split(":")
                if len(parts) >= 4:
                    executed.add(parts[-1])
            if cursor == 0:
                break
        logger.info(
            f"[SchedulerIdempotency] load_executed_for_date({on_date}): " f"{len(executed)}건 복원 — {sorted(executed)}"
        )
    except Exception as exc:
        logger.warning(f"[SchedulerIdempotency] load_executed_for_date 실패: {exc}")

    return executed


async def clear_for_date(on_date: date) -> int:
    """테스트/운영 도구용: 특정 날짜의 모든 멱등성 키 삭제.

    Returns:
        삭제된 키 개수.
    """
    pattern = f"{KEY_PREFIX}:{on_date.isoformat()}:*"
    deleted = 0

    try:
        client = RedisManager.get_client()
        cursor = 0
        while True:
            cursor, keys = await client.scan(cursor=cursor, match=pattern, count=100)
            if keys:
                deleted += await client.delete(*keys)
            if cursor == 0:
                break
        logger.info(f"[SchedulerIdempotency] clear_for_date({on_date}): " f"{deleted}건 삭제")
    except Exception as exc:
        logger.warning(f"[SchedulerIdempotency] clear_for_date 실패: {exc}")

    return deleted
