"""
그레이스풀 셧다운 매니저 (NFR-06)

SIGTERM/SIGINT 수신 시 시스템을 안전하게 종료합니다.

종료 절차:
1. 신규 주문 수신 차단 (accepting_orders = False)
2. 진행 중인 주문 완료 대기 (타임아웃: 60초)
3. 비상 모니터 및 스케줄러 중지
4. 미체결 주문 취소 또는 기록
5. DB 커넥션 정리 (PostgreSQL, MongoDB, Redis)
6. 최종 상태 로깅 및 종료

주요 기능:
- register_service: 종료 대상 서비스 등록
- register_pending_order: 진행 중 주문 추적
- shutdown: 등록된 서비스 역순 종료
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Coroutine, Optional

from config.logging import logger


class ShutdownPhase(str, Enum):
    """셧다운 단계"""
    RUNNING = "RUNNING"            # 정상 가동
    DRAINING = "DRAINING"          # 신규 요청 차단, 진행 중 완료 대기
    STOPPING_SERVICES = "STOPPING_SERVICES"  # 서비스 순차 중지
    CLEANUP = "CLEANUP"            # DB/리소스 정리
    COMPLETED = "COMPLETED"        # 종료 완료


@dataclass
class PendingOrder:
    """진행 중인 주문 추적 정보"""
    order_id: str
    ticker: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    task: Optional[asyncio.Task] = None


class GracefulShutdownManager:
    """
    그레이스풀 셧다운 매니저

    시스템 종료 시 서비스와 주문을 안전하게 정리합니다.

    사용법:
        manager = GracefulShutdownManager()
        manager.register_service("emergency_monitor", monitor.stop)
        manager.register_service("scheduler", scheduler.stop)

        # 주문 추적
        manager.register_pending_order("ORD_001", "005930", task)
        ...
        manager.complete_pending_order("ORD_001")

        # 셧다운
        await manager.shutdown(timeout=60)
    """

    # 셧다운 대기 기본 타임아웃 (초)
    DEFAULT_TIMEOUT = 60
    # 개별 서비스 종료 타임아웃 (초)
    SERVICE_STOP_TIMEOUT = 15

    def __init__(self):
        self._phase = ShutdownPhase.RUNNING
        self._services: list[tuple[str, Callable[[], Coroutine]]] = []
        self._pending_orders: dict[str, PendingOrder] = {}
        self._accepting_orders = True
        self._shutdown_event = asyncio.Event()
        self._cleanup_callbacks: list[Callable[[], Coroutine]] = []

    # ══════════════════════════════════════
    # 상태 접근자
    # ══════════════════════════════════════
    @property
    def phase(self) -> ShutdownPhase:
        """현재 셧다운 단계"""
        return self._phase

    @property
    def is_shutting_down(self) -> bool:
        """셧다운 진행 중 여부"""
        return self._phase != ShutdownPhase.RUNNING

    @property
    def accepting_orders(self) -> bool:
        """신규 주문 수신 가능 여부"""
        return self._accepting_orders

    @property
    def pending_order_count(self) -> int:
        """진행 중인 주문 수"""
        return len(self._pending_orders)

    @property
    def pending_order_ids(self) -> list[str]:
        """진행 중인 주문 ID 목록"""
        return list(self._pending_orders.keys())

    # ══════════════════════════════════════
    # 서비스 등록
    # ══════════════════════════════════════
    def register_service(
        self,
        name: str,
        stop_coro: Callable[[], Coroutine],
    ) -> None:
        """
        종료 대상 서비스를 등록합니다.

        등록 순서의 역순으로 종료됩니다 (LIFO).

        Args:
            name: 서비스 식별 이름
            stop_coro: 종료 시 호출할 코루틴 함수 (async def stop)
        """
        self._services.append((name, stop_coro))
        logger.debug(f"Service registered for shutdown: {name}")

    def register_cleanup(self, cleanup_coro: Callable[[], Coroutine]) -> None:
        """
        최종 정리 콜백을 등록합니다.

        DB 연결 해제, 파일 핸들 정리 등 마지막 단계에서 실행됩니다.

        Args:
            cleanup_coro: 정리 코루틴 함수
        """
        self._cleanup_callbacks.append(cleanup_coro)

    # ══════════════════════════════════════
    # 주문 추적
    # ══════════════════════════════════════
    def register_pending_order(
        self,
        order_id: str,
        ticker: str,
        task: Optional[asyncio.Task] = None,
    ) -> bool:
        """
        진행 중인 주문을 등록합니다.

        셧다운 진행 중이면 등록을 거부하고 False를 반환합니다.

        Args:
            order_id: 주문 ID
            ticker: 종목 코드
            task: 해당 주문의 asyncio.Task (취소 시 사용)

        Returns:
            True=등록 성공, False=셧다운 중 거부
        """
        if not self._accepting_orders:
            logger.warning(
                f"Order rejected (shutdown in progress): {order_id} {ticker}"
            )
            return False

        self._pending_orders[order_id] = PendingOrder(
            order_id=order_id,
            ticker=ticker,
            task=task,
        )
        return True

    def complete_pending_order(self, order_id: str) -> None:
        """진행 중인 주문 완료 처리"""
        self._pending_orders.pop(order_id, None)

    # ══════════════════════════════════════
    # 셧다운 실행
    # ══════════════════════════════════════
    async def shutdown(self, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
        """
        그레이스풀 셧다운을 실행합니다.

        종료 절차:
        1. DRAINING: 신규 주문 차단 + 진행 중 주문 대기
        2. STOPPING_SERVICES: 등록된 서비스 역순 종료
        3. CLEANUP: DB/리소스 정리 콜백 실행

        Args:
            timeout: 진행 중 주문 완료 대기 타임아웃 (초)

        Returns:
            종료 결과 요약 딕셔너리
        """
        if self._phase != ShutdownPhase.RUNNING:
            logger.warning("Shutdown already in progress, ignoring duplicate call")
            return {"status": "already_in_progress"}

        started_at = datetime.now(timezone.utc)
        logger.info("=" * 60)
        logger.info("GRACEFUL SHUTDOWN INITIATED")
        logger.info("=" * 60)

        results: dict[str, Any] = {
            "started_at": started_at.isoformat(),
            "services_stopped": [],
            "orders_drained": 0,
            "orders_cancelled": 0,
        }

        # ── Phase 1: DRAINING ──
        self._phase = ShutdownPhase.DRAINING
        self._accepting_orders = False
        logger.info(
            f"Phase 1: DRAINING - Blocking new orders, "
            f"waiting for {len(self._pending_orders)} pending orders..."
        )

        drained = await self._drain_pending_orders(timeout)
        results["orders_drained"] = drained

        # 미완료 주문 강제 취소
        if self._pending_orders:
            cancelled = await self._cancel_remaining_orders()
            results["orders_cancelled"] = cancelled

        # ── Phase 2: STOPPING_SERVICES ──
        self._phase = ShutdownPhase.STOPPING_SERVICES
        logger.info(
            f"Phase 2: STOPPING_SERVICES - "
            f"Stopping {len(self._services)} services..."
        )

        # 역순으로 서비스 종료 (LIFO)
        for name, stop_coro in reversed(self._services):
            try:
                await asyncio.wait_for(
                    stop_coro(),
                    timeout=self.SERVICE_STOP_TIMEOUT,
                )
                results["services_stopped"].append(name)
                logger.info(f"  ✓ {name} stopped")
            except asyncio.TimeoutError:
                logger.error(
                    f"  ✗ {name} stop timed out "
                    f"({self.SERVICE_STOP_TIMEOUT}s)"
                )
            except Exception as e:
                logger.error(f"  ✗ {name} stop failed: {e}")

        # ── Phase 3: CLEANUP ──
        self._phase = ShutdownPhase.CLEANUP
        logger.info(
            f"Phase 3: CLEANUP - "
            f"Running {len(self._cleanup_callbacks)} cleanup callbacks..."
        )

        for cb in self._cleanup_callbacks:
            try:
                await asyncio.wait_for(cb(), timeout=10)
            except Exception as e:
                logger.error(f"Cleanup callback failed: {e}")

        # ── 완료 ──
        self._phase = ShutdownPhase.COMPLETED
        self._shutdown_event.set()

        elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
        results["elapsed_seconds"] = round(elapsed, 2)

        logger.info("=" * 60)
        logger.info(
            f"GRACEFUL SHUTDOWN COMPLETE in {elapsed:.1f}s "
            f"({len(results['services_stopped'])} services, "
            f"{results['orders_drained']} orders drained, "
            f"{results['orders_cancelled']} orders cancelled)"
        )
        logger.info("=" * 60)

        return results

    async def _drain_pending_orders(self, timeout: float) -> int:
        """
        진행 중인 주문이 완료될 때까지 대기합니다.

        Args:
            timeout: 최대 대기 시간 (초)

        Returns:
            대기 중 완료된 주문 수
        """
        if not self._pending_orders:
            return 0

        initial_count = len(self._pending_orders)
        poll_interval = 1.0  # 1초마다 확인
        elapsed = 0.0

        while self._pending_orders and elapsed < timeout:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            remaining = len(self._pending_orders)
            if remaining > 0 and int(elapsed) % 10 == 0:
                logger.info(
                    f"  Draining: {remaining} orders remaining "
                    f"({elapsed:.0f}s / {timeout:.0f}s)"
                )

        drained = initial_count - len(self._pending_orders)
        return drained

    async def _cancel_remaining_orders(self) -> int:
        """
        타임아웃 후 미완료 주문을 강제 취소합니다.

        asyncio.Task가 등록된 경우 cancel()을 호출하고,
        그렇지 않으면 로그만 기록합니다.

        Returns:
            취소된 주문 수
        """
        cancelled = 0
        for order_id, pending in list(self._pending_orders.items()):
            logger.warning(
                f"Force cancelling order: {order_id} ({pending.ticker}), "
                f"started at {pending.started_at.isoformat()}"
            )

            if pending.task is not None and not pending.task.done():
                pending.task.cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.shield(pending.task),
                        timeout=5,
                    )
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

            cancelled += 1

        self._pending_orders.clear()
        return cancelled

    async def wait_for_shutdown(self) -> None:
        """셧다운 완료까지 대기합니다."""
        await self._shutdown_event.wait()
