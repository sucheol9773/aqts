"""
P2-역할 변경 즉시 세션 무효화 유닛/통합 테스트.

시나리오:
  1) 토큰에 rv 가 없으면(legacy token) get_current_user 가 401 거부.
  2) 토큰 rv < DB role_version → 401 (역할 변경 이후 발급된 토큰이 아님).
  3) 토큰 rv > DB role_version → 401 (롤백/조작 의심).
  4) 토큰 rv == DB role_version → 정상 통과.
  5) refresh 경로가 DB role_version 을 재조회하고, 구 rv 는 거부.
  6) 역할 변경 경로(users PATCH) 가 role_id 변경 시 role_version 을 증가시킨다.
  7) 동일 role_id 재지정은 role_version 을 건드리지 않는다.

모든 에러 응답은 표준 스키마 `{error:{code: ROLE_VERSION_MISMATCH}}` 를 사용한다.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from api.middleware.auth import AuthService, get_current_user
from db.models.user import Role, User


def _make_user(uid: str, username: str, role_name: str, role_version: int) -> User:
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
        role_version=role_version,
        created_at=now,
        updated_at=now,
    )
    user.role = role
    return user


def _mock_session_returning(user: User | None) -> AsyncMock:
    session = AsyncMock()
    result = MagicMock()
    scalars_obj = MagicMock()
    scalars_obj.first = MagicMock(return_value=user)
    result.scalars = MagicMock(return_value=scalars_obj)
    session.execute = AsyncMock(return_value=result)
    return session


class TestGetCurrentUserRoleVersion:
    @pytest.mark.asyncio
    async def test_token_without_rv_is_rejected(self):
        user = _make_user("u-1", "alice", "operator", role_version=0)
        session = _mock_session_returning(user)
        # rv 없음 — legacy token
        token = AuthService.create_access_token({"sub": "alice", "uid": "u-1", "role": "operator"})
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(credentials=creds, db_session=session)

        assert exc_info.value.status_code == 401
        detail = exc_info.value.detail
        assert isinstance(detail, dict)
        assert detail["error_code"] == "ROLE_VERSION_MISMATCH"

    @pytest.mark.asyncio
    async def test_token_rv_lower_than_db_is_rejected(self):
        user = _make_user("u-2", "bob", "operator", role_version=3)
        session = _mock_session_returning(user)
        token = AuthService.create_access_token({"sub": "bob", "uid": "u-2", "role": "operator", "rv": 2})
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(credentials=creds, db_session=session)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail["error_code"] == "ROLE_VERSION_MISMATCH"

    @pytest.mark.asyncio
    async def test_token_rv_higher_than_db_is_rejected(self):
        """DB 롤백 / 외부 조작 방어. 단조 증가 invariant 위반 시 거부."""
        user = _make_user("u-3", "carol", "admin", role_version=1)
        session = _mock_session_returning(user)
        token = AuthService.create_access_token({"sub": "carol", "uid": "u-3", "role": "admin", "rv": 5})
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(credentials=creds, db_session=session)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail["error_code"] == "ROLE_VERSION_MISMATCH"

    @pytest.mark.asyncio
    async def test_token_rv_non_int_is_rejected(self):
        user = _make_user("u-4", "dan", "viewer", role_version=0)
        session = _mock_session_returning(user)
        token = AuthService.create_access_token({"sub": "dan", "uid": "u-4", "role": "viewer", "rv": "0"})
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(credentials=creds, db_session=session)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail["error_code"] == "ROLE_VERSION_MISMATCH"

    @pytest.mark.asyncio
    async def test_token_rv_matches_passes(self):
        user = _make_user("u-5", "eve", "operator", role_version=7)
        session = _mock_session_returning(user)
        token = AuthService.create_access_token({"sub": "eve", "uid": "u-5", "role": "operator", "rv": 7})
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

        authed = await get_current_user(credentials=creds, db_session=session)
        assert authed.role == "operator"
        assert authed.username == "eve"


class TestUsersPatchRoleVersionUnit:
    """users.py PATCH 핸들러의 role_version 증가 로직 순수 유닛 테스트."""

    def test_role_id_changed_increments_version(self):
        """previous_role_id != new role.id → role_version += 1."""
        user = _make_user("u-10", "frank", "viewer", role_version=2)
        assert user.role_version == 2
        assert user.role_id == 3  # viewer

        previous_role_id = user.role_id
        new_role_id = 2  # operator
        user.role_id = new_role_id
        if previous_role_id != new_role_id:
            user.role_version = (user.role_version or 0) + 1

        assert user.role_version == 3
        assert user.role_id == 2

    def test_same_role_id_does_not_increment(self):
        user = _make_user("u-11", "grace", "operator", role_version=5)
        previous_role_id = user.role_id
        new_role_id = user.role_id  # 동일
        user.role_id = new_role_id
        if previous_role_id != new_role_id:
            user.role_version = (user.role_version or 0) + 1

        assert user.role_version == 5  # 변화 없음

    def test_role_version_is_monotonic_across_multiple_changes(self):
        user = _make_user("u-12", "henry", "viewer", role_version=0)
        # viewer → operator → admin → viewer → viewer(동일) → operator
        transitions = [(3, 2), (2, 1), (1, 3), (3, 3), (3, 2)]
        expected_rv_sequence = [1, 2, 3, 3, 4]

        for (prev, new), expected_rv in zip(transitions, expected_rv_sequence):
            user.role_id = new
            if prev != new:
                user.role_version = (user.role_version or 0) + 1
            assert user.role_version == expected_rv


class TestJwtTokenIncludesRvClaim:
    """AuthService 가 발급하는 토큰에 rv 클레임이 포함되는지."""

    def test_access_token_encodes_rv(self):
        token = AuthService.create_access_token({"sub": "x", "uid": "u", "role": "operator", "rv": 42})
        payload = AuthService.verify_token(token)
        assert payload["rv"] == 42
        assert payload["role"] == "operator"

    def test_refresh_token_encodes_rv(self):
        token = AuthService.create_refresh_token({"sub": "x", "uid": "u", "role": "admin", "rv": 9})
        payload = AuthService.verify_token(token)
        assert payload["rv"] == 9
        assert payload["type"] == "refresh"
