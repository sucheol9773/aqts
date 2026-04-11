"""
Token Revocation Store (P0-2a, security-integrity-roadmap §3.2)

목적
----
JWT `jti` 블랙리스트를 단일 인터페이스로 추상화하고, **운영 환경에서는 Redis
백엔드를 강제**한다. 인메모리 백엔드는 단위 테스트와 개발용 fallback 으로만
허용한다.

설계 원칙
---------
1. **fail-closed**: Redis 백엔드 실패 시 `RevocationBackendUnavailable` 을
   raise 하여 호출부(`AuthService.verify_token`)가 401 대신 503 을 반환하도록
   강제한다. "Redis 가 죽으면 모든 토큰을 통과시킨다" 는 패턴(fail-open)을
   금지한다.
2. **TTL 강제**: 블랙리스트 엔트리는 토큰의 잔여 수명만큼만 보관한다. 만료된
   토큰을 영구 보관할 이유가 없고, Redis 메모리 누수를 막는다.
3. **동기 인터페이스 유지**: 기존 `AuthService.verify_token` / `revoke_token`
   가 동기 메서드이므로, 단일 GET/SETEX 만 호출하는 sync `redis.Redis` 클라이
   언트를 사용한다. 이벤트 루프 블로킹은 로컬 Redis 기준 ~1ms 로 허용 가능.
4. **백엔드 선택**: `AQTS_REVOCATION_BACKEND=redis|memory` 환경변수로 명시적
   선택. 운영 컨테이너는 반드시 `redis` 를 설정한다 (compose/CI 에서 강제).

문서: docs/security/security-integrity-roadmap.md §3.2, §3.6
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional, Protocol

import redis
from redis.exceptions import RedisError

from config.settings import get_settings
from core.monitoring.metrics import REVOCATION_BACKEND_FAILURE_TOTAL
from core.utils.env import env_bool  # noqa: F401  (의존성 일관성 표시)

logger = logging.getLogger(__name__)


# ── 예외 ──
class RevocationBackendUnavailable(RuntimeError):
    """Revocation 백엔드가 일시적으로 사용 불가 상태.

    호출부는 이 예외를 잡아서 503 (SESSION_STORE_UNAVAILABLE) 로 변환한다.
    절대로 401 로 변환해서는 안 된다 (fail-open 금지).
    """


# ── Protocol ──
class TokenRevocationStore(Protocol):
    """토큰 무효화 저장소 인터페이스."""

    def revoke(self, jti: str, ttl_seconds: int) -> None: ...

    def is_revoked(self, jti: str) -> bool: ...


# ── In-Memory 백엔드 (테스트/개발 전용) ──
class InMemoryTokenRevocationStore:
    """인메모리 블랙리스트.

    프로세스 재시작 시 모든 엔트리가 손실되며, 멀티 인스턴스 동기화가 불가능
    하므로 운영에서는 절대 사용해서는 안 된다. `_blacklist` 속성은 기존 테스트
    호환성을 위해 노출한다.
    """

    def __init__(self) -> None:
        self._blacklist: set[str] = set()
        # jti -> 만료 epoch (TTL 에뮬레이션)
        self._expiry: dict[str, float] = {}
        self._lock = threading.Lock()

    def revoke(self, jti: str, ttl_seconds: int = 86400) -> None:
        # default ttl: 하위호환용 (테스트에서 ttl 인자 없이 호출). 운영 경로는
        # 항상 명시적으로 토큰 잔여 수명을 전달한다.
        if ttl_seconds <= 0:
            return
        with self._lock:
            self._blacklist.add(jti)
            self._expiry[jti] = time.time() + ttl_seconds

    def is_revoked(self, jti: str) -> bool:
        with self._lock:
            if jti not in self._blacklist:
                return False
            exp = self._expiry.get(jti, 0.0)
            if exp and exp < time.time():
                self._blacklist.discard(jti)
                self._expiry.pop(jti, None)
                return False
            return True


# ── Redis 백엔드 (운영) ──
class RedisTokenRevocationStore:
    """Redis 기반 블랙리스트 (sync 클라이언트).

    키 스킴: `aqts:revoked:<jti>`, value `"1"`, TTL = 토큰 잔여 수명.
    """

    def __init__(
        self,
        redis_url: str,
        *,
        key_prefix: str = "aqts:revoked:",
        socket_timeout: float = 2.0,
        socket_connect_timeout: float = 2.0,
    ) -> None:
        self._client = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_connect_timeout,
            health_check_interval=30,
        )
        self._prefix = key_prefix

    def _key(self, jti: str) -> str:
        return f"{self._prefix}{jti}"

    def revoke(self, jti: str, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return
        try:
            self._client.setex(self._key(jti), ttl_seconds, "1")
        except RedisError as e:
            REVOCATION_BACKEND_FAILURE_TOTAL.labels(op="revoke").inc()
            logger.error(
                "RedisTokenRevocationStore.revoke failed jti=%s err=%s",
                jti,
                e.__class__.__name__,
            )
            raise RevocationBackendUnavailable("revoke failed") from e

    def is_revoked(self, jti: str) -> bool:
        try:
            return bool(self._client.exists(self._key(jti)))
        except RedisError as e:
            REVOCATION_BACKEND_FAILURE_TOTAL.labels(op="is_revoked").inc()
            logger.error(
                "RedisTokenRevocationStore.is_revoked failed jti=%s err=%s",
                jti,
                e.__class__.__name__,
            )
            raise RevocationBackendUnavailable("is_revoked failed") from e


# ── 팩토리 ──
_BACKEND_ENV = "AQTS_REVOCATION_BACKEND"
_VALID_BACKENDS = {"memory", "redis"}

_singleton: Optional[TokenRevocationStore] = None
_singleton_lock = threading.Lock()


def _build_store() -> TokenRevocationStore:
    backend_raw = os.environ.get(_BACKEND_ENV)
    if backend_raw is None:
        raise ValueError(
            f"{_BACKEND_ENV} 환경변수가 설정되지 않았습니다. "
            f"운영 환경에서는 반드시 'redis'로 설정하세요. "
            f"테스트 환경에서는 'memory'를 명시적으로 설정할 수 있습니다. "
            f"유효한 값: {_VALID_BACKENDS}"
        )
    backend = backend_raw.strip().lower()
    if backend not in _VALID_BACKENDS:
        raise ValueError(f"Invalid {_BACKEND_ENV}={backend!r}; must be one of {_VALID_BACKENDS}")
    if backend == "memory":
        return InMemoryTokenRevocationStore()
    settings = get_settings()
    return RedisTokenRevocationStore(redis_url=settings.redis.url)


def get_revocation_store() -> TokenRevocationStore:
    """싱글톤 revocation store 반환."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = _build_store()
    return _singleton


def reset_revocation_store_for_tests() -> None:
    """테스트 격리용. 운영 코드에서는 호출 금지."""
    global _singleton
    with _singleton_lock:
        _singleton = None
