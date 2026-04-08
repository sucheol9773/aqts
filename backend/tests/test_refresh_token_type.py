"""
P0-1: Refresh 엔드포인트 토큰 type 강제 검증

근거: docs/security/security-integrity-roadmap.md §3.1 (P0-1)

검증 항목:
  1. 정상 refresh token 으로 호출 → 200 + 새 토큰 발급
  2. access token 으로 호출 → 401 + counter 증가
  3. type claim 누락 토큰 → 401 + counter 증가
  4. 임의의 비-refresh type 문자열 → 401 + counter 증가
  5. 카운터 라벨 분류 (missing_type vs non_refresh:*) 정확성

주의:
  - 통합 테스트 (FastAPI TestClient + 실제 라우트)
  - dependency_overrides 우회 없이 실제 verify_token 경로 통과
  - 카운터는 prometheus_client 의 글로벌 레지스트리에서 직접 읽는다
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from jose import jwt


def _make_settings():
    mock = MagicMock()
    mock.dashboard.secret_key = "test_secret_key_for_p0_1_refresh_type_check"
    mock.dashboard.previous_secret_key = None
    mock.dashboard.access_token_expire_hours = 24
    mock.dashboard.refresh_token_expire_days = 7
    mock.dashboard.password = "$2b$12$dummy_hash"
    return mock


def _counter_value(reason: str) -> float:
    """현재 누적된 TOKEN_REFRESH_FROM_ACCESS_TOTAL{reason=...} 값."""
    from core.monitoring.metrics import TOKEN_REFRESH_FROM_ACCESS_TOTAL

    metric = TOKEN_REFRESH_FROM_ACCESS_TOTAL.labels(reason=reason)
    return metric._value.get()


def _build_test_client():
    from main import app

    return TestClient(app)


def _override_db_with_user(rv: int = 0, uid: str = "1", username: str = "tester", role_name: str = "viewer"):
    """refresh 라우트가 기대하는 DB 재조회 결과를 주입한다.

    P2-역할 변경 즉시 세션 무효화 이후 refresh 는 DB 에서 role_version 을
    재조회하므로, 정상 refresh 시나리오 테스트는 get_db_session 을 오버라이드해
    일치하는 role_version 을 가진 사용자를 반환해야 한다.
    """
    from db.database import get_db_session
    from db.models.user import Role, User
    from main import app

    role = Role(id={"admin": 1, "operator": 2, "viewer": 3}[role_name], name=role_name)
    now = datetime.now(timezone.utc)
    user = User(
        id=uid,
        username=username,
        password_hash="dummy",
        email=f"{username}@test.local",
        role_id=role.id,
        is_active=True,
        is_locked=False,
        totp_enabled=False,
        failed_login_attempts=0,
        role_version=rv,
        created_at=now,
        updated_at=now,
    )
    user.role = role

    session = AsyncMock()
    result = MagicMock()
    scalars_obj = MagicMock()
    scalars_obj.first = MagicMock(return_value=user)
    result.scalars = MagicMock(return_value=scalars_obj)
    session.execute = AsyncMock(return_value=result)

    async def _fake_get_db():
        yield session

    app.dependency_overrides[get_db_session] = _fake_get_db
    return user


def _clear_db_override():
    from db.database import get_db_session
    from main import app

    app.dependency_overrides.pop(get_db_session, None)


class TestRefreshTokenTypeEnforcement:
    """P0-1 — refresh 엔드포인트는 type=refresh 토큰만 받아들여야 한다."""

    def test_valid_refresh_token_returns_200(self):
        """정상 refresh token 으로 호출 시 200 + 새 access/refresh 발급."""
        _override_db_with_user(rv=0, uid="1", username="tester", role_name="viewer")
        try:
            with patch("api.middleware.auth.get_settings", return_value=_make_settings()):
                from api.middleware.auth import AuthService

                refresh = AuthService.create_refresh_token({"sub": "tester", "uid": "1", "role": "viewer", "rv": 0})

                with patch("api.routes.auth.get_settings", return_value=_make_settings()):
                    client = _build_test_client()
                    resp = client.post("/api/auth/refresh", json={"refresh_token": refresh})

                assert resp.status_code == 200, resp.text
                body = resp.json()
                assert body["success"] is True
                assert "access_token" in body["data"]
                assert "refresh_token" in body["data"]
        finally:
            _clear_db_override()

    def test_access_token_is_rejected_with_401(self):
        """access token 으로 refresh 시도 시 401 + counter 증가."""
        before = _counter_value("non_refresh:access")

        with patch("api.middleware.auth.get_settings", return_value=_make_settings()):
            from api.middleware.auth import AuthService

            access = AuthService.create_access_token({"sub": "tester", "uid": 1, "role": "viewer"})

            with patch("api.routes.auth.get_settings", return_value=_make_settings()):
                client = _build_test_client()
                resp = client.post("/api/auth/refresh", json={"refresh_token": access})

            assert resp.status_code == 401, resp.text
            body = resp.json()
            assert body["success"] is False
            assert body["error"]["code"] == "INVALID_TOKEN_TYPE"
            assert "Invalid token type" in body["error"]["message"]
            assert "WWW-Authenticate" in resp.headers
            assert "invalid_token" in resp.headers["WWW-Authenticate"]

        after = _counter_value("non_refresh:access")
        assert after == before + 1, f"counter should increment by 1: before={before} after={after}"

    def test_token_without_type_claim_is_rejected(self):
        """type claim 이 아예 없는 토큰 → 401 + missing_type counter 증가."""
        before = _counter_value("missing_type")

        # type claim 을 일부러 빼고 직접 jwt 를 조립 (서명은 동일 키)
        settings = _make_settings()
        secret = settings.dashboard.secret_key
        payload = {
            "sub": "tester",
            "uid": 1,
            "role": "viewer",
            "exp": datetime.now(timezone.utc) + timedelta(days=1),
            "iat": datetime.now(timezone.utc),
            "jti": "no-type-jti-1",
            # NOTE: "type" 의도적 누락
        }
        token = jwt.encode(payload, secret, algorithm="HS256")

        with (
            patch("api.middleware.auth.get_settings", return_value=settings),
            patch("api.routes.auth.get_settings", return_value=settings),
        ):
            client = _build_test_client()
            resp = client.post("/api/auth/refresh", json={"refresh_token": token})

        assert resp.status_code == 401, resp.text
        after = _counter_value("missing_type")
        assert after == before + 1

    def test_arbitrary_non_refresh_type_is_rejected(self):
        """type 이 'service' 같은 임의값일 때도 차단되어야 한다."""
        before = _counter_value("non_refresh:service")

        settings = _make_settings()
        secret = settings.dashboard.secret_key
        payload = {
            "sub": "tester",
            "uid": 1,
            "role": "viewer",
            "exp": datetime.now(timezone.utc) + timedelta(days=1),
            "iat": datetime.now(timezone.utc),
            "jti": "service-jti-1",
            "type": "service",
        }
        token = jwt.encode(payload, secret, algorithm="HS256")

        with (
            patch("api.middleware.auth.get_settings", return_value=settings),
            patch("api.routes.auth.get_settings", return_value=settings),
        ):
            client = _build_test_client()
            resp = client.post("/api/auth/refresh", json={"refresh_token": token})

        assert resp.status_code == 401, resp.text
        after = _counter_value("non_refresh:service")
        assert after == before + 1

    def test_counter_does_not_increment_on_valid_refresh(self):
        """정상 refresh 호출은 어떤 reason 라벨도 증가시키지 않아야 한다."""
        before_access = _counter_value("non_refresh:access")
        before_missing = _counter_value("missing_type")

        _override_db_with_user(rv=0, uid="1", username="tester", role_name="viewer")
        try:
            with patch("api.middleware.auth.get_settings", return_value=_make_settings()):
                from api.middleware.auth import AuthService

                refresh = AuthService.create_refresh_token({"sub": "tester", "uid": "1", "role": "viewer", "rv": 0})
                with patch("api.routes.auth.get_settings", return_value=_make_settings()):
                    client = _build_test_client()
                    resp = client.post("/api/auth/refresh", json={"refresh_token": refresh})
        finally:
            _clear_db_override()

        assert resp.status_code == 200
        assert _counter_value("non_refresh:access") == before_access
        assert _counter_value("missing_type") == before_missing
