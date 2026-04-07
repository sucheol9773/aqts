"""
역할 기반 접근 제어 (RBAC) 미들웨어

역할 정의:
  - viewer: 모든 조회 가능 (GET)
  - operator: viewer 권한 + 주문/리밸런싱/백테스트 실행
  - admin: operator 권한 + 사용자 관리 + 시스템 설정 변경

사용법:
  @router.get("/api/orders", dependencies=[Depends(require_operator)])
  async def list_orders():
      ...
"""

from fastapi import Depends, HTTPException, status

from api.middleware.auth import AuthenticatedUser, get_current_user


def require_roles(*allowed_roles: str):
    """지정된 역할 중 하나 이상이 필요한 의존성 생성

    Args:
        *allowed_roles: 허용할 역할 명 ("viewer", "operator", "admin")

    Returns:
        FastAPI 의존성 함수
    """

    async def _check_role(current_user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions for this action",
            )
        return current_user

    return _check_role


# 편의 의존성
require_viewer = require_roles("viewer", "operator", "admin")
require_operator = require_roles("operator", "admin")
require_admin = require_roles("admin")
