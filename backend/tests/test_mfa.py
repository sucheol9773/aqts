"""
MFA (TOTP) 테스트

테스트 케이스:
  1. TOTP 시크릿 생성 및 검증
  2. MFA 등록 및 활성화
  3. MFA 비활성화
  4. TOTP 코드 검증 (정상/오류)
"""

import pytest


class TestTOTPMechanics:
    """TOTP 기본 기능"""

    def test_generate_totp_secret(self):
        """TOTP 시크릿 생성"""
        from api.middleware.auth import AuthService

        secret = AuthService.generate_totp_secret()
        assert secret is not None
        assert len(secret) > 0
        # Base32 인코딩 확인
        assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for c in secret)

    def test_verify_totp_valid_code(self):
        """TOTP 코드 검증 (정상)"""
        import pyotp

        from api.middleware.auth import AuthService

        secret = AuthService.generate_totp_secret()
        totp = pyotp.TOTP(secret)
        code = totp.now()

        result = AuthService.verify_totp(secret, code)
        assert result is True

    def test_verify_totp_invalid_code(self):
        """TOTP 코드 검증 (오류)"""
        from api.middleware.auth import AuthService

        secret = AuthService.generate_totp_secret()
        result = AuthService.verify_totp(secret, "000000")
        assert result is False

    def test_get_provisioning_uri(self):
        """프로비저닝 URI 생성 (QR 코드용)"""
        from api.middleware.auth import AuthService

        secret = AuthService.generate_totp_secret()
        uri = AuthService.get_provisioning_uri(secret, "testuser")

        assert uri.startswith("otpauth://")
        assert "testuser" in uri
        assert "AQTS" in uri  # 발급자명


class TestAuthServiceAuthenticate:
    """AuthService.authenticate() 테스트"""

    async def test_authenticate_missing_db_session(self):
        """DB 세션 없으면 500"""
        from fastapi import HTTPException

        from api.middleware.auth import AuthService

        with pytest.raises(HTTPException) as exc_info:
            await AuthService.authenticate("user", "pass", db_session=None)

        assert exc_info.value.status_code == 500

    async def test_authenticate_user_not_found(self, db_session):
        """사용자 미존재 → 401"""
        from fastapi import HTTPException

        from api.middleware.auth import AuthService

        with pytest.raises(HTTPException) as exc_info:
            await AuthService.authenticate(
                "nonexistent",
                "password",
                db_session=db_session,
            )

        assert exc_info.value.status_code == 401
        assert "Invalid username or password" in exc_info.value.detail

    async def test_authenticate_invalid_password(self, db_session, test_user_admin):
        """비밀번호 오류 → 401"""
        from fastapi import HTTPException

        from api.middleware.auth import AuthService

        with pytest.raises(HTTPException) as exc_info:
            await AuthService.authenticate(
                test_user_admin.username,
                "wrong-password",
                db_session=db_session,
            )

        assert exc_info.value.status_code == 401

    async def test_authenticate_success(self, db_session, test_user_admin):
        """정상 인증 → 토큰 발급"""
        from api.middleware.auth import AuthService

        access_token, refresh_token = await AuthService.authenticate(
            test_user_admin.username,
            "test-admin-password",  # test_user_admin created with this password
            db_session=db_session,
        )

        assert access_token is not None
        assert refresh_token is not None
        # 토큰 검증
        payload = AuthService.verify_token(access_token)
        assert payload["sub"] == test_user_admin.username
        assert payload["role"] == "admin"

    async def test_authenticate_with_inactive_user(self, db_session, test_user_admin):
        """비활성 사용자 → 401"""
        from fastapi import HTTPException

        from api.middleware.auth import AuthService

        test_user_admin.is_active = False
        await db_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await AuthService.authenticate(
                test_user_admin.username,
                "test-admin-password",
                db_session=db_session,
            )

        assert exc_info.value.status_code == 401
        assert "inactive" in exc_info.value.detail.lower()

    async def test_authenticate_with_locked_user(self, db_session, test_user_admin):
        """잠금 사용자 → 403"""
        from fastapi import HTTPException

        from api.middleware.auth import AuthService

        test_user_admin.is_locked = True
        await db_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await AuthService.authenticate(
                test_user_admin.username,
                "test-admin-password",
                db_session=db_session,
            )

        assert exc_info.value.status_code == 403
        assert "locked" in exc_info.value.detail.lower()

    async def test_authenticate_failed_login_increments_counter(self, db_session, test_user_admin):
        """실패 횟수 증가"""
        from fastapi import HTTPException

        from api.middleware.auth import AuthService

        initial_attempts = test_user_admin.failed_login_attempts

        with pytest.raises(HTTPException):
            await AuthService.authenticate(
                test_user_admin.username,
                "wrong-password",
                db_session=db_session,
            )

        await db_session.refresh(test_user_admin)
        assert test_user_admin.failed_login_attempts == initial_attempts + 1

    async def test_authenticate_locks_after_5_failures(self, db_session, test_user_admin):
        """5회 실패 후 자동 잠금"""
        from fastapi import HTTPException

        from api.middleware.auth import AuthService

        # 5회 실패
        for _ in range(5):
            try:
                await AuthService.authenticate(
                    test_user_admin.username,
                    "wrong-password",
                    db_session=db_session,
                )
            except HTTPException:
                pass

        await db_session.refresh(test_user_admin)
        assert test_user_admin.is_locked is True

    async def test_authenticate_with_totp_missing(self, db_session, test_user_admin):
        """TOTP 활성화 시 코드 미제공 → 401"""
        from fastapi import HTTPException

        from api.middleware.auth import AuthService

        # TOTP 활성화
        test_user_admin.totp_enabled = True
        test_user_admin.totp_secret = AuthService.generate_totp_secret()
        await db_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await AuthService.authenticate(
                test_user_admin.username,
                "test-admin-password",
                db_session=db_session,
                # totp_code 없음
            )

        assert exc_info.value.status_code == 401
        assert "TOTP" in exc_info.value.detail

    async def test_authenticate_with_invalid_totp(self, db_session, test_user_admin):
        """TOTP 코드 오류 → 401"""
        from fastapi import HTTPException

        from api.middleware.auth import AuthService

        # TOTP 활성화
        test_user_admin.totp_enabled = True
        test_user_admin.totp_secret = AuthService.generate_totp_secret()
        await db_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await AuthService.authenticate(
                test_user_admin.username,
                "test-admin-password",
                totp_code="000000",
                db_session=db_session,
            )

        assert exc_info.value.status_code == 401
        assert "Invalid TOTP" in exc_info.value.detail

    async def test_authenticate_with_valid_totp(self, db_session, test_user_admin):
        """TOTP 정상 검증 → 토큰 발급"""
        import pyotp

        from api.middleware.auth import AuthService

        # TOTP 활성화
        secret = AuthService.generate_totp_secret()
        test_user_admin.totp_enabled = True
        test_user_admin.totp_secret = secret
        await db_session.commit()

        # 정상 코드 생성
        totp = pyotp.TOTP(secret)
        code = totp.now()

        access_token, refresh_token = await AuthService.authenticate(
            test_user_admin.username,
            "test-admin-password",
            totp_code=code,
            db_session=db_session,
        )

        assert access_token is not None
        assert refresh_token is not None


# Fixtures are defined in conftest.py
