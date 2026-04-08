"""
Stage 1 검증: HTTPBearer auto_error=False → 401 반환 확인
==========================================================
수정 내용:
  - api/middleware/auth.py: HTTPBearer(auto_error=False)
  - get_current_user: credentials is None → 401

이 테스트는 아래 4가지 시나리오를 검증합니다:
  1. Authorization 헤더 없이 접근 → 401 (NOT 403)
  2. 잘못된 토큰으로 접근 → 401
  3. 정상 토큰으로 접근 → 200
  4. 보호된 주문 엔드포인트도 동일 동작 → 401
"""

import pytest


@pytest.mark.asyncio
@pytest.mark.smoke
class TestAuth401Behavior:
    """Authorization 헤더 미제공 시 401 반환 검증."""

    async def test_no_token_returns_401_not_403(self):
        """Case 1: Authorization 헤더 없음 → 401 Unauthorized (NOT 403 Forbidden)."""
        from httpx import ASGITransport, AsyncClient

        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/auth/me")

            # 핵심 검증: 403이 아니라 401이어야 함
            assert response.status_code == 401, (
                f"Expected 401 Unauthorized, got {response.status_code}. "
                f"HTTPBearer(auto_error=False) + None check가 "
                f"올바르게 작동하지 않음"
            )

            # WWW-Authenticate 헤더 존재 확인 (RFC 7235 요구사항)
            assert "www-authenticate" in response.headers, "401 응답에는 WWW-Authenticate 헤더가 포함되어야 함"

            body = response.json()
            assert body.get("detail") == "Not authenticated"

    async def test_invalid_token_returns_401(self):
        """Case 2: 잘못된 JWT 토큰 → 401 Unauthorized."""
        from httpx import ASGITransport, AsyncClient

        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/auth/me", headers={"Authorization": "Bearer this_is_not_a_valid_jwt"})

            assert response.status_code == 401, f"Expected 401 for invalid token, got {response.status_code}"

    async def test_valid_token_returns_200(self, authenticated_app):
        """Case 3: 정상 로그인 후 유효한 토큰 → 200 OK.

        P1-보안: get_current_user 가 DB 재확인을 수행하므로 실제 app 이 아닌
        authenticated_app 픽스처(mock DB + test_user_admin 주입)를 사용한다.
        """
        from httpx import ASGITransport, AsyncClient

        from api.middleware.auth import AuthService

        # 직접 토큰 생성 (RBAC v1.29+: uid, role 포함)
        token = AuthService.create_access_token({"sub": "admin", "uid": "test-admin-uuid", "role": "admin"})

        transport = ASGITransport(app=authenticated_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

            assert response.status_code == 200, (
                f"Expected 200 for valid token, got {response.status_code}. "
                f"auto_error=False가 정상 인증 경로를 깨뜨렸을 수 있음"
            )
            data = response.json()
            assert data["data"]["username"] == "admin"

    async def test_orders_endpoint_no_token_returns_401(self):
        """Case 4: 보호된 주문 엔드포인트, 토큰 없음 → 401."""
        from httpx import ASGITransport, AsyncClient

        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/orders/",
                json={"ticker": "005930", "market": "KRX", "side": "BUY", "quantity": 100, "order_type": "MARKET"},
            )

            assert response.status_code == 401, f"Expected 401 for orders without auth, got {response.status_code}"
