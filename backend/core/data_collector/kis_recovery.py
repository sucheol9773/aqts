"""KIS API degraded → healthy 자동 복원 로직.

main.py lifespan startup 단계에서 KIS 토큰 발급이 실패하면 ``app.state.kis_degraded``
가 True 로 고착되어 영구히 남는 회귀가 있었다. 본 모듈은 health 엔드포인트가 호출될
때마다 (단, 쿨다운이 만료된 시점에만) 토큰 재발급을 시도하고, 성공하면 새 KISClient
를 반환해 호출자가 글로벌 클라이언트를 교체할 수 있도록 한다.

설계 원칙
---------
- **KIS EGW00133 회피**: KIS 의 "1분 1회 토큰 발급 제한" 에 다시 걸리지 않도록 기본
  쿨다운을 75초로 둔다. 환경변수 ``KIS_RECOVERY_COOLDOWN_SECONDS`` 로 조정 가능.
- **동시 호출 직렬화**: 다수의 health 호출이 동시에 들어와도 한 번만 재발급을
  시도하도록 ``asyncio.Lock`` 을 사용한다.
- **FastAPI 비의존**: 본 모듈은 ``app.state`` 를 직접 알지 못한다. ``KISRecoveryState``
  dataclass 와 비동기 콜백(client_factory)으로만 동작한다. main.py 는 app.state 와
  글로벌 클라이언트를 본 모듈의 결과에 따라 갱신할 책임을 진다.
- **회복 후에도 추적**: ``recovery_count`` / ``attempt_count`` 로 회복 빈도를 관찰
  가능하게 한다 (향후 Prometheus 메트릭 후속 PR 의 기반).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Optional

from loguru import logger

from core.data_collector.kis_client import KISAPIError, KISClient

DEFAULT_COOLDOWN_SECONDS = 75
DEFAULT_ALERT_THRESHOLD = 5


def _record_attempt() -> None:
    """Prometheus 카운터 inc — 메트릭 모듈은 lazy import 로 순환 의존성 회피."""
    try:
        from core.monitoring.metrics import KIS_RECOVERY_ATTEMPTS_TOTAL

        KIS_RECOVERY_ATTEMPTS_TOTAL.inc()
    except Exception:  # pragma: no cover - 메트릭 누락은 회복 경로를 막지 않는다
        pass


def _record_success() -> None:
    try:
        from core.monitoring.metrics import KIS_DEGRADED, KIS_RECOVERY_SUCCESS_TOTAL

        KIS_RECOVERY_SUCCESS_TOTAL.inc()
        KIS_DEGRADED.set(0)
    except Exception:  # pragma: no cover
        pass


def _record_degraded() -> None:
    try:
        from core.monitoring.metrics import KIS_DEGRADED

        KIS_DEGRADED.set(1)
    except Exception:  # pragma: no cover
        pass


@dataclass
class KISRecoveryState:
    """KIS degraded 상태 + 회복 시도 메타데이터.

    Attributes:
        degraded: 현재 KIS 가 degraded 상태인지.
        next_attempt_at: 다음 회복 시도가 허용되는 시각. None 이면 즉시 시도 가능.
        last_error: 가장 최근 실패의 사람-읽기용 메시지. 시크릿은 포함하지 않는다.
        attempt_count: 누적 회복 시도 횟수 (성공/실패 모두 포함).
        recovery_count: 누적 회복 성공 횟수.
        cooldown_seconds: 다음 시도까지 대기할 초.
        consecutive_failures: 현재 incident 의 연속 실패 횟수. 성공 시 0 으로 리셋.
        alert_threshold: 같은 incident 에서 알림을 1회 발송할 임계 연속 실패 횟수.
        alert_dispatched: 같은 incident 안에서 알림을 이미 발송했는지 (중복 발송 방지).
        lock: 동시 호출 직렬화를 위한 asyncio.Lock.
    """

    degraded: bool = False
    next_attempt_at: Optional[datetime] = None
    last_error: Optional[str] = None
    attempt_count: int = 0
    recovery_count: int = 0
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS
    consecutive_failures: int = 0
    alert_threshold: int = DEFAULT_ALERT_THRESHOLD
    alert_dispatched: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def mark_degraded(self, error: str, now: Optional[datetime] = None) -> None:
        """degraded 진입 시점 + 첫 쿨다운 스케줄."""
        self.degraded = True
        self.last_error = error
        self.next_attempt_at = (now or datetime.now()) + timedelta(seconds=self.cooldown_seconds)
        _record_degraded()

    def mark_recovered(self) -> None:
        """회복 성공 — degraded 해제 + 카운터 증가 + 알림 상태 리셋."""
        self.degraded = False
        self.last_error = None
        self.next_attempt_at = None
        self.recovery_count += 1
        self.consecutive_failures = 0
        self.alert_dispatched = False
        _record_success()


async def _maybe_dispatch_alert(
    state: KISRecoveryState,
    alert_callback: Optional[Callable[["KISRecoveryState"], Awaitable[None]]],
) -> None:
    """연속 실패가 임계값에 도달했고 아직 알림을 보내지 않았다면 1회 발송.

    callback 자체가 실패해도 회복 경로를 막지 않도록 예외는 swallow 한다 (경고 로그만).
    """
    if alert_callback is None:
        return
    if state.alert_dispatched:
        return
    if state.consecutive_failures < state.alert_threshold:
        return
    try:
        await alert_callback(state)
        state.alert_dispatched = True
        logger.warning(f"KIS recovery 연속 실패 {state.consecutive_failures}회 — 운영자 알림 발송 완료")
    except Exception as exc:  # pragma: no cover - 알림 실패는 회복을 막지 않음
        logger.warning(f"KIS recovery 알림 발송 실패 (회복 경로는 정상): {exc}")


async def try_recover_kis(
    state: KISRecoveryState,
    client_factory: Callable[[], Awaitable[KISClient]],
    now: Optional[datetime] = None,
    alert_callback: Optional[Callable[["KISRecoveryState"], Awaitable[None]]] = None,
) -> Optional[KISClient]:
    """쿨다운 만료 시 KIS 토큰 재발급을 시도하고, 성공하면 새 KISClient 를 반환.

    Args:
        state: 현재 KIS 회복 상태. degraded 가 False 면 즉시 None 반환.
        client_factory: 새 KISClient 를 생성하고 토큰 발급까지 마치는 비동기 콜백.
            실패 시 KISAPIError (또는 임의 Exception) 를 raise 해야 한다.
        now: 테스트 주입용 현재 시각. None 이면 ``datetime.now()`` 사용.

    Returns:
        - ``None``: degraded 가 아니거나, 쿨다운 미만이거나, 회복 시도가 실패한 경우.
        - ``KISClient``: 회복 성공. 호출자는 글로벌 ``kis_client`` 를 교체하고
          ``app.state.kis_degraded = False`` 를 설정해야 한다.
    """
    if not state.degraded:
        return None

    current = now or datetime.now()
    if state.next_attempt_at is not None and current < state.next_attempt_at:
        return None

    # 동시 호출 직렬화 — lock 진입 후 조건을 다시 확인하여 double-attempt 방지
    async with state.lock:
        if not state.degraded:
            return None
        current = now or datetime.now()
        if state.next_attempt_at is not None and current < state.next_attempt_at:
            return None

        state.attempt_count += 1
        _record_attempt()
        logger.info(f"KIS recovery 시도 #{state.attempt_count} (cooldown={state.cooldown_seconds}s)")
        try:
            new_client = await client_factory()
        except KISAPIError as exc:
            state.last_error = f"{exc.code}: {exc.message}"
            state.next_attempt_at = current + timedelta(seconds=state.cooldown_seconds)
            state.consecutive_failures += 1
            logger.warning(
                f"KIS recovery 실패 #{state.attempt_count} "
                f"(연속 {state.consecutive_failures}회): {state.last_error} "
                f"(다음 시도: {state.next_attempt_at:%H:%M:%S})"
            )
            await _maybe_dispatch_alert(state, alert_callback)
            return None
        except Exception as exc:
            state.last_error = f"{type(exc).__name__}: {exc}"
            state.next_attempt_at = current + timedelta(seconds=state.cooldown_seconds)
            state.consecutive_failures += 1
            logger.warning(
                f"KIS recovery 실패 #{state.attempt_count} "
                f"(연속 {state.consecutive_failures}회): {state.last_error}"
            )
            await _maybe_dispatch_alert(state, alert_callback)
            return None

        state.mark_recovered()
        logger.info(f"KIS recovery 성공 (recovery_count={state.recovery_count}, " f"attempts={state.attempt_count})")
        return new_client
