"""TradingGuard kill switch 수동 해제 엔드포인트 통합 테스트.

검증 범위:
    1. ``GET /api/system/kill-switch/status`` — 현재 상태 snapshot 반환.
    2. ``POST /api/system/kill-switch/deactivate``:
       a. ``confirm=false`` → 400 CONFIRM_REQUIRED.
       b. ``confirm=true`` + kill switch on → 감사 선행 → 해제 → ledger 재hydrate.
       c. 감사 fail-closed: ``log_strict`` 실패 시 503 AUDIT_UNAVAILABLE 반환,
          kill switch 는 여전히 on 상태를 유지해야 한다 (해제 금지).
       d. kill switch 가 이미 off 인 경우 — idempotent 하게 감사 + 200 반환.
       e. Ledger 재hydrate 실패해도 해제 자체는 유효 (warning 로그만).
       f. Prometheus gauge ``aqts_trading_guard_kill_switch_active`` 가
          해제 후 0 으로 떨어진다.
    3. Wiring: 해제 경로가 실제로 ``TradingGuard`` 싱글톤을 변경하는지 확인
       (mock 이 아닌 실제 get_trading_guard() 상태 전이 검증).

본 테스트는 §10.17 에서 추가된 수동 해제 경로의 전 사이클 회귀를 차단한다.
RBAC 가드(require_admin/viewer) 는 ``test_rbac_routes.py`` 가 자동으로 커버하므로
여기서는 도메인 로직(감사 fail-closed + ledger 재hydrate + 상태 전이) 에 집중한다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from api.middleware.auth import AuthenticatedUser
from core.trading_guard import get_trading_guard, reset_trading_guard
from db.repositories.audit_log import AuditWriteFailure


def _mock_admin(username: str = "kill_switch_admin") -> AuthenticatedUser:
    return AuthenticatedUser(id=username, username=username, role="admin")


def _mock_viewer(username: str = "kill_switch_viewer") -> AuthenticatedUser:
    return AuthenticatedUser(id=username, username=username, role="viewer")


@pytest.fixture(autouse=True)
def _reset_guard_between_tests():
    """각 테스트 전후로 TradingGuard 싱글톤 재초기화 (상태 격리)."""
    reset_trading_guard()
    yield
    reset_trading_guard()


# ═════════════════════════════════════════════════════════════════════════
# GET /kill-switch/status
# ═════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
class TestKillSwitchStatus:
    async def test_status_returns_off_when_guard_fresh(self):
        """fresh guard: kill_switch_on=False, reason=''."""
        from api.routes.system import get_kill_switch_status

        response = await get_kill_switch_status(current_user=_mock_viewer())

        assert response.success is True
        assert response.data.kill_switch_on is False
        assert response.data.kill_switch_reason == ""

    async def test_status_reflects_activated_state(self):
        """kill switch 활성화 후 상태 조회는 on + reason 을 그대로 반영."""
        from api.routes.system import get_kill_switch_status

        guard = get_trading_guard()
        guard.activate_kill_switch("테스트용 활성화")

        response = await get_kill_switch_status(current_user=_mock_viewer())

        assert response.data.kill_switch_on is True
        assert response.data.kill_switch_reason == "테스트용 활성화"


# ═════════════════════════════════════════════════════════════════════════
# POST /kill-switch/deactivate — confirm 플래그 검증
# ═════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
class TestKillSwitchDeactivateConfirmGate:
    async def test_confirm_false_raises_400(self):
        """confirm=false 면 HTTPException 400, 해제되지 않는다."""
        from api.routes.system import (
            KillSwitchDeactivateRequest,
            deactivate_kill_switch,
        )

        guard = get_trading_guard()
        guard.activate_kill_switch("활성 상태")
        db = MagicMock()

        body = KillSwitchDeactivateRequest(
            reason="10자 이상의 충분한 해제 사유입니다",
            confirm=False,
        )

        with pytest.raises(HTTPException) as exc_info:
            await deactivate_kill_switch(body=body, current_user=_mock_admin(), db=db)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["error_code"] == "CONFIRM_REQUIRED"
        # 해제되지 않고 활성 상태를 유지해야 한다.
        assert guard.state.kill_switch_on is True

    async def test_confirm_required_field_rejects_empty_reason(self):
        """reason 이 10자 미만이면 Pydantic 검증이 ValueError 로 거부한다."""
        from pydantic import ValidationError

        from api.routes.system import KillSwitchDeactivateRequest

        with pytest.raises(ValidationError):
            KillSwitchDeactivateRequest(reason="short", confirm=True)


# ═════════════════════════════════════════════════════════════════════════
# POST /kill-switch/deactivate — 정상 해제 경로
# ═════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
class TestKillSwitchDeactivateHappyPath:
    async def test_deactivate_fires_audit_then_clears_state(self):
        """감사 선행 → 해제 → ledger 재hydrate 순서와 부작용 검증."""
        from api.routes.system import (
            KillSwitchDeactivateRequest,
            deactivate_kill_switch,
        )

        guard = get_trading_guard()
        guard.activate_kill_switch("원인 A")
        assert guard.state.kill_switch_on is True

        audit_log_strict = AsyncMock()
        mock_ledger = MagicMock()
        mock_ledger.repository = MagicMock()  # 영속 계층 주입된 상태
        mock_ledger.hydrate = AsyncMock()
        mock_ledger.get_positions = MagicMock(return_value={"005930": 10.0, "000660": 5.0})

        with (
            patch("api.routes.system.AuditLogger") as mock_audit_cls,
            patch("api.routes.system.get_portfolio_ledger", return_value=mock_ledger),
        ):
            mock_audit = MagicMock()
            mock_audit.log_strict = audit_log_strict
            mock_audit_cls.return_value = mock_audit

            body = KillSwitchDeactivateRequest(
                reason="DEMO 환경 MIDDAY_CHECK mismatch 후 수동 해제",
                confirm=True,
            )
            response = await deactivate_kill_switch(
                body=body,
                current_user=_mock_admin("admin_alice"),
                db=MagicMock(),
            )

        # 1. 감사가 해제 이전에 호출됐다.
        assert audit_log_strict.await_count == 1
        audit_kwargs = audit_log_strict.await_args.kwargs
        assert audit_kwargs["action_type"] == "KILL_SWITCH_DEACTIVATE"
        assert audit_kwargs["module"] == "trading_guard"
        assert audit_kwargs["before_state"]["kill_switch_on"] is True
        assert audit_kwargs["before_state"]["kill_switch_reason"] == "원인 A"
        assert audit_kwargs["after_state"]["kill_switch_on"] is False
        assert audit_kwargs["metadata"]["username"] == "admin_alice"

        # 2. 실제 guard 상태가 해제됐다.
        assert guard.state.kill_switch_on is False
        assert guard.state.kill_switch_reason == ""

        # 3. Ledger 재hydrate 가 호출됐다.
        mock_ledger.hydrate.assert_awaited_once()

        # 4. 응답 body 검증.
        assert response.success is True
        assert response.data.was_on is True
        assert response.data.previous_reason == "원인 A"
        assert response.data.ledger_rehydrated is True
        assert response.data.ledger_positions_count == 2
        assert response.data.operator == "admin_alice"

    async def test_deactivate_idempotent_when_already_off(self):
        """kill switch 가 이미 off 여도 200 + 감사 기록 + was_on=False 반환."""
        from api.routes.system import (
            KillSwitchDeactivateRequest,
            deactivate_kill_switch,
        )

        guard = get_trading_guard()
        assert guard.state.kill_switch_on is False  # fresh

        audit_log_strict = AsyncMock()
        mock_ledger = MagicMock()
        mock_ledger.repository = None  # 영속 계층 미주입 (테스트 모드)
        mock_ledger.get_positions = MagicMock(return_value={})

        with (
            patch("api.routes.system.AuditLogger") as mock_audit_cls,
            patch("api.routes.system.get_portfolio_ledger", return_value=mock_ledger),
        ):
            mock_audit = MagicMock()
            mock_audit.log_strict = audit_log_strict
            mock_audit_cls.return_value = mock_audit

            body = KillSwitchDeactivateRequest(
                reason="상태 확인 후 명시적 off 재확인 절차",
                confirm=True,
            )
            response = await deactivate_kill_switch(
                body=body,
                current_user=_mock_admin("admin_bob"),
                db=MagicMock(),
            )

        assert audit_log_strict.await_count == 1  # 감사 기록 보장.
        assert response.data.was_on is False
        assert response.data.previous_reason == ""
        # repository=None 인 경우 재hydrate 생략되지만 positions_count 는 0.
        assert response.data.ledger_rehydrated is False
        assert response.data.ledger_positions_count == 0


# ═════════════════════════════════════════════════════════════════════════
# POST /kill-switch/deactivate — fail-closed 경로
# ═════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
class TestKillSwitchDeactivateFailClosed:
    async def test_audit_failure_refuses_deactivation(self):
        """log_strict 가 AuditWriteFailure 를 raise 하면 해제하지 않고 503."""
        from api.routes.system import (
            KillSwitchDeactivateRequest,
            deactivate_kill_switch,
        )

        guard = get_trading_guard()
        guard.activate_kill_switch("원인 B")

        with (
            patch("api.routes.system.AuditLogger") as mock_audit_cls,
            patch("api.routes.system.get_portfolio_ledger") as mock_get_ledger,
        ):
            mock_audit = MagicMock()
            mock_audit.log_strict = AsyncMock(side_effect=AuditWriteFailure("KILL_SWITCH_DEACTIVATE"))
            mock_audit_cls.return_value = mock_audit

            body = KillSwitchDeactivateRequest(
                reason="감사 장애 상황에서의 해제 시도 (차단되어야 함)",
                confirm=True,
            )
            with pytest.raises(HTTPException) as exc_info:
                await deactivate_kill_switch(
                    body=body,
                    current_user=_mock_admin(),
                    db=MagicMock(),
                )

            # 503 + AUDIT_UNAVAILABLE 반환.
            assert exc_info.value.status_code == 503
            assert exc_info.value.detail["error_code"] == "AUDIT_UNAVAILABLE"
            # Ledger 재hydrate 는 호출되지 않았어야 한다 (해제 자체가 차단됨).
            mock_get_ledger.assert_not_called()

        # Kill switch 는 여전히 활성 상태.
        assert guard.state.kill_switch_on is True
        assert guard.state.kill_switch_reason == "원인 B"


@pytest.mark.asyncio
class TestKillSwitchDeactivateRehydrateResilience:
    async def test_ledger_rehydrate_failure_does_not_block_deactivation(self):
        """Ledger hydrate 실패는 warning 로그만 남기고 해제는 완료된다."""
        from api.routes.system import (
            KillSwitchDeactivateRequest,
            deactivate_kill_switch,
        )

        guard = get_trading_guard()
        guard.activate_kill_switch("원인 C")

        mock_ledger = MagicMock()
        mock_ledger.repository = MagicMock()
        mock_ledger.hydrate = AsyncMock(side_effect=RuntimeError("DB connection timeout"))
        mock_ledger.get_positions = MagicMock(return_value={})

        with (
            patch("api.routes.system.AuditLogger") as mock_audit_cls,
            patch("api.routes.system.get_portfolio_ledger", return_value=mock_ledger),
        ):
            mock_audit = MagicMock()
            mock_audit.log_strict = AsyncMock()
            mock_audit_cls.return_value = mock_audit

            body = KillSwitchDeactivateRequest(
                reason="재hydrate 실패 시나리오 — 해제 자체는 유효해야 함",
                confirm=True,
            )
            response = await deactivate_kill_switch(
                body=body,
                current_user=_mock_admin(),
                db=MagicMock(),
            )

        # 해제 자체는 성공.
        assert guard.state.kill_switch_on is False
        assert response.success is True
        assert response.data.was_on is True
        # ledger 재hydrate 는 실패로 기록.
        assert response.data.ledger_rehydrated is False


# ═════════════════════════════════════════════════════════════════════════
# Prometheus gauge wiring
# ═════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
class TestKillSwitchGaugeWiring:
    async def test_gauge_drops_to_zero_after_deactivation(self):
        """aqts_trading_guard_kill_switch_active gauge 가 해제 후 0."""
        from api.routes.system import (
            KillSwitchDeactivateRequest,
            deactivate_kill_switch,
        )
        from core.monitoring.metrics import TRADING_GUARD_KILL_SWITCH_ACTIVE

        guard = get_trading_guard()
        guard.activate_kill_switch("원인 D")
        # 활성화 직후 gauge 는 1.
        assert TRADING_GUARD_KILL_SWITCH_ACTIVE._value.get() == 1

        mock_ledger = MagicMock()
        mock_ledger.repository = None
        mock_ledger.get_positions = MagicMock(return_value={})

        with (
            patch("api.routes.system.AuditLogger") as mock_audit_cls,
            patch("api.routes.system.get_portfolio_ledger", return_value=mock_ledger),
        ):
            mock_audit = MagicMock()
            mock_audit.log_strict = AsyncMock()
            mock_audit_cls.return_value = mock_audit

            body = KillSwitchDeactivateRequest(
                reason="gauge wiring 회귀 테스트 — 해제 후 0 이 되어야 함",
                confirm=True,
            )
            await deactivate_kill_switch(
                body=body,
                current_user=_mock_admin(),
                db=MagicMock(),
            )

        # 해제 후 gauge 는 0.
        assert TRADING_GUARD_KILL_SWITCH_ACTIVE._value.get() == 0
