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
