"""KIS degraded → healthy 자동 복원 로직 단위 테스트.

검증 대상:
    - 쿨다운 미만이면 시도하지 않음 (None 반환)
    - 쿨다운 만료 후 성공하면 새 KISClient 반환 + degraded 해제
    - 실패하면 next_attempt_at 재스케줄 + degraded 유지
    - 동시 호출이 들어와도 단 한 번만 실제 발급 시도 (asyncio.Lock 직렬화)
    - degraded 가 아닌 상태에서는 즉시 None 반환
    - mark_degraded / mark_recovered 카운터 일관성
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from core.data_collector.kis_client import KISAPIError, KISClient
from core.data_collector.kis_recovery import (
    DEFAULT_COOLDOWN_SECONDS,
    KISRecoveryState,
    try_recover_kis,
)


class TestKISRecoveryState:
    """KISRecoveryState 메서드 단위 테스트."""

    def test_default_state_is_healthy(self):
        state = KISRecoveryState()
        assert state.degraded is False
        assert state.next_attempt_at is None
        assert state.attempt_count == 0
        assert state.recovery_count == 0
        assert state.cooldown_seconds == DEFAULT_COOLDOWN_SECONDS

    def test_mark_degraded_sets_next_attempt(self):
        state = KISRecoveryState(cooldown_seconds=60)
        now = datetime(2026, 4, 7, 12, 0, 0)
        state.mark_degraded("EGW00133: rate limit", now=now)

        assert state.degraded is True
        assert state.last_error == "EGW00133: rate limit"
        assert state.next_attempt_at == now + timedelta(seconds=60)

    def test_mark_recovered_clears_state_and_increments_count(self):
        state = KISRecoveryState()
        state.mark_degraded("err", now=datetime(2026, 4, 7, 12, 0, 0))
        state.mark_recovered()

        assert state.degraded is False
        assert state.last_error is None
        assert state.next_attempt_at is None
        assert state.recovery_count == 1


class TestTryRecoverKIS:
    """try_recover_kis() 의 핵심 동작 검증."""

    @pytest.mark.asyncio
    async def test_returns_none_when_not_degraded(self):
        state = KISRecoveryState()
        factory = AsyncMock(return_value=AsyncMock(spec=KISClient))

        result = await try_recover_kis(state, factory)

        assert result is None
        factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_within_cooldown(self):
        now = datetime(2026, 4, 7, 12, 0, 0)
        state = KISRecoveryState(cooldown_seconds=60)
        state.mark_degraded("err", now=now)
        # 30초 후 호출 — 쿨다운 60초 미만
        factory = AsyncMock(return_value=AsyncMock(spec=KISClient))

        result = await try_recover_kis(state, factory, now=now + timedelta(seconds=30))

        assert result is None
        factory.assert_not_called()
        assert state.degraded is True
        assert state.attempt_count == 0

    @pytest.mark.asyncio
    async def test_recovery_success_replaces_client_and_clears_degraded(self):
        now = datetime(2026, 4, 7, 12, 0, 0)
        state = KISRecoveryState(cooldown_seconds=60)
        state.mark_degraded("err", now=now)

        new_client = AsyncMock(spec=KISClient)
        factory = AsyncMock(return_value=new_client)

        result = await try_recover_kis(state, factory, now=now + timedelta(seconds=61))

        assert result is new_client
        factory.assert_awaited_once()
        assert state.degraded is False
        assert state.next_attempt_at is None
        assert state.last_error is None
        assert state.attempt_count == 1
        assert state.recovery_count == 1

    @pytest.mark.asyncio
    async def test_recovery_failure_reschedules_and_keeps_degraded(self):
        now = datetime(2026, 4, 7, 12, 0, 0)
        state = KISRecoveryState(cooldown_seconds=60)
        state.mark_degraded("first", now=now)

        factory = AsyncMock(side_effect=KISAPIError(code="EGW00133", message="rate limit"))

        attempt_time = now + timedelta(seconds=61)
        result = await try_recover_kis(state, factory, now=attempt_time)

        assert result is None
        assert state.degraded is True
        assert state.recovery_count == 0
        assert state.attempt_count == 1
        assert state.last_error == "EGW00133: rate limit"
        assert state.next_attempt_at == attempt_time + timedelta(seconds=60)

    @pytest.mark.asyncio
    async def test_recovery_failure_with_generic_exception_uses_type_name(self):
        now = datetime(2026, 4, 7, 12, 0, 0)
        state = KISRecoveryState(cooldown_seconds=60)
        state.mark_degraded("first", now=now)

        factory = AsyncMock(side_effect=RuntimeError("network down"))

        result = await try_recover_kis(state, factory, now=now + timedelta(seconds=61))

        assert result is None
        assert state.degraded is True
        assert state.attempt_count == 1
        assert state.last_error == "RuntimeError: network down"

    @pytest.mark.asyncio
    async def test_concurrent_recovery_serializes_to_single_attempt(self):
        """동시 health 호출이 들어와도 lock 으로 단 한 번만 시도해야 한다.

        쿨다운이 이미 만료된 상태에서 두 코루틴이 동시에 try_recover_kis 를 호출.
        첫 번째가 lock 을 잡고 성공/복원하면, 두 번째는 lock 진입 후 degraded=False
        를 보고 None 을 반환해야 한다 (factory 는 정확히 1번만 호출).
        """
        now = datetime(2026, 4, 7, 12, 0, 0)
        state = KISRecoveryState(cooldown_seconds=60)
        state.mark_degraded("err", now=now)

        new_client = AsyncMock(spec=KISClient)
        call_count = 0
        gate = asyncio.Event()

        async def slow_factory():
            nonlocal call_count
            call_count += 1
            await gate.wait()  # 첫 호출자가 lock 을 점유한 상태에서 두 번째가 진입
            return new_client

        attempt_time = now + timedelta(seconds=61)
        task1 = asyncio.create_task(try_recover_kis(state, slow_factory, now=attempt_time))
        # task1 이 lock 을 잡고 factory 안에서 대기하도록 양보
        await asyncio.sleep(0)
        task2 = asyncio.create_task(try_recover_kis(state, slow_factory, now=attempt_time))
        await asyncio.sleep(0)
        gate.set()

        result1, result2 = await asyncio.gather(task1, task2)

        # task1 이 새 클라이언트를 받았고, task2 는 lock 진입 후 degraded=False 를 보고 None
        assert call_count == 1
        assert (result1 is new_client and result2 is None) or (result2 is new_client and result1 is None)
        assert state.degraded is False
        assert state.recovery_count == 1
        assert state.attempt_count == 1

    @pytest.mark.asyncio
    async def test_attempt_then_cooldown_then_retry_succeeds(self):
        """첫 시도 실패 → 쿨다운 → 두 번째 시도 성공 시나리오."""
        now = datetime(2026, 4, 7, 12, 0, 0)
        state = KISRecoveryState(cooldown_seconds=60)
        state.mark_degraded("first", now=now)

        new_client = AsyncMock(spec=KISClient)
        factory = AsyncMock(
            side_effect=[
                KISAPIError(code="EGW00133", message="rate limit"),
                new_client,
            ]
        )

        # 1차 시도 (61초 후) — 실패
        first_attempt = now + timedelta(seconds=61)
        first = await try_recover_kis(state, factory, now=first_attempt)
        assert first is None
        assert state.attempt_count == 1
        assert state.next_attempt_at == first_attempt + timedelta(seconds=60)

        # 두 번째 시도 — 쿨다운 미만이면 None
        too_early = first_attempt + timedelta(seconds=30)
        second_early = await try_recover_kis(state, factory, now=too_early)
        assert second_early is None
        assert state.attempt_count == 1  # factory 호출 안됨

        # 세 번째 시도 — 쿨다운 만료 후 성공
        ok_time = first_attempt + timedelta(seconds=61)
        recovered = await try_recover_kis(state, factory, now=ok_time)
        assert recovered is new_client
        assert state.degraded is False
        assert state.attempt_count == 2
        assert state.recovery_count == 1


class TestKISRecoveryMetrics:
    """Prometheus 메트릭 갱신 검증.

    Counter/Gauge 의 _value._value 직접 접근은 prometheus_client 의 내부 API 이지만
    공식 문서에서도 단위 테스트 용도로 허용된 패턴이다 (get_sample_value 우회).
    """

    def _read_counter(self, counter):
        from prometheus_client import REGISTRY

        return REGISTRY.get_sample_value(counter._name + "_total") or 0.0

    def _read_gauge(self, gauge):
        from prometheus_client import REGISTRY

        return REGISTRY.get_sample_value(gauge._name) or 0.0

    def test_mark_degraded_sets_kis_degraded_gauge_to_1(self):
        from core.monitoring.metrics import KIS_DEGRADED

        KIS_DEGRADED.set(0)
        state = KISRecoveryState(cooldown_seconds=60)
        state.mark_degraded("err", now=datetime(2026, 4, 7, 12, 0, 0))

        assert self._read_gauge(KIS_DEGRADED) == 1.0

    def test_mark_recovered_sets_kis_degraded_gauge_to_0_and_increments_success(self):
        from core.monitoring.metrics import KIS_DEGRADED, KIS_RECOVERY_SUCCESS_TOTAL

        before = self._read_counter(KIS_RECOVERY_SUCCESS_TOTAL)
        KIS_DEGRADED.set(1)
        state = KISRecoveryState()
        state.mark_degraded("err", now=datetime(2026, 4, 7, 12, 0, 0))
        state.mark_recovered()

        assert self._read_gauge(KIS_DEGRADED) == 0.0
        assert self._read_counter(KIS_RECOVERY_SUCCESS_TOTAL) == before + 1.0

    @pytest.mark.asyncio
    async def test_try_recover_increments_attempts_counter_on_real_attempt(self):
        from core.monitoring.metrics import KIS_RECOVERY_ATTEMPTS_TOTAL

        before = self._read_counter(KIS_RECOVERY_ATTEMPTS_TOTAL)

        now = datetime(2026, 4, 7, 12, 0, 0)
        state = KISRecoveryState(cooldown_seconds=60)
        state.mark_degraded("err", now=now)

        new_client = AsyncMock(spec=KISClient)
        factory = AsyncMock(return_value=new_client)

        await try_recover_kis(state, factory, now=now + timedelta(seconds=61))

        assert self._read_counter(KIS_RECOVERY_ATTEMPTS_TOTAL) == before + 1.0

    @pytest.mark.asyncio
    async def test_try_recover_does_not_increment_when_in_cooldown(self):
        from core.monitoring.metrics import KIS_RECOVERY_ATTEMPTS_TOTAL

        now = datetime(2026, 4, 7, 12, 0, 0)
        state = KISRecoveryState(cooldown_seconds=60)
        state.mark_degraded("err", now=now)

        before = self._read_counter(KIS_RECOVERY_ATTEMPTS_TOTAL)
        factory = AsyncMock()
        await try_recover_kis(state, factory, now=now + timedelta(seconds=10))

        # 쿨다운 미만이면 메트릭 카운터도 증가하지 않아야 한다.
        assert self._read_counter(KIS_RECOVERY_ATTEMPTS_TOTAL) == before
        factory.assert_not_called()


class TestKISRecoveryAlerting:
    """연속 실패 시 운영자 알림 콜백 검증."""

    @pytest.mark.asyncio
    async def test_alert_dispatched_when_consecutive_failures_reach_threshold(self):
        now = datetime(2026, 4, 7, 12, 0, 0)
        state = KISRecoveryState(cooldown_seconds=60, alert_threshold=3)
        state.mark_degraded("err", now=now)

        factory = AsyncMock(side_effect=KISAPIError(code="EGW00133", message="rate limit"))
        callback = AsyncMock()

        # 3회 실패 누적 → 임계값 도달 시 1회 발송
        for i in range(3):
            t = now + timedelta(seconds=61 + i * 61)
            state.next_attempt_at = t  # 즉시 시도 가능하도록
            await try_recover_kis(state, factory, now=t, alert_callback=callback)

        assert state.consecutive_failures == 3
        assert state.alert_dispatched is True
        callback.assert_awaited_once()
        # callback 인자가 state 본인인지 확인
        assert callback.await_args.args[0] is state

    @pytest.mark.asyncio
    async def test_alert_not_re_dispatched_on_subsequent_failures(self):
        now = datetime(2026, 4, 7, 12, 0, 0)
        state = KISRecoveryState(cooldown_seconds=60, alert_threshold=2)
        state.mark_degraded("err", now=now)

        factory = AsyncMock(side_effect=RuntimeError("boom"))
        callback = AsyncMock()

        # 5회 연속 실패 — 임계값(2) 도달 후에도 추가 발송 없어야 함
        for i in range(5):
            t = now + timedelta(seconds=61 + i * 61)
            state.next_attempt_at = t
            await try_recover_kis(state, factory, now=t, alert_callback=callback)

        assert state.consecutive_failures == 5
        assert state.alert_dispatched is True
        callback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_alert_state_reset_on_successful_recovery(self):
        now = datetime(2026, 4, 7, 12, 0, 0)
        state = KISRecoveryState(cooldown_seconds=60, alert_threshold=2)
        state.mark_degraded("err", now=now)

        new_client = AsyncMock(spec=KISClient)
        factory = AsyncMock(
            side_effect=[
                KISAPIError(code="EGW00133", message="rl"),
                KISAPIError(code="EGW00133", message="rl"),
                new_client,
            ]
        )
        callback = AsyncMock()

        # 2회 실패 → 알림 발송
        for i in range(2):
            t = now + timedelta(seconds=61 + i * 61)
            state.next_attempt_at = t
            await try_recover_kis(state, factory, now=t, alert_callback=callback)
        assert state.alert_dispatched is True
        assert state.consecutive_failures == 2

        # 3번째 시도 — 성공
        t3 = now + timedelta(seconds=61 + 2 * 61)
        state.next_attempt_at = t3
        result = await try_recover_kis(state, factory, now=t3, alert_callback=callback)

        assert result is new_client
        assert state.consecutive_failures == 0
        assert state.alert_dispatched is False
        callback.assert_awaited_once()  # 회복 성공으로 추가 발송 없음

    @pytest.mark.asyncio
    async def test_alert_callback_exception_does_not_break_recovery_path(self):
        now = datetime(2026, 4, 7, 12, 0, 0)
        state = KISRecoveryState(cooldown_seconds=60, alert_threshold=1)
        state.mark_degraded("err", now=now)

        factory = AsyncMock(side_effect=RuntimeError("boom"))
        callback = AsyncMock(side_effect=RuntimeError("alert sink down"))

        result = await try_recover_kis(
            state,
            factory,
            now=now + timedelta(seconds=61),
            alert_callback=callback,
        )

        # 알림 실패에도 회복 함수는 None 을 정상 반환해야 한다
        assert result is None
        assert state.consecutive_failures == 1
        # 알림 발송이 예외로 실패했으므로 alert_dispatched 는 여전히 False (재시도 여지)
        assert state.alert_dispatched is False
        callback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_callback_provided_does_not_raise(self):
        now = datetime(2026, 4, 7, 12, 0, 0)
        state = KISRecoveryState(cooldown_seconds=60, alert_threshold=1)
        state.mark_degraded("err", now=now)

        factory = AsyncMock(side_effect=RuntimeError("boom"))

        result = await try_recover_kis(state, factory, now=now + timedelta(seconds=61))

        assert result is None
        assert state.consecutive_failures == 1
        assert state.alert_dispatched is False
