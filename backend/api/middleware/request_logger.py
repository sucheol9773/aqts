"""Request logging middleware for AQTS API."""

import time
import uuid
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from config.logging import logger


def _get_trace_id() -> str:
    """현재 OpenTelemetry trace ID를 가져옵니다 (없으면 빈 문자열)."""
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.trace_id:
            return format(ctx.trace_id, "032x")
    except (ImportError, Exception):
        pass
    return ""


def _get_span_id() -> str:
    """현재 OpenTelemetry span ID를 가져옵니다 (없으면 빈 문자열)."""
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.span_id:
            return format(ctx.span_id, "016x")
    except (ImportError, Exception):
        pass
    return ""


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log HTTP request details including method, path, status, and duration.

    모든 요청에 request_id (X-Request-ID)를 부여하고,
    클라이언트가 보낸 correlation_id (X-Correlation-ID)가 있으면 전파합니다.
    OpenTelemetry가 활성화되면 trace_id/span_id도 로그에 포함됩니다.
    """

    # Endpoints to skip logging
    SKIP_PATHS = {"/health", "/healthz", "/ready"}

    # 헤더 이름
    REQUEST_ID_HEADER = "X-Request-ID"
    CORRELATION_ID_HEADER = "X-Correlation-ID"

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Log request details and pass to next middleware/handler.

        요청 처리 흐름:
        1. request_id 생성 (또는 클라이언트가 보낸 값 사용)
        2. correlation_id 전파 (없으면 request_id를 사용)
        3. request.state에 저장 → 하위 핸들러에서 접근 가능
        4. 응답 헤더에 X-Request-ID, X-Correlation-ID, trace_id 포함

        Args:
            request: HTTP request
            call_next: Next middleware/handler in chain

        Returns:
            HTTP response with tracking headers
        """
        # Skip logging for health check endpoints
        if request.url.path in self.SKIP_PATHS:
            return await call_next(request)

        # ── ID 생성/전파 ──
        request_id = request.headers.get(self.REQUEST_ID_HEADER) or str(uuid.uuid4())
        correlation_id = request.headers.get(self.CORRELATION_ID_HEADER) or request_id

        # request.state에 저장 → 하위 핸들러/서비스에서 접근 가능
        request.state.request_id = request_id
        request.state.correlation_id = correlation_id

        # Record start time
        start_time = time.time()

        # Call next middleware/handler
        response = await call_next(request)

        # Calculate duration in milliseconds
        duration_ms = (time.time() - start_time) * 1000

        # ── 응답 헤더에 추적 ID 포함 ──
        response.headers[self.REQUEST_ID_HEADER] = request_id
        response.headers[self.CORRELATION_ID_HEADER] = correlation_id

        # OpenTelemetry trace ID 전파 (Jaeger UI 연동용)
        trace_id = _get_trace_id()
        if trace_id:
            response.headers["X-Trace-ID"] = trace_id

        # Log request details
        log_extra = {
            "request_id": request_id,
            "correlation_id": correlation_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": f"{duration_ms:.2f}",
        }

        # OTel trace 정보가 있으면 로그에 포함
        if trace_id:
            log_extra["trace_id"] = trace_id
            log_extra["span_id"] = _get_span_id()

        logger.info("HTTP request", extra=log_extra)

        return response
