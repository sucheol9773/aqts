"""
인증 API 라우터

로그인, 토큰 갱신, 인증 상태 확인 엔드포인트를 제공합니다.
"""

from fastapi import APIRouter, Depends
from starlette.requests import Request

from api.middleware.auth import AuthService, get_current_user
from api.middleware.rate_limiter import RATE_LOGIN, limiter
from api.schemas.auth import LoginRequest, RefreshTokenRequest, TokenResponse
from api.schemas.common import APIResponse
from config.settings import get_settings

router = APIRouter()


@router.post("/login", response_model=APIResponse[TokenResponse])
@limiter.limit(RATE_LOGIN)
async def login(request: Request, login_req: LoginRequest):
    """
    대시보드 로그인

    비밀번호 인증 후 JWT 토큰 쌍(access + refresh)을 반환합니다.
    """
    access_token, refresh_token = AuthService.authenticate(login_req.password)
    settings = get_settings()

    token_data = TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.dashboard.access_token_expire_hours * 3600,
    )

    return APIResponse(success=True, data=token_data, message="로그인 성공")


@router.post("/refresh", response_model=APIResponse[TokenResponse])
async def refresh_token(request: RefreshTokenRequest):
    """
    토큰 갱신

    유효한 Refresh Token으로 새 Access Token을 발급합니다.
    """
    payload = AuthService.verify_token(request.refresh_token)
    settings = get_settings()

    new_access = AuthService.create_access_token({"sub": payload.get("sub", "admin")})
    new_refresh = AuthService.create_refresh_token({"sub": payload.get("sub", "admin")})

    token_data = TokenResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        expires_in=settings.dashboard.access_token_expire_hours * 3600,
    )

    return APIResponse(success=True, data=token_data, message="토큰 갱신 성공")


@router.get("/me", response_model=APIResponse[dict])
async def get_me(current_user: str = Depends(get_current_user)):
    """
    현재 인증된 사용자 정보 확인
    """
    return APIResponse(
        success=True,
        data={"username": current_user, "role": "admin"},
        message="인증 확인됨",
    )
