"""
투자자 프로필 API 라우터

투자자 프로필 조회·수정 엔드포인트를 제공합니다.
InvestorProfileManager와 직접 연동하여 DB 기반 프로필을 관리합니다.
"""

from fastapi import APIRouter, Depends

from api.middleware.rbac import require_operator, require_viewer
from api.schemas.common import APIResponse
from api.schemas.profile import ProfileResponse, ProfileUpdateRequest
from config.constants import InvestmentGoal, InvestmentStyle, RiskProfile
from config.logging import logger
from core.portfolio_manager.profile import InvestorProfileManager

router = APIRouter()


def _profile_to_response(profile) -> ProfileResponse:
    """InvestorProfile → ProfileResponse 변환 헬퍼"""
    return ProfileResponse(
        risk_profile=profile.risk_profile.value,
        investment_style=profile.investment_style.value,
        investment_goal=profile.investment_purpose,
        initial_capital=profile.seed_capital,
        max_loss_tolerance=profile.loss_tolerance,
        created_at=profile.created_at.isoformat() if profile.created_at else None,
        updated_at=profile.updated_at.isoformat() if profile.updated_at else None,
    )


@router.get("/", response_model=APIResponse[ProfileResponse])
async def get_profile(current_user=Depends(require_viewer)):
    """
    현재 투자자 프로필 조회

    DB에서 현재 사용자의 프로필을 조회합니다.
    프로필이 없으면 기본 프로필을 반환합니다.
    """
    try:
        manager = InvestorProfileManager()
        profile = await manager.get_profile(current_user)

        if profile is None:
            # 프로필 미존재 시 기본 프로필 반환
            logger.info(f"No profile found for user {current_user}, returning defaults")
            default_profile = ProfileResponse(
                risk_profile=RiskProfile.BALANCED.value,
                investment_style=InvestmentStyle.ADVISORY.value,
                investment_goal=InvestmentGoal.WEALTH_GROWTH.value,
                initial_capital=50_000_000,
                max_loss_tolerance=0.10,
            )
            return APIResponse(
                success=True,
                data=default_profile,
                message="기본 프로필입니다. 프로필을 설정해 주세요.",
            )

        return APIResponse(success=True, data=_profile_to_response(profile))
    except Exception as e:
        logger.error(f"Profile query error: {e}")
        return APIResponse(success=False, message=f"조회 실패: {str(e)}")


@router.put("/", response_model=APIResponse[ProfileResponse])
async def update_profile(
    request: ProfileUpdateRequest,
    current_user=Depends(require_operator),
):
    """
    투자자 프로필 수정

    위험 성향, 투자 스타일, 투자 목적, 초기 자본금 등을 수정합니다.
    프로필이 없으면 새로 생성합니다.
    """
    try:
        manager = InvestorProfileManager()
        existing = await manager.get_profile(current_user)

        if existing is None:
            # 프로필 미존재 → 신규 생성
            logger.info(f"Creating new profile for user {current_user}")
            profile = await manager.create_profile(
                user_id=current_user,
                risk_profile=RiskProfile(request.risk_profile),
                seed_capital=request.initial_capital,
                investment_purpose=request.investment_goal,
                investment_style=InvestmentStyle(request.investment_style),
                loss_tolerance=request.max_loss_tolerance or 0.10,
            )
        else:
            # 기존 프로필 갱신
            update_kwargs = {}
            if request.risk_profile:
                update_kwargs["risk_profile"] = RiskProfile(request.risk_profile)
            if request.investment_style:
                update_kwargs["investment_style"] = InvestmentStyle(request.investment_style)
            if request.investment_goal:
                update_kwargs["investment_purpose"] = request.investment_goal
            if request.initial_capital:
                update_kwargs["seed_capital"] = request.initial_capital
            if request.max_loss_tolerance is not None:
                update_kwargs["loss_tolerance"] = request.max_loss_tolerance

            profile = await manager.update_profile(current_user, **update_kwargs)

        logger.info(f"Profile updated: {request.model_dump(exclude_none=True)}")
        return APIResponse(
            success=True,
            data=_profile_to_response(profile),
            message="프로필이 수정되었습니다.",
        )
    except Exception as e:
        logger.error(f"Profile update error: {e}")
        return APIResponse(success=False, message=f"수정 실패: {str(e)}")
