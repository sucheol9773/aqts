"""
P0-3a 검증: Order Idempotency Store (Redis + In-Memory).

문서: docs/security/security-integrity-roadmap.md §3.3, §3.6

검증 범위
---------
1. fingerprint 계산의 determinism (키 순서 무관 / 동일 payload == 동일 hash)
2. 정상 흐름: claim → store_result → lookup replay
3. 동일 키 + 동일 body 재시도 → replay 허용
4. 동일 키 + 다른 body → IdempotencyConflict
5. 동시 claim (두 번째 호출) → IdempotencyInProgress
6. 실행 실패 후 release_claim → 동일 키 재시도 가능
7. Redis 백엔드 장애 → IdempotencyStoreUnavailable (fail-closed)
8. 사용자/route 분리 — 서로 다른 user_id/route 는 서로 영향 없음
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from redis.exceptions import RedisError

from core.idempotency.order_idempotency import (
    DEFAULT_CLAIM_TTL_SECONDS,
    IdempotencyConflict,
    IdempotencyInProgress,
    IdempotencyStoreUnavailable,
    InMemoryOrderIdempotencyStore,
    RedisOrderIdempotencyStore,
    compute_request_fingerprint,
)


# ── fingerprint ──
class TestFingerprint:
    def test_same_payload_same_fingerprint(self) -> None:
        p1 = {"ticker": "005930", "qty": 10, "side": "BUY"}
        p2 = {"ticker": "005930", "qty": 10, "side": "BUY"}
        assert compute_request_fingerprint(p1) == compute_request_fingerprint(p2)

    def test_key_order_does_not_matter(self) -> None:
        p1 = {"ticker": "005930", "qty": 10, "side": "BUY"}
        p2 = {"side": "BUY", "ticker": "005930", "qty": 10}
        assert compute_request_fingerprint(p1) == compute_request_fingerprint(p2)

    def test_different_payload_different_fingerprint(self) -> None:
        p1 = {"ticker": "005930", "qty": 10}
        p2 = {"ticker": "005930", "qty": 11}
        assert compute_request_fingerprint(p1) != compute_request_fingerprint(p2)

    def test_fingerprint_is_64_hex(self) -> None:
        fp = compute_request_fingerprint({"a": 1})
        assert len(fp) == 64
        int(fp, 16)  # parseable as hex


# ── InMemory backend ──
class TestInMemoryStore:
    def setup_method(self) -> None:
        self.store = InMemoryOrderIdempotencyStore()
        self.user = "u1"
        self.route = "POST /api/orders"
        self.key = "k1"

    def test_lookup_returns_none_for_unseen_key(self) -> None:
        assert self.store.lookup(self.user, self.route, self.key) is None

    def test_claim_then_store_then_lookup_replays(self) -> None:
        fp = compute_request_fingerprint({"ticker": "AAPL", "qty": 1})
        self.store.try_claim(self.user, self.route, self.key, fp)
        body = {"success": True, "data": {"order_id": "o1"}}
        self.store.store_result(self.user, self.route, self.key, fp, 200, body)

        rec = self.store.lookup(self.user, self.route, self.key)
        assert rec is not None
        assert rec.fingerprint == fp
        assert rec.status_code == 200
        assert rec.body == body

    def test_duplicate_claim_raises_in_progress(self) -> None:
        fp = compute_request_fingerprint({"x": 1})
        self.store.try_claim(self.user, self.route, self.key, fp)
        with pytest.raises(IdempotencyInProgress):
            self.store.try_claim(self.user, self.route, self.key, fp)

    def test_same_key_different_fingerprint_after_result_raises_conflict(self) -> None:
        fp_a = compute_request_fingerprint({"qty": 1})
        fp_b = compute_request_fingerprint({"qty": 2})
        self.store.try_claim(self.user, self.route, self.key, fp_a)
        self.store.store_result(self.user, self.route, self.key, fp_a, 200, {"ok": True})
        with pytest.raises(IdempotencyConflict):
            self.store.try_claim(self.user, self.route, self.key, fp_b)

    def test_release_claim_allows_retry(self) -> None:
        fp = compute_request_fingerprint({"q": 1})
        self.store.try_claim(self.user, self.route, self.key, fp)
        self.store.release_claim(self.user, self.route, self.key)
        # Must succeed (claim was released)
        self.store.try_claim(self.user, self.route, self.key, fp)

    def test_release_does_not_delete_committed_result(self) -> None:
        fp = compute_request_fingerprint({"q": 1})
        self.store.try_claim(self.user, self.route, self.key, fp)
        self.store.store_result(self.user, self.route, self.key, fp, 200, {"ok": True})
        self.store.release_claim(self.user, self.route, self.key)
        assert self.store.lookup(self.user, self.route, self.key) is not None

    def test_different_users_isolated(self) -> None:
        fp = compute_request_fingerprint({"a": 1})
        self.store.try_claim("alice", self.route, self.key, fp)
        # bob should not be blocked
        self.store.try_claim("bob", self.route, self.key, fp)

    def test_different_routes_isolated(self) -> None:
        fp = compute_request_fingerprint({"a": 1})
        self.store.try_claim(self.user, "POST /api/orders", self.key, fp)
        self.store.try_claim(self.user, "POST /api/orders/batch", self.key, fp)

    def test_claim_expires_after_ttl(self) -> None:
        store = InMemoryOrderIdempotencyStore(claim_ttl_seconds=1)
        fp = compute_request_fingerprint({"a": 1})
        store.try_claim(self.user, self.route, self.key, fp)
        time.sleep(1.05)
        # After TTL, a new claim should succeed
        store.try_claim(self.user, self.route, self.key, fp)

    def test_default_claim_ttl_constant_sane(self) -> None:
        assert DEFAULT_CLAIM_TTL_SECONDS >= 5


# ── Redis backend (mocked) ──
class TestRedisStoreFailClosed:
    def _make(self) -> tuple[RedisOrderIdempotencyStore, MagicMock]:
        store = RedisOrderIdempotencyStore.__new__(RedisOrderIdempotencyStore)
        client = MagicMock()
        store._client = client
        store._claim_ttl = DEFAULT_CLAIM_TTL_SECONDS
        store._result_ttl = 3600
        return store, client

    def test_lookup_redis_error_raises_unavailable(self) -> None:
        store, client = self._make()
        client.get.side_effect = RedisError("down")
        with pytest.raises(IdempotencyStoreUnavailable):
            store.lookup("u", "r", "k")

    def test_try_claim_redis_error_raises_unavailable(self) -> None:
        store, client = self._make()
        client.set.side_effect = RedisError("down")
        with pytest.raises(IdempotencyStoreUnavailable):
            store.try_claim("u", "r", "k", "fp")

    def test_store_result_redis_error_raises_unavailable(self) -> None:
        store, client = self._make()
        client.setex.side_effect = RedisError("down")
        with pytest.raises(IdempotencyStoreUnavailable):
            store.store_result("u", "r", "k", "fp", 200, {"ok": True})

    def test_try_claim_set_nx_ok_returns_normally(self) -> None:
        store, client = self._make()
        client.set.return_value = True  # NX succeeded
        store.try_claim("u", "r", "k", "fp")
        client.set.assert_called_once()

    def test_try_claim_existing_claim_marker_raises_in_progress(self) -> None:
        store, client = self._make()
        client.set.return_value = False  # NX failed
        client.get.return_value = "__CLAIM__"
        with pytest.raises(IdempotencyInProgress):
            store.try_claim("u", "r", "k", "fp")

    def test_try_claim_existing_same_fingerprint_noop(self) -> None:
        import json

        store, client = self._make()
        client.set.return_value = False
        client.get.return_value = json.dumps(
            {
                "fingerprint": "fp-match",
                "status_code": 200,
                "body": {"ok": True},
                "created_at": 1.0,
            }
        )
        # Must NOT raise — caller is replaying same logical request.
        store.try_claim("u", "r", "k", "fp-match")

    def test_try_claim_existing_different_fingerprint_raises_conflict(self) -> None:
        import json

        store, client = self._make()
        client.set.return_value = False
        client.get.return_value = json.dumps(
            {
                "fingerprint": "fp-other",
                "status_code": 200,
                "body": {"ok": True},
                "created_at": 1.0,
            }
        )
        with pytest.raises(IdempotencyConflict):
            store.try_claim("u", "r", "k", "fp-mine")
