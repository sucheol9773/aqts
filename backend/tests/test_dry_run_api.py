"""
드라이런 API 라우터 테스트

테스트 대상:
  1. POST /api/system/dry-run/start — 세션 시작
  2. POST /api/system/dry-run/stop — 세션 종료
  3. GET  /api/system/dry-run/status — 상태 조회
  4. GET  /api/system/dry-run/report — 리포트 조회
  5. GET  /api/system/dry-run/sessions/{id} — 세션 상세
  6. DELETE /api/system/dry-run/sessions — 세션 초기화
"""

from unittest.mock import patch

import pytest

from config.constants import Market, OrderSide
from core.dry_run.engine import (
    DryRunEngine,
)


@pytest.fixture
def dry_run_engine():
    """테스트용 DryRunEngine (매 테스트마다 새로 생성)"""
    engine = DryRunEngine()
    with patch("api.routes.dry_run.get_dry_run_engine", return_value=engine):
        yield engine


@pytest.fixture
def auth_override():
    """인증 미들웨어 우회"""
    with patch("api.routes.dry_run.get_current_user", return_value="test_user"):
        yield


class TestStartDryRun:
    """POST /start 테스트"""

    @pytest.mark.asyncio
    async def test_start_session(self, dry_run_engine, auth_override):
        """세션 시작 성공"""
        from api.routes.dry_run import start_dry_run

        response = await start_dry_run(current_user="test_user")
        assert response.success is True
        assert "session_id" in response.data
        assert response.data["status"] == "RUNNING"
        assert dry_run_engine.current_session is not None

    @pytest.mark.asyncio
    async def test_start_duplicate_session(self, dry_run_engine, auth_override):
        """중복 세션 시작 시 409 에러"""
        from fastapi import HTTPException

        from api.routes.dry_run import start_dry_run

        await start_dry_run(current_user="test_user")

        with pytest.raises(HTTPException) as exc_info:
            await start_dry_run(current_user="test_user")
        assert exc_info.value.status_code == 409


class TestStopDryRun:
    """POST /stop 테스트"""

    @pytest.mark.asyncio
    async def test_stop_session(self, dry_run_engine, auth_override):
        """세션 정상 종료"""
        from api.routes.dry_run import start_dry_run, stop_dry_run

        await start_dry_run(current_user="test_user")
        response = await stop_dry_run(current_user="test_user")

        assert response.success is True
        assert response.data["status"] == "COMPLETED"
        assert dry_run_engine.current_session is None

    @pytest.mark.asyncio
    async def test_stop_no_active_session(self, dry_run_engine, auth_override):
        """활성 세션 없이 종료 시 404"""
        from fastapi import HTTPException

        from api.routes.dry_run import stop_dry_run

        with pytest.raises(HTTPException) as exc_info:
            await stop_dry_run(current_user="test_user")
        assert exc_info.value.status_code == 404


class TestGetStatus:
    """GET /status 테스트"""

    @pytest.mark.asyncio
    async def test_status_active_session(self, dry_run_engine, auth_override):
        """활성 세션 상태 조회"""
        from api.routes.dry_run import get_dry_run_status, start_dry_run

        await start_dry_run(current_user="test_user")
        response = await get_dry_run_status(current_user="test_user")

        assert response.success is True
        assert response.data["active"] is True
        assert "session" in response.data

    @pytest.mark.asyncio
    async def test_status_no_session(self, dry_run_engine, auth_override):
        """세션 없을 때 상태 조회"""
        from api.routes.dry_run import get_dry_run_status

        response = await get_dry_run_status(current_user="test_user")

        assert response.success is True
        assert response.data["active"] is False
        assert response.data["total_sessions"] == 0

    @pytest.mark.asyncio
    async def test_status_after_completion(self, dry_run_engine, auth_override):
        """완료 후 상태 조회 — 마지막 세션 정보 반환"""
        from api.routes.dry_run import (
            get_dry_run_status,
            start_dry_run,
            stop_dry_run,
        )

        await start_dry_run(current_user="test_user")
        await stop_dry_run(current_user="test_user")
        response = await get_dry_run_status(current_user="test_user")

        assert response.data["active"] is False
        assert "last_session" in response.data
        assert response.data["total_sessions"] == 1


class TestGetReport:
    """GET /report 테스트"""

    @pytest.mark.asyncio
    async def test_report_empty(self, dry_run_engine, auth_override):
        """빈 리포트"""
        from api.routes.dry_run import get_dry_run_report

        response = await get_dry_run_report(current_user="test_user")
        assert response.success is True
        assert response.data["total_sessions"] == 0
        assert response.data["total_orders"] == 0

    @pytest.mark.asyncio
    async def test_report_with_data(self, dry_run_engine, auth_override):
        """데이터 포함 리포트"""
        from api.routes.dry_run import get_dry_run_report

        dry_run_engine.start_session()
        dry_run_engine.record_order(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=10,
        )
        dry_run_engine.end_session()

        response = await get_dry_run_report(current_user="test_user")
        assert response.data["total_sessions"] == 1
        assert response.data["total_orders"] == 1


class TestGetSession:
    """GET /sessions/{id} 테스트"""

    @pytest.mark.asyncio
    async def test_get_session(self, dry_run_engine, auth_override):
        """세션 상세 조회"""
        from api.routes.dry_run import get_dry_run_session

        session = dry_run_engine.start_session()
        sid = session.session_id
        dry_run_engine.end_session()

        response = await get_dry_run_session(session_id=sid, current_user="test_user")
        assert response.success is True
        assert response.data["session_id"] == sid

    @pytest.mark.asyncio
    async def test_get_session_not_found(self, dry_run_engine, auth_override):
        """존재하지 않는 세션 조회 시 404"""
        from fastapi import HTTPException

        from api.routes.dry_run import get_dry_run_session

        with pytest.raises(HTTPException) as exc_info:
            await get_dry_run_session(session_id="nonexistent", current_user="test_user")
        assert exc_info.value.status_code == 404


class TestClearSessions:
    """DELETE /sessions 테스트"""

    @pytest.mark.asyncio
    async def test_clear_sessions(self, dry_run_engine, auth_override):
        """세션 초기화"""
        from api.routes.dry_run import clear_dry_run_sessions

        dry_run_engine.start_session()
        dry_run_engine.end_session()
        dry_run_engine.start_session()
        dry_run_engine.end_session()

        response = await clear_dry_run_sessions(current_user="test_user")
        assert response.success is True
        assert response.data["deleted_sessions"] == 2
        assert dry_run_engine.sessions == []

    @pytest.mark.asyncio
    async def test_clear_with_active_session(self, dry_run_engine, auth_override):
        """활성 세션 있을 때 초기화 시도 → 409"""
        from fastapi import HTTPException

        from api.routes.dry_run import clear_dry_run_sessions

        dry_run_engine.start_session()

        with pytest.raises(HTTPException) as exc_info:
            await clear_dry_run_sessions(current_user="test_user")
        assert exc_info.value.status_code == 409
