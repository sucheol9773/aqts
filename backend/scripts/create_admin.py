"""일회성 admin 사용자 부트스트랩 CLI

본 스크립트는 운영 DB 에 admin 계정이 한 명도 존재하지 않는 환경
(예: 신규 region, DR 복제 후 첫 기동, init_db.sql 기반 부트스트랩
직후) 에서 한 번만 실행되는 것을 전제로 한다. 마이그레이션 002 의
ADMIN_BOOTSTRAP_PASSWORD 자동 시드 경로는 "마이그레이션이 처음
적용되는 시점" 에만 동작하므로, 마이그레이션이 이미 적용된 뒤에는
본 CLI 가 유일한 admin 생성 경로가 된다.

운영 정책:
  - CD 파이프라인은 ADMIN_BOOTSTRAP_PASSWORD 를 알지 못한다.
    비밀번호는 본 CLI 실행 시점에만 셸 환경변수로 일회성 주입된다.
  - 생성 직후 운영자는 즉시 비밀번호를 회전한다 (`/api/users/me`).
  - 본 CLI 는 멱등하다: 이미 admin 역할 사용자가 1명 이상 존재하면
    아무 변경 없이 종료 코드 0 으로 종료한다.

사용:
  ADMIN_BOOTSTRAP_USERNAME=admin \\
  ADMIN_BOOTSTRAP_PASSWORD='<강력한 비밀번호>' \\
  docker run --rm \\
    --network aqts_aqts-network \\
    --env-file ~/aqts/.env \\
    -e ADMIN_BOOTSTRAP_USERNAME \\
    -e ADMIN_BOOTSTRAP_PASSWORD \\
    "$IMAGE_REF" \\
    python -m scripts.create_admin

종료 코드:
  0  성공 또는 이미 존재 (멱등)
  1  필수 환경변수 부재 / 비밀번호 정책 위반 / DB 오류
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("create_admin")


# 비밀번호 정책: 운영 admin 계정의 최소 강도. 회전 후 사용자가 자유롭게
# 변경할 수 있으나, 부트스트랩 시점의 초기값만큼은 본 정책을 강제한다.
MIN_PASSWORD_LENGTH = 12


class AdminBootstrapError(Exception):
    """admin 부트스트랩 실패. 종료 코드 1 로 매핑된다."""


def validate_password(password: str) -> None:
    """비밀번호 정책 검증.

    Raises:
        AdminBootstrapError: 정책 위반 시.
    """
    if not password:
        raise AdminBootstrapError("ADMIN_BOOTSTRAP_PASSWORD 가 비어 있다")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AdminBootstrapError(
            f"ADMIN_BOOTSTRAP_PASSWORD 길이는 최소 {MIN_PASSWORD_LENGTH}자 이상이어야 한다 " f"(현재 {len(password)}자)"
        )
    # 단순 강도 검증: 영문/숫자/특수문자 중 최소 2종류 이상.
    has_alpha = any(c.isalpha() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_symbol = any(not c.isalnum() for c in password)
    classes = sum([has_alpha, has_digit, has_symbol])
    if classes < 2:
        raise AdminBootstrapError("ADMIN_BOOTSTRAP_PASSWORD 는 영문/숫자/특수문자 중 최소 2종류 이상을 포함해야 한다")


def read_env() -> Tuple[str, str]:
    """환경변수에서 username/password 를 읽는다.

    Returns:
        (username, password)

    Raises:
        AdminBootstrapError: 필수 변수 부재 또는 정책 위반.
    """
    username = os.environ.get("ADMIN_BOOTSTRAP_USERNAME", "admin").strip()
    password = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD", "")
    if not username:
        raise AdminBootstrapError("ADMIN_BOOTSTRAP_USERNAME 이 빈 문자열이다")
    validate_password(password)
    return username, password


async def find_admin_role_id(session: AsyncSession) -> int:
    """roles 테이블에서 'admin' 역할의 id 를 조회한다.

    하드코딩 대신 동적으로 조회하여, 추후 마이그레이션이 INSERT
    순서를 바꾸더라도 안전하게 동작한다.

    Raises:
        AdminBootstrapError: admin 역할이 존재하지 않으면 (마이그레이션 002 미적용).
    """
    from db.models.user import Role

    result = await session.execute(select(Role.id).where(Role.name == "admin"))
    role_id: Optional[int] = result.scalar_one_or_none()
    if role_id is None:
        raise AdminBootstrapError(
            "roles 테이블에 'admin' 역할이 존재하지 않는다. " "alembic upgrade head 가 002 까지 적용되었는지 확인하라."
        )
    return role_id


async def admin_already_exists(session: AsyncSession, admin_role_id: int) -> bool:
    """admin 역할 사용자가 1명 이상 존재하는지 확인 (멱등성 체크)."""
    from db.models.user import User

    result = await session.execute(select(User.id).where(User.role_id == admin_role_id).limit(1))
    return result.scalar_one_or_none() is not None


async def create_admin(session: AsyncSession, username: str, password: str) -> str:
    """admin 사용자를 생성한다.

    Returns:
        생성된 사용자 id (UUID).

    Raises:
        AdminBootstrapError: 검증 실패 또는 username 중복.
    """
    from uuid import uuid4

    from api.middleware.auth import AuthService
    from db.models.user import User

    admin_role_id = await find_admin_role_id(session)

    if await admin_already_exists(session, admin_role_id):
        logger.info("admin 역할 사용자가 이미 1명 이상 존재한다. 멱등 종료 (변경 없음).")
        return ""

    # username 중복 방지 (다른 역할로 동일 이름이 존재할 수 있음).
    dup = await session.execute(select(User.id).where(User.username == username))
    if dup.scalar_one_or_none() is not None:
        raise AdminBootstrapError(
            f"username='{username}' 사용자가 이미 존재한다 (admin 역할이 아님). "
            "다른 ADMIN_BOOTSTRAP_USERNAME 을 지정하라."
        )

    user = User(
        id=str(uuid4()),
        username=username,
        email=None,
        password_hash=AuthService.hash_password(password),
        role_id=admin_role_id,
        is_active=True,
        is_locked=False,
        failed_login_attempts=0,
    )
    session.add(user)
    await session.flush()
    logger.info("admin 사용자 생성 완료: username=%s id=%s", username, user.id)
    return user.id


async def main_async() -> int:
    """엔트리포인트. 종료 코드 반환."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    try:
        username, password = read_env()
    except AdminBootstrapError as e:
        logger.error("환경변수 검증 실패: %s", e)
        return 1

    # DB import 는 환경변수 검증 후로 미뤄, 단위테스트가 DB 없이 read_env/
    # validate_password 만 검증할 수 있게 한다.
    from db.database import async_session_factory

    try:
        async with async_session_factory() as session:
            async with session.begin():
                await create_admin(session, username, password)
    except AdminBootstrapError as e:
        logger.error("admin 부트스트랩 실패: %s", e)
        return 1
    except Exception as e:
        logger.exception("DB 작업 중 예기치 못한 오류: %s", e)
        return 1

    logger.info("admin 부트스트랩 완료. 즉시 비밀번호를 회전하라 (/api/users/me).")
    return 0


def main() -> None:
    sys.exit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
