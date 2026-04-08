"""
P0-2b 검증: rate limiter Redis storage + 복합 키 + fail-closed.

문서: docs/security/security-integrity-roadmap.md §3.2, §3.6
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from jose import jwt
from limits.errors import StorageError
from starlette.requests import Request

from api.middleware.rate_limiter import (
    _extract_user_sub,
    composite_rate_key,
    rate_limit_storage_unavailable_handler,
)
from core.monitoring.metrics import RATE_LIMIT_STORAGE_FAILURE_TOTAL


def _make_request(*, headers: dict | None = None, client_host: str = "10.0.0.1") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/test",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": (client_host, 12345),
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
    }
    return Request(scope)


def _make_jwt(sub: str) -> str:
    return jwt.encode(
        {
            "sub": sub,
            "uid": "u1",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        "any-secret-not-validated-here",
        algorithm="HS256",
    )


# ── 복합 키 함수 ──
class TestCompositeRateKey:
    def test_unauthenticated_uses_ip(self) -> None:
        req = _make_request(client_host="203.0.113.7")
        assert composite_rate_key(req) == "ip:203.0.113.7"

    def test_bearer_token_uses_user_sub(self) -> None:
        token = _make_jwt("alice")
        req = _make_request(
            headers={"authorization": f"Bearer {token}"},
            client_host="203.0.113.7",
        )
        assert composite_rate_key(req) == "user:alice"

    def test_malformed_bearer_falls_back_to_ip(self) -> None:
        req = _make_request(
            headers={"authorization": "Bearer not.a.jwt"},
            client_host="203.0.113.7",
        )
        assert composite_rate_key(req) == "ip:203.0.113.7"

    def test_non_bearer_scheme_falls_back_to_ip(self) -> None:
        req = _make_request(
            headers={"authorization": "Basic abc123"},
            client_host="203.0.113.7",
        )
        assert composite_rate_key(req) == "ip:203.0.113.7"

    def test_extract_sub_returns_none_without_header(self) -> None:
        req = _make_request()
        assert _extract_user_sub(req) is None

    def test_extract_sub_returns_none_for_token_without_sub(self) -> None:
        token = jwt.encode({"uid": "x"}, "k", algorithm="HS256")
        req = _make_request(headers={"authorization": f"Bearer {token}"})
        assert _extract_user_sub(req) is None

    def test_two_users_get_distinct_keys(self) -> None:
        """동일 IP 뒤의 두 사용자가 서로의 throttle 을 침범하지 않아야 한다."""
        t1 = _make_jwt("alice")
        t2 = _make_jwt("bob")
        r1 = _make_request(headers={"authorization": f"Bearer {t1}"}, client_host="10.0.0.1")
        r2 = _make_request(headers={"authorization": f"Bearer {t2}"}, client_host="10.0.0.1")
        assert composite_rate_key(r1) != composite_rate_key(r2)


# ── storage URI 결정 ──
class TestStorageUriResolution:
    def test_testing_mode_uses_memory(self, monkeypatch) -> None:
        # Limiter 모듈 변수는 import 시 결정되므로 함수 직접 호출.
        # _resolve_storage_uri 는 모듈 전역 _is_testing 을 본다.
        import api.middleware.rate_limiter as rl

        monkeypatch.setattr(rl, "_is_testing", True)
        assert rl._resolve_storage_uri() == "memory://"

    def test_production_mode_uses_redis(self, monkeypatch) -> None:
        import api.middleware.rate_limiter as rl

        fake_settings = MagicMock()
        fake_settings.redis.url = "redis://:pwd@redis:6379/0"
        monkeypatch.setattr(rl, "_is_testing", False)
        with patch("api.middleware.rate_limiter.get_settings", return_value=fake_settings):
            assert rl._resolve_storage_uri() == "redis://:pwd@redis:6379/0"


# ── fail-closed: storage 장애 → 503 ──
class TestStorageFailureHandler:
    @pytest.mark.asyncio
    async def test_handler_returns_503_with_error_code(self) -> None:
        before = RATE_LIMIT_STORAGE_FAILURE_TOTAL._value.get()
        req = _make_request()
        exc = StorageError("redis down")
        resp = await rate_limit_storage_unavailable_handler(req, exc)
        after = RATE_LIMIT_STORAGE_FAILURE_TOTAL._value.get()

        assert resp.status_code == 503
        body = resp.body.decode()
        assert "RATE_LIMIT_STORE_UNAVAILABLE" in body
        assert after == before + 1

    @pytest.mark.asyncio
    async def test_handler_does_not_return_200_on_failure(self) -> None:
        """fail-closed 회귀 방지: storage 실패 시 절대 통과(2xx)되어서는 안 된다."""
        req = _make_request()
        resp = await rate_limit_storage_unavailable_handler(req, StorageError("x"))
        assert not (200 <= resp.status_code < 300)


# ── slowapi limiter 인스턴스가 fail-closed 옵션을 갖는지 확인 ──
class TestLimiterFailClosedConfiguration:
    def test_swallow_errors_disabled(self) -> None:
        from api.middleware.rate_limiter import limiter

        # slowapi Limiter 는 _swallow_errors 로 보관
        assert getattr(limiter, "_swallow_errors", True) is False

    def test_in_memory_fallback_disabled(self) -> None:
        from api.middleware.rate_limiter import limiter

        assert getattr(limiter, "_in_memory_fallback_enabled", True) is False


# ── 환경변수 격리 ──
@pytest.fixture(autouse=True)
def _restore_testing_env():
    prev = os.environ.get("TESTING")
    yield
    if prev is None:
        os.environ.pop("TESTING", None)
    else:
        os.environ["TESTING"] = prev
