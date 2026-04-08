"""
사용자 관리 API 라우터 (Admin only)

사용자 생성, 조회, 업데이트, 비밀번호 리셋, 잠금/해제 기능 제공.
"""

from typing import List
from uuid import uuid4

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.errors import ErrorCode, raise_api_error
from api.middleware.auth import AuthenticatedUser, AuthService
from api.middleware.rbac import require_admin
from api.schemas.common import APIResponse
from api.schemas.users import (
    PasswordResetRequest,
    UserCreateRequest,
    UserLockRequest,
    UserResponse,
    UserUpdateRequest,
)
from config.logging import logger
from db.database import get_db_session
from db.models.user import Role, User
from db.repositories.audit_log import AuditLogger

router = APIRouter()


@router.get("/users", response_model=APIResponse[List[UserResponse]], dependencies=[Depends(require_admin)])
async def list_users(
    current_user: AuthenticatedUser = Depends(require_admin),
    db_session: AsyncSession = Depends(get_db_session),
):
    """
    모든 사용자 목록 조회 (Admin only)
    """
    try:
        result = await db_session.execute(select(User))
        users = result.scalars().all()

        response_data = [
            UserResponse(
                id=u.id,
                username=u.username,
                email=u.email,
                role=u.role.name,
                is_active=u.is_active,
                is_locked=u.is_locked,
                totp_enabled=u.totp_enabled,
                created_at=u.created_at.isoformat(),
                updated_at=u.updated_at.isoformat(),
                last_login_at=u.last_login_at.isoformat() if u.last_login_at else None,
            )
            for u in users
        ]

        return APIResponse(success=True, data=response_data, message=f"총 {len(users)}명의 사용자")
    except Exception as e:
        logger.error(f"Failed to list users: {e}")
        raise_api_error(
            500,
            ErrorCode.USER_STORE_UNAVAILABLE,
            "사용자 목록 조회 중 내부 오류가 발생했습니다.",
        )


@router.get("/users/{user_id}", response_model=APIResponse[UserResponse], dependencies=[Depends(require_admin)])
async def get_user(
    user_id: str,
    current_user: AuthenticatedUser = Depends(require_admin),
    db_session: AsyncSession = Depends(get_db_session),
):
    """
    특정 사용자 조회 (Admin only)
    """
    try:
        user = await db_session.get(User, user_id)
        if not user:
            return APIResponse(success=False, data=None, message="User not found")

        response_data = UserResponse(
            id=user.id,
            username=user.username,
            email=user.email,
            role=user.role.name,
            is_active=user.is_active,
            is_locked=user.is_locked,
            totp_enabled=user.totp_enabled,
            created_at=user.created_at.isoformat(),
            updated_at=user.updated_at.isoformat(),
            last_login_at=user.last_login_at.isoformat() if user.last_login_at else None,
        )

        return APIResponse(success=True, data=response_data, message="User retrieved")
    except Exception as e:
        logger.error(f"Failed to get user: {e}")
        raise_api_error(
            500,
            ErrorCode.USER_STORE_UNAVAILABLE,
            "사용자 조회 중 내부 오류가 발생했습니다.",
        )


@router.post("/users", response_model=APIResponse[UserResponse], dependencies=[Depends(require_admin)])
async def create_user(
    create_req: UserCreateRequest,
    current_user: AuthenticatedUser = Depends(require_admin),
    db_session: AsyncSession = Depends(get_db_session),
):
    """
    새 사용자 생성 (Admin only)
    """
    try:
        # 역할 조회
        result = await db_session.execute(select(Role).where(Role.name == create_req.role))
        role = result.scalars().first()
        if not role:
            return APIResponse(success=False, data=None, message=f"Invalid role: {create_req.role}")

        # username 중복 확인
        existing = await db_session.execute(select(User).where(User.username == create_req.username))
        if existing.scalars().first():
            return APIResponse(success=False, data=None, message="Username already exists")

        # 새 사용자 생성
        user = User(
            id=str(uuid4()),
            username=create_req.username,
            email=create_req.email,
            password_hash=AuthService.hash_password(create_req.password),
            role_id=role.id,
            is_active=True,
        )
        db_session.add(user)
        await db_session.commit()

        # 감사 로그
        audit = AuditLogger(db_session)
        await audit.log(
            action_type="USER_CREATED",
            module="users",
            description=f"User {create_req.username} created by {current_user.username} with role {create_req.role}",
        )

        response_data = UserResponse(
            id=user.id,
            username=user.username,
            email=user.email,
            role=user.role.name,
            is_active=user.is_active,
            is_locked=user.is_locked,
            totp_enabled=user.totp_enabled,
            created_at=user.created_at.isoformat(),
            updated_at=user.updated_at.isoformat(),
            last_login_at=None,
        )

        return APIResponse(success=True, data=response_data, message="User created successfully")
    except Exception as e:
        logger.error(f"Failed to create user: {e}")
        raise_api_error(
            500,
            ErrorCode.USER_STORE_UNAVAILABLE,
            "사용자 생성 중 내부 오류가 발생했습니다.",
        )


