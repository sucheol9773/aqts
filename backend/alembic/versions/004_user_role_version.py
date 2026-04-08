"""P2-역할 변경 즉시 세션 무효화: users.role_version 컬럼 추가

Revision ID: 004
Revises: 003
Create Date: 2026-04-09

설계 근거 (docs/security/security-integrity-roadmap.md §8, §9 P2)
-----------------------------------------------------------------
P1-보안 에서 `get_current_user` 가 DB 의 현재 `role.name` 과 토큰의 role
클레임을 비교하도록 1차 방어를 도입했지만, 이는 **역할 이름이 실제로 바뀐
경우에만** 동작한다. 다음 시나리오는 여전히 silent window 가 있다:

  1. operator → viewer 강등 후 **다시 operator 로 복구** 한 경우, 이전 토큰이
     계속 operator 로 인가된다 (role.name 이 현재와 같으므로).
  2. 같은 사용자에게 동일 역할이 계속 부여되는데 **권한 scope 만 바뀐** 경우
     (예: scope 필드가 나중에 추가되는 경우) role 이름 비교로 검출 불가.

이를 막기 위해 `users.role_version` 을 단조 증가 카운터로 도입한다:

  - JWT 발급 시 현재 `user.role_version` 을 `rv` 클레임으로 포함.
  - `get_current_user` 가 `token.rv == user.role_version` 을 DB 에서 재확인.
  - admin 이 역할을 변경할 때마다 `user.role_version += 1` 증가 → 기존 토큰의
    `rv` 는 구 버전이므로 자동 무효화.
  - **단조 증가만 허용** — 롤백 / 감소 / 외부 조작을 금지해야 의미가 있다.

컬럼 스펙:
  - 이름: `role_version`
  - 타입: `INTEGER`
  - NOT NULL, default 0
  - server_default="0" 로 기존 레코드도 0 으로 초기화 (alembic 이 자동으로
    추가).

호환성:
  - 기존 토큰: `rv` 클레임 없음 → `get_current_user` 가 None 으로 간주하고
    **fail-closed 로 401 재로그인 요구**. 배포 순간 모든 기존 세션이 끊기지만,
    이는 의도된 동작(정책 상향) 이고 재로그인 비용은 1회 뿐이다.
  - 대안(기존 토큰은 rv 없을 때 통과) 은 롤링 업그레이드 창을 만들지만,
    "역할 변경 즉시 무효화" 라는 본 기능의 핵심 invariant 를 흐리므로
    본 마이그레이션과 동시에 강제 재로그인 정책을 채택한다.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """users 테이블에 role_version INTEGER NOT NULL DEFAULT 0 컬럼 추가."""
    op.add_column(
        "users",
        sa.Column(
            "role_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    """role_version 컬럼 제거.

    주의: downgrade 는 운영 안전망이 아니다 — 다운그레이드 직후 발급된 토큰은
    `rv` 클레임이 없으므로 재업그레이드 시 전부 무효화된다. 운영에서는
    롤백 대신 forward-fix 를 사용한다.
    """
    op.drop_column("users", "role_version")
