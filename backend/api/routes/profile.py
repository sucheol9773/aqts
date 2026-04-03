"""
투자자 프로필 API 라우터

투자자 프로필 조회·수정 엔드포인트를 제공합니다.
"""

from fastapi import APIRouter, Depends

from api.middleware.auth import get_current_user
from api.schemas.common import APIResponse
from api.schemas.profile import ProfileResponse, ProfileUpdateRequest
from config.constants import InvestmentGoal, InvestmentStyle, RiskProfile
from config.logging import logger

router = APIRouter()


@router.get("/", response_model=APIResponse[ProfileResponse])
async def get_profile(current_user: str = Depends(get_current_user)):
    """
    현재 투자자 프로필 조회
    """
    try:
        # TODO: InvestorProfileManager 연동
        profile = ProfileResponse(
            risk_profile=RiskProfile.BALANCED,
            investment_style=InvestmentStyle.ADVISORY,
            investment_goal=InvestmentGoal.WEALTH_GROWTH,
            initial_capital=50_000_000,
            max_loss_tolerance=0.10,
        )
        return APIResponse(success=True, data=profile)
    except Exception as e:
        logger.error(f"Profile query error: {e}")
        return APIResponse(success=False, message=f"조회 실패: {str(e)}")


@router.put("/", response_model=APIResponse[ProfileResponse])
async def update_profile(
    request: ProfileUpdateRequest,
    current_user: str = Depends(get_current_user),
):
    """
    투자자 프로필 수정

    위험 성향, 투자 스타일, 투자 목적, 초기 자본금 등을 수정합니다.
    """
    try:
        # TODO: InvestorProfileManager 연동
        logger.info(f"Profile updated: {request.model_dump(exclude_none=True)}")

        updated = ProfileResponse(
            risk_profile=request.risk_profile or RiskProfile.BALANCED,
            investment_style=request.investment_style or InvestmentStyle.ADVISORY,
            investment_goal=request.investment_goal or InvestmentGoal.WEALTH_GROWTH,
            initial_capital=request.initial_capital or 50_000_000,
            max_loss_tolerance=request.max_loss_tolerance or 0.10,
        )
        return APIResponse(success=True, data=updated, message="프로필이 수정되었습니다.")
    except Exception as e:
        logger.error(f"Profile update error: {e}")
        return APIResponse(success=False, message=f"수정 실패: {str(e)}")
