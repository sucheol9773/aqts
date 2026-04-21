"""RBAC: Add roles and users tables with admin bootstrap

Revision ID: 002
Revises: 001
Create Date: 2026-04-07

설계:
  - roles: operator / viewer / admin 3종 기본 역할 (bulk_insert)
  - users: username unique, password_hash bcrypt, role_id FK, TOTP 필드
  - admin 시드: ADMIN_BOOTSTRAP_USERNAME (기본 "admin"), ADMIN_BOOTSTRAP_PASSWORD (필수)
    → 환경변수 미제공 시 insert 스킵, 경고 print
"""

import os
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """RBAC 테이블 생성 및 admin 시드"""

    # roles 테이블
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(20), unique=True, nullable=False),
        sa.Column("description", sa.String(200), nullable=True),
    )

    # 기본 역할 3종 삽입
    op.bulk_insert(
        sa.table(
            "roles",
            sa.column("name", sa.String),
            sa.column("description", sa.String),
        ),
        [
            {"name": "viewer", "description": "Read-only access (모든 조회)"},
            {"name": "operator", "description": "Viewer + 주문/리밸런싱 실행"},
            {"name": "admin", "description": "Operator + 사용자/역할 관리 + 시스템 설정"},
        ],
    )

    # users 테이블
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(50), unique=True, nullable=False),
        sa.Column("email", sa.String(255), unique=True, nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role_id", sa.Integer, sa.ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("is_locked", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("failed_login_attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("totp_secret", sa.Text, nullable=True),
        sa.Column("totp_enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index("ix_users_username", "users", ["username"])
    op.create_index("ix_users_role_id", "users", ["role_id"])

    # Admin 시드 사용자 생성
    _create_admin_seed()


def downgrade() -> None:
    """이전 버전으로 롤백"""
    op.drop_index("ix_users_role_id", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
    op.drop_table("roles")


def _create_admin_seed() -> None:
    """환경변수에서 admin 시드 사용자 생성

    필수: ADMIN_BOOTSTRAP_PASSWORD
    선택: ADMIN_BOOTSTRAP_USERNAME (기본 "admin")

    미제공 시 경고만 출력하고 스킵. 서버 첫 요청 시 401로 유도.
    """
    from uuid import uuid4

    from passlib.context import CryptContext

    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    username = os.environ.get("ADMIN_BOOTSTRAP_USERNAME", "admin").strip()
    password = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD", "").strip()

    if not password:
        print(
            "[WARN] ADMIN_BOOTSTRAP_PASSWORD not set. "
            "Admin user will NOT be created. "
            "Set ADMIN_BOOTSTRAP_PASSWORD environment variable to create admin user."
        )
        return

    password_hash = pwd_context.hash(password)
    admin_id = str(uuid4())

    # admin role id는 3 (INSERT 순서: viewer=1, operator=2, admin=3)
    admin_role_id = 3

    # op.execute() 는 두 번째 인자를 받지 않으므로 bind 를 통해 직접 실행한다.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO users (id, username, email, password_hash, role_id, is_active, is_locked, failed_login_attempts)
            VALUES (:id, :username, :email, :password_hash, :role_id, true, false, 0)
            """
        ),
        {
            "id": admin_id,
            "username": username,
            "email": None,
            "password_hash": password_hash,
            "role_id": admin_role_id,
        },
    )

    print(f"[INFO] Admin user created: username={username}")
