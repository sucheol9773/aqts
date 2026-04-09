"""
AlertManager NotificationRouter wiring 테스트 (Commit 2).

검증 대상:
  1. set_router setter 주입 메커니즘 (set_collection 과 동형)
  2. create_and_persist_alert 의 즉시 디스패치 경로:
     - router 주입 시: dispatch 호출 + 상태 전이 (SENT / FAILED / DEAD)
     - router 미주입 시: dispatch 스킵, in-memory 저장만 동작
  3. 예외 정책: router.dispatch 가 raise 해도 create_and_persist_alert 는
     raise 하지 않고 alert 는 FAILED 로 영속화
  4. 상태 전이 통합: claim_for_sending → dispatch → mark_sent_by_id
     / mark_failed_with_retry 경로가 Commit 1 의 재시도 모델과 정합적으로
     연결되는지

비범위 (Commit 3 로 이월):
  - dispatch_pending_alerts 스케줄러 주기 등록
  - Prometheus 카운터/히스토그램
  - exp backoff
  - meta-alert (AlertPipelineFailureRate)
"""

from dataclasses import dataclass, field
from typing import Optional

import pytest

from config.constants import AlertType
from core.notification.alert_manager import (
    AlertLevel,
    AlertManager,
    AlertStatus,
)


# ══════════════════════════════════════
# 스파이 router — NotificationRouter 인터페이스 복제
# ══════════════════════════════════════
@dataclass
class _FakeDispatchResult:
    """NotificationRouter.dispatch() 반환 타입 복제.

    DispatchResult 의 필드 시그니처를 그대로 따라 duck typing 으로 호환.
    실제 DispatchResult 를 import 해도 무방하나, 테스트 의존성 최소화를
    위해 로컬에 복제한다.
    """

    success: bool
    channel_used: str = ""
    fallback_used: bool = False
    all_failed: bool = False
    channels_tried: list[str] = field(default_factory=list)


class _SpyRouter:
    """dispatch 호출을 기록하고 지정된 결과를 반환하는 스파이.

    - `dispatch_calls`: 호출된 alert 객체 리스트
    - `result`: 반환할 _FakeDispatchResult
    - `raise_exc`: 지정되면 dispatch 시 해당 예외 raise
    """

    def __init__(
        self,
        result: Optional[_FakeDispatchResult] = None,
        raise_exc: Optional[Exception] = None,
    ):
        self.dispatch_calls: list = []
        self.result = result or _FakeDispatchResult(success=True, channel_used="telegram")
        self.raise_exc = raise_exc

    async def dispatch(self, alert):
        self.dispatch_calls.append(alert)
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.result


# ══════════════════════════════════════
# 테스트 fixture
# ══════════════════════════════════════
@pytest.fixture
def alert_manager():
    """in-memory 모드 AlertManager (collection 미주입)."""
    return AlertManager()


# ══════════════════════════════════════
# 1. set_router setter 주입 메커니즘
# ══════════════════════════════════════
class TestSetRouterInjection:
    def test_default_router_is_none(self, alert_manager):
        """기본 생성자는 router 를 None 으로 초기화한다."""
        assert alert_manager._router is None

    def test_set_router_injects_router(self, alert_manager):
        """set_router 로 router 가 주입되어야 한다."""
        spy = _SpyRouter()
        alert_manager.set_router(spy)
        assert alert_manager._router is spy

    def test_set_router_reinjection_replaces(self, alert_manager):
        """재주입 시 이전 router 를 교체해야 한다 (운영 중 토큰 로테이션 시나리오)."""
        spy1 = _SpyRouter()
        spy2 = _SpyRouter()
        alert_manager.set_router(spy1)
        alert_manager.set_router(spy2)
        assert alert_manager._router is spy2
        assert alert_manager._router is not spy1

    def test_set_router_none_disables(self, alert_manager):
        """None 주입으로 디스패치 경로를 비활성화할 수 있다 (테스트 격리 용도)."""
        spy = _SpyRouter()
        alert_manager.set_router(spy)
        alert_manager.set_router(None)
        assert alert_manager._router is None


