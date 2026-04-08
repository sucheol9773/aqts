"""
Order Idempotency Store (P0-3a, security-integrity-roadmap §3.3, §3.6).

목적
----
POST /api/orders 계열 경로에서 클라이언트의 재시도(네트워크 타임아웃, 프록시
재전송, 유저 더블클릭 등) 가 이중 주문으로 이어지는 것을 차단한다.

프로토콜
--------
1. 클라이언트는 모든 주문 요청에 `Idempotency-Key: <uuid>` 헤더를 첨부한다.
2. 서버는 `(user_id, route, idempotency_key)` 조합으로 저장소를 조회한다.
3. 동일 키 + 동일 body fingerprint 로 재시도 → **기존 응답을 그대로 replay**.
4. 동일 키 + 다른 body → `IdempotencyConflict` (422).
5. 동시 진행중 (claim 중) → `IdempotencyInProgress` (409).
6. 저장소(Redis) 장애 → `IdempotencyStoreUnavailable` → 503 (fail-closed).
   "Redis 가 죽으면 전부 통과시킨다" 패턴(fail-open) 금지. 주문 경로는
   스케줄러(fail-open 허용)와 반대 방향으로 운영한다.

fingerprint
-----------
요청 body 의 canonical JSON (sorted keys) 의 sha256. 같은 논리 요청이면
같은 fingerprint. 클라이언트가 실수로 동일 키로 다른 body 를 보내면 서버가
422 로 거부한다.

저장 구조 (Redis)
-----------------
- 키: `aqts:order_idem:{user_id}:{route}:{idempotency_key}`
- 값: JSON { fingerprint, status_code, body, created_at }
- TTL: 24h (기본), `_claim_ttl_seconds` 짧은 claim TTL(30s) 을 거쳐
       최종 결과 저장 시 `_result_ttl_seconds` 로 갱신된다.

두 단계 저장
-------------
1. `try_claim`: `SET NX EX 30` — 동일 키 동시 요청을 직렬화.
2. 실행 완료 후 `store_result`: 동일 키를 최종 결과 JSON 으로 덮어쓰며
   24h TTL 재설정.
3. 실행 실패 시 `release_claim`: 클레임 키 삭제 → 클라이언트가 재시도 가능.

DB durability 계층 (P0-3b 에서 추가 예정)
----------------------------------------
현재 커밋(P0-3a)은 Redis 전용. 저장소 다운 시 fail-closed 로 503 반환하므로
보안상 안전하지만, Redis 데이터 손실(FLUSHDB, 메모리 제약에 의한 evict 등)
은 재전송 허용으로 이어질 수 있다. P0-3b 에서 `order_idempotency_keys`
테이블 + `UNIQUE (user_id, route, key)` 제약으로 영속 계층을 추가한다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional, Protocol

import redis
from redis.exceptions import RedisError

from config.settings import get_settings
from core.monitoring.metrics import (
    ORDER_IDEMPOTENCY_CONFLICT_TOTAL,
    ORDER_IDEMPOTENCY_HIT_TOTAL,
    ORDER_IDEMPOTENCY_IN_PROGRESS_TOTAL,
    ORDER_IDEMPOTENCY_STORE_FAILURE_TOTAL,
)

logger = logging.getLogger(__name__)

# ── 상수 ──
KEY_PREFIX = "aqts:order_idem"
DEFAULT_CLAIM_TTL_SECONDS = 30
DEFAULT_RESULT_TTL_SECONDS = 24 * 3600


# ── 예외 ──
class IdempotencyStoreUnavailable(RuntimeError):
    """저장소 백엔드 장애 (fail-closed → 503)."""


class IdempotencyConflict(RuntimeError):
    """동일 키에 서로 다른 fingerprint 요청 (422)."""


class IdempotencyInProgress(RuntimeError):
    """동일 키의 다른 요청이 아직 실행 중 (409)."""


# ── 레코드 ──
@dataclass(frozen=True)
class OrderIdempotencyRecord:
    fingerprint: str
    status_code: int
    body: dict[str, Any]
    created_at: float  # epoch seconds


def compute_request_fingerprint(payload: dict[str, Any]) -> str:
    """canonical JSON 의 sha256 (64 hex)."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_key(user_id: str, route: str, idempotency_key: str) -> str:
    return f"{KEY_PREFIX}:{user_id}:{route}:{idempotency_key}"


