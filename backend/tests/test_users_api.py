"""
사용자 관리 API 테스트

Admin only 엔드포인트:
  - GET /api/users (목록)
  - GET /api/users/{id} (상세)
  - POST /api/users (생성)
  - PATCH /api/users/{id} (업데이트)
  - POST /api/users/{id}/password-reset (비밀번호 리셋)
  - POST /api/users/{id}/lock (잠금/해제)
  - DELETE /api/users/{id} (삭제)
"""

import pytest


@pytest.mark.asyncio
class TestUsersAPI:
    """사용자 관리 API 테스트"""

    async def test_users_list_admin_only(self, admin_token):
        """GET /users는 admin만 조회 가능"""
        # TODO: 통합 테스트 (DB 세션 필요)
        pass

    async def test_user_create(self, admin_token):
        """POST /users 새 사용자 생성"""
        # TODO: 통합 테스트
        pass

    async def test_user_update(self, admin_token):
        """PATCH /users/{id} 사용자 정보 수정"""
        # TODO: 통합 테스트
        pass

    async def test_user_password_reset(self, admin_token):
        """POST /users/{id}/password-reset 비밀번호 리셋"""
        # TODO: 통합 테스트
        pass

    async def test_user_lock_unlock(self, admin_token):
        """POST /users/{id}/lock 잠금/해제"""
        # TODO: 통합 테스트
        pass

    async def test_user_delete(self, admin_token):
        """DELETE /users/{id} 사용자 삭제 (soft delete)"""
        # TODO: 통합 테스트
        pass

    async def test_insufficient_permissions(self, viewer_token, operator_token):
        """Viewer/Operator는 /users 접근 불가"""
        # TODO: 통합 테스트
        pass

    async def test_user_crud_workflow(self, admin_token):
        """사용자 CRUD 전체 워크플로우"""
        # TODO: 통합 테스트
        pass