# ══════════════════════════════════════
# 2. create_and_persist_alert 의 디스패치 경로
# ══════════════════════════════════════
class TestCreateAndPersistDispatch:
    @pytest.mark.asyncio
    async def test_router_not_injected_skips_dispatch(self, alert_manager):
        """router 미주입 시 dispatch 는 호출되지 않고 alert 는 PENDING 상태로 남는다."""
        alert = await alert_manager.create_and_persist_alert(
            alert_type=AlertType.SYSTEM_ERROR,
            level=AlertLevel.ERROR,
            title="test",
            message="router not injected",
        )
        assert alert.status == AlertStatus.PENDING
        assert alert.send_attempts == 0

    @pytest.mark.asyncio
    async def test_router_injected_dispatches_exactly_once(self, alert_manager):
        """router 주입 시 dispatch 가 정확히 1회 호출된다."""
        spy = _SpyRouter(result=_FakeDispatchResult(success=True, channel_used="telegram"))
        alert_manager.set_router(spy)
        alert = await alert_manager.create_and_persist_alert(
            alert_type=AlertType.SYSTEM_ERROR,
            level=AlertLevel.ERROR,
            title="test",
            message="dispatch once",
        )
        assert len(spy.dispatch_calls) == 1
        assert spy.dispatch_calls[0].id == alert.id

    @pytest.mark.asyncio
    async def test_successful_dispatch_marks_sent(self, alert_manager):
        """DispatchResult.success=True → 상태가 SENT 로 전이되고 sent_at 이 기록된다."""
        spy = _SpyRouter(result=_FakeDispatchResult(success=True, channel_used="telegram"))
        alert_manager.set_router(spy)
        alert = await alert_manager.create_and_persist_alert(
            alert_type=AlertType.SYSTEM_ERROR,
            level=AlertLevel.ERROR,
            title="success",
            message="should be SENT",
        )
        assert alert.status == AlertStatus.SENT
        assert alert.sent_at is not None
        assert alert.send_attempts == 1

    @pytest.mark.asyncio
    async def test_all_channels_failed_marks_failed(self, alert_manager):
        """DispatchResult.success=False, all_failed=True → 상태가 FAILED 로 전이된다.

        첫 시도 실패이므로 send_attempts=1 < max_attempts=3 → FAILED (not DEAD).
        """
        spy = _SpyRouter(
            result=_FakeDispatchResult(
                success=False,
                channel_used="",
                all_failed=True,
                channels_tried=["telegram", "file", "console"],
            )
        )
        alert_manager.set_router(spy)
        alert = await alert_manager.create_and_persist_alert(
            alert_type=AlertType.SYSTEM_ERROR,
            level=AlertLevel.ERROR,
            title="failed",
            message="all channels failed",
        )
        assert alert.status == AlertStatus.FAILED
        assert alert.send_attempts == 1
        assert alert.last_send_error is not None
        assert "telegram" in alert.last_send_error

    @pytest.mark.asyncio
    async def test_dispatch_exception_is_swallowed(self, alert_manager):
        """router.dispatch 가 raise 해도 create_and_persist_alert 는 raise 하지 않는다.

        원인 이벤트 처리 경로를 보호하는 핵심 보장.
        """
        spy = _SpyRouter(raise_exc=RuntimeError("network down"))
        alert_manager.set_router(spy)

        # 예외가 raise 되지 않아야 함
        alert = await alert_manager.create_and_persist_alert(
            alert_type=AlertType.SYSTEM_ERROR,
            level=AlertLevel.ERROR,
            title="boom",
            message="router crashed",
        )

        # alert 는 여전히 생성되고 FAILED 로 전이돼야 함
        assert alert.status == AlertStatus.FAILED
        assert alert.send_attempts == 1
        assert alert.last_send_error is not None
        assert "network down" in alert.last_send_error

    @pytest.mark.asyncio
    async def test_alert_still_persisted_on_dispatch_failure(self, alert_manager):
        """dispatch 실패해도 alert 는 조회 가능해야 한다 (at-least-once 기반).

        in-memory 모드에서는 _in_memory_alerts 에 남아있어야 하고,
        get_alert_by_id 로 조회 가능해야 한다.
        """
        spy = _SpyRouter(result=_FakeDispatchResult(success=False, all_failed=True, channels_tried=["telegram"]))
        alert_manager.set_router(spy)
        alert = await alert_manager.create_and_persist_alert(
            alert_type=AlertType.SYSTEM_ERROR,
            level=AlertLevel.ERROR,
            title="persisted",
            message="should survive dispatch failure",
        )
        fetched = await alert_manager.get_alert_by_id(alert.id)
        assert fetched is not None
        assert fetched["id"] == alert.id
        assert fetched["status"] == AlertStatus.FAILED.value


# ══════════════════════════════════════
# 3. Commit 1 재시도 모델과의 정합성
# ══════════════════════════════════════
class TestRetryModelIntegration:
    @pytest.mark.asyncio
    async def test_send_attempts_incremented_on_dispatch(self, alert_manager):
        """dispatch 1회당 send_attempts 가 +1 증가해야 한다 (claim_for_sending 경유)."""
        spy = _SpyRouter(result=_FakeDispatchResult(success=True))
        alert_manager.set_router(spy)
        alert = await alert_manager.create_and_persist_alert(
            alert_type=AlertType.SYSTEM_ERROR,
            level=AlertLevel.ERROR,
            title="t",
            message="m",
        )
        assert alert.send_attempts == 1

    @pytest.mark.asyncio
    async def test_last_send_attempt_at_set_on_dispatch(self, alert_manager):
        """dispatch 직전 last_send_attempt_at 이 기록되어야 한다."""
        spy = _SpyRouter(result=_FakeDispatchResult(success=True))
        alert_manager.set_router(spy)
        alert = await alert_manager.create_and_persist_alert(
            alert_type=AlertType.SYSTEM_ERROR,
            level=AlertLevel.ERROR,
            title="t",
            message="m",
        )
        assert alert.last_send_attempt_at is not None


# ══════════════════════════════════════
# 4. main.py lifespan wiring smoke (import 가능 여부만 검증)
# ══════════════════════════════════════
class TestLifespanWiringImports:
    def test_fallback_notifier_imports(self):
        """main.py 가 사용하는 컴포넌트가 import 가능해야 한다."""
        from core.notification.fallback_notifier import (
            ConsoleNotifier,
            FileNotifier,
            NotificationRouter,
        )

        assert NotificationRouter is not None
        assert FileNotifier is not None
        assert ConsoleNotifier is not None

    def test_telegram_adapter_imports(self):
        """TelegramChannelAdapter 가 import 가능해야 한다."""
        from core.notification.telegram_adapter import TelegramChannelAdapter

        assert TelegramChannelAdapter is not None

    def test_alert_manager_singleton_import(self):
        """api.routes.alerts._alert_manager 싱글톤이 import 가능해야 한다."""
        from api.routes.alerts import _alert_manager

        assert _alert_manager is not None
        assert hasattr(_alert_manager, "set_router")
        assert hasattr(_alert_manager, "set_collection")
