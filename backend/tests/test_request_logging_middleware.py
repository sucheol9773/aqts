"""
RequestLoggingMiddleware 테스트

request_id / correlation_id 생성·전파·헤더 포함을 검증합니다.
"""

import unittest
import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from api.middleware.request_logger import RequestLoggingMiddleware


def _create_test_app() -> FastAPI:
    """테스트용 FastAPI 앱"""
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/test")
    async def test_endpoint(request: Request):
        return {
            "request_id": getattr(request.state, "request_id", None),
            "correlation_id": getattr(request.state, "correlation_id", None),
        }

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


@pytest.mark.smoke
class TestRequestIdGeneration(unittest.TestCase):
    """request_id 자동 생성 검증"""

    def setUp(self):
        self.client = TestClient(_create_test_app())

    def test_response_has_request_id_header(self):
        """응답에 X-Request-ID 헤더가 포함됩니다."""
        resp = self.client.get("/test")
        assert resp.status_code == 200
        assert "X-Request-ID" in resp.headers
        # UUID 형식 확인
        rid = resp.headers["X-Request-ID"]
        uuid.UUID(rid)  # 유효하지 않으면 ValueError

    def test_auto_generated_request_id_is_uuid4(self):
        """자동 생성된 request_id는 UUID4 형식입니다."""
        resp = self.client.get("/test")
        rid = resp.headers["X-Request-ID"]
        parsed = uuid.UUID(rid)
        assert parsed.version == 4

    def test_request_id_propagated_to_handler(self):
        """request.state.request_id에 값이 전달됩니다."""
        resp = self.client.get("/test")
        body = resp.json()
        assert body["request_id"] is not None
        assert body["request_id"] == resp.headers["X-Request-ID"]

    def test_client_supplied_request_id_honored(self):
        """클라이언트가 X-Request-ID를 보내면 그대로 사용합니다."""
        custom_id = "client-req-12345"
        resp = self.client.get("/test", headers={"X-Request-ID": custom_id})
        assert resp.headers["X-Request-ID"] == custom_id
        assert resp.json()["request_id"] == custom_id

    def test_each_request_gets_unique_id(self):
        """매 요청마다 고유한 request_id가 생성됩니다."""
        resp1 = self.client.get("/test")
        resp2 = self.client.get("/test")
        assert resp1.headers["X-Request-ID"] != resp2.headers["X-Request-ID"]


class TestCorrelationId(unittest.TestCase):
    """correlation_id 전파 검증"""

    def setUp(self):
        self.client = TestClient(_create_test_app())

    def test_response_has_correlation_id_header(self):
        """응답에 X-Correlation-ID 헤더가 포함됩니다."""
        resp = self.client.get("/test")
        assert "X-Correlation-ID" in resp.headers

    def test_default_correlation_id_equals_request_id(self):
        """X-Correlation-ID 없으면 request_id를 correlation_id로 사용."""
        resp = self.client.get("/test")
        assert resp.headers["X-Correlation-ID"] == resp.headers["X-Request-ID"]

    def test_client_supplied_correlation_id(self):
        """클라이언트가 X-Correlation-ID를 보내면 전파."""
        corr_id = "corr-abc-123"
        resp = self.client.get("/test", headers={"X-Correlation-ID": corr_id})
        assert resp.headers["X-Correlation-ID"] == corr_id
        assert resp.json()["correlation_id"] == corr_id

    def test_both_ids_supplied(self):
        """클라이언트가 둘 다 보내면 각각 존중."""
        req_id = "req-111"
        corr_id = "corr-222"
        resp = self.client.get(
            "/test",
            headers={
                "X-Request-ID": req_id,
                "X-Correlation-ID": corr_id,
            },
        )
        assert resp.headers["X-Request-ID"] == req_id
        assert resp.headers["X-Correlation-ID"] == corr_id
        body = resp.json()
        assert body["request_id"] == req_id
        assert body["correlation_id"] == corr_id


class TestSkipPaths(unittest.TestCase):
    """SKIP_PATHS 엔드포인트는 request_id 생성 안함"""

    def setUp(self):
        self.client = TestClient(_create_test_app())

    def test_health_endpoint_no_tracking_headers(self):
        """health 엔드포인트는 추적 헤더가 없습니다."""
        resp = self.client.get("/health")
        assert resp.status_code == 200
        assert "X-Request-ID" not in resp.headers
        assert "X-Correlation-ID" not in resp.headers


if __name__ == "__main__":
    unittest.main()
