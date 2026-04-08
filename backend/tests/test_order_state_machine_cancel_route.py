"""Integration tests: /api/orders/{id} DELETE wiring with OrderStateMachine.

근거: docs/security/security-integrity-roadmap.md §7.3 Wiring Rule.

검증 범위:
    1. PENDING/SUBMITTED/PARTIAL → 200 + status=CANCELLED
    2. FILLED/CANCELLED/FAILED → 409 + INVALID_ORDER_TRANSITION +
       error.context 에 current_status/order_id 노출
    3. DB status 컬럼이 enum 범위 밖(무결성 위반) → 503 + ORDER_STORE_UNAVAILABLE
    4. 존재하지 않는 order_id → 404 + ORDER_NOT_FOUND
    5. viewer 토큰은 RBAC 로 403 (require_operator 확인)
    6. 인라인 하드코딩 비교 대신 OrderStateMachine 단일 진실원천을 사용하는지
       확인 — 거부 시 Prometheus counter 증가가 관측된다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from config.constants import OrderStatus
from core.monitoring.metrics import ORDER_STATE_TRANSITION_REJECTS_TOTAL


def _override_db_for_status(app, status_value, user):
    """get_db_session override — SELECT status 쿼리 시 주어진 값을 반환한다.

    ``status_value`` 가 None 이면 주문 없음(404) 시나리오. User SELECT 쿼리
    (``get_current_user`` 가 사용) 에 대해서는 주입된 ``user`` 를 반환한다.
    """
    from sqlalchemy.sql import Select

    from db.database import get_db_session

    async def _fake_get_db():
        session = AsyncMock()

        async def _execute(query, *args, **kwargs):
            result = MagicMock()
            scalars_obj = MagicMock()
            if isinstance(query, Select):
                scalars_obj.first = MagicMock(return_value=user)
                scalars_obj.all = MagicMock(return_value=[user])
                result.scalars = MagicMock(return_value=scalars_obj)
                return result
            # text() 쿼리 — orders.status
            if status_value is None:
                result.fetchone = MagicMock(return_value=None)
            else:
                result.fetchone = MagicMock(return_value=(status_value,))
            scalars_obj.first = MagicMock(return_value=None)
            scalars_obj.all = MagicMock(return_value=[])
            result.scalars = MagicMock(return_value=scalars_obj)
            return result

        session.execute = AsyncMock(side_effect=_execute)
        session.commit = AsyncMock(return_value=None)
        session.rollback = AsyncMock(return_value=None)
        yield session

    app.dependency_overrides[get_db_session] = _fake_get_db


def _counter_value(from_state: str, to_state: str) -> float:
    return ORDER_STATE_TRANSITION_REJECTS_TOTAL.labels(
        from_state=from_state,
        to_state=to_state,
    )._value.get()


def _bypass_audit(monkeypatch):
    """AuditLogger.log_strict 를 no-op 으로 만들어 DB 의존성을 제거한다."""
    from api.routes import orders as orders_module

    class _FakeAudit:
        def __init__(self, db):  # noqa: D401
            self._db = db

        async def log_strict(self, **kwargs):
            return None

    monkeypatch.setattr(orders_module, "AuditLogger", _FakeAudit)


@pytest.mark.asyncio
class TestCancelOrderStateMachineWiring:
    @pytest.mark.parametrize(
        "status",
        [OrderStatus.PENDING.value, OrderStatus.SUBMITTED.value, OrderStatus.PARTIAL.value],
    )
    async def test_cancellable_status_returns_200(
        self, authenticated_app, operator_token, test_user_operator, monkeypatch, status
    ):
        _override_db_for_status(authenticated_app, status, test_user_operator)
        _bypass_audit(monkeypatch)
        async with AsyncClient(transport=ASGITransport(app=authenticated_app), base_url="http://test") as client:
            resp = await client.delete(
                "/api/orders/ord-abc",
                headers={"Authorization": f"Bearer {operator_token}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["status"] == "CANCELLED"

    @pytest.mark.parametrize(
        "status",
        [OrderStatus.FILLED.value, OrderStatus.CANCELLED.value, OrderStatus.FAILED.value],
    )
    async def test_terminal_status_returns_409(self, authenticated_app, operator_token, test_user_operator, status):
        _override_db_for_status(authenticated_app, status, test_user_operator)
        before = _counter_value(status, OrderStatus.CANCELLED.value)
        async with AsyncClient(transport=ASGITransport(app=authenticated_app), base_url="http://test") as client:
            resp = await client.delete(
                "/api/orders/ord-xyz",
                headers={"Authorization": f"Bearer {operator_token}"},
            )
        after = _counter_value(status, OrderStatus.CANCELLED.value)
        assert resp.status_code == 409
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "INVALID_ORDER_TRANSITION"
        ctx = body["error"].get("context") or {}
        assert ctx.get("current_status") == status
        assert ctx.get("target_status") == OrderStatus.CANCELLED.value
        assert ctx.get("order_id") == "ord-xyz"
        assert after == before + 1

    async def test_unknown_status_returns_503(self, authenticated_app, operator_token, test_user_operator):
        _override_db_for_status(authenticated_app, "WEIRD_VALUE", test_user_operator)
        async with AsyncClient(transport=ASGITransport(app=authenticated_app), base_url="http://test") as client:
            resp = await client.delete(
                "/api/orders/ord-broken",
                headers={"Authorization": f"Bearer {operator_token}"},
            )
        assert resp.status_code == 503
        body = resp.json()
        assert body["error"]["code"] == "ORDER_STORE_UNAVAILABLE"
        assert resp.headers.get("retry-after") == "30"

    async def test_missing_order_returns_404(self, authenticated_app, operator_token, test_user_operator):
        _override_db_for_status(authenticated_app, None, test_user_operator)
        async with AsyncClient(transport=ASGITransport(app=authenticated_app), base_url="http://test") as client:
            resp = await client.delete(
                "/api/orders/ord-none",
                headers={"Authorization": f"Bearer {operator_token}"},
            )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "ORDER_NOT_FOUND"

    async def test_viewer_token_forbidden(self, authenticated_app, viewer_token, test_user_viewer):
        _override_db_for_status(authenticated_app, OrderStatus.PENDING.value, test_user_viewer)
        async with AsyncClient(transport=ASGITransport(app=authenticated_app), base_url="http://test") as client:
            resp = await client.delete(
                "/api/orders/ord-viewer",
                headers={"Authorization": f"Bearer {viewer_token}"},
            )
        assert resp.status_code == 403
