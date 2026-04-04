"""
API Rate Limiting 미들웨어 (Gate B 보안 요건)

slowapi 기반 요청 제한을 제공합니다.

전략:
- 로그인: 5회/분 (브루트포스 방지)
- 주문 생성: 10회/분 (과도한 매매 방지)
- 일반 API: 60회/분
- 시스템/헬스: 제한 없음

사용법:
    from api.middleware.rate_limiter import limiter, rate_limit_exceeded_handler

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
"""

import os

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse

from config.logging import logger

# ══════════════════════════════════════
# Rate Limiter 인스턴스
# ══════════════════════════════════════
# 테스트 환경에서는 Rate Limiting 비활성화 (TESTING=1 환경변수)
_is_testing = os.environ.get("TESTING", "0") == "1"

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["60/minute"],
    storage_uri="memory://",  # 프로덕션에서는 Redis URI로 교체
    enabled=not _is_testing,
)


# ══════════════════════════════════════
# Rate Limit 초과 핸들러
# ══════════════════════════════════════
async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """
    Rate Limit 초과 시 JSON 응답 반환

    429 Too Many Requests를 AQTS 표준 응답 형식으로 반환합니다.
    """
    client_ip = get_remote_address(request)
    logger.warning(f"Rate limit exceeded: {client_ip} -> {request.url.path} " f"(limit: {exc.detail})")
    return JSONResponse(
        status_code=429,
        content={
            "success": False,
            "data": None,
            "message": f"요청 한도 초과. 잠시 후 다시 시도하세요. (제한: {exc.detail})",
        },
    )


# ══════════════════════════════════════
# 엔드포인트별 Rate Limit 상수
# ══════════════════════════════════════
# 라우터에서 @limiter.limit(RATE_LOGIN) 형태로 사용
RATE_LOGIN = "5/minute"
RATE_ORDER = "10/minute"
RATE_GENERAL = "60/minute"
RATE_PIPELINE = "5/minute"
