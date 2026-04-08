"""P1-정합성: portfolio_positions (PortfolioLedger DB persistence)

Revision ID: 005
Revises: 004
Create Date: 2026-04-09

설계 근거 (docs/security/security-integrity-roadmap.md §7.3, §9)
----------------------------------------------------------------
직전 커밋(`fb0d5b9`) 에서 도입된 ``PortfolioLedger`` 는 in-memory 싱글톤이며
프로세스 재시작 시 상태가 사라진다. 그 결과 ReconciliationRunner 가 부팅
직후 broker 잔고와의 mismatch 를 보고하고 ``TradingGuard`` 가 즉시 kill
switch 를 발화시키는 회귀 위험이 존재한다.

본 마이그레이션은 ledger 의 영속 계층을 도입한다.

- ``ticker`` 가 PRIMARY KEY (단일 시점 단일 잔량)
- ``quantity`` 는 NOT NULL + ``CHECK (quantity > 0)`` — 0 잔량 row 는
  ``DELETE`` 로 제거하여 ledger ↔ broker 비교 시 불필요한 mismatch 가
  발생하지 않게 한다 (broker 응답에 0주 종목이 포함되지 않는 정책과 일치).
- ``updated_at`` 으로 마지막 변경 시각을 기록 — 향후 alert (`ledger_stale`)
  과 reconcile 의 freshness SLO 산출에 사용한다.

본 테이블은 작고(보유 종목 수 ≤ 수백), 쓰기 빈도도 체결 시점에만 발생하므로
별도 파티셔닝/BRIN 은 도입하지 않는다.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "portfolio_positions",
        sa.Column("ticker", sa.String(32), primary_key=True),
        sa.Column("quantity", sa.Float, nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "quantity > 0",
            name="ck_portfolio_positions_quantity_positive",
        ),
    )


def downgrade() -> None:
    op.drop_table("portfolio_positions")