# ── Store Protocol ──
class OrderIdempotencyStore(Protocol):
    def lookup(self, user_id: str, route: str, key: str) -> Optional[OrderIdempotencyRecord]: ...

    def try_claim(self, user_id: str, route: str, key: str, fingerprint: str) -> None: ...

    def store_result(
        self,
        user_id: str,
        route: str,
        key: str,
        fingerprint: str,
        status_code: int,
        body: dict[str, Any],
    ) -> None: ...

    def release_claim(self, user_id: str, route: str, key: str) -> None: ...


# ── 공통 헬퍼 ──
_CLAIM_MARKER = "__CLAIM__"


def _serialize(record: OrderIdempotencyRecord) -> str:
    return json.dumps(
        {
            "fingerprint": record.fingerprint,
            "status_code": record.status_code,
            "body": record.body,
            "created_at": record.created_at,
        },
        separators=(",", ":"),
    )


def _deserialize(raw: str) -> OrderIdempotencyRecord:
    data = json.loads(raw)
    return OrderIdempotencyRecord(
        fingerprint=data["fingerprint"],
        status_code=int(data["status_code"]),
        body=data["body"],
        created_at=float(data["created_at"]),
    )


# ── In-Memory 백엔드 (테스트/개발 전용) ──
class InMemoryOrderIdempotencyStore:
    def __init__(
        self,
        *,
        claim_ttl_seconds: int = DEFAULT_CLAIM_TTL_SECONDS,
        result_ttl_seconds: int = DEFAULT_RESULT_TTL_SECONDS,
    ) -> None:
        self._data: dict[str, tuple[str, float]] = {}  # key -> (raw, expires_at)
        self._lock = threading.Lock()
        self._claim_ttl = claim_ttl_seconds
        self._result_ttl = result_ttl_seconds

    def _get_live(self, redis_key: str) -> Optional[str]:
        with self._lock:
            entry = self._data.get(redis_key)
            if not entry:
                return None
            raw, expires_at = entry
            if expires_at < time.time():
                self._data.pop(redis_key, None)
                return None
            return raw

    def lookup(self, user_id: str, route: str, key: str) -> Optional[OrderIdempotencyRecord]:
        raw = self._get_live(_build_key(user_id, route, key))
        if raw is None or raw == _CLAIM_MARKER:
            return None
        return _deserialize(raw)

    def try_claim(self, user_id: str, route: str, key: str, fingerprint: str) -> None:
        redis_key = _build_key(user_id, route, key)
        with self._lock:
            entry = self._data.get(redis_key)
            if entry is not None:
                raw, expires_at = entry
                if expires_at >= time.time():
                    if raw == _CLAIM_MARKER:
                        raise IdempotencyInProgress(key)
                    existing = _deserialize(raw)
                    if existing.fingerprint == fingerprint:
                        # 정상 replay 경로 — lookup 에서 처리되어야 하므로
                        # 여기 도달하면 caller 가 lookup 을 생략한 것. 그래도
                        # 허용 (no-op).
                        return
                    raise IdempotencyConflict(key)
            self._data[redis_key] = (
                _CLAIM_MARKER,
                time.time() + self._claim_ttl,
            )

    def store_result(
        self,
        user_id: str,
        route: str,
        key: str,
        fingerprint: str,
        status_code: int,
        body: dict[str, Any],
    ) -> None:
        record = OrderIdempotencyRecord(
            fingerprint=fingerprint,
            status_code=status_code,
            body=body,
            created_at=time.time(),
        )
        with self._lock:
            self._data[_build_key(user_id, route, key)] = (
                _serialize(record),
                time.time() + self._result_ttl,
            )

    def release_claim(self, user_id: str, route: str, key: str) -> None:
        redis_key = _build_key(user_id, route, key)
        with self._lock:
            entry = self._data.get(redis_key)
            if entry is not None and entry[0] == _CLAIM_MARKER:
                self._data.pop(redis_key, None)


