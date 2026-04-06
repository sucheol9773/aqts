"""
JWT 보안 강화 테스트

검증 항목:
  1. Key Rotation: kid 헤더, 현재/이전 키 검증, 레거시 토큰 호환
  2. Token ID (jti): 고유 식별자 존재, revocation 동작
  3. Token Revocation: revoke 후 재사용 불가
  4. bcrypt 전용: 평문 비밀번호 차단
  5. 토큰 타입: access/refresh 구분
"""

from unittest.mock import MagicMock, patch

import pytest
from jose import jwt


class TestKeyRotation:
    """JWT Key Rotation (kid 헤더) 테스트"""

    def _make_settings(self, secret="current_secret_key_12345", prev=None):
        mock = MagicMock()
        mock.dashboard.secret_key = secret
        mock.dashboard.previous_secret_key = prev
        mock.dashboard.access_token_expire_hours = 24
        mock.dashboard.refresh_token_expire_days = 7
        mock.dashboard.password = "$2b$12$dummy_hash"
        return mock

    def test_token_has_kid_header(self):
        """생성된 토큰에 kid 헤더가 존재해야 한다"""
        with patch(
            "api.middleware.auth.get_settings",
            return_value=self._make_settings(),
        ):
            from api.middleware.auth import AuthService

            token = AuthService.create_access_token({"sub": "admin"})
            headers = jwt.get_unverified_headers(token)
            assert "kid" in headers
            assert len(headers["kid"]) == 8

    def test_kid_is_deterministic(self):
        """같은 키에 대해 kid는 항상 동일해야 한다"""
        with patch(
            "api.middleware.auth.get_settings",
            return_value=self._make_settings(),
        ):
            from api.middleware.auth import AuthService

            token1 = AuthService.create_access_token({"sub": "admin"})
            token2 = AuthService.create_access_token({"sub": "admin"})
            kid1 = jwt.get_unverified_headers(token1)["kid"]
            kid2 = jwt.get_unverified_headers(token2)["kid"]
            assert kid1 == kid2

    def test_verify_with_current_key(self):
        """현재 키로 서명된 토큰은 정상 검증되어야 한다"""
        settings = self._make_settings()
        with patch("api.middleware.auth.get_settings", return_value=settings):
            from api.middleware.auth import AuthService

            token = AuthService.create_access_token({"sub": "admin"})
            payload = AuthService.verify_token(token)
            assert payload["sub"] == "admin"

    def test_verify_with_previous_key(self):
        """이전 키로 서명된 토큰도 rotation 기간 동안 검증되어야 한다"""
        old_key = "old_secret_key_12345"
        new_key = "new_secret_key_67890"

        # 이전 키로 토큰 생성
        old_settings = self._make_settings(secret=old_key)
        with patch("api.middleware.auth.get_settings", return_value=old_settings):
            from api.middleware.auth import AuthService

            token = AuthService.create_access_token({"sub": "admin"})

        # 키 교체 후 이전 키를 previous로 설정
        new_settings = self._make_settings(secret=new_key, prev=old_key)
        with patch("api.middleware.auth.get_settings", return_value=new_settings):
            payload = AuthService.verify_token(token)
            assert payload["sub"] == "admin"

    def test_verify_fails_with_unknown_key(self):
        """알 수 없는 키로 서명된 토큰은 검증 실패해야 한다"""
        from fastapi import HTTPException

        unknown_key = "unknown_key_99999"

        # 알 수 없는 키로 직접 JWT 생성
        token = jwt.encode(
            {"sub": "admin", "exp": 9999999999},
            unknown_key,
            algorithm="HS256",
            headers={"kid": "deadbeef"},
        )

        settings = self._make_settings()
        with patch("api.middleware.auth.get_settings", return_value=settings):
            from api.middleware.auth import AuthService

            with pytest.raises(HTTPException) as exc:
                AuthService.verify_token(token)
            assert exc.value.status_code == 401

    def test_legacy_token_without_kid(self):
        """kid가 없는 레거시 토큰도 현재 키로 검증 가능해야 한다"""
        key = "current_secret_key_12345"
        token = jwt.encode(
            {"sub": "admin", "exp": 9999999999},
            key,
            algorithm="HS256",
        )

        settings = self._make_settings(secret=key)
        with patch("api.middleware.auth.get_settings", return_value=settings):
            from api.middleware.auth import AuthService

            payload = AuthService.verify_token(token)
            assert payload["sub"] == "admin"


