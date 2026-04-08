"""
RBAC 권한 제어 테스트

역할: viewer / operator / admin
권한: viewer < operator < admin

테스트 케이스:
  1. 권한 부족 → 403 Forbidden
  2. 권한 충분 → 200 OK
  3. 역할별 엔드포인트 접근 제어
"""

import pytest


@pytest.mark.asyncio
class TestRBACRoles:
    """RBAC 역할 기반 접근 제어"""

    async def test_admin_token_structure(self, admin_token):
        """Admin 토큰이 올바른 클레임 포함"""
        from api.middleware.auth import AuthService

        payload = AuthService.verify_token(admin_token)
        assert payload.get("role") == "admin"
        assert payload.get("sub") == "admin"
        assert payload.get("uid") == "test-admin-uuid"

    async def test_operator_token_structure(self, operator_token):
        """Operator 토큰이 올바른 클레임 포함"""
        from api.middleware.auth import AuthService

        payload = AuthService.verify_token(operator_token)
        assert payload.get("role") == "operator"
        assert payload.get("sub") == "operator"

    async def test_viewer_token_structure(self, viewer_token):
        """Viewer 토큰이 올바른 클레임 포함"""
        from api.middleware.auth import AuthService

        payload = AuthService.verify_token(viewer_token)
        assert payload.get("role") == "viewer"
        assert payload.get("sub") == "viewer"


@pytest.mark.asyncio
class TestRBACEndpoints:
    """RBAC 엔드포인트 접근 제어"""

    async def test_users_endpoint_requires_admin(self, authenticated_app, viewer_token, operator_token, admin_token):
        """GET /users는 admin만 접근 가능"""
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=authenticated_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Viewer: 403
            response = await client.get(
                "/api/users",
                headers={"Authorization": f"Bearer {viewer_token}"},
            )
            assert response.status_code == 403

            # Operator: 403
            response = await client.get(
                "/api/users",
                headers={"Authorization": f"Bearer {operator_token}"},
            )
            assert response.status_code == 403

            # Admin: 200
            response = await client.get(
                "/api/users",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert response.status_code == 200

    async def test_me_endpoint_requires_authentication(self, authenticated_app):
        """GET /auth/me는 인증 필수 (모든 역할)"""
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=authenticated_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 토큰 없음: 401
            response = await client.get("/api/auth/me")
            assert response.status_code == 401

            # 정상 토큰: 200
            from api.middleware.auth import AuthService

            # P1-보안: uid/role 은 DB 에 존재하는 사용자와 일치해야 한다
            token = AuthService.create_access_token(
                {
                    "sub": "viewer",
                    "uid": "test-viewer-uuid",
                    "role": "viewer",
                    "rv": 0,
                }
            )
            response = await client.get(
                "/api/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["data"]["role"] == "viewer"


@pytest.mark.asyncio
class TestGetCurrentUser:
    """AuthenticatedUser 객체 생성 테스트"""

    async def test_get_current_user_with_valid_token(self, admin_token, db_session):
        """정상 토큰으로 AuthenticatedUser 객체 생성 (DB 재확인 포함)"""
        from fastapi.security import HTTPAuthorizationCredentials

        from api.middleware.auth import AuthService, get_current_user

        # 토큰 검증
        payload = AuthService.verify_token(admin_token)
        assert payload.get("sub") == "admin"

        # AuthenticatedUser 생성 (P1-보안: db_session 필수)
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=admin_token)
        user = await get_current_user(credentials, db_session=db_session)

        assert user.username == "admin"
        assert user.id == "test-admin-uuid"
        assert user.role == "admin"

    async def test_get_current_user_with_invalid_token(self, db_session):
        """잘못된 토큰으로 401 (DB 조회 전에 차단)"""
        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials

        from api.middleware.auth import get_current_user

        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="invalid-token")

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(credentials, db_session=db_session)

        assert exc_info.value.status_code == 401

    async def test_get_current_user_with_no_credentials(self, db_session):
        """토큰 없으면 401 (DB 조회 전에 차단)"""
        from fastapi import HTTPException

        from api.middleware.auth import get_current_user

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(None, db_session=db_session)

        assert exc_info.value.status_code == 401
