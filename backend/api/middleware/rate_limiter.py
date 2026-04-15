"""
API Rate Limiting 미들웨어 (P0-2b, security-integrity-roadmap §3.2)

전략
----
- 로그인: 5/min  (브루트포스 방지)
- 주문: 10/min  (과도한 매매 방지)
- 일반: 60/min
- 시스템/헬스: 제한 없음

P0-2b 변경
-----------
1. **storage**: `memory://` → 운영은 Redis (`settings.redis.url`).
   `TESTING=true` 일 때만 인메모리. 멀티 인스턴스 일관성 확보.
2. **key 함수**: 단일 IP 키 → `_composite_rate_key(request)`.
   인증된 요청은 `user:<sub>`, 미인증은 `ip:<addr>`. NAT 뒤의 합법 사용자가
   다른 사용자의 throttle 에 영향을 받지 않게 분리하면서, 동시에 무인증 무차
   별 공격에 대해서는 IP 기반으로 차단.
3. **fail-mode**: slowapi 기본값 (`swallow_errors=False`,
   `in_memory_fallback_enabled=False`) 을 명시 적용 → Redis 장애 시 limits
   가 `StorageError` 를 raise 하고 핸들러가 503 으로 변환 (fail-closed).
   "Redis 가 죽으면 throttle 을 전부 풀어버린다" 패턴을 금지한다.
4. **메트릭**: `aqts_rate_limit_exceeded_total{route}` (429 발생),
   `aqts_rate_limit_storage_failure_total` (백엔드 장애 → 503).

문서: docs/security/security-integrity-roadmap.md §3.2, §3.6
"""

from __future__ import annotations

from typing import Optional

from jose import JWTError, jwt
from limits.errors import StorageError
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse

from config.logging import logger
from config.settings import get_settings
from core.monitoring.metrics import (
    RATE_LIMIT_EXCEEDED_TOTAL,
    RATE_LIMIT_STORAGE_FAILURE_TOTAL,
)
from core.utils.env import env_bool


# ══════════════════════════════════════
# 복합 키 함수
# ══════════════════════════════════════
def _extract_user_sub(request: Request) -> Optional[str]:
    """Authorization 헤더에서 sub 클레임 추출 (서명 미검증, 키 분리 목적).

    rate limit 키 생성용으로만 사용한다. 인증/인가는 별도의 의존성에서 수행
    되며, 본 함수는 단지 "어떤 사용자 슬롯에 카운트할지" 만 결정한다. 따라서
    JWT 무결성을 다시 검증하지 않는다. 토큰이 위조되면 위조된 sub 의 슬롯이
    소진될 뿐이며, 이는 공격자 본인의 throttle 만 깎는 형태로 귀결된다.
    """
    auth_header = request.headers.get("authorization") or ""
    if not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header.split(None, 1)[1].strip()
    if not token:
        return None
    try:
        claims = jwt.get_unverified_claims(token)
    except JWTError:
        return None
    sub = claims.get("sub")
    return str(sub) if sub else None


def composite_rate_key(request: Request) -> str:
    """rate limit 키.

    - 인증 토큰이 있으면 `user:<sub>` (계정 단위 throttle)
    - 없으면 `ip:<remote_addr>` (무인증 공격 차단)
    """
    sub = _extract_user_sub(request)
    if sub is not None:
        return f"user:{sub}"
    return f"ip:{get_remote_address(request)}"


# ══════════════════════════════════════
# Limiter 인스턴스
# ══════════════════════════════════════
_is_testing = env_bool("TESTING", default=False)


def _resolve_storage_uri() -> str:
    """테스트 모드는 in-memory, 그 외에는 Redis URL."""
    if _is_testing:
        return "memory://"
    return get_settings().redis.url


limiter = Limiter(
    key_func=composite_rate_key,
    default_limits=["60/minute"],
    storage_uri=_resolve_storage_uri(),
    # fail-closed 강제: storage 에러를 삼키지 않고, in-memory fallback 도 끔.
    swallow_errors=False,
    in_memory_fallback_enabled=False,
    enabled=not _is_testing,
)


# ══════════════════════════════════════
# 예외 핸들러
# ══════════════════════════════════════
async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """429 핸들러 — 정상 throttle 작동."""
    route = request.url.path
    RATE_LIMIT_EXCEEDED_TOTAL.labels(route=route).inc()
    logger.warning(f"Rate limit exceeded: key={composite_rate_key(request)} " f"route={route} detail={exc.detail}")
    return JSONResponse(
        status_code=429,
        content={
            "success": False,
            "data": None,
            "message": (f"요청 한도 초과. 잠시 후 다시 시도하세요. (제한: {exc.detail})"),
        },
    )


async def rate_limit_storage_unavailable_handler(request: Request, exc: StorageError) -> JSONResponse:
    """Redis 장애 등 storage 백엔드 실패 → 503 (fail-closed)."""
    RATE_LIMIT_STORAGE_FAILURE_TOTAL.inc()
    logger.error(f"Rate limit storage unavailable: route={request.url.path} " f"err={exc.__class__.__name__}")
    return JSONResponse(
        status_code=503,
        content={
            "success": False,
            "data": None,
            "error_code": "RATE_LIMIT_STORE_UNAVAILABLE",
            "message": "요청 한도 저장소가 일시적으로 사용 불가합니다.",
        },
    )


# ══════════════════════════════════════
# 엔드포인트별 Rate Limit 상수
# ══════════════════════════════════════
RATE_LOGIN = "5/minute"
RATE_ORDER = "10/minute"
RATE_GENERAL = "60/minute"
RATE_PIPELINE = "5/minute"