# ── Redis 백엔드 (운영) ──
class RedisOrderIdempotencyStore:
    def __init__(
        self,
        redis_url: str,
        *,
        claim_ttl_seconds: int = DEFAULT_CLAIM_TTL_SECONDS,
        result_ttl_seconds: int = DEFAULT_RESULT_TTL_SECONDS,
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
        self._claim_ttl = claim_ttl_seconds
        self._result_ttl = result_ttl_seconds

    def _fail(self, op: str, exc: Exception) -> IdempotencyStoreUnavailable:
        ORDER_IDEMPOTENCY_STORE_FAILURE_TOTAL.labels(op=op).inc()
        logger.error(
            "OrderIdempotencyStore.%s failed err=%s",
            op,
            exc.__class__.__name__,
        )
        return IdempotencyStoreUnavailable(op)

    def lookup(self, user_id: str, route: str, key: str) -> Optional[OrderIdempotencyRecord]:
        redis_key = _build_key(user_id, route, key)
        try:
            raw = self._client.get(redis_key)
        except RedisError as e:
            raise self._fail("lookup", e) from e
        if raw is None or raw == _CLAIM_MARKER:
            return None
        return _deserialize(raw)

    def try_claim(self, user_id: str, route: str, key: str, fingerprint: str) -> None:
        redis_key = _build_key(user_id, route, key)
        try:
            # SET NX 로 claim 마커를 설치. 기존 값이 있으면 False 반환.
            ok = self._client.set(redis_key, _CLAIM_MARKER, nx=True, ex=self._claim_ttl)
        except RedisError as e:
            raise self._fail("try_claim", e) from e

        if ok:
            return

        # 이미 존재 → 기존 값 조회
        try:
            raw = self._client.get(redis_key)
        except RedisError as e:
            raise self._fail("try_claim_get", e) from e

        if raw is None:
            # race: 방금 만료된 경우. 다시 시도.
            try:
                ok2 = self._client.set(redis_key, _CLAIM_MARKER, nx=True, ex=self._claim_ttl)
            except RedisError as e:
                raise self._fail("try_claim_retry", e) from e
            if not ok2:
                raise IdempotencyInProgress(key)
            return

        if raw == _CLAIM_MARKER:
            ORDER_IDEMPOTENCY_IN_PROGRESS_TOTAL.inc()
            raise IdempotencyInProgress(key)

        existing = _deserialize(raw)
        if existing.fingerprint == fingerprint:
            # 같은 논리 요청 재시도 — lookup 에서 처리되었어야 함
            return
        ORDER_IDEMPOTENCY_CONFLICT_TOTAL.inc()
        raise IdempotencyConflict(key)

    def store_result(
        self,
        user_id: str,
        route: str,
        key: str,
        fingerprint: str,
        status_code: int,
        body: dict[str, Any],
    ) -> None:
        record = OrderIdempotencyRecord(
            fingerprint=fingerprint,
            status_code=status_code,
            body=body,
            created_at=time.time(),
        )
        redis_key = _build_key(user_id, route, key)
        try:
            self._client.setex(redis_key, self._result_ttl, _serialize(record))
        except RedisError as e:
            raise self._fail("store_result", e) from e

    def release_claim(self, user_id: str, route: str, key: str) -> None:
        redis_key = _build_key(user_id, route, key)
        try:
            raw = self._client.get(redis_key)
            if raw == _CLAIM_MARKER:
                self._client.delete(redis_key)
        except RedisError as e:
            raise self._fail("release_claim", e) from e


# ── 팩토리 ──
_BACKEND_ENV = "AQTS_ORDER_IDEMPOTENCY_BACKEND"
_VALID_BACKENDS = {"memory", "redis"}

_singleton: Optional[OrderIdempotencyStore] = None
_singleton_lock = threading.Lock()


def _build_store() -> OrderIdempotencyStore:
    backend = os.environ.get(_BACKEND_ENV, "memory").strip().lower()
    if backend not in _VALID_BACKENDS:
        raise ValueError(f"Invalid {_BACKEND_ENV}={backend!r}; must be one of {_VALID_BACKENDS}")
    if backend == "memory":
        return InMemoryOrderIdempotencyStore()
    return RedisOrderIdempotencyStore(redis_url=get_settings().redis.url)


def get_order_idempotency_store() -> OrderIdempotencyStore:
    """싱글톤 OrderIdempotencyStore."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = _build_store()
    return _singleton


def reset_order_idempotency_store_for_tests() -> None:
    global _singleton
    with _singleton_lock:
        _singleton = None


# ── lookup 카운터 헬퍼 (orders 라우터에서 hit 시 호출) ──
def record_hit() -> None:
    ORDER_IDEMPOTENCY_HIT_TOTAL.inc()
