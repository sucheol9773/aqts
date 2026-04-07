"""
RBAC 모델 — Users, Roles, 권한 관리

설계:
  - Role: operator / viewer / admin 3종 역할
  - User: username (unique, CI), password_hash (bcrypt), 활성/잠금/TOTP 상태
  - 연동: 감사 로그, 토큰 페이로드 (role 클레임)
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.database import Base


class Role(Base):
    """역할(Role) 모델

    operator / viewer / admin 3종 기본 역할.
    """

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(200))

    # Relationships
    users: Mapped[list["User"]] = relationship("User", back_populates="role")

    def __repr__(self) -> str:
        return f"<Role {self.name}>"


class User(Base):
    """사용자(User) 모델

    필드:
      - id: UUID (primary key)
      - username: 사용자명 (unique, case-insensitive)
      - email: 이메일 (unique, nullable)
      - password_hash: bcrypt 해시
      - role_id: 역할 FK
      - is_active: 활성 여부 (기본 True)
      - is_locked: 계정 잠금 여부 (failed login × 5 시 자동 잠금)
      - failed_login_attempts: 연속 실패 횟수
      - totp_secret: TOTP 시크릿 (nullable)
      - totp_enabled: TOTP 활성화 여부
      - created_at: 생성 시각
      - updated_at: 수정 시각
      - last_login_at: 마지막 로그인 시각 (nullable)
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role_id: Mapped[int] = mapped_column(Integer, ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    totp_secret: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    role: Mapped[Role] = relationship("Role", back_populates="users")

    def __repr__(self) -> str:
        return f"<User {self.username} ({self.role.name})>"
