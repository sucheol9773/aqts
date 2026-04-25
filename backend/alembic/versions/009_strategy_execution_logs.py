"""strategy_execution_logs 테이블 추가

Revision ID: 009
Revises: 008
Create Date: 2026-04-25

설계 근거
--------
전략 앙상블 실행 이력을 DB 레벨에서 추적한다. 현재 전략 실행 결과는
로그와 Prometheus 메트릭으로만 관측 가능하며, 실행 이력 조회·레짐별
성과 분석·게이트 차단 패턴 분석이 불가능하다.

DynamicEnsembleRunner(strategy_ensemble/runner.py)의 실행 완료 시점에
기록하며, 레짐 판정·앙상블 신호·게이트 결과·실행 상태를 포함한다.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "strategy_execution_logs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column("strategy_name", sa.String(100), nullable=False),
        # 레짐 판정
        sa.Column("regime", sa.String(32), nullable=False),
        sa.Column("regime_confidence", sa.Float, nullable=False),
        # 앙상블 신호
        sa.Column("ensemble_signal", sa.Float, nullable=False),
        sa.Column("ensemble_confidence", sa.Float, nullable=False),
        sa.Column(
            "weights_used",
            sa.Text,
            nullable=True,
            comment="JSON-encoded strategy weights: {MEAN_REVERSION: 0.45, ...}",
        ),
        # 행동 결정
        sa.Column("final_action", sa.String(10), nullable=False),
        sa.Column("signals_generated", sa.Integer, nullable=False, server_default="0"),
        sa.Column("orders_submitted", sa.Integer, nullable=False, server_default="0"),
        # 게이트 결과
        sa.Column(
            "gate_results",
            sa.Text,
            nullable=True,
            comment="JSON-encoded gate results: {DataGate: PASS, SignalGate: BLOCK, ...}",
        ),
        # 실행 상태
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("execution_duration_ms", sa.Integer, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "executed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    # 종목별 시계열 조회
    op.create_index(
        "idx_strategy_exec_ticker_time",
        "strategy_execution_logs",
        ["ticker", sa.text("executed_at DESC")],
    )
    # 상태별 필터 (ERROR/BLOCKED 추적)
    op.create_index(
        "idx_strategy_exec_status",
        "strategy_execution_logs",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("idx_strategy_exec_status")
    op.drop_index("idx_strategy_exec_ticker_time")
    op.drop_table("strategy_execution_logs")
