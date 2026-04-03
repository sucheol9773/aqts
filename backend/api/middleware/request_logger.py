"""Request logging middleware for AQTS API."""

import time
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from config.logging import logger


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log HTTP request details including method, path, status, and duration."""

    # Endpoints to skip logging
    SKIP_PATHS = {"/health", "/healthz", "/ready"}

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Log request details and pass to next middleware/handler.

        Args:
            request: HTTP request
            call_next: Next middleware/handler in chain

        Returns:
            HTTP response
        """
        # Skip logging for health check endpoints
        if request.url.path in self.SKIP_PATHS:
            return await call_next(request)

        # Record start time
        start_time = time.time()

        # Call next middleware/handler
        response = await call_next(request)

        # Calculate duration in milliseconds
        duration_ms = (time.time() - start_time) * 1000

        # Log request details
        logger.info(
            "HTTP request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": f"{duration_ms:.2f}",
            },
        )

        return response
