"""P1-정합성: 프로세스 내부 포지션 ledger.

본 모듈은 OrderExecutor 가 체결을 확정한 직후에 호출하는 단일 진입점이며,
ReconciliationRunner 가 "내부 측 진실" 로 비교하는 대조원이다. 영구 저장
계층(예: PostgreSQL) 이 도입되기 전 단계의 in-memory 구현으로, 다음 두
요건만 보장한다:

  1. ``record_fill(ticker, side, qty)`` 는 BUY 는 양수, SELL 은 음수로
     수량을 누적하며, 결과 수량이 음수가 되는 BUY/SELL 시퀀스는
     ``LedgerInvariantError`` 로 즉시 거부한다 (short 포지션 금지 — 본
     ledger 는 long-only 정합성만 검증한다).
  2. ``get_positions()`` 는 0 이 아닌 종목만 반환하여
     ReconciliationEngine 의 비교 정확성을 해치지 않는다 (브로커는 0주
     종목을 응답에 포함하지 않으므로 ledger 에도 0 잔량 잔재를 남기면
     불필요한 mismatch 가 발생한다).

설계 근거: 정합성 ledger 는 "체결 직후 한 점의 진실" 을 기록하면 충분하며
(가격은 보관하지 않음 — 수량 정합성만 비교 대상), 영속화는 후속 P1 항목
(`PortfolioLedger DB persistence`) 에서 다룬다. 본 모듈을 분리하면 OrderExecutor
는 ledger 의 영속성 정책을 알 필요가 없고, 후속 교체 시 인터페이스만 유지하면
ReconciliationRunner 는 변경되지 않는다 (의존성 역전).

스레드/태스크 안전성: ``asyncio.Lock`` 으로 record_fill 직렬화. 단일 프로세스
이벤트 루프 가정. 멀티프로세스 환경에서는 본 ledger 가 무력화되므로 그 시점이
DB 영속화 전환 시점이다.
"""

from __future__ import annotations

import asyncio
from typing import Dict, Optional

from config.constants import OrderSide

PositionMap = Dict[str, float]


class LedgerInvariantError(RuntimeError):
    """ledger 가 음수 포지션이 되려는 시도를 거부할 때 raise."""


class PortfolioLedger:
    """프로세스 내부 포지션 ledger (in-memory).

    Notes
    -----
    * 본 클래스는 OrderExecutor 의 체결 후속 호출만 수신하며, 외부에서
      직접 dict 를 조작하지 않는다.
    * Reconciliation 비교는 ``get_positions()`` 가 반환하는 dict 의 snapshot
      을 사용한다 — 호출자는 결과를 mutate 해도 ledger 에 영향이 없다.
    """

    def __init__(self) -> None:
        self._positions: Dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def record_fill(self, ticker: str, side: OrderSide, quantity: float) -> None:
        if not isinstance(ticker, str) or not ticker.strip():
            raise ValueError("ticker must be a non-empty string")
        if quantity <= 0:
            raise ValueError(f"quantity must be > 0, got {quantity}")
        if side not in (OrderSide.BUY, OrderSide.SELL):
            raise ValueError(f"unsupported side: {side}")

        delta = quantity if side is OrderSide.BUY else -quantity
        async with self._lock:
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


# ── 프로세스 전역 싱글톤 ─────────────────────────────────────────────────────

_singleton: Optional[PortfolioLedger] = None


def get_portfolio_ledger() -> PortfolioLedger:
    global _singleton
    if _singleton is None:
        _singleton = PortfolioLedger()
    return _singleton


def reset_portfolio_ledger() -> None:
    """테스트 격리용 — 운영 코드에서 호출 금지."""
    global _singleton
    _singleton = None


__all__ = [
    "LedgerInvariantError",
    "PortfolioLedger",
    "PositionMap",
    "get_portfolio_ledger",
    "reset_portfolio_ledger",
]
