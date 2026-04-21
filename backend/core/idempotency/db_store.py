"""
Order Idempotency — DB durability tier (P0-3b, §3.3, §3.6.3).

P0-3a 는 Redis 단일 계층이었다. Redis 는 빠르지만 휘발성이라 evict / FLUSHDB
/ 재부팅 시 24h TTL 이 조기 소멸될 수 있다. 동일 Idempotency-Key 로
재시도가 들어오면 Redis 는 "본 적 없음" 이라고 답하고 결국 이중 주문이
발생한다.

이 모듈은 PostgreSQL 에 영속 레코드를 저장하는 2차(콜드) 계층이다.

- `order_idempotency_keys` 테이블 (alembic 003)
- `(user_id, route, idempotency_key)` UNIQUE 제약 → DB 자체가 중복 원자적
  차단. 동시 INSERT 경합은 IntegrityError 로 실패 → 상위 계층이
  `IdempotencyInProgress` 로 매핑.
- fingerprint 불일치 → `IdempotencyConflict`.
- DB 연결 장애 → `IdempotencyStoreUnavailable` (fail-closed).

두 계층의 관계
--------------
`TwoTierOrderIdempotencyStore` 가 Redis(핫) + DB(콜드) 를 감싼다.

- `lookup`: Redis 먼저 → hit 이면 즉시 반환. miss 면 DB 조회, 발견 시
  Redis 를 warm 하고 반환.
- `try_claim`: Redis SET NX (짧은 직렬화 창). Redis 장애 시 fail-closed.
  DB 충돌(이미 committed 레코드 존재)은 `store_result` 단계에서 잡힌다.
- `store_result`: **DB 먼저 INSERT** (durable) → 성공하면 Redis 갱신.
  DB IntegrityError (동일 user/route/key 이미 존재) 시 fingerprint 비교하여
  `IdempotencyConflict` 또는 `IdempotencyInProgress` 로 분기.
- `release_claim`: Redis 클레임 마커만 제거 (DB 에는 최종 결과만 들어감).

DB 세션 전략
-------------
이 모듈은 sync SQLAlchemy 엔진을 자체 소유한다. orders 라우터는 async 지만
store 호출은 ~ms 단위 짧은 쿼리(인덱스 UNIQUE 룩업) 이므로 blocking 허용.
비동기 전환은 `verify_token` 등 상위 체인 전체의 async 화가 필요해서
정책적으로 제외 (P0-2a 와 동일 결정).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from core.idempotency.order_idempotency import (
    _CLAIM_MARKER,
    DEFAULT_CLAIM_TTL_SECONDS,
    DEFAULT_RESULT_TTL_SECONDS,
    IdempotencyConflict,
    IdempotencyInProgress,
    IdempotencyStoreUnavailable,
    OrderIdempotencyRecord,
    OrderIdempotencyStore,
)
from core.monitoring.metrics import (
    ORDER_IDEMPOTENCY_CONFLICT_TOTAL,
    ORDER_IDEMPOTENCY_IN_PROGRESS_TOTAL,
    ORDER_IDEMPOTENCY_STORE_FAILURE_TOTAL,
)

logger = logging.getLogger(__name__)


TABLE_NAME = "order_idempotency_keys"


class PgOrderIdempotencyStore:
    """PostgreSQL 영속 계층.

    UNIQUE(user_id, route, idempotency_key) 제약으로 중복 INSERT 를 DB 가
    원자적으로 차단한다. 동시 요청 경합은 IntegrityError 로 잡힌다.
    """

    def __init__(
        self,
        sync_url: str,
        *,
        result_ttl_seconds: int = DEFAULT_RESULT_TTL_SECONDS,
        engine: Optional[Engine] = None,
    ) -> None:
        self._engine: Engine = (
            engine
            if engine is not None
            else create_engine(
                sync_url,
                pool_size=5,
                max_overflow=5,
                pool_pre_ping=True,
                pool_recycle=1800,
                future=True,
            )
        )
        self._result_ttl = result_ttl_seconds

    # ── 내부 헬퍼 ──
    def _fail(self, op: str, exc: Exception) -> IdempotencyStoreUnavailable:
        ORDER_IDEMPOTENCY_STORE_FAILURE_TOTAL.labels(op=f"db_{op}").inc()
        logger.error(
            "PgOrderIdempotencyStore.%s failed err=%s",
            op,
            exc.__class__.__name__,
        )
        return IdempotencyStoreUnavailable(f"db_{op}")

    def lookup(self, user_id: str, route: str, key: str) -> Optional[OrderIdempotencyRecord]:
        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    text(f"""
                        SELECT fingerprint, status_code, response_body,
                               EXTRACT(EPOCH FROM created_at) AS created_at_epoch,
                               expires_at
                        FROM {TABLE_NAME}
                        WHERE user_id = :user_id
                          AND route = :route
                          AND idempotency_key = :key
                        """),
                    {"user_id": user_id, "route": route, "key": key},
                ).fetchone()
        except SQLAlchemyError as e:
            raise self._fail("lookup", e) from e

        if row is None:
            return None

        # 만료 레코드는 없는 것으로 취급 (janitor 가 청소할 때까지 무시).
        expires_at = row[4]
        if expires_at is not None:
            now = datetime.now(timezone.utc)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < now:
                return None

        return OrderIdempotencyRecord(
            fingerprint=row[0],
            status_code=int(row[1]),
            body=row[2],
            created_at=float(row[3]),
        )

    def try_claim(self, user_id: str, route: str, key: str, fingerprint: str) -> None:
        """DB 계층의 try_claim 은 조회만 수행한다.

        실질적인 직렬화는 store_result 의 INSERT 원자성으로 보장된다.
        여기서는 이미 committed 된 결과가 있는지 미리 확인하여 불필요한
        OrderExecutor 실행을 피한다.
        """
        existing = self.lookup(user_id, route, key)
        if existing is None:
            return
        if existing.fingerprint != fingerprint:
            ORDER_IDEMPOTENCY_CONFLICT_TOTAL.inc()
            raise IdempotencyConflict(key)
        # 동일 fingerprint → lookup 경로로 처리되어야 하지만 no-op 허용.

    def store_result(
        self,
        user_id: str,
        route: str,
        key: str,
        fingerprint: str,
        status_code: int,
        body: dict[str, Any],
    ) -> None:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=self._result_ttl)
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    text(f"""
                        INSERT INTO {TABLE_NAME}
                            (user_id, route, idempotency_key, fingerprint,
                             status_code, response_body, created_at, expires_at)
                        VALUES
                            (:user_id, :route, :key, :fingerprint,
                             :status_code, CAST(:body AS JSONB), :created_at, :expires_at)
                        """),
                    {
                        "user_id": user_id,
                        "route": route,
                        "key": key,
                        "fingerprint": fingerprint,
                        "status_code": status_code,
                        "body": _json_dumps(body),
                        "created_at": now,
                        "expires_at": expires_at,
                    },
                )
        except IntegrityError as e:
            # UNIQUE 위반 → 동일 키 이미 committed. fingerprint 비교.
            existing = self.lookup(user_id, route, key)
            if existing is None:
                # race: 방금 만료 삭제됨. 드물지만 in_progress 로 매핑.
                ORDER_IDEMPOTENCY_IN_PROGRESS_TOTAL.inc()
                raise IdempotencyInProgress(key) from e
            if existing.fingerprint != fingerprint:
                ORDER_IDEMPOTENCY_CONFLICT_TOTAL.inc()
                raise IdempotencyConflict(key) from e
            # 동일 fingerprint → 중복 호출(이미 저장됨). 성공 처리.
            return
        except SQLAlchemyError as e:
            raise self._fail("store_result", e) from e

    def release_claim(self, user_id: str, route: str, key: str) -> None:
        # DB 계층은 claim 을 갖지 않음 (Redis 가 담당). no-op.
        return


def _json_dumps(body: dict[str, Any]) -> str:
    import json

    return json.dumps(body, separators=(",", ":"), ensure_ascii=False)


# ══════════════════════════════════════
# TwoTierOrderIdempotencyStore
# ══════════════════════════════════════
class TwoTierOrderIdempotencyStore:
    """Redis(핫) + PostgreSQL(콜드) 복합 저장소.

    - lookup: Redis → DB (warm on miss)
    - try_claim: Redis SET NX 로 동시성 직렬화 + DB 사전 조회로 replay 감지
    - store_result: **DB 우선 INSERT** → 성공 시 Redis 갱신 (durability first)
    - release_claim: Redis 클레임만 해제

    DB 와 Redis 의 failure 정책은 모두 fail-closed. DB 장애는 즉시 503,
    Redis 장애도 즉시 503. 두 계층 중 하나라도 죽으면 통과시키지 않는다.
    """

    def __init__(
        self,
        redis_store: OrderIdempotencyStore,
        db_store: PgOrderIdempotencyStore,
    ) -> None:
        self._redis = redis_store
        self._db = db_store
        self._warm_lock = threading.Lock()

    def lookup(self, user_id: str, route: str, key: str) -> Optional[OrderIdempotencyRecord]:
        # 1) Redis hot path
        record = self._redis.lookup(user_id, route, key)
        if record is not None:
            return record

        # 2) DB cold path
        record = self._db.lookup(user_id, route, key)
        if record is None:
            return None

        # 3) Redis warm-up (best-effort — 실패해도 DB 결과는 반환)
        try:
            self._redis.store_result(
                user_id,
                route,
                key,
                record.fingerprint,
                record.status_code,
                record.body,
            )
        except IdempotencyStoreUnavailable:
            logger.warning(
                "Redis warm-up failed after DB lookup; returning DB record key=%s",
                key,
            )
        return record

    def try_claim(self, user_id: str, route: str, key: str, fingerprint: str) -> None:
        # DB 의 committed 결과가 이미 있으면 여기서 잡힌다 (conflict / replay).
        self._db.try_claim(user_id, route, key, fingerprint)
        # Redis 는 30s 직렬화 창만 담당.
        self._redis.try_claim(user_id, route, key, fingerprint)

    def store_result(
        self,
        user_id: str,
        route: str,
        key: str,
        fingerprint: str,
        status_code: int,
        body: dict[str, Any],
    ) -> None:
        # DB 우선 — durability 를 Redis write 보다 앞에 둔다.
        self._db.store_result(user_id, route, key, fingerprint, status_code, body)
        try:
            self._redis.store_result(user_id, route, key, fingerprint, status_code, body)
        except IdempotencyStoreUnavailable:
            # DB 에 이미 영속화됐으므로 Redis 실패는 다음 요청 시 자동 warm-up.
            logger.warning(
                "Redis store_result failed after DB commit (will warm on next hit) key=%s",
                key,
            )

    def release_claim(self, user_id: str, route: str, key: str) -> None:
        self._redis.release_claim(user_id, route, key)
        self._db.release_claim(user_id, route, key)


__all__ = [
    "PgOrderIdempotencyStore",
    "TwoTierOrderIdempotencyStore",
    "TABLE_NAME",
    "DEFAULT_CLAIM_TTL_SECONDS",
]


# CLAIM_MARKER re-export for tests needing to peek at Redis state alongside DB.
_ = _CLAIM_MARKER  # keep import alive for consumers of this module
_ = time  # reserved for future monotonic-clock comparisons
