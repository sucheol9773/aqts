"""/api/alerts RBAC 회귀 테스트 (dependency_overrides 우회 없음).

근거: docs/security/security-integrity-roadmap.md §7.1 — "/api/alerts 권한
경계 회귀 테스트 (e2e dependency_overrides 우회 보완)".

기존 `tests/test_alerts_route_e2e.py` 는 `require_viewer` / `require_operator`
의존성을 `dependency_overrides` 로 우회한 뒤 라우트 본문 동작만 검증한다.
이 테스트 파일은 그 우회 없이 **실제 RBAC 가드**를 통과시켜 다음 불변식을
검증한다:

1.  GET `/api/alerts/` 와 GET `/api/alerts/stats` 는
    - 토큰 없이 호출 → 401
    - viewer/operator/admin 토큰 → 200 (읽기 허용)

2.  PUT `/api/alerts/{id}/read` 와 PUT `/api/alerts/read-all` 는
    - 토큰 없이 호출 → 401
    - viewer 토큰 → 403 (read-only 역할은 mutation 금지)
    - operator/admin 토큰 → 200 (mutation 허용)

즉 RBAC 가드가 실제로 작동하는지 (`require_viewer` 가 GET 을 허용하고,
`require_operator` 가 viewer 를 막고, 모두 인증을 요구하는지) 를 라우트
수준에서 끝에서 끝까지 검증한다.

주의:
    - `authenticated_app` fixture 는 get_current_user 가 기대하는 DB 재확인
      경로를 이미 목킹해 두므로 (admin/operator/viewer UUID 는 fixture 안에
      준비), 토큰만 유효하면 RBAC 가드까지 그대로 통과한다.
    - `AlertManager` 는 dependency override 로 fake 를 주입하여 Mongo 없이도
      route handler 가 정상 동작하도록 한다. 이 override 는 **AlertManager
      주입 지점만** 우회할 뿐, RBAC 가드는 우회하지 않는다.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from api.routes.alerts import get_alert_manager
from config.constants import AlertType
from core.notification.alert_manager import AlertLevel, AlertManager


async def _prepare_manager_with_one_alert() -> AlertManager:
    manager = AlertManager()
    alert = await manager.create_and_persist_alert(
        alert_type=AlertType.SYSTEM_ERROR,
        level=AlertLevel.ERROR,
        title="rbac-test alert",
        message="m",
    )
    # 테스트가 alert.id 를 참조할 수 있도록 manager 에 메모.
    manager._rbac_test_alert_id = alert.id  # type: ignore[attr-defined]
    return manager


def _inject_manager(app, manager: AlertManager) -> None:
    app.dependency_overrides[get_alert_manager] = lambda: manager


def _clear_manager_override(app) -> None:
    app.dependency_overrides.pop(get_alert_manager, None)


@pytest.mark.asyncio
class TestAlertsReadRoutesRBAC:
    """GET /api/alerts/ 와 /stats 의 RBAC 동작."""

    async def test_no_token_returns_401_on_list(self, authenticated_app):
        manager = await _prepare_manager_with_one_alert()
        _inject_manager(authenticated_app, manager)
        try:
            transport = ASGITransport(app=authenticated_app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/alerts/")
            assert resp.status_code == 401, resp.text
        finally:
            _clear_manager_override(authenticated_app)

    async def test_no_token_returns_401_on_stats(self, authenticated_app):
        manager = await _prepare_manager_with_one_alert()
        _inject_manager(authenticated_app, manager)
        try:
            transport = ASGITransport(app=authenticated_app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/alerts/stats")
            assert resp.status_code == 401, resp.text
        finally:
            _clear_manager_override(authenticated_app)

    @pytest.mark.parametrize("role_token_fixture", ["viewer_token", "operator_token", "admin_token"])
    async def test_list_allowed_for_all_roles(self, authenticated_app, request, role_token_fixture):
        token = request.getfixturevalue(role_token_fixture)
        manager = await _prepare_manager_with_one_alert()
        _inject_manager(authenticated_app, manager)
        try:
            transport = ASGITransport(app=authenticated_app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/api/alerts/",
                    headers={"Authorization": f"Bearer {token}"},
                )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["success"] is True
            assert body["data"]["unread_count"] == 1
            assert len(body["data"]["alerts"]) == 1
        finally:
            _clear_manager_override(authenticated_app)

    @pytest.mark.parametrize("role_token_fixture", ["viewer_token", "operator_token", "admin_token"])
    async def test_stats_allowed_for_all_roles(self, authenticated_app, request, role_token_fixture):
        token = request.getfixturevalue(role_token_fixture)
        manager = await _prepare_manager_with_one_alert()
        _inject_manager(authenticated_app, manager)
        try:
            transport = ASGITransport(app=authenticated_app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    "/api/alerts/stats",
                    headers={"Authorization": f"Bearer {token}"},
                )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["success"] is True
            assert body["data"]["total"] == 1
            assert body["data"]["unread"] == 1
        finally:
            _clear_manager_override(authenticated_app)


@pytest.mark.asyncio
class TestAlertsMutationRoutesRBAC:
    """PUT /{id}/read 와 PUT /read-all 의 RBAC 동작."""

    async def test_mark_read_no_token_returns_401(self, authenticated_app):
        manager = await _prepare_manager_with_one_alert()
        alert_id = manager._rbac_test_alert_id  # type: ignore[attr-defined]
        _inject_manager(authenticated_app, manager)
        try:
            transport = ASGITransport(app=authenticated_app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.put(f"/api/alerts/{alert_id}/read")
            assert resp.status_code == 401, resp.text
        finally:
            _clear_manager_override(authenticated_app)

    async def test_mark_read_viewer_token_returns_403(self, authenticated_app, viewer_token):
        manager = await _prepare_manager_with_one_alert()
        alert_id = manager._rbac_test_alert_id  # type: ignore[attr-defined]
        _inject_manager(authenticated_app, manager)
        try:
            transport = ASGITransport(app=authenticated_app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.put(
                    f"/api/alerts/{alert_id}/read",
                    headers={"Authorization": f"Bearer {viewer_token}"},
                )
            assert resp.status_code == 403, resp.text
        finally:
            _clear_manager_override(authenticated_app)

    @pytest.mark.parametrize("role_token_fixture", ["operator_token", "admin_token"])
    async def test_mark_read_allowed_for_operator_and_admin(self, authenticated_app, request, role_token_fixture):
        token = request.getfixturevalue(role_token_fixture)
        manager = await _prepare_manager_with_one_alert()
        alert_id = manager._rbac_test_alert_id  # type: ignore[attr-defined]
        _inject_manager(authenticated_app, manager)
        try:
            transport = ASGITransport(app=authenticated_app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.put(
                    f"/api/alerts/{alert_id}/read",
                    headers={"Authorization": f"Bearer {token}"},
                )
            assert resp.status_code == 200, resp.text
            assert resp.json()["success"] is True
        finally:
            _clear_manager_override(authenticated_app)

    async def test_read_all_no_token_returns_401(self, authenticated_app):
        manager = await _prepare_manager_with_one_alert()
        _inject_manager(authenticated_app, manager)
        try:
            transport = ASGITransport(app=authenticated_app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.put("/api/alerts/read-all")
            assert resp.status_code == 401, resp.text
        finally:
            _clear_manager_override(authenticated_app)

    async def test_read_all_viewer_token_returns_403(self, authenticated_app, viewer_token):
        manager = await _prepare_manager_with_one_alert()
        _inject_manager(authenticated_app, manager)
        try:
            transport = ASGITransport(app=authenticated_app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.put(
                    "/api/alerts/read-all",
                    headers={"Authorization": f"Bearer {viewer_token}"},
                )
            assert resp.status_code == 403, resp.text
        finally:
            _clear_manager_override(authenticated_app)

    @pytest.mark.parametrize("role_token_fixture", ["operator_token", "admin_token"])
    async def test_read_all_allowed_for_operator_and_admin(self, authenticated_app, request, role_token_fixture):
        token = request.getfixturevalue(role_token_fixture)
        manager = await _prepare_manager_with_one_alert()
        _inject_manager(authenticated_app, manager)
        try:
            transport = ASGITransport(app=authenticated_app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.put(
                    "/api/alerts/read-all",
                    headers={"Authorization": f"Bearer {token}"},
                )
            assert resp.status_code == 200, resp.text
            assert resp.json()["success"] is True
            assert resp.json()["data"]["marked_count"] == 1
        finally:
            _clear_manager_override(authenticated_app)
