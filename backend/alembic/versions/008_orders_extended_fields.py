"""orders 테이블 확장 — 감사 체인·슬리피지·거래 비용 컬럼 추가

Revision ID: 008
Revises: 007
Create Date: 2026-04-25

설계 근거
--------
contracts/order.py(OrderIntent)와 contracts/execution.py(ExecutionResult)에
decision_id, strategy_id, slippage, commission 등이 정의되어 있으나
001_initial_schema.py 의 orders 테이블에는 미반영. 주문 → 전략 감사 체인
연결, post-mortem 슬리피지 분석, 거래 비용 추적에 필요한 컬럼을 추가한다.

추가 컬럼:
- slippage_bps: 체결가 vs 요청가 슬리피지 (basis points)
- commission: 거래 수수료
- decision_id: 전략 신호 → 주문 감사 체인 연결 ID
- strategy_id: 주문을 생성한 전략 식별자
- submitted_at: KIS API 제출 시각 (created_at 과 구분)
- reason: 주문 사유 (리밸런싱/신호/수동 등)
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("slippage_bps", sa.Numeric(8, 2), nullable=True))
    op.add_column("orders", sa.Column("commission", sa.Numeric(18, 4), nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("decision_id", sa.String(100), nullable=True))
    op.add_column("orders", sa.Column("strategy_id", sa.String(50), nullable=True))
    op.add_column("orders", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("reason", sa.Text, nullable=True))

    # 감사 체인 조회 최적화
    op.create_index("idx_orders_decision_id", "orders", ["decision_id"])
    op.create_index("idx_orders_strategy_id", "orders", ["strategy_id"])


def downgrade() -> None:
    op.drop_index("idx_orders_strategy_id")
    op.drop_index("idx_orders_decision_id")
    op.drop_column("orders", "reason")
    op.drop_column("orders", "submitted_at")
    op.drop_column("orders", "strategy_id")
    op.drop_column("orders", "decision_id")
    op.drop_column("orders", "commission")
    op.drop_column("orders", "slippage_bps")
