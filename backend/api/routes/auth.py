"""
인증 API 라우터

로그인, 토큰 갱신, MFA 관리, 인증 상태 확인 엔드포인트를 제공합니다.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from api.middleware.auth import AuthenticatedUser, AuthService, get_current_user
from api.middleware.rate_limiter import RATE_LOGIN, limiter
from api.schemas.auth import (
    LoginRequest,
    MFADisableRequest,
    MFAEnrollResponse,
    MFAVerifyRequest,
    RefreshTokenRequest,
    TokenResponse,
)
from api.schemas.common import APIResponse
from config.settings import get_settings
from db.database import get_db_session
from db.models.user import User
from db.repositories.audit_log import AuditLogger

router = APIRouter()


@router.post("/login", response_model=APIResponse[TokenResponse])
@limiter.limit(RATE_LOGIN)
async def login(request: Request, login_req: LoginRequest, db_session: AsyncSession = Depends(get_db_session)):
    """
    사용자 로그인

    username + password + optional totp_code로 인증 후 JWT 토큰 쌍 반환.
    """
    try:
        access_token, refresh_token = await AuthService.authenticate(
            username=login_req.username,
            password=login_req.password,
            totp_code=login_req.totp_code,
            db_session=db_session,
        )
        settings = get_settings()

        # 감사 로그
        audit = AuditLogger(db_session)
        await audit.log(
            action_type="LOGIN_SUCCESS",
            module="auth",
            description=f"User {login_req.username} logged in",
        )

        token_data = TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.dashboard.access_token_expire_hours * 3600,
        )

        return APIResponse(success=True, data=token_data, message="로그인 성공")
    except Exception as e:
        # 감사 로그 (실패)
        try:
            audit = AuditLogger(db_session)
            await audit.log(
                action_type="LOGIN_FAILED",
                module="auth",
                description=f"Login attempt failed for {login_req.username}: {str(e)[:100]}",
            )
        except Exception:
            pass
        raise


@router.post("/refresh", response_model=APIResponse[TokenResponse])
async def refresh_token(request: RefreshTokenRequest):
    """
    토큰 갱신

    유효한 Refresh Token으로 새 Access Token을 발급합니다.
    역할은 기존 토큰의 role 클레임을 유지합니다.
    """
    payload = AuthService.verify_token(request.refresh_token)
    settings = get_settings()

    new_access = AuthService.create_access_token(
        {
            "sub": payload.get("sub", "admin"),
            "uid": payload.get("uid"),
            "role": payload.get("role", "viewer"),
        }
    )
    new_refresh = AuthService.create_refresh_token(
        {
            "sub": payload.get("sub", "admin"),
            "uid": payload.get("uid"),
            "role": payload.get("role", "viewer"),
        }
    )

    token_data = TokenResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        expires_in=settings.dashboard.access_token_expire_hours * 3600,
    )

    return APIResponse(success=True, data=token_data, message="토큰 갱신 성공")


@router.post("/logout", response_model=APIResponse[dict])
async def logout(
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    """
    로그아웃 — 현재 토큰을 무효화 (revocation)

    Authorization 헤더의 access token을 블랙리스트에 등록합니다.
    """
    auth_header = request.headers.get("authorization", "")
    token = auth_header.replace("Bearer ", "") if auth_header else ""

    jti = AuthService.revoke_token(token)
    revoked = jti is not None

    # 감사 로그
    audit = AuditLogger(db_session)
    await audit.log(
        action_type="LOGIN_LOGOUT",
        module="auth",
        description=f"User {current_user.username} logged out",
    )

    return APIResponse(
        success=True,
        data={"revoked": revoked, "jti": jti},
        message="로그아웃 완료" if revoked else "토큰에 jti가 없어 revoke 불가 (레거시 토큰)",
    )


@router.get("/me", response_model=APIResponse[dict])
async def get_me(current_user: AuthenticatedUser = Depends(get_current_user)):
    """
    현재 인증된 사용자 정보 확인
    """
    return APIResponse(
        success=True,
        data={
            "id": current_user.id,
            "username": current_user.username,
            "role": current_user.role,
        },
        message="인증 확인됨",
    )


@router.post("/mfa/enroll", response_model=APIResponse[MFAEnrollResponse])
async def mfa_enroll(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    """
    MFA 등록 시작

    TOTP 시크릿 + QR 코드 프로비저닝 URI 발급.
    반환된 URI로 QR 코드 생성하여 인증기 앱에 등록.
    이후 /mfa/verify로 코드 검증하여 활성화.
    """
    # TOTP 시크릿 생성
    secret = AuthService.generate_totp_secret()

    # 프로비저닝 URI 생성 (QR 코드 생성용)
    uri = AuthService.get_provisioning_uri(secret, current_user.username)

    # DB에 임시 저장 (verify 요청 시 확인)
    user = await db_session.get(User, current_user.id)
    if user:
        user.totp_secret = secret
        # totp_enabled는 아직 False (verify 후 True)
        await db_session.commit()

    response_data = MFAEnrollResponse(secret=secret, provisioning_uri=uri)

    return APIResponse(success=True, data=response_data, message="TOTP 시크릿 생성됨. 인증기에 등록하세요.")


@router.post("/mfa/verify", response_model=APIResponse[dict])
async def mfa_verify(
    verify_req: MFAVerifyRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    """
    MFA 검증 및 활성화

    /mfa/enroll에서 받은 시크릿으로 인증기에 등록 후,
    6자리 코드를 입력하여 MFA 활성화.
    """
    user = await db_session.get(User, current_user.id)
    if not user or not user.totp_secret:
        return APIResponse(
            success=False,
            data=None,
            message="TOTP enrollment not started. Call /mfa/enroll first.",
        )

    # 코드 검증
    if not AuthService.verify_totp(user.totp_secret, verify_req.totp_code):
        return APIResponse(success=False, data=None, message="Invalid TOTP code")

    # 활성화
    user.totp_enabled = True
    await db_session.commit()

    # 감사 로그
    audit = AuditLogger(db_session)
    await audit.log(
        action_type="MFA_ENROLLED",
        module="auth",
        description=f"User {current_user.username} enabled MFA (TOTP)",
    )

    return APIResponse(success=True, data={"enabled": True}, message="MFA가 활성화되었습니다.")


@router.post("/mfa/disable", response_model=APIResponse[dict])
async def mfa_disable(
    disable_req: MFADisableRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    """
    MFA 비활성화

    현재 비밀번호로 인증 후 MFA 비활성화.
    """
    user = await db_session.get(User, current_user.id)
    if not user:
        return APIResponse(success=False, data=None, message="User not found")

    # 비밀번호 확인
    if not AuthService.verify_password(disable_req.password, user.password_hash):
        return APIResponse(success=False, data=None, message="Incorrect password")

    # 비활성화
    user.totp_enabled = False
    user.totp_secret = None
    await db_session.commit()

    # 감사 로그
    audit = AuditLogger(db_session)
    await audit.log(
        action_type="MFA_DISABLED",
        module="auth",
        description=f"User {current_user.username} disabled MFA (TOTP)",
    )

    return APIResponse(success=True, data={"enabled": False}, message="MFA가 비활성화되었습니다.")
