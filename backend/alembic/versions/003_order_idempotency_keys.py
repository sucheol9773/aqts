"""P0-3b: order_idempotency_keys (durable second-tier store)

Revision ID: 003
Revises: 002
Create Date: 2026-04-08

설계 근거 (docs/security/security-integrity-roadmap.md §3.3, §3.6.3)
--------------------------------------------------------------------
Redis 는 주문 idempotency 의 1차(핫) 계층이지만 휘발성이다. Redis evict /
failover / 24h TTL 만료 이후에도 동일 Idempotency-Key 재시도는 이중 주문을
만들어선 안 된다. 따라서 DB 에 영속 계층을 둔다.

- (user_id, route, idempotency_key) 복합 UNIQUE 제약 → DB 수준에서 중복
  주문 원자적 차단. 동시 INSERT 경합 시 나중 트랜잭션이 IntegrityError 로
  실패 → 상위 계층이 `IdempotencyInProgress` 또는 `IdempotencyConflict`
  로 매핑.
- fingerprint(sha256 hex, 64자) 저장 → 동일 키 + 다른 body 재시도 검출.
- status_code + body(JSONB) 저장 → replay 시 동일 응답 재구성.
- created_at / expires_at: expires_at 은 result TTL 기준 (기본 24h).
  별도 janitor/cron 이 `DELETE WHERE expires_at < NOW()` 로 청소한다.
- BRIN index on created_at: 시계열 append-only 특성에 맞춰 B-tree 대비
  공간/쓰기 비용 절감.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "order_idempotency_keys",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("route", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("status_code", sa.Integer, nullable=False),
        sa.Column(
            "response_body",
            sa.dialects.postgresql.JSONB,
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "user_id",
            "route",
            "idempotency_key",
            name="uq_order_idempotency_user_route_key",
        ),
    )

    # 만료 레코드 청소용 — 시계열 insert 특성에 맞춰 BRIN.
    op.execute("CREATE INDEX ix_order_idempotency_expires_at_brin " "ON order_idempotency_keys USING BRIN (expires_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_order_idempotency_expires_at_brin")
    op.drop_table("order_idempotency_keys")
