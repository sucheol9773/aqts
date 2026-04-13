"""rebalancing_history 테이블 추가

Revision ID: 006
Revises: 005
Create Date: 2026-04-13

설계 근거
--------
RebalancingEngine(F-05-03)과 EmergencyRebalancingMonitor(F-05-04)가
리밸런싱 실행 이력을 기록하기 위해 참조하는 테이블.
코드에서 INSERT/SELECT가 이미 구현되어 있었으나 마이그레이션이 누락되어
런타임에 ProgrammingError가 발생하던 스키마-코드 불일치를 해소한다.

컬럼 설계:
- user_id: 사용자 식별자 (멀티유저 대비)
- rebalancing_type: SCHEDULED / EMERGENCY
- trigger_reason: 트리거 사유 (텍스트)
- orders: 실행된 주문 목록 (JSONB)
- old_summary: 리밸런싱 전 포트폴리오 요약 (JSONB)
- new_summary: 리밸런싱 후 포트폴리오 요약 (JSONB)
- executed_at: 실행 시각
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rebalancing_history",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(100), nullable=False),
        sa.Column("rebalancing_type", sa.String(20), nullable=False),
        sa.Column("trigger_reason", sa.Text, nullable=True),
        sa.Column(
            "orders",
            sa.Text,
            nullable=True,
            comment="JSON-encoded list of executed orders",
        ),
        sa.Column(
            "old_summary",
            sa.Text,
            nullable=True,
            comment="JSON-encoded pre-rebalancing portfolio summary",
        ),
        sa.Column(
            "new_summary",
            sa.Text,
            nullable=True,
            comment="JSON-encoded post-rebalancing portfolio summary",
        ),
        sa.Column(
            "executed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    # 사용자별 최근 리밸런싱 조회 최적화
    op.create_index(
        "ix_rebalancing_history_user_type",
        "rebalancing_history",
        ["user_id", "rebalancing_type", "executed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_rebalancing_history_user_type")
    op.drop_table("rebalancing_history")
