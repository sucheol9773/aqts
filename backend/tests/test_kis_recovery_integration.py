"""KIS 자동 복원 wiring 통합 테스트.

목적: try_recover_kis / alert_callback / AlertManager 가 실제 health_check 라우트
경로에서 올바르게 연결되었는지 검증한다. 단위 테스트(test_kis_recovery.py)는 엔진을
독립적으로 호출하므로 main.py 의 wiring 을 보장하지 못한다 (CLAUDE.md Wiring Rule).

검증 시나리오:
    1. KIS degraded 상태 + 낮은 alert_threshold 로 KISRecoveryState 주입
    2. KISClient factory 를 항상 실패하도록 패치
    3. /api/system/health 를 threshold 횟수만큼 호출
    4. lazy import 되는 _alert_manager.create_alert 가 정확히 1회 호출되었는지 확인
    5. metadata 에 consecutive_failures / last_error / alert_threshold 가 포함되는지 확인
    6. 추가 호출에도 중복 발송이 없는지 확인
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from config.constants import AlertType
from core.data_collector.kis_recovery import KISRecoveryState
from core.notification.alert_manager import AlertLevel


class TestKISRecoveryWiring(unittest.TestCase):
    """health_check → try_recover_kis → alert_callback → AlertManager wiring 검증."""

    @patch("main.MongoDBManager")
    @patch("main.RedisManager")
    def test_alert_callback_dispatched_through_health_check_route(self, mock_redis, mock_mongo):
        import main

        mock_redis.get_client.return_value.ping = AsyncMock()
        mock_mongo.get_db.return_value.command = AsyncMock()

        # KIS degraded 상태 + 임계값 2 + 쿨다운 만료된 시점으로 강제
        threshold = 2
        past = datetime.utcnow() - timedelta(seconds=3600)
        state = KISRecoveryState(cooldown_seconds=1, alert_threshold=threshold)
        state.degraded = True
        state.next_attempt_at = past
        state.last_error = "EGW00133: rate limit"

        old_state = getattr(main.app.state, "kis_recovery_state", None)
        old_kis_degraded = getattr(main.app.state, "kis_degraded", False)
        main.app.state.kis_recovery_state = state
        main.app.state.kis_degraded = True

        # is_backtest=False 로 강제
        fake_settings = MagicMock()
        fake_settings.kis.is_backtest = False

        # KISClient 생성을 무조건 실패시킴
        failing_client = MagicMock()
        failing_client._token_manager.get_access_token = AsyncMock(side_effect=RuntimeError("token issue down"))

        # _alert_manager 는 lazy import 되므로 모듈 로드 후 patch
        import api.routes.alerts as alerts_module

        original_alert_manager = alerts_module._alert_manager
        mock_alert_manager = MagicMock()
        mock_alert_manager.create_and_persist_alert = AsyncMock()
        alerts_module._alert_manager = mock_alert_manager

        try:
            with (
                patch("main.get_settings", return_value=fake_settings),
                patch("main.KISClient", return_value=failing_client),
            ):
                client = TestClient(main.app, raise_server_exceptions=False)

                # 임계값 도달까지 health 를 threshold 회 호출
                for _ in range(threshold):
                    state.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
                    resp = client.get("/api/system/health")
                    assert resp.status_code == 200
                    body = resp.json()
                    assert body["components"]["kis_api"] == "degraded"

                # 정확히 1회 발송
                assert mock_alert_manager.create_and_persist_alert.await_count == 1
                call_kwargs = mock_alert_manager.create_and_persist_alert.await_args.kwargs
                assert call_kwargs["alert_type"] == AlertType.SYSTEM_ERROR
                assert call_kwargs["level"] == AlertLevel.ERROR
                metadata = call_kwargs["metadata"]
                assert metadata["consecutive_failures"] == threshold
                assert metadata["alert_threshold"] == threshold
                assert "RuntimeError" in metadata["last_error"]

                # 추가 호출 — 중복 발송 없음
                state.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
                client.get("/api/system/health")
                assert mock_alert_manager.create_and_persist_alert.await_count == 1
                assert state.alert_dispatched is True
        finally:
            alerts_module._alert_manager = original_alert_manager
            main.app.state.kis_recovery_state = old_state
            main.app.state.kis_degraded = old_kis_degraded

    @patch("main.MongoDBManager")
    @patch("main.RedisManager")
    def test_recovery_success_resets_alert_state_through_health_check(self, mock_redis, mock_mongo):
        """실패 누적 → 임계값 도달 → 회복 성공 시 alert_dispatched 가 리셋된다."""
        import main

        mock_redis.get_client.return_value.ping = AsyncMock()
        mock_mongo.get_db.return_value.command = AsyncMock()

        threshold = 1
        state = KISRecoveryState(cooldown_seconds=1, alert_threshold=threshold)
        state.degraded = True
        state.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)

        old_state = getattr(main.app.state, "kis_recovery_state", None)
        old_kis_degraded = getattr(main.app.state, "kis_degraded", False)
        old_kis_client = main.kis_client
        main.app.state.kis_recovery_state = state
        main.app.state.kis_degraded = True

        fake_settings = MagicMock()
        fake_settings.kis.is_backtest = False

        # 첫 호출: 실패 → 임계값 1 도달 → 알림 발송
        failing_client = MagicMock()
        failing_client._token_manager.get_access_token = AsyncMock(side_effect=RuntimeError("down"))
        # 두 번째 호출: 성공
        ok_client = MagicMock()
        ok_client._token_manager.get_access_token = AsyncMock(return_value="TOKEN")

        import api.routes.alerts as alerts_module

        original_alert_manager = alerts_module._alert_manager
        mock_alert_manager = MagicMock()
        mock_alert_manager.create_and_persist_alert = AsyncMock()
        alerts_module._alert_manager = mock_alert_manager

        try:
            with (
                patch("main.get_settings", return_value=fake_settings),
                patch("main.KISClient", side_effect=[failing_client, ok_client]),
            ):
                client = TestClient(main.app, raise_server_exceptions=False)

                # 1) 실패 — 알림 발송
                state.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
                client.get("/api/system/health")
                assert mock_alert_manager.create_and_persist_alert.await_count == 1
                assert state.alert_dispatched is True

                # 2) 성공 — 회복 + 상태 리셋
                state.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
                resp = client.get("/api/system/health")
                body = resp.json()
                assert body["components"]["kis_api"] == "healthy"
                assert state.degraded is False
                assert state.consecutive_failures == 0
                assert state.alert_dispatched is False
                # 회복 성공으로 추가 알림 없음
                assert mock_alert_manager.create_and_persist_alert.await_count == 1
        finally:
            alerts_module._alert_manager = original_alert_manager
            main.app.state.kis_recovery_state = old_state
            main.app.state.kis_degraded = old_kis_degraded
            main.kis_client = old_kis_client


if __name__ == "__main__":
    unittest.main()
