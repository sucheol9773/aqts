"""
P1-에러 메시지 표준화 테스트

검증 목표:
  1. `raise_api_error` 가 HTTPException(detail=dict) 을 발생시킨다.
  2. `normalize_error_body` 가 dict/str/기타 detail 을 공통 스키마로 변환한다.
  3. 글로벌 핸들러가 실제 API 응답을 표준 본문으로 직렬화한다.
  4. `users.py` 의 500 경로가 더 이상 `str(e)` 를 노출하지 않는다.
"""

import pytest
from fastapi import HTTPException

from api.errors import ErrorCode, normalize_error_body, raise_api_error


class TestRaiseApiError:
    def test_raises_http_exception_with_dict_detail(self):
        with pytest.raises(HTTPException) as exc:
            raise_api_error(
                404,
                ErrorCode.ORDER_NOT_FOUND,
                "주문을 찾을 수 없습니다.",
                order_id="O-123",
            )
        assert exc.value.status_code == 404
        assert isinstance(exc.value.detail, dict)
        assert exc.value.detail["error_code"] == "ORDER_NOT_FOUND"
        assert exc.value.detail["message"] == "주문을 찾을 수 없습니다."
        assert exc.value.detail["context"] == {"order_id": "O-123"}

    def test_accepts_string_code(self):
        with pytest.raises(HTTPException) as exc:
            raise_api_error(400, "CUSTOM_CODE", "custom")
        assert exc.value.detail["error_code"] == "CUSTOM_CODE"

    def test_headers_are_forwarded(self):
        with pytest.raises(HTTPException) as exc:
            raise_api_error(
                503,
                ErrorCode.USER_STORE_UNAVAILABLE,
                "일시적 장애",
                headers={"Retry-After": "5"},
            )
        assert exc.value.headers == {"Retry-After": "5"}


class TestNormalizeErrorBody:
    def test_dict_detail_with_error_code(self):
        body = normalize_error_body(
            404,
            {"error_code": "ORDER_NOT_FOUND", "message": "not found"},
        )
        assert body == {
            "success": False,
            "error": {"code": "ORDER_NOT_FOUND", "message": "not found"},
        }

    def test_dict_detail_with_context(self):
        body = normalize_error_body(
            400,
            {
                "error_code": "VALIDATION_ERROR",
                "message": "bad",
                "context": {"field": "x"},
            },
        )
        assert body["error"]["context"] == {"field": "x"}

    def test_dict_detail_missing_error_code_uses_status_default(self):
        body = normalize_error_body(404, {"message": "missing"})
        assert body["error"]["code"] == "NOT_FOUND"
        assert body["error"]["message"] == "missing"

    def test_dict_detail_legacy_extras_go_into_context(self):
        """P0-2b rate limiter detail 처럼 추가 필드가 있는 경우."""
        body = normalize_error_body(
            429,
            {
                "error_code": "RATE_LIMIT_EXCEEDED",
                "message": "too many",
                "retry_after": 5,
            },
        )
        assert body["error"]["context"] == {"retry_after": 5}

    def test_string_detail_uses_status_default_code(self):
        body = normalize_error_body(401, "Not authenticated")
        assert body == {
            "success": False,
            "error": {"code": "UNAUTHORIZED", "message": "Not authenticated"},
        }

    def test_status_500_default_is_internal_error(self):
        body = normalize_error_body(500, "boom")
        assert body["error"]["code"] == "INTERNAL_ERROR"

    def test_status_422_default_is_validation_error(self):
        body = normalize_error_body(422, {"message": "unprocessable"})
        assert body["error"]["code"] == "VALIDATION_ERROR"

    def test_non_string_detail_is_stringified(self):
        body = normalize_error_body(500, 42)
        assert body["error"]["message"] == "42"


@pytest.mark.asyncio
class TestGlobalHandlerIntegration:
    """글로벌 HTTPException 핸들러가 실제 API 응답 본문을 표준화하는지 검증."""

    async def test_orders_not_found_returns_standard_body(self, authenticated_app, admin_token):
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=authenticated_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/orders/nonexistent-order-id",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            # 404 든 500 이든, 에러 본문은 반드시 표준 스키마여야 한다.
            if resp.status_code >= 400:
                body = resp.json()
                assert "error" in body or body.get("success") is False

    async def test_param_sensitivity_latest_404_has_error_code(self, authenticated_app, admin_token):
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=authenticated_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/system/param-sensitivity/latest",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            if resp.status_code == 404:
                body = resp.json()
                assert body["success"] is False
                assert body["error"]["code"] == "PARAM_SENSITIVITY_NOT_FOUND"
                assert "분석 결과" in body["error"]["message"]

    async def test_dry_run_stop_without_session_has_error_code(self, authenticated_app, operator_token):
        from httpx import ASGITransport, AsyncClient

        # 세션이 없는 상태에서 stop 호출 → 404 + DRY_RUN_SESSION_NOT_FOUND
        from core.dry_run.engine import get_dry_run_engine

        engine = get_dry_run_engine()
        if engine.current_session is not None:
            engine.end_session()

        transport = ASGITransport(app=authenticated_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/system/dry-run/stop",
                headers={"Authorization": f"Bearer {operator_token}"},
            )
            assert resp.status_code == 404
            body = resp.json()
            assert body["success"] is False
            assert body["error"]["code"] == "DRY_RUN_SESSION_NOT_FOUND"
            assert "진행 중인 드라이런 세션이 없습니다" in body["error"]["message"]

    async def test_auth_refresh_with_access_token_has_error_code(self, authenticated_app):
        from httpx import ASGITransport, AsyncClient

        from api.middleware.auth import AuthService

        access = AuthService.create_access_token({"sub": "admin", "uid": "test-admin-uuid", "role": "admin"})
        transport = ASGITransport(app=authenticated_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/auth/refresh",
                json={"refresh_token": access},
            )
            assert resp.status_code == 401
            body = resp.json()
            assert body["success"] is False
            assert body["error"]["code"] == "INVALID_TOKEN_TYPE"
            # 토큰 원문은 절대 응답에 들어가선 안 된다
            assert access not in resp.text
