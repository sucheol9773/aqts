"""
Circuit Breaker 테스트

CircuitBreaker, CircuitBreakerRegistry의 상태 전이,
실패 카운팅, 복구 로직을 검증합니다.
"""

import asyncio
import time

import pytest

from core.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerError,
    CircuitBreakerRegistry,
    CircuitState,
)


# ══════════════════════════════════════
# 기본 상태 전이 테스트
# ══════════════════════════════════════
class TestCircuitBreakerStates:
    """서킷 브레이커 상태 전이 테스트"""

    def test_initial_state_is_closed(self):
        """초기 상태는 CLOSED"""
        breaker = CircuitBreaker(name="test_initial", failure_threshold=3)
        assert breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_stays_closed_on_success(self):
        """성공 시 CLOSED 유지"""
        breaker = CircuitBreaker(name="test_success", failure_threshold=3)

        @breaker
        async def succeed():
            return "ok"

        result = await succeed()
        assert result == "ok"
        assert breaker.state == CircuitState.CLOSED
        assert breaker.stats.total_calls == 1
        assert breaker.stats.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_opens_after_threshold_failures(self):
        """실패 임계값 도달 시 OPEN으로 전이"""
        breaker = CircuitBreaker(
            name="test_open", failure_threshold=3, recovery_timeout=60.0
        )

        @breaker
        async def fail():
            raise ConnectionError("service down")

        for _ in range(3):
            with pytest.raises(ConnectionError):
                await fail()

        assert breaker.state == CircuitState.OPEN
        assert breaker.stats.consecutive_failures == 3

    @pytest.mark.asyncio
    async def test_open_rejects_calls(self):
        """OPEN 상태에서 요청은 CircuitBreakerError로 거부"""
        breaker = CircuitBreaker(
            name="test_reject", failure_threshold=2, recovery_timeout=60.0
        )

        @breaker
        async def fail():
            raise ConnectionError("down")

        # threshold까지 실패
        for _ in range(2):
            with pytest.raises(ConnectionError):
                await fail()

        assert breaker.state == CircuitState.OPEN

        # OPEN 상태에서 호출 시 CircuitBreakerError
        with pytest.raises(CircuitBreakerError) as exc_info:
            await fail()

        assert "test_reject" in str(exc_info.value)
        assert exc_info.value.breaker_name == "test_reject"

    @pytest.mark.asyncio
    async def test_transitions_to_half_open_after_timeout(self):
        """recovery_timeout 이후 HALF_OPEN으로 전이"""
        breaker = CircuitBreaker(
            name="test_half_open",
            failure_threshold=2,
            recovery_timeout=0.1,  # 100ms
        )

        @breaker
        async def fail():
            raise ConnectionError("down")

        for _ in range(2):
            with pytest.raises(ConnectionError):
                await fail()

        assert breaker.state == CircuitState.OPEN

        # recovery_timeout 대기
        await asyncio.sleep(0.15)

        # 이제 HALF_OPEN
        assert breaker.state == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_half_open_to_closed_on_success(self):
        """HALF_OPEN에서 성공하면 CLOSED로 복귀"""
        breaker = CircuitBreaker(
            name="test_recovery",
            failure_threshold=2,
            recovery_timeout=0.1,
            half_open_max_calls=2,
        )

        call_count = 0

        @breaker
        async def maybe_fail():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise ConnectionError("down")
            return "recovered"

        # 2회 실패 → OPEN
        for _ in range(2):
            with pytest.raises(ConnectionError):
                await maybe_fail()

        assert breaker.state == CircuitState.OPEN

        # 대기 후 HALF_OPEN
        await asyncio.sleep(0.15)
        assert breaker.state == CircuitState.HALF_OPEN

        # HALF_OPEN에서 성공
        result = await maybe_fail()
        assert result == "recovered"
        result = await maybe_fail()
        assert result == "recovered"

        # 2회 성공 → CLOSED
        assert breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_half_open_back_to_open_on_failure(self):
        """HALF_OPEN에서 실패하면 다시 OPEN"""
        breaker = CircuitBreaker(
            name="test_reopen",
            failure_threshold=2,
            recovery_timeout=0.1,
            half_open_max_calls=3,
        )

        @breaker
        async def fail():
            raise ConnectionError("still down")

        for _ in range(2):
            with pytest.raises(ConnectionError):
                await fail()

        await asyncio.sleep(0.15)
        assert breaker.state == CircuitState.HALF_OPEN

        # HALF_OPEN에서도 실패
        with pytest.raises(ConnectionError):
            await fail()

        assert breaker.state == CircuitState.OPEN


