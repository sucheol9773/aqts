"""
P1-정합성: ReconciliationEngine 의 스케줄러 wiring 진입점.

`ReconciliationEngine` 자체는 순수 비교 로직만 담당하는 정의이며, 종전에는
어떤 스케줄러 핸들러에서도 호출되지 않아 형식적 통제에 머물렀다 (RBAC
Wiring Rule 의 정의 ≠ 적용 문제와 동일 패턴 — 정합성 도메인 확장).

`ReconciliationRunner` 는 다음을 단일 진입점에서 강제한다:
  1. 브로커/내부 포지션 provider 호출
  2. `ReconciliationEngine.reconcile` 평가
  3. Prometheus 카운터/게이지 업데이트
  4. mismatch 가 임계를 초과하면 프로세스 전역 `TradingGuard` 에 kill
     switch 를 활성화 — 이후 OrderExecutor 는 P0-5 의 wiring 을 통해
     모든 주문을 자동 차단한다.

provider 는 Protocol 로 분리하여 production 에서는 KIS API + DB 쿼리,
테스트에서는 in-memory dict 로 주입한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Optional, Protocol

from config.logging import logger
from core.monitoring.metrics import (
    RECONCILIATION_LEDGER_DIFF_ABS,
    RECONCILIATION_MISMATCHES_TOTAL,
    RECONCILIATION_RUNS_TOTAL,
)
from core.reconciliation import ReconciliationEngine, ReconciliationResult
from core.trading_guard import TradingGuard, get_trading_guard

PositionMap = Dict[str, float]


class PositionProvider(Protocol):
    """브로커 또는 내부 포지션을 비동기로 제공하는 의존성."""

    async def get_positions(self) -> PositionMap:  # pragma: no cover - protocol
        ...


@dataclass
class ReconciliationRunner:
    """
    ReconciliationEngine + TradingGuard wiring.

    Parameters
    ----------
    engine:
        포지션 비교 엔진 (순수 함수성).
    broker_provider / internal_provider:
        포지션 조회 callable. Protocol 객체가 아니어도 `get_positions()` 만
        만족하면 된다.
    guard:
        kill switch 를 활성화할 TradingGuard. 미지정 시 프로세스 전역 싱글톤.
    mismatch_threshold:
        kill switch 를 활성화하는 mismatch 개수 임계 (포함). 기본 0 — 즉,
        하나라도 불일치가 발생하면 즉시 kill switch.
    """

    engine: ReconciliationEngine
    broker_provider: PositionProvider
    internal_provider: PositionProvider
    guard: Optional[TradingGuard] = None
    mismatch_threshold: int = 0

    def __post_init__(self) -> None:
        if self.guard is None:
            self.guard = get_trading_guard()
        if self.mismatch_threshold < 0:
            raise ValueError("mismatch_threshold must be >= 0")

    async def run(self) -> ReconciliationResult:
        """
        한 번의 reconcile 사이클을 실행한다.

        provider 호출이 실패하면 result 라벨 "error" 로 카운터 증가 후 예외
        재전파 (fail-closed: 정합성 데이터를 못 읽으면 안전을 가정할 수 없음).
        mismatch 가 임계를 초과하면 kill switch 를 활성화하고 정상 결과를
        반환한다 (호출자가 후속 조치 가능하도록).
        """
        try:
            broker_positions = await _maybe_await(self.broker_provider.get_positions())
            internal_positions = await _maybe_await(self.internal_provider.get_positions())
        except Exception as exc:
            RECONCILIATION_RUNS_TOTAL.labels(result="error").inc()
            # loguru f-string 포맷 — stdlib logging 의 % posarg 는 해석되지 않음.
            # 회고: phase1-demo-verification-2026-04-11 §10.15.
            logger.error(f"Reconciliation provider failure: {exc}")
            raise

        result = self.engine.reconcile(broker_positions, internal_positions)
        diff_abs = abs(result.broker_total - result.internal_total)
        RECONCILIATION_LEDGER_DIFF_ABS.set(diff_abs)
        mismatch_count = len(result.mismatches)

        if result.matched:
            RECONCILIATION_RUNS_TOTAL.labels(result="matched").inc()
            logger.info(
                "Reconciliation matched: broker_total=%.2f internal_total=%.2f",
                result.broker_total,
                result.internal_total,
            )
            return result

        RECONCILIATION_RUNS_TOTAL.labels(result="mismatch").inc()
        RECONCILIATION_MISMATCHES_TOTAL.inc(mismatch_count)
        logger.critical(
            f"Reconciliation mismatch detected: count={mismatch_count} "
            f"diff_abs={diff_abs:.2f} mismatches={result.mismatches}"
        )

        if mismatch_count > self.mismatch_threshold:
            assert self.guard is not None  # __post_init__ 에서 보장
            reason = (
                f"Reconciliation mismatch: {mismatch_count}건 "
                f"(임계 {self.mismatch_threshold}) ledger_diff={diff_abs:.2f}"
            )
            self.guard.activate_kill_switch(reason)

        return result


async def _maybe_await(value):
    """provider 가 sync dict 를 반환하든 coroutine 을 반환하든 동일하게 처리."""
    if hasattr(value, "__await__"):
        return await value
    return value


# ── 단순 dict provider 헬퍼 (테스트/부트스트랩용) ────────────────────────────


@dataclass
class StaticPositionProvider:
    """미리 주어진 dict 를 반환하는 provider — 테스트 및 마이그레이션 단계용."""

    positions: PositionMap

    async def get_positions(self) -> PositionMap:
        return dict(self.positions)


@dataclass
class CallablePositionProvider:
    """async callable 을 provider 로 감싸는 어댑터."""

    fn: Callable[[], Awaitable[PositionMap]]

    async def get_positions(self) -> PositionMap:
        return await self.fn()