@router.patch("/users/{user_id}", response_model=APIResponse[UserResponse], dependencies=[Depends(require_admin)])
async def update_user(
    user_id: str,
    update_req: UserUpdateRequest,
    current_user: AuthenticatedUser = Depends(require_admin),
    db_session: AsyncSession = Depends(get_db_session),
):
    """
    사용자 업데이트 (Admin only) — 역할, 이메일, 활성 여부 변경
    """
    try:
        user = await db_session.get(User, user_id)
        if not user:
            return APIResponse(success=False, data=None, message="User not found")

        changes = []

        # 이메일 변경
        if update_req.email is not None:
            user.email = update_req.email
            changes.append(f"email={update_req.email}")

        # 활성 여부 변경
        if update_req.is_active is not None:
            user.is_active = update_req.is_active
            changes.append(f"is_active={update_req.is_active}")

        # 역할 변경
        if update_req.role is not None:
            role_result = await db_session.execute(select(Role).where(Role.name == update_req.role))
            role = role_result.scalars().first()
            if not role:
                return APIResponse(success=False, data=None, message=f"Invalid role: {update_req.role}")
            user.role_id = role.id
            changes.append(f"role={update_req.role}")

        if changes:
            await db_session.commit()

            # 감사 로그
            audit = AuditLogger(db_session)
            await audit.log(
                action_type="USER_UPDATED",
                module="users",
                description=f"User {user.username} updated by {current_user.username}: {', '.join(changes)}",
            )

        response_data = UserResponse(
            id=user.id,
            username=user.username,
            email=user.email,
            role=user.role.name,
            is_active=user.is_active,
            is_locked=user.is_locked,
            totp_enabled=user.totp_enabled,
            created_at=user.created_at.isoformat(),
            updated_at=user.updated_at.isoformat(),
            last_login_at=user.last_login_at.isoformat() if user.last_login_at else None,
        )

        return APIResponse(
            success=True,
            data=response_data,
            message=f"User updated: {', '.join(changes) if changes else 'No changes'}",
        )
    except Exception as e:
        logger.error(f"Failed to update user: {e}")
        raise_api_error(
            500,
            ErrorCode.USER_STORE_UNAVAILABLE,
            "사용자 업데이트 중 내부 오류가 발생했습니다.",
        )


@router.post("/users/{user_id}/password-reset", response_model=APIResponse[dict], dependencies=[Depends(require_admin)])
async def reset_password(
    user_id: str,
    reset_req: PasswordResetRequest,
    current_user: AuthenticatedUser = Depends(require_admin),
    db_session: AsyncSession = Depends(get_db_session),
):
    """
    사용자 비밀번호 리셋 (Admin only)
    """
    try:
        user = await db_session.get(User, user_id)
        if not user:
            return APIResponse(success=False, data=None, message="User not found")

        user.password_hash = AuthService.hash_password(reset_req.new_password)
        user.failed_login_attempts = 0  # 실패 횟수 초기화
        user.is_locked = False  # 잠금 해제
        await db_session.commit()

        # 감사 로그
        audit = AuditLogger(db_session)
        await audit.log(
            action_type="PASSWORD_RESET",
            module="users",
            description=f"Password reset for user {user.username} by {current_user.username}",
        )

        return APIResponse(success=True, data={"password_reset": True}, message="Password reset successfully")
    except Exception as e:
        logger.error(f"Failed to reset password: {e}")
        raise_api_error(
            500,
            ErrorCode.USER_STORE_UNAVAILABLE,
            "비밀번호 리셋 중 내부 오류가 발생했습니다.",
        )


@router.post(
    "/users/{user_id}/lock",
    response_model=APIResponse[dict],
    dependencies=[Depends(require_admin)],
)
async def lock_user(
    user_id: str,
    lock_req: UserLockRequest,
    current_user: AuthenticatedUser = Depends(require_admin),
    db_session: AsyncSession = Depends(get_db_session),
):
    """
    사용자 계정 잠금/해제 (Admin only)
    """
    try:
        user = await db_session.get(User, user_id)
        if not user:
            return APIResponse(success=False, data=None, message="User not found")

        user.is_locked = lock_req.is_locked
        user.failed_login_attempts = 0  # 실패 횟수 초기화
        await db_session.commit()

        # 감사 로그
        action_type = "USER_LOCKED" if lock_req.is_locked else "USER_UNLOCKED"
        audit = AuditLogger(db_session)
        await audit.log(
            action_type=action_type,
            module="users",
            description=f"User {user.username} {'locked' if lock_req.is_locked else 'unlocked'} by {current_user.username}",
        )

        return APIResponse(
            success=True,
            data={"is_locked": user.is_locked},
            message=f"User {'locked' if lock_req.is_locked else 'unlocked'} successfully",
        )
    except Exception as e:
        logger.error(f"Failed to lock/unlock user: {e}")
        raise_api_error(
            500,
            ErrorCode.USER_STORE_UNAVAILABLE,
            "사용자 잠금/해제 중 내부 오류가 발생했습니다.",
        )


@router.delete("/users/{user_id}", response_model=APIResponse[dict], dependencies=[Depends(require_admin)])
async def delete_user(
    user_id: str,
    current_user: AuthenticatedUser = Depends(require_admin),
    db_session: AsyncSession = Depends(get_db_session),
):
    """
    사용자 삭제 (Admin only) — Soft delete (is_active=False)

    완전 삭제가 아닌 비활성화 처리.
    """
    try:
        user = await db_session.get(User, user_id)
        if not user:
            return APIResponse(success=False, data=None, message="User not found")

        # 자신을 삭제할 수 없음
        if user.id == current_user.id:
            return APIResponse(success=False, data=None, message="Cannot delete your own account")

        user.is_active = False
        await db_session.commit()

        # 감사 로그
        audit = AuditLogger(db_session)
        await audit.log(
            action_type="USER_DELETED",
            module="users",
            description=f"User {user.username} deleted (soft delete) by {current_user.username}",
        )

        return APIResponse(success=True, data={"deleted": True}, message="User deleted (deactivated) successfully")
    except Exception as e:
        logger.error(f"Failed to delete user: {e}")
        raise_api_error(
            500,
            ErrorCode.USER_STORE_UNAVAILABLE,
            "사용자 삭제 중 내부 오류가 발생했습니다.",
        )
