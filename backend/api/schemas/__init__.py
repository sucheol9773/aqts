"""
API 스키마 패키지

모든 API 요청/응답 모델을 일관되게 관리합니다.
"""

from .alerts import (
    AlertListResponse,
    AlertResponse,
    AlertStatsResponse,
)
from .auth import (
    LoginRequest,
    RefreshTokenRequest,
    TokenResponse,
)
from .common import (
    APIResponse,
    ErrorResponse,
    PaginatedResponse,
)
from .orders import (
    BatchOrderRequest,
    BatchOrderResponse,
    OrderCreateRequest,
    OrderResponse,
)
from .portfolio import (
    PerformanceResponse,
    PortfolioSummaryResponse,
    PositionResponse,
)
from .profile import (
    ProfileResponse,
    ProfileUpdateRequest,
)

__all__ = [
    # Common
    "APIResponse",
    "ErrorResponse",
    "PaginatedResponse",
    # Auth
    "LoginRequest",
    "TokenResponse",
    "RefreshTokenRequest",
    # Portfolio
    "PositionResponse",
    "PortfolioSummaryResponse",
    "PerformanceResponse",
    # Orders
    "OrderCreateRequest",
    "OrderResponse",
    "BatchOrderRequest",
    "BatchOrderResponse",
    # Profile
    "ProfileUpdateRequest",
    "ProfileResponse",
    # Alerts
    "AlertResponse",
    "AlertStatsResponse",
    "AlertListResponse",
]