# ══════════════════════════════════════
# 제외 예외 테스트
# ══════════════════════════════════════
class TestExcludedExceptions:
    """제외 예외는 실패로 카운트되지 않아야 함"""

    @pytest.mark.asyncio
    async def test_excluded_exceptions_not_counted(self):
        """excluded_exceptions에 해당하면 실패 카운트 안 됨"""
        breaker = CircuitBreaker(
            name="test_exclude",
            failure_threshold=3,
            excluded_exceptions=(ValueError,),
        )

        @breaker
        async def raise_value_error():
            raise ValueError("not a failure")

        for _ in range(5):
            with pytest.raises(ValueError):
                await raise_value_error()

        # ValueError는 제외되므로 CLOSED 유지
        assert breaker.state == CircuitState.CLOSED
        assert breaker.stats.consecutive_failures == 0
        assert breaker.stats.total_calls == 5


# ══════════════════════════════════════
# 컨텍스트 매니저 테스트
# ══════════════════════════════════════
class TestContextManager:
    """async with 문으로 사용"""

    @pytest.mark.asyncio
    async def test_context_manager_success(self):
        """컨텍스트 매니저 성공 경로"""
        breaker = CircuitBreaker(name="test_ctx_success", failure_threshold=3)

        async with breaker:
            result = 42

        assert result == 42
        assert breaker.stats.total_calls == 1
        assert breaker.stats.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_context_manager_failure(self):
        """컨텍스트 매니저 실패 경로"""
        breaker = CircuitBreaker(name="test_ctx_fail", failure_threshold=3)

        with pytest.raises(RuntimeError):
            async with breaker:
                raise RuntimeError("oops")

        assert breaker.stats.consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_context_manager_open_rejection(self):
        """OPEN 상태에서 컨텍스트 매니저 진입 거부"""
        breaker = CircuitBreaker(
            name="test_ctx_open", failure_threshold=2, recovery_timeout=60.0
        )

        for _ in range(2):
            with pytest.raises(RuntimeError):
                async with breaker:
                    raise RuntimeError("down")

        assert breaker.state == CircuitState.OPEN

        with pytest.raises(CircuitBreakerError):
            async with breaker:
                pass  # 여기 도달하면 안 됨


# ══════════════════════════════════════
# to_dict / 모니터링 테스트
# ══════════════════════════════════════
class TestMonitoring:
    """상태 정보 직렬화"""

    def test_to_dict(self):
        """to_dict가 올바른 구조를 반환"""
        breaker = CircuitBreaker(
            name="test_dict",
            failure_threshold=5,
            recovery_timeout=60.0,
        )

        info = breaker.to_dict()
        assert info["name"] == "test_dict"
        assert info["state"] == "CLOSED"
        assert info["failure_threshold"] == 5
        assert info["recovery_timeout"] == 60.0
        assert "stats" in info
        assert info["stats"]["total_calls"] == 0


# ══════════════════════════════════════
# 레지스트리 테스트
# ══════════════════════════════════════
class TestCircuitBreakerRegistry:
    """CircuitBreakerRegistry 기능 테스트"""

    def test_register_and_get(self):
        """등록 후 이름으로 조회"""
        breaker = CircuitBreaker(name="test_reg_1", failure_threshold=3)
        CircuitBreakerRegistry.register(breaker)

        found = CircuitBreakerRegistry.get("test_reg_1")
        assert found is breaker

    def test_get_nonexistent(self):
        """존재하지 않는 이름 조회 시 None"""
        assert CircuitBreakerRegistry.get("nonexistent_breaker") is None

    def test_status_returns_all(self):
        """status()가 전체 상태를 반환"""
        status = CircuitBreakerRegistry.status()
        # 글로벌 등록된 kis_api, fred_api, ecos_api, anthropic_api가 있어야 함
        assert "kis_api" in status
        assert "fred_api" in status
        assert "ecos_api" in status
        assert "anthropic_api" in status
        assert status["kis_api"]["state"] == "CLOSED"

    def test_all_returns_dict(self):
        """all()이 딕셔너리 반환"""
        all_breakers = CircuitBreakerRegistry.all()
        assert isinstance(all_breakers, dict)
        assert len(all_breakers) >= 4  # 최소 4개 (글로벌 정의)


# ══════════════════════════════════════
# 실패 후 성공 시 카운터 리셋
# ══════════════════════════════════════
class TestFailureReset:
    """실패 카운터가 성공 시 리셋되는지 확인"""

    @pytest.mark.asyncio
    async def test_success_resets_consecutive_failures(self):
        """성공 호출 시 consecutive_failures가 0으로 리셋"""
        breaker = CircuitBreaker(name="test_reset", failure_threshold=5)

        call_count = 0

        @breaker
        async def intermittent():
            nonlocal call_count
            call_count += 1
            if call_count % 3 == 0:
                return "ok"
            raise ConnectionError("flaky")

        # 2회 실패
        with pytest.raises(ConnectionError):
            await intermittent()
        with pytest.raises(ConnectionError):
            await intermittent()

        assert breaker.stats.consecutive_failures == 2

        # 1회 성공 → 리셋
        result = await intermittent()
        assert result == "ok"
        assert breaker.stats.consecutive_failures == 0

        # 여전히 CLOSED (threshold 5에 미도달)
        assert breaker.state == CircuitState.CLOSED
