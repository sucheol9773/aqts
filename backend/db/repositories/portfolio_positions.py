"""SQL 기반 PortfolioLedger 영속 계층.

설계 근거: docs/security/security-integrity-roadmap.md §7.3 / §9.

본 모듈은 ``core.portfolio_ledger.PortfolioLedger`` 가 의존하는
``LedgerRepository`` Protocol 의 운영 구현체이다. 단일 트랜잭션 내에서
``SELECT ... FOR UPDATE`` → 잔량 계산 → INSERT/UPDATE/DELETE 를 원자적으로
수행하여 다음을 보장한다.

1. **단조성** — 같은 ticker 에 대한 동시 ``apply_delta`` 호출은 row-level
   lock 으로 직렬화되며, 결과 잔량이 음수가 되면 transaction 을 rollback
   하고 ``LedgerInvariantError`` 를 raise 한다 (long-only 정합성).
2. **0 잔량 정책** — 결과 잔량이 0 이면 row 를 ``DELETE`` 한다. broker
   응답에 0주 종목이 포함되지 않는 정책과 일치시켜 reconcile 시
   불필요한 mismatch 를 피한다.
3. **휘발성 격리** — 호출자(`PortfolioLedger`)는 in-memory cache 를 DB
   commit **이후에만** 갱신한다. DB 가 실패하면 cache 도 변경되지 않으므로
   "DB 와 cache 가 영구적으로 어긋나는" 경로가 존재하지 않는다.

session_factory 는 ``async_sessionmaker[AsyncSession]`` 를 받으며,
``apply_delta`` / ``load_all`` 은 호출 시점에 새 세션을 열고 호출 후 닫는다.
FastAPI 요청 컨텍스트 외부(스케줄러, OrderExecutor wiring) 에서도 동작해야
하기 때문에 외부 세션 주입을 강제하지 않는다.
"""

from __future__ import annotations

from typing import Callable, Dict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.portfolio_ledger import LedgerInvariantError

SessionFactory = Callable[[], "AsyncSession"]


class SqlPortfolioLedgerRepository:
    """PostgreSQL 기반 ledger 저장소.

    Parameters
    ----------
    session_factory:
        호출 시 새 ``AsyncSession`` 을 반환하는 callable. 보통은
        ``db.database.async_session_factory`` 를 그대로 전달하지만, 테스트는
        외부 세션을 wrap 한 fake factory 를 주입할 수 있다.
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def load_all(self) -> Dict[str, float]:
        """모든 잔량 row 를 dict 로 반환. 0 또는 음수는 자동 제외."""
        async with self._session_factory() as session:
            result = await session.execute(text("SELECT ticker, quantity FROM portfolio_positions WHERE quantity > 0"))
            rows = result.all()
            return {row[0]: float(row[1]) for row in rows}

    async def apply_delta(self, ticker: str, delta: float) -> float:
        """``ticker`` 의 잔량에 ``delta`` 를 더하고 새 잔량을 반환.

        - row 가 없으면 ``current = 0`` 으로 시작.
        - 결과가 음수이면 트랜잭션을 rollback 하고 ``LedgerInvariantError``.
        - 결과가 0 이면 row 를 DELETE.
        - 결과가 양수이면 row 가 없을 때 INSERT, 있을 때 UPDATE.
        """
        async with self._session_factory() as session:
            try:
                async with session.begin():
                    select_stmt = text("SELECT quantity FROM portfolio_positions " "WHERE ticker = :ticker FOR UPDATE")
                    result = await session.execute(select_stmt, {"ticker": ticker})
                    row = result.first()
                    current = float(row[0]) if row is not None else 0.0
                    new_qty = current + delta

                    if new_qty < 0:
                        raise LedgerInvariantError(
                            f"ledger refuses to record short position for {ticker}: "
                            f"current={current} delta={delta} → {new_qty}"
                        )

                    if new_qty == 0:
                        if row is not None:
                            await session.execute(
                                text("DELETE FROM portfolio_positions WHERE ticker = :ticker"),
                                {"ticker": ticker},
                            )
                    elif row is None:
                        await session.execute(
                            text(
                                "INSERT INTO portfolio_positions (ticker, quantity, updated_at) "
                                "VALUES (:ticker, :quantity, NOW())"
                            ),
                            {"ticker": ticker, "quantity": new_qty},
                        )
                    else:
                        await session.execute(
                            text(
                                "UPDATE portfolio_positions "
                                "SET quantity = :quantity, updated_at = NOW() "
                                "WHERE ticker = :ticker"
                            ),
                            {"ticker": ticker, "quantity": new_qty},
                        )

                    return new_qty
            except LedgerInvariantError:
                raise


__all__ = ["SqlPortfolioLedgerRepository", "SessionFactory"]
