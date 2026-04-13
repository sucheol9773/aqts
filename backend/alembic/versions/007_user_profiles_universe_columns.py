"""user_profiles: user_id 추가, universe: market_cap/avg_daily_volume 추가

user_profiles 테이블에 user_id 컬럼 추가 (사용자 식별 FK).
universe 테이블에 market_cap, avg_daily_volume 컬럼 추가 (유동성 필터용).

Revision ID: 007
Revises: 006
"""

from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── user_profiles: user_id 추가 ──
    # 사용자 프로필을 users 테이블과 연결하기 위한 필수 컬럼.
    # 기존 행이 있을 수 있으므로 nullable=True 로 추가 후,
    # 운영에서 데이터 보정 후 NOT NULL 로 전환한다.
    op.add_column(
        "user_profiles",
        sa.Column("user_id", sa.String(100), nullable=True),
    )
    op.create_unique_constraint("uq_user_profiles_user_id", "user_profiles", ["user_id"])
    op.create_index("idx_user_profiles_user_id", "user_profiles", ["user_id"])

    # ── universe: market_cap, avg_daily_volume 추가 ──
    # 유동성 필터 및 시가총액 기반 정렬에 사용.
    # 초기값 NULL 허용 — 수집 시점에 채워진다.
    op.add_column(
        "universe",
        sa.Column("market_cap", sa.Numeric(20, 2), nullable=True),
    )
    op.add_column(
        "universe",
        sa.Column("avg_daily_volume", sa.Numeric(20, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("universe", "avg_daily_volume")
    op.drop_column("universe", "market_cap")
    op.drop_index("idx_user_profiles_user_id", table_name="user_profiles")
    op.drop_constraint("uq_user_profiles_user_id", "user_profiles", type_="unique")
    op.drop_column("user_profiles", "user_id")
