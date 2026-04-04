"""
main.py startup 로직 테스트

스케줄러 시작, KIS 토큰 초기화, degraded 모드 처리를 검증합니다.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient


class TestHealthCheckComponents(unittest.TestCase):
    """health 엔드포인트의 scheduler/kis_api 컴포넌트 검증"""

    @patch("main.MongoDBManager")
    @patch("main.RedisManager")
    def test_health_includes_scheduler_and_kis(self, mock_redis, mock_mongo):
        """health 응답에 scheduler, kis_api 컴포넌트가 포함됩니다."""
        import main

        # DB mock
        mock_redis.get_client.return_value.ping = AsyncMock()
        mock_mongo.get_db.return_value.command = AsyncMock()

        client = TestClient(main.app, raise_server_exceptions=False)
        resp = client.get("/api/system/health")
        body = resp.json()
        assert "scheduler" in body["components"]
        assert "kis_api" in body["components"]

    @patch("main.MongoDBManager")
    @patch("main.RedisManager")
    def test_health_scheduler_degraded_flag(self, mock_redis, mock_mongo):
        """scheduler_degraded 플래그가 health에 반영됩니다."""
        import main

        mock_redis.get_client.return_value.ping = AsyncMock()
        mock_mongo.get_db.return_value.command = AsyncMock()

        # degraded 시뮬레이션
        main.app.state.scheduler_degraded = True
        old_scheduler = main.trading_scheduler
        main.trading_scheduler = None

        try:
            client = TestClient(main.app, raise_server_exceptions=False)
            resp = client.get("/api/system/health")
            body = resp.json()
            assert body["components"]["scheduler"] == "degraded"
            assert body["status"] == "degraded"
        finally:
            main.app.state.scheduler_degraded = False
            main.trading_scheduler = old_scheduler

    @patch("main.MongoDBManager")
    @patch("main.RedisManager")
    def test_health_kis_degraded_flag(self, mock_redis, mock_mongo):
        """kis_degraded 플래그가 health에 반영됩니다."""
        import main

        mock_redis.get_client.return_value.ping = AsyncMock()
        mock_mongo.get_db.return_value.command = AsyncMock()

        main.app.state.kis_degraded = True
        old_client = main.kis_client
        main.kis_client = None

        try:
            client = TestClient(main.app, raise_server_exceptions=False)
            resp = client.get("/api/system/health")
            body = resp.json()
            assert body["components"]["kis_api"] == "degraded"
            assert body["status"] == "degraded"
        finally:
            main.app.state.kis_degraded = False
            main.kis_client = old_client

    @patch("main.MongoDBManager")
    @patch("main.RedisManager")
    def test_health_scheduler_running(self, mock_redis, mock_mongo):
        """스케줄러가 실행 중이면 healthy."""
        import main

        mock_redis.get_client.return_value.ping = AsyncMock()
        mock_mongo.get_db.return_value.command = AsyncMock()

        mock_scheduler = MagicMock()
        mock_scheduler.is_running = True
        old_scheduler = main.trading_scheduler
        main.trading_scheduler = mock_scheduler

        try:
            client = TestClient(main.app, raise_server_exceptions=False)
            resp = client.get("/api/system/health")
            body = resp.json()
            assert body["components"]["scheduler"] == "healthy"
        finally:
            main.trading_scheduler = old_scheduler


class TestStartupImports(unittest.TestCase):
    """main.py에 TradingScheduler, KISClient import 존재 확인"""

    def test_trading_scheduler_imported(self):
        import main

        assert hasattr(main, "trading_scheduler")
        assert hasattr(main, "TradingScheduler")

    def test_kis_client_imported(self):
        import main

        assert hasattr(main, "kis_client")
        assert hasattr(main, "KISClient")


if __name__ == "__main__":
    unittest.main()
