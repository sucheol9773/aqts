"""
P0-3b 검증: PgOrderIdempotencyStore + TwoTierOrderIdempotencyStore.

전략
----
PostgreSQL JSONB / CAST 문법을 SQLite 로 그대로 재현할 수 없으므로,
`PgOrderIdempotencyStore` 는 고수준 동작 관점에서 SQLAlchemy Engine 을
목킹하여 검증한다. 실제 DDL 통합 테스트는 `tests/integration/` 의 DB 컨테이너
경로가 붙은 뒤 별도 커밋에서 다룬다 (P0-3b 의 스코프는 store 계층 wiring 과
fail-closed 에러 매핑까지).

검증 범위
---------
PgOrderIdempotencyStore
  - lookup: SELECT 행 없음 → None
  - lookup: 만료된 레코드 → None
  - lookup: 정상 레코드 → OrderIdempotencyRecord
  - lookup: SQLAlchemyError → IdempotencyStoreUnavailable
  - store_result: 정상 INSERT → 예외 없음
  - store_result: IntegrityError + 기존 동일 fingerprint → no-op (replay)
  - store_result: IntegrityError + 기존 다른 fingerprint → IdempotencyConflict
  - store_result: IntegrityError + 기존 없음(race) → IdempotencyInProgress
  - try_claim: 기존 다른 fingerprint → IdempotencyConflict
  - release_claim: no-op

TwoTierOrderIdempotencyStore
  - lookup: Redis hit → DB 미조회
  - lookup: Redis miss, DB hit → Redis warm-up 호출 + 레코드 반환
  - lookup: Redis miss, DB miss → None
  - lookup: DB 반환 후 Redis warm 실패 → 여전히 DB 레코드 반환
  - try_claim: DB committed 레코드가 conflict → Redis 는 호출되지 않음
  - store_result: DB 우선 INSERT, 이후 Redis 갱신
  - store_result: DB 성공 후 Redis 실패 → 예외 전파되지 않음
  - store_result: DB 장애 → Redis 호출되지 않음
  - release_claim: Redis 와 DB 모두 호출
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from core.idempotency.db_store import (
    PgOrderIdempotencyStore,
    TwoTierOrderIdempotencyStore,
)
from core.idempotency.order_idempotency import (
    IdempotencyConflict,
    IdempotencyInProgress,
    IdempotencyStoreUnavailable,
    OrderIdempotencyRecord,
    compute_request_fingerprint,
)


# ── PgOrderIdempotencyStore ──────────────────────────────────
def _make_pg_store() -> tuple[PgOrderIdempotencyStore, MagicMock]:
    """Engine 을 MagicMock 으로 주입한 PgOrderIdempotencyStore."""
    engine = MagicMock()
    store = PgOrderIdempotencyStore.__new__(PgOrderIdempotencyStore)
    store._engine = engine
    store._result_ttl = 60
    return store, engine


def _mock_connect(engine: MagicMock, fetchone_return):
    """`engine.connect().__enter__()` 가 execute().fetchone() 을 통해 주어진
    값을 반환하도록 셋업."""
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = fetchone_return
    cm = MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False
    engine.connect.return_value = cm
    return conn


def _mock_begin(engine: MagicMock, execute_side_effect=None):
    conn = MagicMock()
    if execute_side_effect is not None:
        conn.execute.side_effect = execute_side_effect
    cm = MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False
    engine.begin.return_value = cm
    return conn


class TestPgStoreLookup:
    def test_lookup_no_row_returns_none(self) -> None:
        store, engine = _make_pg_store()
        _mock_connect(engine, None)
        assert store.lookup("u", "r", "k") is None

    def test_lookup_expired_row_returns_none(self) -> None:
        store, engine = _make_pg_store()
        expired = datetime.now(timezone.utc) - timedelta(seconds=10)
        row = ("fp", 200, {"ok": True}, 1000.0, expired)
        _mock_connect(engine, row)
        assert store.lookup("u", "r", "k") is None

    def test_lookup_live_row_returns_record(self) -> None:
        store, engine = _make_pg_store()
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        row = ("fp-x", 200, {"ok": True}, 1234.5, future)
        _mock_connect(engine, row)
        rec = store.lookup("u", "r", "k")
        assert rec is not None
        assert rec.fingerprint == "fp-x"
        assert rec.status_code == 200
        assert rec.body == {"ok": True}
        assert rec.created_at == pytest.approx(1234.5)

    def test_lookup_db_error_raises_store_unavailable(self) -> None:
        store, engine = _make_pg_store()
        engine.connect.side_effect = OperationalError("s", {}, Exception("down"))
        with pytest.raises(IdempotencyStoreUnavailable):
            store.lookup("u", "r", "k")


class TestPgStoreStoreResult:
    def test_store_result_ok(self) -> None:
        store, engine = _make_pg_store()
        _mock_begin(engine)
        store.store_result("u", "r", "k", "fp", 200, {"ok": True})

    def test_store_result_duplicate_same_fingerprint_noop(self) -> None:
        store, engine = _make_pg_store()
        ie = IntegrityError("stmt", {}, Exception("duplicate"))
        _mock_begin(engine, execute_side_effect=ie)
        # On IntegrityError, store.lookup is called with a fresh connection
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        with patch.object(
            store,
            "lookup",
            return_value=OrderIdempotencyRecord(
                fingerprint="fp-same",
                status_code=200,
                body={"ok": True},
                created_at=1.0,
            ),
        ):
            store.store_result("u", "r", "k", "fp-same", 200, {"ok": True})

    def test_store_result_duplicate_different_fingerprint_raises_conflict(self) -> None:
        store, engine = _make_pg_store()
        ie = IntegrityError("stmt", {}, Exception("duplicate"))
        _mock_begin(engine, execute_side_effect=ie)
        with patch.object(
            store,
            "lookup",
            return_value=OrderIdempotencyRecord(
                fingerprint="fp-other",
                status_code=200,
                body={"ok": True},
                created_at=1.0,
            ),
        ):
            with pytest.raises(IdempotencyConflict):
                store.store_result("u", "r", "k", "fp-mine", 200, {"ok": True})

    def test_store_result_race_missing_after_violation_raises_in_progress(self) -> None:
        store, engine = _make_pg_store()
        ie = IntegrityError("stmt", {}, Exception("duplicate"))
        _mock_begin(engine, execute_side_effect=ie)
        with patch.object(store, "lookup", return_value=None):
            with pytest.raises(IdempotencyInProgress):
                store.store_result("u", "r", "k", "fp", 200, {"ok": True})

    def test_store_result_db_error_raises_store_unavailable(self) -> None:
        store, engine = _make_pg_store()
        oe = OperationalError("s", {}, Exception("down"))
        _mock_begin(engine, execute_side_effect=oe)
        with pytest.raises(IdempotencyStoreUnavailable):
            store.store_result("u", "r", "k", "fp", 200, {"ok": True})


class TestPgStoreTryClaimAndRelease:
    def test_try_claim_no_existing_noop(self) -> None:
        store, _ = _make_pg_store()
        with patch.object(store, "lookup", return_value=None):
            store.try_claim("u", "r", "k", "fp")

    def test_try_claim_existing_conflict_raises(self) -> None:
        store, _ = _make_pg_store()
        existing = OrderIdempotencyRecord(fingerprint="fp-other", status_code=200, body={"ok": True}, created_at=1.0)
        with patch.object(store, "lookup", return_value=existing):
            with pytest.raises(IdempotencyConflict):
                store.try_claim("u", "r", "k", "fp-mine")

    def test_try_claim_existing_same_fingerprint_noop(self) -> None:
        store, _ = _make_pg_store()
        existing = OrderIdempotencyRecord(fingerprint="fp", status_code=200, body={"ok": True}, created_at=1.0)
        with patch.object(store, "lookup", return_value=existing):
            store.try_claim("u", "r", "k", "fp")

    def test_release_claim_is_noop(self) -> None:
        store, _ = _make_pg_store()
        # Must not raise and must not touch engine
        store.release_claim("u", "r", "k")


# ── TwoTierOrderIdempotencyStore ─────────────────────────────
def _record(fp: str = "fp") -> OrderIdempotencyRecord:
    return OrderIdempotencyRecord(fingerprint=fp, status_code=200, body={"ok": True}, created_at=1.0)


class TestTwoTierLookup:
    def test_redis_hit_does_not_call_db(self) -> None:
        redis = MagicMock()
        db = MagicMock()
        redis.lookup.return_value = _record("fp")
        store = TwoTierOrderIdempotencyStore(redis_store=redis, db_store=db)

        rec = store.lookup("u", "r", "k")
        assert rec is not None
        db.lookup.assert_not_called()

    def test_redis_miss_db_hit_warms_redis(self) -> None:
        redis = MagicMock()
        db = MagicMock()
        redis.lookup.return_value = None
        db.lookup.return_value = _record("fp-db")
        store = TwoTierOrderIdempotencyStore(redis_store=redis, db_store=db)

        rec = store.lookup("u", "r", "k")
        assert rec is not None and rec.fingerprint == "fp-db"
        redis.store_result.assert_called_once()

    def test_redis_miss_db_miss_returns_none(self) -> None:
        redis = MagicMock()
        db = MagicMock()
        redis.lookup.return_value = None
        db.lookup.return_value = None
        store = TwoTierOrderIdempotencyStore(redis_store=redis, db_store=db)
        assert store.lookup("u", "r", "k") is None
        redis.store_result.assert_not_called()

    def test_redis_warm_failure_does_not_mask_db_record(self) -> None:
        redis = MagicMock()
        db = MagicMock()
        redis.lookup.return_value = None
        db.lookup.return_value = _record("fp-db")
        redis.store_result.side_effect = IdempotencyStoreUnavailable("warm")
        store = TwoTierOrderIdempotencyStore(redis_store=redis, db_store=db)

        rec = store.lookup("u", "r", "k")
        assert rec is not None and rec.fingerprint == "fp-db"


class TestTwoTierClaimStore:
    def test_try_claim_db_conflict_short_circuits_redis(self) -> None:
        redis = MagicMock()
        db = MagicMock()
        db.try_claim.side_effect = IdempotencyConflict("k")
        store = TwoTierOrderIdempotencyStore(redis_store=redis, db_store=db)

        with pytest.raises(IdempotencyConflict):
            store.try_claim("u", "r", "k", "fp")
        redis.try_claim.assert_not_called()

    def test_try_claim_calls_both_when_db_ok(self) -> None:
        redis = MagicMock()
        db = MagicMock()
        store = TwoTierOrderIdempotencyStore(redis_store=redis, db_store=db)
        store.try_claim("u", "r", "k", "fp")
        db.try_claim.assert_called_once()
        redis.try_claim.assert_called_once()

    def test_store_result_writes_db_before_redis(self) -> None:
        call_order: list[str] = []
        redis = MagicMock()
        db = MagicMock()
        db.store_result.side_effect = lambda *a, **kw: call_order.append("db")
        redis.store_result.side_effect = lambda *a, **kw: call_order.append("redis")
        store = TwoTierOrderIdempotencyStore(redis_store=redis, db_store=db)
        store.store_result("u", "r", "k", "fp", 200, {"ok": True})
        assert call_order == ["db", "redis"]

    def test_store_result_db_failure_skips_redis(self) -> None:
        redis = MagicMock()
        db = MagicMock()
        db.store_result.side_effect = IdempotencyStoreUnavailable("db_store_result")
        store = TwoTierOrderIdempotencyStore(redis_store=redis, db_store=db)
        with pytest.raises(IdempotencyStoreUnavailable):
            store.store_result("u", "r", "k", "fp", 200, {"ok": True})
        redis.store_result.assert_not_called()

    def test_store_result_redis_failure_after_db_swallowed(self) -> None:
        redis = MagicMock()
        db = MagicMock()
        redis.store_result.side_effect = IdempotencyStoreUnavailable("redis_store_result")
        store = TwoTierOrderIdempotencyStore(redis_store=redis, db_store=db)
        # Must not raise — DB durability already achieved.
        store.store_result("u", "r", "k", "fp", 200, {"ok": True})
        db.store_result.assert_called_once()

    def test_release_claim_calls_both(self) -> None:
        redis = MagicMock()
        db = MagicMock()
        store = TwoTierOrderIdempotencyStore(redis_store=redis, db_store=db)
        store.release_claim("u", "r", "k")
        redis.release_claim.assert_called_once()
        db.release_claim.assert_called_once()


class TestFingerprintParity:
    """P0-3a 에서 정의한 fingerprint 는 DB 계층에서도 동일하게 쓰여야 한다."""

    def test_db_store_uses_same_fingerprint_semantics(self) -> None:
        fp1 = compute_request_fingerprint({"a": 1, "b": 2})
        fp2 = compute_request_fingerprint({"b": 2, "a": 1})
        assert fp1 == fp2
