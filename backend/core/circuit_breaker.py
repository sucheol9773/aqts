"""
Circuit Breaker 패턴 구현 (Gate C 운영 요건)

외부 API (KIS, FRED, ECOS, Anthropic) 장애 시 자동으로 요청을 차단하여
시스템 전체 장애 전파를 방지합니다.

상태:
- CLOSED: 정상 동작, 모든 요청 통과
- OPEN: 차단 상태, 모든 요청 즉시 실패 (fallback 반환)
- HALF_OPEN: 복구 시도, 제한적 요청 통과

설정:
- failure_threshold: 연속 실패 N회 후 OPEN 전이 (기본 5)
- recovery_timeout: OPEN 상태 유지 시간 (기본 60초)
- half_open_max_calls: HALF_OPEN에서 허용하는 최대 요청 수 (기본 3)

사용법:
    breaker = CircuitBreaker(name="kis_api", failure_threshold=5)

    @breaker
    async def call_kis():
        ...

    # 또는 수동 사용:
    async with breaker:
        result = await httpx_client.get(url)
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from config.logging import logger


class CircuitState(str, Enum):
    """서킷 브레이커 상태"""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class CircuitStats:
    """서킷 브레이커 통계"""

    total_calls: int = 0
    total_failures: int = 0
    consecutive_failures: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    state_changed_at: float = field(default_factory=time.monotonic)
    half_open_calls: int = 0


class CircuitBreakerError(Exception):
    """서킷이 OPEN 상태일 때 발생하는 예외"""

    def __init__(self, breaker_name: str, remaining_seconds: float):
        self.breaker_name = breaker_name
        self.remaining_seconds = remaining_seconds
        super().__init__(f"Circuit breaker '{breaker_name}' is OPEN. " f"Recovery in {remaining_seconds:.1f}s")


class CircuitBreaker:
    """
    비동기 Circuit Breaker

    외부 서비스 호출을 감싸서 장애 전파를 차단합니다.
    데코레이터, 컨텍스트 매니저 모두 지원합니다.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 3,
        excluded_exceptions: Optional[tuple[type[Exception], ...]] = None,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self.excluded_exceptions = excluded_exceptions or ()

        self._state = CircuitState.CLOSED
        self._stats = CircuitStats()
        self._lock = asyncio.Lock()

        logger.info(f"CircuitBreaker '{name}' 초기화: " f"threshold={failure_threshold}, timeout={recovery_timeout}s")

    @property
    def state(self) -> CircuitState:
        """현재 상태 (시간 기반 자동 전이 포함)"""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._stats.state_changed_at
            if elapsed >= self.recovery_timeout:
                self._transition_to(CircuitState.HALF_OPEN)
        return self._state

    @property
    def stats(self) -> CircuitStats:
        """현재 통계"""
        return self._stats

    def _transition_to(self, new_state: CircuitState) -> None:
        """상태 전이 (내부)"""
        old_state = self._state
        self._state = new_state
        self._stats.state_changed_at = time.monotonic()

        if new_state == CircuitState.HALF_OPEN:
            self._stats.half_open_calls = 0

        logger.warning(f"CircuitBreaker '{self.name}': {old_state.value} → {new_state.value}")

    def _record_success(self) -> None:
        """성공 기록"""
        self._stats.total_calls += 1
        self._stats.consecutive_failures = 0
        self._stats.last_success_time = time.monotonic()

        if self._state == CircuitState.HALF_OPEN:
            self._stats.half_open_calls += 1
            if self._stats.half_open_calls >= self.half_open_max_calls:
                self._transition_to(CircuitState.CLOSED)

    def _record_failure(self, exc: Exception) -> None:
        """실패 기록"""
        # 제외 예외는 실패로 카운트하지 않음
        if isinstance(exc, self.excluded_exceptions):
            self._stats.total_calls += 1
            return

        self._stats.total_calls += 1
        self._stats.total_failures += 1
        self._stats.consecutive_failures += 1
        self._stats.last_failure_time = time.monotonic()

        if self._state == CircuitState.HALF_OPEN:
            # HALF_OPEN에서 실패하면 즉시 OPEN으로 복귀
            self._transition_to(CircuitState.OPEN)
        elif self._state == CircuitState.CLOSED and self._stats.consecutive_failures >= self.failure_threshold:
            self._transition_to(CircuitState.OPEN)

    def _check_state(self) -> None:
        """요청 허용 여부 확인"""
        current_state = self.state  # 시간 기반 전이 트리거

        if current_state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._stats.state_changed_at
            remaining = max(0, self.recovery_timeout - elapsed)
            raise CircuitBreakerError(self.name, remaining)

        if current_state == CircuitState.HALF_OPEN:
            if self._stats.half_open_calls >= self.half_open_max_calls:
                raise CircuitBreakerError(self.name, 0)

    def __call__(self, func: Callable) -> Callable:
        """데코레이터로 사용"""

        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            async with self._lock:
                self._check_state()

            try:
                result = await func(*args, **kwargs)
            except Exception as e:
                async with self._lock:
                    self._record_failure(e)
                raise
            else:
                async with self._lock:
                    self._record_success()
                return result

        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper

    async def __aenter__(self) -> "CircuitBreaker":
        """컨텍스트 매니저 진입"""
        async with self._lock:
            self._check_state()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """컨텍스트 매니저 종료"""
        async with self._lock:
            if exc_val is not None:
                self._record_failure(exc_val)
            else:
                self._record_success()

    def to_dict(self) -> dict[str, Any]:
        """상태 정보를 딕셔너리로 반환 (모니터링/API 응답용)"""
        current_state = self.state
        return {
            "name": self.name,
            "state": current_state.value,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
            "stats": {
                "total_calls": self._stats.total_calls,
                "total_failures": self._stats.total_failures,
                "consecutive_failures": self._stats.consecutive_failures,
            },
        }


