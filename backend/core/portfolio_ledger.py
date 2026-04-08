"""P1-정합성: 포지션 ledger (in-memory cache + 선택적 DB 영속화).

본 모듈은 OrderExecutor 가 체결을 확정한 직후에 호출하는 단일 진입점이며,
ReconciliationRunner 가 "내부 측 진실" 로 비교하는 대조원이다.

구조
----
``PortfolioLedger`` 는 항상 in-memory dict 를 1차 캐시로 유지한다. ``repository``
가 주입되면 모든 mutation 을 DB 트랜잭션에 위임하며, **DB commit 이 성공한
이후에만** 로컬 캐시를 갱신한다 — 즉 cache 와 DB 가 영구적으로 어긋나는
경로가 코드상 존재하지 않는다.

운영 부트스트랩은 다음과 같이 사용한다::

    from db.database import async_session_factory
    from db.repositories.portfolio_positions import SqlPortfolioLedgerRepository

    repo = SqlPortfolioLedgerRepository(async_session_factory)
    ledger = configure_portfolio_ledger(repo)
    await ledger.hydrate()

테스트/백테스트/dry-run 은 ``repository=None`` (또는
``configure_portfolio_ledger`` 미호출) 로 in-memory 모드만 사용한다.

불변식
------

1. ``record_fill(ticker, side, qty)`` 는 BUY 는 양수, SELL 은 음수로 누적,
   결과 잔량이 음수가 되면 ``LedgerInvariantError`` 로 거부 (long-only).
2. ``get_positions()`` 는 0 이 아닌 종목만 반환 (broker 응답 정책과 일치).
3. cache 와 repository 가 모두 있을 때 cache 는 DB commit 의 결과만 반영하며
   commit 실패 시 unchanged 를 유지한다.
4. ``hydrate()`` 는 DB → cache 단방향이며 외부에서 cache 를 직접 mutate 할 수
   있는 경로는 없다.
"""

from __future__ import annotations

import asyncio
from typing import Dict, Optional, Protocol, runtime_checkable

from config.constants import OrderSide

PositionMap = Dict[str, float]


class LedgerInvariantError(RuntimeError):
    """ledger 가 음수 포지션이 되려는 시도를 거부할 때 raise."""


@runtime_checkable
class LedgerRepository(Protocol):
    """PortfolioLedger 가 의존하는 영속 계층 contract.

    구현체는 다음을 보장해야 한다.

    - ``apply_delta`` 는 단일 트랜잭션 내에서 row-level lock + 잔량 갱신을
      원자적으로 수행한다.
    - 결과 잔량이 음수가 되면 ``LedgerInvariantError`` 를 raise 한다.
    - 결과 잔량이 0 이면 row 를 제거한다.
    - 성공 시 새 잔량을 반환한다.
    """

    async def load_all(self) -> Dict[str, float]: ...

    async def apply_delta(self, ticker: str, delta: float) -> float: ...


class PortfolioLedger:
    """프로세스 내부 포지션 ledger (cache + 선택적 DB 영속화)."""

    def __init__(self, repository: Optional[LedgerRepository] = None) -> None:
        self._positions: Dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._repository = repository
        self._hydrated = False

    @property
    def repository(self) -> Optional[LedgerRepository]:
        return self._repository

    @property
    def is_hydrated(self) -> bool:
        return self._hydrated

    async def hydrate(self) -> None:
        """DB 에서 모든 잔량을 읽어 cache 를 초기화. repository 가 없으면 no-op.

        부팅 시 정확히 1회 호출되어야 하며, 이후 mutation 은 ``record_fill``
        만 사용한다. 재호출 시에도 안전하게 동작 (cache 를 덮어쓴다).
        """
        if self._repository is None:
            self._hydrated = True
            return
        async with self._lock:
            loaded = await self._repository.load_all()
            self._positions = {t: float(q) for t, q in loaded.items() if q > 0}
            self._hydrated = True

    async def record_fill(self, ticker: str, side: OrderSide, quantity: float) -> None:
        if not isinstance(ticker, str) or not ticker.strip():
            raise ValueError("ticker must be a non-empty string")
        if quantity <= 0:
            raise ValueError(f"quantity must be > 0, got {quantity}")
        if side not in (OrderSide.BUY, OrderSide.SELL):
            raise ValueError(f"unsupported side: {side}")

        delta = quantity if side is OrderSide.BUY else -quantity

        async with self._lock:
            if self._repository is not None:
                # DB 가 진실 — commit 성공 후에만 cache 갱신.
                new_qty = await self._repository.apply_delta(ticker, delta)
            else:
                current = self._positions.get(ticker, 0.0)
                new_qty = current + delta
                if new_qty < 0:
                    raise LedgerInvariantError(
                        f"ledger refuses to record short position for {ticker}: "
                        f"current={current} delta={delta} → {new_qty}"
                    )

            if new_qty == 0:
                self._positions.pop(ticker, None)
            else:
                self._positions[ticker] = new_qty

    def get_positions(self) -> PositionMap:
        """현재 0 이 아닌 종목 snapshot."""
        return {t: q for t, q in self._positions.items() if q != 0}

    def reset(self) -> None:
        """테스트/마이그레이션 전용 — 운영 코드에서 호출 금지."""
        self._positions.clear()
        self._hydrated = False


# ── 프로세스 전역 싱글톤 ─────────────────────────────────────────────────────

_singleton: Optional[PortfolioLedger] = None


def get_portfolio_ledger() -> PortfolioLedger:
    global _singleton
    if _singleton is None:
        _singleton = PortfolioLedger()
    return _singleton


def configure_portfolio_ledger(
    repository: Optional[LedgerRepository],
) -> PortfolioLedger:
    """프로세스 전역 ledger 를 주어진 repository 로 (재)구성한다.

    부트스트랩(`main.py`, `scheduler_main.py`) 에서 DB engine 초기화 직후
    1회 호출한다. 호출자는 반환된 ledger 에 대해 ``await ledger.hydrate()``
    를 실행해야 cache 가 채워진다.
    """
    global _singleton
    _singleton = PortfolioLedger(repository=repository)
    return _singleton


def reset_portfolio_ledger() -> None:
    """테스트 격리용 — 운영 코드에서 호출 금지."""
    global _singleton
    _singleton = None


__all__ = [
    "LedgerInvariantError",
    "LedgerRepository",
    "PortfolioLedger",
    "PositionMap",
    "configure_portfolio_ledger",
    "get_portfolio_ledger",
    "reset_portfolio_ledger",
]