class TestTokenJTI:
    """Token ID (jti) 테스트"""

    def _make_settings(self):
        mock = MagicMock()
        mock.dashboard.secret_key = "test_secret_key_12345"
        mock.dashboard.previous_secret_key = None
        mock.dashboard.access_token_expire_hours = 24
        mock.dashboard.refresh_token_expire_days = 7
        return mock

    def test_access_token_has_jti(self):
        """Access Token에 jti가 포함되어야 한다"""
        with patch(
            "api.middleware.auth.get_settings",
            return_value=self._make_settings(),
        ):
            from api.middleware.auth import AuthService

            token = AuthService.create_access_token({"sub": "admin"})
            payload = jwt.get_unverified_claims(token)
            assert "jti" in payload
            assert len(payload["jti"]) == 36  # UUID4 형식

    def test_refresh_token_has_jti(self):
        """Refresh Token에 jti가 포함되어야 한다"""
        with patch(
            "api.middleware.auth.get_settings",
            return_value=self._make_settings(),
        ):
            from api.middleware.auth import AuthService

            token = AuthService.create_refresh_token({"sub": "admin"})
            payload = jwt.get_unverified_claims(token)
            assert "jti" in payload

    def test_each_token_has_unique_jti(self):
        """매번 생성되는 토큰의 jti는 고유해야 한다"""
        with patch(
            "api.middleware.auth.get_settings",
            return_value=self._make_settings(),
        ):
            from api.middleware.auth import AuthService

            token1 = AuthService.create_access_token({"sub": "admin"})
            token2 = AuthService.create_access_token({"sub": "admin"})
            jti1 = jwt.get_unverified_claims(token1)["jti"]
            jti2 = jwt.get_unverified_claims(token2)["jti"]
            assert jti1 != jti2

    def test_token_has_type_claim(self):
        """토큰에 type claim이 있어야 한다 (access/refresh 구분)"""
        with patch(
            "api.middleware.auth.get_settings",
            return_value=self._make_settings(),
        ):
            from api.middleware.auth import AuthService

            access = AuthService.create_access_token({"sub": "admin"})
            refresh = AuthService.create_refresh_token({"sub": "admin"})
            assert jwt.get_unverified_claims(access)["type"] == "access"
            assert jwt.get_unverified_claims(refresh)["type"] == "refresh"


class TestTokenRevocation:
    """Token Revocation 테스트"""

    def _make_settings(self):
        mock = MagicMock()
        mock.dashboard.secret_key = "test_secret_key_12345"
        mock.dashboard.previous_secret_key = None
        mock.dashboard.access_token_expire_hours = 24
        mock.dashboard.refresh_token_expire_days = 7
        return mock

    def test_revoke_token(self):
        """revoke된 토큰은 검증 실패해야 한다"""
        from fastapi import HTTPException

        with patch(
            "api.middleware.auth.get_settings",
            return_value=self._make_settings(),
        ):
            from api.middleware.auth import AuthService, get_revocation_store

            store = get_revocation_store()
            token = AuthService.create_access_token({"sub": "admin"})

            # revoke 전에는 검증 성공
            payload = AuthService.verify_token(token)
            assert payload["sub"] == "admin"

            # revoke
            jti = AuthService.revoke_token(token)
            assert jti is not None

            # revoke 후에는 검증 실패
            with pytest.raises(HTTPException) as exc:
                AuthService.verify_token(token)
            assert exc.value.status_code == 401
            assert "revoked" in exc.value.detail.lower()

            # cleanup
            store._blacklist.discard(jti)

    def test_revocation_store_basic(self):
        """TokenRevocationStore 기본 동작 검증"""
        from api.middleware.auth import TokenRevocationStore

        store = TokenRevocationStore()
        assert not store.is_revoked("test-jti-123")

        store.revoke("test-jti-123")
        assert store.is_revoked("test-jti-123")
        assert not store.is_revoked("other-jti-456")

    def test_revoke_returns_jti(self):
        """revoke_token은 jti를 반환해야 한다"""
        with patch(
            "api.middleware.auth.get_settings",
            return_value=self._make_settings(),
        ):
            from api.middleware.auth import AuthService, get_revocation_store

            store = get_revocation_store()
            token = AuthService.create_access_token({"sub": "admin"})
            jti = AuthService.revoke_token(token)
            assert jti is not None
            assert len(jti) == 36
            store._blacklist.discard(jti)

    def test_revoke_invalid_token_returns_none(self):
        """잘못된 토큰 revoke 시 None 반환"""
        from api.middleware.auth import AuthService

        result = AuthService.revoke_token("not-a-valid-jwt")
        assert result is None


class TestBcryptOnly:
    """평문 비밀번호 차단 테스트"""

    def _make_settings(self, password="plaintext_password"):
        mock = MagicMock()
        mock.dashboard.secret_key = "test_secret_key_12345"
        mock.dashboard.previous_secret_key = None
        mock.dashboard.password = password
        mock.dashboard.access_token_expire_hours = 24
        mock.dashboard.refresh_token_expire_days = 7
        return mock

    def test_plaintext_password_rejected(self):
        """평문 저장된 비밀번호로 인증 시 500 에러 발생"""
        from fastapi import HTTPException

        settings = self._make_settings(password="my_plain_password")
        with patch("api.middleware.auth.get_settings", return_value=settings):
            from api.middleware.auth import AuthService

            with pytest.raises(HTTPException) as exc:
                AuthService.authenticate("my_plain_password")
            assert exc.value.status_code == 500
            assert "bcrypt" in exc.value.detail.lower()

    def test_bcrypt_password_accepted(self):
        """bcrypt 해시 비밀번호로 인증 성공"""
        from api.middleware.auth import AuthService

        hashed = AuthService.hash_password("secure_password")
        settings = self._make_settings(password=hashed)

        with patch("api.middleware.auth.get_settings", return_value=settings):
            access, refresh = AuthService.authenticate("secure_password")
            assert access is not None
            assert refresh is not None

    def test_wrong_password_rejected(self):
        """잘못된 비밀번호는 401"""
        from fastapi import HTTPException

        from api.middleware.auth import AuthService

        hashed = AuthService.hash_password("correct_password")
        settings = self._make_settings(password=hashed)

        with patch("api.middleware.auth.get_settings", return_value=settings):
            with pytest.raises(HTTPException) as exc:
                AuthService.authenticate("wrong_password")
            assert exc.value.status_code == 401
