"""
그레이스풀 셧다운 매니저 테스트 (NFR-06)

GracefulShutdownManager의 종합 단위 테스트

테스트 범위:
- 초기 상태 및 속성 검증
- 서비스 등록 및 역순 종료
- 주문 추적 (등록/완료/거부)
- 진행 중 주문 대기 (draining)
- 미완료 주문 강제 취소
- 타임아웃 처리
- 종료 결과 요약
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.graceful_shutdown import (
    GracefulShutdownManager,
    ShutdownPhase,
    PendingOrder,
)


# ══════════════════════════════════════
# 초기 상태 테스트
# ══════════════════════════════════════
class TestShutdownManagerState:
    """셧다운 매니저 초기 상태 테스트"""

    def test_initial_phase_is_running(self):
        """초기 단계는 RUNNING"""
        mgr = GracefulShutdownManager()
        assert mgr.phase == ShutdownPhase.RUNNING

    def test_initial_accepting_orders(self):
        """초기에는 주문 수신 가능"""
        mgr = GracefulShutdownManager()
        assert mgr.accepting_orders is True

    def test_initial_not_shutting_down(self):
        """초기에는 셧다운 상태가 아님"""
        mgr = GracefulShutdownManager()
        assert mgr.is_shutting_down is False

    def test_initial_no_pending_orders(self):
        """초기에는 진행 중인 주문 없음"""
        mgr = GracefulShutdownManager()
        assert mgr.pending_order_count == 0
        assert mgr.pending_order_ids == []


# ══════════════════════════════════════
# 서비스 등록 테스트
# ══════════════════════════════════════
class TestServiceRegistration:
    """서비스 등록 및 종료 테스트"""

    @pytest.mark.asyncio
    async def test_register_service(self):
        """서비스 등록"""
        mgr = GracefulShutdownManager()
        stop_fn = AsyncMock()
        mgr.register_service("test_service", stop_fn)

        assert len(mgr._services) == 1
        assert mgr._services[0][0] == "test_service"

    @pytest.mark.asyncio
    async def test_services_stopped_in_reverse_order(self):
        """서비스가 등록 역순(LIFO)으로 종료됨"""
        mgr = GracefulShutdownManager()
        order = []

        async def stop_a():
            order.append("A")

        async def stop_b():
            order.append("B")

        async def stop_c():
            order.append("C")

        mgr.register_service("A", stop_a)
        mgr.register_service("B", stop_b)
        mgr.register_service("C", stop_c)

        await mgr.shutdown(timeout=5)

        assert order == ["C", "B", "A"]

    @pytest.mark.asyncio
    async def test_service_stop_timeout(self):
        """서비스 종료 타임아웃 처리"""
        mgr = GracefulShutdownManager()

        async def hang():
            await asyncio.sleep(999)

        mgr.register_service("hanging", hang)
        # SERVICE_STOP_TIMEOUT을 짧게 설정
        mgr.SERVICE_STOP_TIMEOUT = 1

        result = await mgr.shutdown(timeout=2)

        # 타임아웃되어도 셧다운은 완료
        assert mgr.phase == ShutdownPhase.COMPLETED
        # 타임아웃된 서비스는 stopped 목록에 미포함
        assert "hanging" not in result["services_stopped"]

    @pytest.mark.asyncio
    async def test_service_stop_exception(self):
        """서비스 종료 시 예외 처리"""
        mgr = GracefulShutdownManager()

        async def fail():
            raise RuntimeError("Stop failed!")

        mgr.register_service("failing", fail)

        result = await mgr.shutdown(timeout=5)

        assert mgr.phase == ShutdownPhase.COMPLETED
        assert "failing" not in result["services_stopped"]


# ══════════════════════════════════════
# 주문 추적 테스트
# ══════════════════════════════════════
class TestOrderTracking:
    """주문 등록, 완료, 거부 테스트"""

    def test_register_order(self):
        """주문 등록 성공"""
        mgr = GracefulShutdownManager()
        result = mgr.register_pending_order("ORD_001", "005930")

        assert result is True
        assert mgr.pending_order_count == 1
        assert "ORD_001" in mgr.pending_order_ids

    def test_complete_order(self):
        """주문 완료 처리"""
        mgr = GracefulShutdownManager()
        mgr.register_pending_order("ORD_001", "005930")
        mgr.complete_pending_order("ORD_001")

        assert mgr.pending_order_count == 0

    def test_complete_nonexistent_order(self):
        """존재하지 않는 주문 완료 시 에러 없음"""
        mgr = GracefulShutdownManager()
        mgr.complete_pending_order("NONEXISTENT")
        assert mgr.pending_order_count == 0

    def test_reject_order_during_shutdown(self):
        """셧다운 중 주문 등록 거부"""
        mgr = GracefulShutdownManager()
        mgr._accepting_orders = False

        result = mgr.register_pending_order("ORD_002", "AAPL")

        assert result is False
        assert mgr.pending_order_count == 0

    def test_multiple_orders(self):
        """여러 주문 동시 추적"""
        mgr = GracefulShutdownManager()
        mgr.register_pending_order("ORD_001", "005930")
        mgr.register_pending_order("ORD_002", "AAPL")
        mgr.register_pending_order("ORD_003", "GOOGL")

        assert mgr.pending_order_count == 3

        mgr.complete_pending_order("ORD_002")
        assert mgr.pending_order_count == 2
        assert "ORD_002" not in mgr.pending_order_ids


# ══════════════════════════════════════
# 셧다운 실행 테스트
# ══════════════════════════════════════
class TestShutdownExecution:
    """셧다운 실행 흐름 테스트"""

    @pytest.mark.asyncio
    async def test_full_shutdown_flow(self):
        """전체 셧다운 흐름 검증"""
        mgr = GracefulShutdownManager()

        # 서비스 등록
        stop_fn = AsyncMock()
        mgr.register_service("svc1", stop_fn)

        # 정리 콜백 등록
        cleanup_fn = AsyncMock()
        mgr.register_cleanup(cleanup_fn)

        result = await mgr.shutdown(timeout=5)

        assert mgr.phase == ShutdownPhase.COMPLETED
        assert mgr.is_shutting_down is True
        assert mgr.accepting_orders is False
        assert stop_fn.called
        assert cleanup_fn.called
        assert "svc1" in result["services_stopped"]
        assert "elapsed_seconds" in result

    @pytest.mark.asyncio
    async def test_duplicate_shutdown_ignored(self):
        """중복 셧다운 호출 무시"""
        mgr = GracefulShutdownManager()
        await mgr.shutdown(timeout=2)

        result = await mgr.shutdown(timeout=2)

        assert result["status"] == "already_in_progress"

    @pytest.mark.asyncio
    async def test_drain_pending_orders(self):
        """진행 중 주문이 완료될 때까지 대기"""
        mgr = GracefulShutdownManager()
        mgr.register_pending_order("ORD_001", "005930")

        # 2초 후 주문 완료 시뮬레이션
        async def complete_after_delay():
            await asyncio.sleep(1.5)
            mgr.complete_pending_order("ORD_001")

        asyncio.create_task(complete_after_delay())

        result = await mgr.shutdown(timeout=10)

        assert result["orders_drained"] == 1
        assert result["orders_cancelled"] == 0

    @pytest.mark.asyncio
    async def test_cancel_orders_after_timeout(self):
        """타임아웃 후 미완료 주문 강제 취소"""
        mgr = GracefulShutdownManager()
        mgr.register_pending_order("ORD_001", "005930")
        # 주문을 완료하지 않음 → 타임아웃 후 취소

        result = await mgr.shutdown(timeout=2)

        assert result["orders_cancelled"] >= 1
        assert mgr.pending_order_count == 0

    @pytest.mark.asyncio
    async def test_cancel_order_with_task(self):
        """asyncio.Task가 등록된 주문 취소 시 task.cancel() 호출"""
        mgr = GracefulShutdownManager()

        async def long_running():
            await asyncio.sleep(999)

        task = asyncio.create_task(long_running())
        mgr.register_pending_order("ORD_001", "005930", task=task)

        result = await mgr.shutdown(timeout=1)

        assert task.cancelled() or task.done()
        assert result["orders_cancelled"] >= 1

    @pytest.mark.asyncio
    async def test_cleanup_callbacks_executed(self):
        """정리 콜백이 실행됨"""
        mgr = GracefulShutdownManager()

        cb1 = AsyncMock()
        cb2 = AsyncMock()
        mgr.register_cleanup(cb1)
        mgr.register_cleanup(cb2)

        await mgr.shutdown(timeout=5)

        assert cb1.called
        assert cb2.called

    @pytest.mark.asyncio
    async def test_cleanup_exception_handled(self):
        """정리 콜백 예외 처리"""
        mgr = GracefulShutdownManager()

        async def fail_cleanup():
            raise Exception("DB disconnect failed!")

        mgr.register_cleanup(fail_cleanup)

        # 예외가 전파되지 않아야 함
        result = await mgr.shutdown(timeout=5)
        assert mgr.phase == ShutdownPhase.COMPLETED

    @pytest.mark.asyncio
    async def test_shutdown_result_summary(self):
        """셧다운 결과 요약 검증"""
        mgr = GracefulShutdownManager()

        svc_stop = AsyncMock()
        mgr.register_service("monitor", svc_stop)
        mgr.register_service("scheduler", svc_stop)

        result = await mgr.shutdown(timeout=5)

        assert "started_at" in result
        assert "elapsed_seconds" in result
        assert "services_stopped" in result
        assert "orders_drained" in result
        assert "orders_cancelled" in result
        assert len(result["services_stopped"]) == 2


# ══════════════════════════════════════
# ShutdownPhase Enum 테스트
# ══════════════════════════════════════
class TestShutdownPhase:
    """셧다운 단계 Enum 테스트"""

    def test_phases(self):
        """모든 단계가 존재"""
        assert ShutdownPhase.RUNNING.value == "RUNNING"
        assert ShutdownPhase.DRAINING.value == "DRAINING"
        assert ShutdownPhase.STOPPING_SERVICES.value == "STOPPING_SERVICES"
        assert ShutdownPhase.CLEANUP.value == "CLEANUP"
        assert ShutdownPhase.COMPLETED.value == "COMPLETED"


# ══════════════════════════════════════
# PendingOrder 테스트
# ══════════════════════════════════════
class TestPendingOrder:
    """PendingOrder 데이터 클래스 테스트"""

    def test_create_pending_order(self):
        """PendingOrder 생성"""
        order = PendingOrder(order_id="ORD_001", ticker="005930")
        assert order.order_id == "ORD_001"
        assert order.ticker == "005930"
        assert order.task is None
        assert isinstance(order.started_at, datetime)

    def test_pending_order_with_task(self):
        """Task 포함 PendingOrder"""
        mock_task = MagicMock()
        order = PendingOrder(
            order_id="ORD_002",
            ticker="AAPL",
            task=mock_task,
        )
        assert order.task is mock_task


# ══════════════════════════════════════
# wait_for_shutdown 테스트
# ══════════════════════════════════════
class TestWaitForShutdown:
    """wait_for_shutdown 테스트"""

    @pytest.mark.asyncio
    async def test_wait_completes_after_shutdown(self):
        """셧다운 후 wait_for_shutdown이 반환"""
        mgr = GracefulShutdownManager()

        async def do_shutdown():
            await asyncio.sleep(0.5)
            await mgr.shutdown(timeout=2)

        asyncio.create_task(do_shutdown())

        # 셧다운 완료까지 대기 (타임아웃 포함)
        await asyncio.wait_for(mgr.wait_for_shutdown(), timeout=10)

        assert mgr.phase == ShutdownPhase.COMPLETED