# ══════════════════════════════════════
# 글로벌 Circuit Breaker 레지스트리
# ══════════════════════════════════════
class CircuitBreakerRegistry:
    """
    Circuit Breaker 중앙 관리

    모든 외부 서비스의 서킷 브레이커를 등록하고 조회합니다.
    시스템 헬스체크 엔드포인트에서 전체 상태를 확인할 수 있습니다.
    """

    _breakers: dict[str, CircuitBreaker] = {}

    @classmethod
    def register(cls, breaker: CircuitBreaker) -> CircuitBreaker:
        """서킷 브레이커 등록"""
        cls._breakers[breaker.name] = breaker
        return breaker

    @classmethod
    def get(cls, name: str) -> Optional[CircuitBreaker]:
        """이름으로 조회"""
        return cls._breakers.get(name)

    @classmethod
    def all(cls) -> dict[str, CircuitBreaker]:
        """전체 조회"""
        return dict(cls._breakers)

    @classmethod
    def status(cls) -> dict[str, dict]:
        """전체 상태 조회 (API 응답용)"""
        return {name: breaker.to_dict() for name, breaker in cls._breakers.items()}

    @classmethod
    def reset_all(cls) -> None:
        """전체 초기화 (테스트용)"""
        cls._breakers.clear()


# ══════════════════════════════════════
# 사전 정의된 서킷 브레이커
# ══════════════════════════════════════
kis_breaker = CircuitBreakerRegistry.register(
    CircuitBreaker(
        name="kis_api",
        failure_threshold=5,
        recovery_timeout=60.0,
        half_open_max_calls=2,
    )
)

fred_breaker = CircuitBreakerRegistry.register(
    CircuitBreaker(
        name="fred_api",
        failure_threshold=3,
        recovery_timeout=120.0,
        half_open_max_calls=1,
    )
)

ecos_breaker = CircuitBreakerRegistry.register(
    CircuitBreaker(
        name="ecos_api",
        failure_threshold=3,
        recovery_timeout=120.0,
        half_open_max_calls=1,
    )
)

anthropic_breaker = CircuitBreakerRegistry.register(
    CircuitBreaker(
        name="anthropic_api",
        failure_threshold=3,
        recovery_timeout=90.0,
        half_open_max_calls=1,
    )
)
