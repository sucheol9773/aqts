"""
P1-보안: get_current_user DB 재확인 테스트

검증 목표:
  1. 토큰이 유효해도 DB 에서 사용자가 없으면 401
  2. is_active=False 면 401 ("inactive")
  3. is_locked=True 면 403 ("locked")
  4. DB role 이 토큰 role 과 다르면 401 ("role has changed")
  5. DB 장애 시 503 (fail-closed)
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from api.middleware.auth import AuthService, get_current_user
from db.models.user import Role, User


def _make_user(*, user_id: str, username: str, role_name: str, is_active=True, is_locked=False):
    role = Role(id={"admin": 1, "operator": 2, "viewer": 3}[role_name], name=role_name)
    now = datetime.now(timezone.utc)
    user = User(
        id=user_id,
        username=username,
        password_hash="$2b$12$dummy",
        email=f"{username}@test.local",
        role_id=role.id,
        is_active=is_active,
        is_locked=is_locked,
        totp_enabled=False,
        totp_secret=None,
        failed_login_attempts=0,
        created_at=now,
        updated_at=now,
    )
    user.role = role
    return user


def _mock_session(returned_user):
    session = AsyncMock()
    result = MagicMock()
    scalars_obj = MagicMock()
    scalars_obj.first = MagicMock(return_value=returned_user)
    result.scalars = MagicMock(return_value=scalars_obj)
    session.execute = AsyncMock(return_value=result)
    return session


def _mock_session_raises(exc: Exception):
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=exc)
    return session


def _token(role: str, uid: str = "u-1", sub: str = "u1"):
    return AuthService.create_access_token({"sub": sub, "uid": uid, "role": role})


def _creds(token: str):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


@pytest.mark.asyncio
async def test_user_not_found_returns_401():
    token = _token("operator")
    session = _mock_session(None)
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_creds(token), db_session=session)
    assert exc.value.status_code == 401
    assert "no longer exists" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_inactive_user_returns_401():
    token = _token("operator")
    user = _make_user(user_id="u-1", username="u1", role_name="operator", is_active=False)
    session = _mock_session(user)
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_creds(token), db_session=session)
    assert exc.value.status_code == 401
    assert "inactive" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_locked_user_returns_403():
    token = _token("operator")
    user = _make_user(user_id="u-1", username="u1", role_name="operator", is_locked=True)
    session = _mock_session(user)
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_creds(token), db_session=session)
    assert exc.value.status_code == 403
    assert "locked" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_role_mismatch_returns_401():
    """토큰 role=operator, DB role=viewer → 401."""
    token = _token("operator")
    user = _make_user(user_id="u-1", username="u1", role_name="viewer")
    session = _mock_session(user)
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_creds(token), db_session=session)
    assert exc.value.status_code == 401
    assert "role has changed" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_db_failure_returns_503_fail_closed():
    token = _token("operator")
    session = _mock_session_raises(RuntimeError("connection lost"))
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_creds(token), db_session=session)
    assert exc.value.status_code == 503
    assert "unavailable" in exc.value.detail.lower()
    assert exc.value.headers.get("Retry-After") == "5"


@pytest.mark.asyncio
async def test_valid_user_returns_current_db_role():
    """토큰/DB role 일치 → DB 의 현재 role/username 반환."""
    token = _token("operator", uid="u-42", sub="u42")
    user = _make_user(user_id="u-42", username="u42", role_name="operator")
    session = _mock_session(user)
    result = await get_current_user(_creds(token), db_session=session)
    assert result.id == "u-42"
    assert result.username == "u42"
    assert result.role == "operator"


@pytest.mark.asyncio
async def test_role_missing_returns_401():
    token = _token("operator")
    user = _make_user(user_id="u-1", username="u1", role_name="operator")
    user.role = None  # DB 에서 role 관계가 깨진 경우
    session = _mock_session(user)
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_creds(token), db_session=session)
    assert exc.value.status_code == 401
    assert "role missing" in exc.value.detail.lower()
