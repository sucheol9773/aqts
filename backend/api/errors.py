"""
P1-에러 메시지 표준화 — 공통 API 에러 스키마.

설계 원칙:
  1. 모든 4xx/5xx 응답은 동일한 JSON 본문 스키마를 따른다::

       {
         "success": false,
         "error": {
           "code": "USER_STORE_UNAVAILABLE",
           "message": "사용자 저장소를 일시적으로 사용할 수 없습니다.",
           "context": { ... }   # optional, 디버깅/클라이언트 분기용
         }
       }

  2. error_code 는 ``api.errors.ErrorCode`` 에 **enum 으로만** 추가한다.
     임시 문자열은 금지 — 모든 신규 코드는 회고 가능해야 한다.

  3. 라우트에서는 직접 ``HTTPException(detail=...)`` 을 만들지 말고
     ``raise_api_error(status, code, message, **context)`` 을 사용한다.
     기존 dict detail 을 쓰는 경로(P0-3a/P0-4 idempotency, audit)는
     dict 형태 그대로 하위호환으로 허용한다. 글로벌 handler 가 dict
     detail 을 ErrorResponse 본문으로 정규화한다.

  4. ``str(e)`` 를 response detail 로 노출하는 것은 금지 — 내부 스택/쿼리
     정보가 클라이언트에 유출될 수 있다. 예외 객체는 서버 로그에만 남기고
     응답에는 일반화된 메시지 + error_code 만 노출한다.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Optional

from fastapi import HTTPException


class ErrorCode(str, Enum):
    """표준 에러 코드 레지스트리.

    신규 코드는 반드시 이 enum 에 추가한다. 코드 추가 시 docs/api/error-codes.md
    를 함께 업데이트한다.
    """

    # 공통 4xx
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"

    # 5xx / 서비스 상태
    INTERNAL_ERROR = "INTERNAL_ERROR"
    USER_STORE_UNAVAILABLE = "USER_STORE_UNAVAILABLE"
    AUDIT_UNAVAILABLE = "AUDIT_UNAVAILABLE"

    # auth
    INVALID_TOKEN_TYPE = "INVALID_TOKEN_TYPE"
    ROLE_VERSION_MISMATCH = "ROLE_VERSION_MISMATCH"

    # orders / idempotency (P0-3a 레거시 — dict detail 에서 인식)
    IDEMPOTENCY_KEY_REQUIRED = "IDEMPOTENCY_KEY_REQUIRED"
    IDEMPOTENCY_KEY_TOO_LONG = "IDEMPOTENCY_KEY_TOO_LONG"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    IDEMPOTENCY_IN_PROGRESS = "IDEMPOTENCY_IN_PROGRESS"
    IDEMPOTENCY_STORE_UNAVAILABLE = "IDEMPOTENCY_STORE_UNAVAILABLE"
    ORDER_NOT_FOUND = "ORDER_NOT_FOUND"

    # dry-run
    DRY_RUN_SESSION_NOT_FOUND = "DRY_RUN_SESSION_NOT_FOUND"
    DRY_RUN_SESSION_CONFLICT = "DRY_RUN_SESSION_CONFLICT"
    DRY_RUN_UNAVAILABLE = "DRY_RUN_UNAVAILABLE"

    # param_sensitivity
    PARAM_SENSITIVITY_NOT_FOUND = "PARAM_SENSITIVITY_NOT_FOUND"
    PARAM_SENSITIVITY_INVALID_METRIC = "PARAM_SENSITIVITY_INVALID_METRIC"

    # rate limiter (P0-2b 레거시)
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    RATE_LIMIT_STORE_UNAVAILABLE = "RATE_LIMIT_STORE_UNAVAILABLE"


def raise_api_error(
    status_code: int,
    code: ErrorCode | str,
    message: str,
    *,
    headers: Optional[Mapping[str, str]] = None,
    **context: Any,
) -> "None":
    """표준화된 HTTPException 을 발생시킨다.

    Args:
        status_code: HTTP status code
        code: ``ErrorCode`` enum 또는 동일 문자열 값
        message: 사용자/클라이언트 대상 일반화된 메시지 (예외 객체 직접
            노출 금지). 내부 예외 정보는 호출부에서 ``logger.error`` 로
            서버 로그에만 남긴다.
        headers: 추가 응답 헤더 (예: ``Retry-After``)
        **context: ErrorResponse.context 에 포함될 키/값 쌍

    Raises:
        HTTPException: detail 이 dict 형태 — 글로벌 handler 가 이를
            ``ErrorResponse`` 본문으로 정규화한다.
    """
    code_value = code.value if isinstance(code, ErrorCode) else str(code)
    detail: dict[str, Any] = {
        "error_code": code_value,
        "message": message,
    }
    if context:
        detail["context"] = dict(context)
    raise HTTPException(
        status_code=status_code,
        detail=detail,
        headers=dict(headers) if headers else None,
    )


def normalize_error_body(status_code: int, detail: Any) -> dict[str, Any]:
    """HTTPException.detail 을 표준 ErrorResponse 본문으로 정규화한다.

    - dict 인 경우: ``error_code`` / ``message`` / ``context`` 필드를 추출.
      ``error_code`` 가 없으면 상태 코드 기반 기본 매핑을 사용한다.
    - str 인 경우: ``message`` 로 사용하고 ``error_code`` 는 상태 코드
      기반 기본값을 사용 (점진적 마이그레이션 허용).
    - 그 외: ``str(detail)`` 로 강제 변환 + 기본 코드.
    """
    default_code = _default_code_for_status(status_code)

    if isinstance(detail, Mapping):
        error_code = str(detail.get("error_code") or default_code)
        message = str(detail.get("message") or detail.get("detail") or error_code)
        body: dict[str, Any] = {
            "success": False,
            "error": {"code": error_code, "message": message},
        }
        context = detail.get("context")
        if context is not None:
            body["error"]["context"] = context
        # Preserve extra top-level legacy fields (e.g. Retry-After already in
        # header). Any extra keys in `detail` that are not handled above can
        # be surfaced under `context` for diagnostic purposes.
        extras = {k: v for k, v in detail.items() if k not in {"error_code", "message", "detail", "context"}}
        if extras:
            body["error"].setdefault("context", {}).update(extras)
        return body

    if isinstance(detail, str):
        return {
            "success": False,
            "error": {"code": default_code, "message": detail},
        }

    return {
        "success": False,
        "error": {"code": default_code, "message": str(detail)},
    }


def _default_code_for_status(status_code: int) -> str:
    """상태 코드 → 기본 ErrorCode 매핑 (legacy detail 경로 호환)."""
    if status_code == 400:
        return ErrorCode.VALIDATION_ERROR.value
    if status_code == 401:
        return ErrorCode.UNAUTHORIZED.value
    if status_code == 403:
        return ErrorCode.FORBIDDEN.value
    if status_code == 404:
        return ErrorCode.NOT_FOUND.value
    if status_code == 409:
        return ErrorCode.CONFLICT.value
    if status_code == 422:
        return ErrorCode.VALIDATION_ERROR.value
    if 500 <= status_code < 600:
        return ErrorCode.INTERNAL_ERROR.value
    return ErrorCode.INTERNAL_ERROR.value
