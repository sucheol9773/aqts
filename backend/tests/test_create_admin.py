"""create_admin CLI 단위테스트

DB 의존성을 모킹하여 다음 동작을 검증한다:
  1. 환경변수 검증 (필수/길이/문자 종류)
  2. admin 역할 조회 (없으면 명시적 에러)
  3. 멱등성 (admin 이 이미 존재하면 변경 없이 종료)
  4. username 중복 차단
  5. 정상 생성 시 AuthService.hash_password 사용 + role_id 매핑
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.create_admin import (
    MIN_PASSWORD_LENGTH,
    AdminBootstrapError,
    admin_already_exists,
    create_admin,
    find_admin_role_id,
    read_env,
    validate_password,
)


# ════════════════════════════════════════
# validate_password
# ════════════════════════════════════════
class TestValidatePassword:
    def test_empty_rejected(self):
        with pytest.raises(AdminBootstrapError, match="비어 있다"):
            validate_password("")

    def test_too_short_rejected(self):
        with pytest.raises(AdminBootstrapError, match="최소 12자"):
            validate_password("Short1!")

    def test_only_letters_rejected(self):
        with pytest.raises(AdminBootstrapError, match="2종류 이상"):
            validate_password("OnlyLettersHere")

    def test_only_digits_rejected(self):
        with pytest.raises(AdminBootstrapError, match="2종류 이상"):
            validate_password("123456789012")

    def test_letters_and_digits_ok(self):
        validate_password("Letters12345")

    def test_letters_and_symbols_ok(self):
        validate_password("Letters!@#$%")

    def test_all_three_classes_ok(self):
        validate_password("Letters1234!")

    def test_min_length_boundary(self):
        # 정확히 MIN_PASSWORD_LENGTH 자: 통과해야 함
        validate_password("a" * (MIN_PASSWORD_LENGTH - 1) + "1")
        # 한 글자 부족: 거부
        with pytest.raises(AdminBootstrapError):
            validate_password("a" * (MIN_PASSWORD_LENGTH - 2) + "1")


# ════════════════════════════════════════
# read_env
# ════════════════════════════════════════
class TestReadEnv:
    def test_password_required(self, monkeypatch):
        monkeypatch.delenv("ADMIN_BOOTSTRAP_PASSWORD", raising=False)
        with pytest.raises(AdminBootstrapError, match="비어 있다"):
            read_env()

    def test_default_username(self, monkeypatch):
        monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", "ValidPass123!")
        monkeypatch.delenv("ADMIN_BOOTSTRAP_USERNAME", raising=False)
        username, password = read_env()
        assert username == "admin"
        assert password == "ValidPass123!"

    def test_custom_username(self, monkeypatch):
        monkeypatch.setenv("ADMIN_BOOTSTRAP_USERNAME", "ops_admin")
        monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", "ValidPass123!")
        username, password = read_env()
        assert username == "ops_admin"

    def test_blank_username_rejected(self, monkeypatch):
        monkeypatch.setenv("ADMIN_BOOTSTRAP_USERNAME", "   ")
        monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", "ValidPass123!")
        with pytest.raises(AdminBootstrapError, match="빈 문자열"):
            read_env()

    def test_password_policy_enforced_in_read_env(self, monkeypatch):
        monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", "short")
        with pytest.raises(AdminBootstrapError, match="최소 12자"):
            read_env()


# ════════════════════════════════════════
# find_admin_role_id
# ════════════════════════════════════════
class TestFindAdminRoleId:
    @pytest.mark.asyncio
    async def test_returns_id_when_role_exists(self):
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = 3
        session.execute = AsyncMock(return_value=result_mock)

        role_id = await find_admin_role_id(session)
        assert role_id == 3

    @pytest.mark.asyncio
    async def test_raises_when_role_missing(self):
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(AdminBootstrapError, match="admin' 역할이 존재하지 않는다"):
            await find_admin_role_id(session)


# ════════════════════════════════════════
# admin_already_exists
# ════════════════════════════════════════
class TestAdminAlreadyExists:
    @pytest.mark.asyncio
    async def test_true_when_user_found(self):
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = "some-uuid"
        session.execute = AsyncMock(return_value=result_mock)

        assert await admin_already_exists(session, admin_role_id=3) is True

    @pytest.mark.asyncio
    async def test_false_when_no_user(self):
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)

        assert await admin_already_exists(session, admin_role_id=3) is False


# ════════════════════════════════════════
# create_admin
# ════════════════════════════════════════
class TestCreateAdmin:
    @pytest.mark.asyncio
    async def test_idempotent_when_admin_exists(self):
        session = MagicMock()
        # 1st call: find_admin_role_id → returns 3
        # 2nd call: admin_already_exists → returns "some-uuid"
        results = [
            SimpleNamespace(scalar_one_or_none=lambda: 3),
            SimpleNamespace(scalar_one_or_none=lambda: "existing-uuid"),
        ]
        session.execute = AsyncMock(side_effect=results)
        session.add = MagicMock()
        session.flush = AsyncMock()

        returned_id = await create_admin(session, "admin", "ValidPass123!")
        assert returned_id == ""
        session.add.assert_not_called()
        session.flush.assert_not_called()

    @pytest.mark.asyncio
    async def test_username_collision_rejected(self):
        session = MagicMock()
        # find_admin_role_id → 3
        # admin_already_exists → None (no admin)
        # username dup check → "other-uuid" (collision)
        results = [
            SimpleNamespace(scalar_one_or_none=lambda: 3),
            SimpleNamespace(scalar_one_or_none=lambda: None),
            SimpleNamespace(scalar_one_or_none=lambda: "other-uuid"),
        ]
        session.execute = AsyncMock(side_effect=results)
        session.add = MagicMock()
        session.flush = AsyncMock()

        with pytest.raises(AdminBootstrapError, match="이미 존재한다"):
            await create_admin(session, "admin", "ValidPass123!")
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_admin_when_absent(self):
        session = MagicMock()
        results = [
            SimpleNamespace(scalar_one_or_none=lambda: 3),  # role lookup
            SimpleNamespace(scalar_one_or_none=lambda: None),  # admin check
            SimpleNamespace(scalar_one_or_none=lambda: None),  # username check
        ]
        session.execute = AsyncMock(side_effect=results)
        session.add = MagicMock()
        session.flush = AsyncMock()

        with patch(
            "api.middleware.auth.AuthService.hash_password",
            return_value="$2b$12$mockhash",
        ) as hash_mock:
            returned_id = await create_admin(session, "admin", "ValidPass123!")

        assert returned_id  # non-empty UUID
        hash_mock.assert_called_once_with("ValidPass123!")
        session.add.assert_called_once()
        added_user = session.add.call_args[0][0]
        assert added_user.username == "admin"
        assert added_user.role_id == 3
        assert added_user.password_hash == "$2b$12$mockhash"
        assert added_user.is_active is True
        assert added_user.is_locked is False
        assert added_user.failed_login_attempts == 0
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_uses_dynamic_role_id_not_hardcoded(self):
        """admin role id 가 1 이든 99 든 동적으로 사용되어야 한다."""
        session = MagicMock()
        results = [
            SimpleNamespace(scalar_one_or_none=lambda: 99),
            SimpleNamespace(scalar_one_or_none=lambda: None),
            SimpleNamespace(scalar_one_or_none=lambda: None),
        ]
        session.execute = AsyncMock(side_effect=results)
        session.add = MagicMock()
        session.flush = AsyncMock()

        with patch(
            "api.middleware.auth.AuthService.hash_password",
            return_value="$2b$12$mockhash",
        ):
            await create_admin(session, "admin", "ValidPass123!")

        added_user = session.add.call_args[0][0]
        assert added_user.role_id == 99
