"""
P0-2a 검증: TokenRevocationStore Redis 백엔드 + fail-closed.

문서: docs/security/security-integrity-roadmap.md §3.2, §3.6
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from api.middleware.token_revocation import (
    InMemoryTokenRevocationStore,
    RedisTokenRevocationStore,
    RevocationBackendUnavailable,
    _build_store,
    reset_revocation_store_for_tests,
)
from core.monitoring.metrics import REVOCATION_BACKEND_FAILURE_TOTAL


def _failure_value(op: str) -> float:
    return REVOCATION_BACKEND_FAILURE_TOTAL.labels(op=op)._value.get()


# ── In-Memory backend ──
class TestInMemoryStore:
    def test_revoke_then_is_revoked(self) -> None:
        store = InMemoryTokenRevocationStore()
        store.revoke("jti-A", ttl_seconds=10)
        assert store.is_revoked("jti-A") is True
        assert store.is_revoked("jti-B") is False

    def test_ttl_expiry(self) -> None:
        store = InMemoryTokenRevocationStore()
        store.revoke("jti-A", ttl_seconds=1)
        assert store.is_revoked("jti-A") is True
        # 만료 후 자동 정리
        store._expiry["jti-A"] = time.time() - 0.001
        assert store.is_revoked("jti-A") is False
        assert "jti-A" not in store._blacklist

    def test_zero_or_negative_ttl_is_noop(self) -> None:
        store = InMemoryTokenRevocationStore()
        store.revoke("jti-A", ttl_seconds=0)
        store.revoke("jti-B", ttl_seconds=-5)
        assert store.is_revoked("jti-A") is False
        assert store.is_revoked("jti-B") is False


# ── Redis backend (mocked client) ──
class TestRedisStoreSuccess:
    def _make_store(self) -> tuple[RedisTokenRevocationStore, MagicMock]:
        fake_client = MagicMock()
        with patch(
            "api.middleware.token_revocation.redis.Redis.from_url",
            return_value=fake_client,
        ):
            store = RedisTokenRevocationStore(redis_url="redis://localhost:6379/0")
        return store, fake_client

    def test_revoke_calls_setex_with_prefix(self) -> None:
        store, client = self._make_store()
        store.revoke("jti-XYZ", ttl_seconds=120)
        client.setex.assert_called_once_with("aqts:revoked:jti-XYZ", 120, "1")

    def test_is_revoked_true_when_exists(self) -> None:
        store, client = self._make_store()
        client.exists.return_value = 1
        assert store.is_revoked("jti-XYZ") is True
        client.exists.assert_called_once_with("aqts:revoked:jti-XYZ")

    def test_is_revoked_false_when_missing(self) -> None:
        store, client = self._make_store()
        client.exists.return_value = 0
        assert store.is_revoked("jti-XYZ") is False


class TestRedisStoreFailClosed:
    """Redis 장애 시 RevocationBackendUnavailable 전파 + 카운터 증가."""

    def _make_store_with_error(self) -> RedisTokenRevocationStore:
        fake_client = MagicMock()
        fake_client.setex.side_effect = RedisConnectionError("redis down")
        fake_client.exists.side_effect = RedisConnectionError("redis down")
        with patch(
            "api.middleware.token_revocation.redis.Redis.from_url",
            return_value=fake_client,
        ):
            return RedisTokenRevocationStore(redis_url="redis://localhost:6379/0")

    def test_revoke_failure_raises_and_increments_counter(self) -> None:
        store = self._make_store_with_error()
        before = _failure_value("revoke")
        with pytest.raises(RevocationBackendUnavailable):
            store.revoke("jti-FAIL", ttl_seconds=60)
        after = _failure_value("revoke")
        assert after == before + 1

    def test_is_revoked_failure_raises_and_increments_counter(self) -> None:
        store = self._make_store_with_error()
        before = _failure_value("is_revoked")
        with pytest.raises(RevocationBackendUnavailable):
            store.is_revoked("jti-FAIL")
        after = _failure_value("is_revoked")
        assert after == before + 1

    def test_failure_does_not_swallow_to_false(self) -> None:
        """fail-closed 검증: 절대로 False (= 통과) 를 반환해서는 안 된다."""
        store = self._make_store_with_error()
        with pytest.raises(RevocationBackendUnavailable):
            # 만약 except 에서 False 를 반환하면 fail-open 회귀.
            _ = store.is_revoked("jti-FAIL")


# ── 팩토리 ──
class TestFactory:
    def teardown_method(self, method) -> None:
        reset_revocation_store_for_tests()

    def test_missing_env_raises_valueerror(self, monkeypatch) -> None:
        """AQTS_REVOCATION_BACKEND 미설정 시 ValueError 발생."""
        monkeypatch.delenv("AQTS_REVOCATION_BACKEND", raising=False)
        with pytest.raises(ValueError, match="환경변수가 설정되지 않았습니다"):
            _build_store()

    def test_explicit_memory(self, monkeypatch) -> None:
        monkeypatch.setenv("AQTS_REVOCATION_BACKEND", "memory")
        store = _build_store()
        assert isinstance(store, InMemoryTokenRevocationStore)

    def test_explicit_redis(self, monkeypatch) -> None:
        monkeypatch.setenv("AQTS_REVOCATION_BACKEND", "redis")
        with patch(
            "api.middleware.token_revocation.redis.Redis.from_url",
            return_value=MagicMock(),
        ):
            store = _build_store()
        assert isinstance(store, RedisTokenRevocationStore)

    def test_invalid_backend_raises(self, monkeypatch) -> None:
        monkeypatch.setenv("AQTS_REVOCATION_BACKEND", "memcached")
        with pytest.raises(ValueError, match="Invalid AQTS_REVOCATION_BACKEND"):
            _build_store()


# ── 호출부 통합: verify_token 이 fail-closed 로 503 을 던지는지 ──
class TestVerifyTokenFailClosed:
    def test_verify_token_returns_503_when_backend_fails(self, monkeypatch) -> None:
        """verify_token 의 jti revocation 체크가 백엔드 장애 시 503 을 raise."""
        from fastapi import HTTPException

        from api.middleware import auth as auth_mod
        from api.middleware.auth import AuthService

        # 1) 정상 access token 발급
        settings = MagicMock()
        settings.dashboard.secret_key = "test_secret_key_p0_2a_xx"
        settings.dashboard.previous_secret_key = None
        settings.dashboard.access_token_expire_hours = 1
        settings.dashboard.refresh_token_expire_days = 7

        with patch("api.middleware.auth.get_settings", return_value=settings):
            token = AuthService.create_access_token({"sub": "u", "uid": "1"})

        # 2) revocation store 가 RevocationBackendUnavailable 을 던지도록 패치
        broken_store = MagicMock()
        broken_store.is_revoked.side_effect = RevocationBackendUnavailable("down")

        monkeypatch.setattr(
            auth_mod,
            "_factory_get_revocation_store",
            lambda: broken_store,
        )

        # 3) verify_token → 503
        with patch("api.middleware.auth.get_settings", return_value=settings):
            with pytest.raises(HTTPException) as exc:
                AuthService.verify_token(token)
        assert exc.value.status_code == 503
        assert "Session store unavailable" in exc.value.detail
