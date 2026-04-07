"""
Prometheus 메트릭 모듈

FastAPI 앱에 Prometheus 메트릭 수집을 통합한다.
/metrics 엔드포인트로 Prometheus가 스크래핑한다.

메트릭 카테고리:
  1. HTTP 요청 메트릭 (latency, count, status)
  2. 시스템 컴포넌트 상태 (DB, Redis, Scheduler)
  3. 비즈니스 메트릭 (주문, 포트폴리오, 시그널)
  4. 서킷브레이커 상태
"""

import time
from typing import Callable

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    Info,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# ══════════════════════════════════════
# 1. HTTP 요청 메트릭
# ══════════════════════════════════════
HTTP_REQUEST_DURATION = Histogram(
    "aqts_http_request_duration_seconds",
    "HTTP request latency in seconds",
    labelnames=["method", "endpoint", "status_code"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

HTTP_REQUEST_TOTAL = Counter(
    "aqts_http_requests_total",
    "Total HTTP requests",
    labelnames=["method", "endpoint", "status_code"],
)

HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "aqts_http_requests_in_progress",
    "Number of HTTP requests currently being processed",
    labelnames=["method"],
)

# 환경변수 비표준 bool 표기 사용 추적 (Phase 1 → Phase 2 strict 전환 판단용)
ENV_BOOL_NONSTANDARD_TOTAL = Counter(
    "aqts_env_bool_nonstandard_total",
    "Number of times env_bool() encountered a non-standard literal " "(anything other than 'true'/'false')",
    labelnames=["key", "value"],
)

# ══════════════════════════════════════
# 2. 시스템 컴포넌트 상태
# ══════════════════════════════════════
COMPONENT_HEALTH = Gauge(
    "aqts_component_health",
    "Component health status (1=healthy, 0.5=degraded, 0=unhealthy)",
    labelnames=["component"],
)

SYSTEM_STATUS = Gauge(
    "aqts_system_status",
    "Overall system status (1=healthy, 0.5=degraded, 0=unhealthy)",
)

APP_INFO = Info(
    "aqts_app",
    "AQTS application metadata",
)

# ── KIS API degraded → healthy 자동 복원 추적 ──
# core/data_collector/kis_recovery.py 의 try_recover_kis() 가 호출될 때마다 갱신.
# - attempts_total: 실제 토큰 재발급을 시도한 횟수 (쿨다운으로 스킵된 경우 제외)
# - success_total: 그중 성공한 횟수
# - degraded: 현재 KIS 가 degraded(1) 인지 healthy(0) 인지의 즉시값
# 알림 룰 / 대시보드의 데이터 소스. 시크릿 라벨은 절대 두지 않는다.
KIS_RECOVERY_ATTEMPTS_TOTAL = Counter(
    "aqts_kis_recovery_attempts_total",
    "Total KIS token re-issue attempts after entering degraded state",
)

KIS_RECOVERY_SUCCESS_TOTAL = Counter(
    "aqts_kis_recovery_success_total",
    "Total successful KIS token recoveries from degraded state",
)

KIS_DEGRADED = Gauge(
    "aqts_kis_degraded",
    "Current KIS API degraded flag (1=degraded, 0=healthy)",
)

# ══════════════════════════════════════
# 3. 비즈니스 메트릭
# ══════════════════════════════════════
ORDERS_TOTAL = Counter(
    "aqts_orders_total",
    "Total orders executed",
    labelnames=["side", "status"],
)

PORTFOLIO_VALUE = Gauge(
    "aqts_portfolio_value_krw",
    "Current portfolio value in KRW",
)

DAILY_RETURN_PCT = Gauge(
    "aqts_daily_return_pct",
    "Daily return percentage",
)

SIGNAL_GENERATED = Counter(
    "aqts_signals_generated_total",
    "Total trading signals generated",
    labelnames=["strategy", "direction"],
)

ENSEMBLE_CONFIDENCE = Gauge(
    "aqts_ensemble_confidence",
    "Latest ensemble signal confidence score",
)

# ══════════════════════════════════════
# 4. 서킷브레이커 상태
# ══════════════════════════════════════
CIRCUIT_BREAKER_STATE = Gauge(
    "aqts_circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open, 0.5=half_open)",
    labelnames=["service"],
)

CIRCUIT_BREAKER_FAILURES = Counter(
    "aqts_circuit_breaker_failures_total",
    "Total circuit breaker failure count",
    labelnames=["service"],
)

# ══════════════════════════════════════
# 5. 데이터 수집 메트릭
# ══════════════════════════════════════
DATA_COLLECTION_DURATION = Histogram(
    "aqts_data_collection_duration_seconds",
    "Data collection task duration",
    labelnames=["source"],
    buckets=[1, 5, 10, 30, 60, 120, 300],
)

DATA_COLLECTION_ERRORS = Counter(
    "aqts_data_collection_errors_total",
    "Data collection errors",
    labelnames=["source"],
)


# ══════════════════════════════════════
# 메트릭 미들웨어
# ══════════════════════════════════════
class PrometheusMiddleware(BaseHTTPMiddleware):
    """HTTP 요청에 대한 Prometheus 메트릭 자동 수집 미들웨어"""

    # 메트릭 수집에서 제외할 경로
    SKIP_PATHS = {"/metrics", "/api/system/health", "/healthz", "/ready"}

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path in self.SKIP_PATHS:
            return await call_next(request)

        method = request.method
        HTTP_REQUESTS_IN_PROGRESS.labels(method=method).inc()

        start = time.perf_counter()
        try:
            response = await call_next(request)
            status = str(response.status_code)
        except Exception:
            status = "500"
            raise
        finally:
            duration = time.perf_counter() - start
            endpoint = self._normalize_path(request.url.path)

            HTTP_REQUEST_DURATION.labels(method=method, endpoint=endpoint, status_code=status).observe(duration)
            HTTP_REQUEST_TOTAL.labels(method=method, endpoint=endpoint, status_code=status).inc()
            HTTP_REQUESTS_IN_PROGRESS.labels(method=method).dec()

        return response

    @staticmethod
    def _normalize_path(path: str) -> str:
        """경로 정규화 — ID 파라미터를 {id}로 치환하여 카디널리티 제한"""
        parts = path.strip("/").split("/")
        normalized = []
        for part in parts:
            # UUID 또는 숫자 ID를 {id}로 치환
            if len(part) >= 20 or part.isdigit():
                normalized.append("{id}")
            else:
                normalized.append(part)
        return "/" + "/".join(normalized)


# ══════════════════════════════════════
# /metrics 엔드포인트 핸들러
# ══════════════════════════════════════
async def metrics_endpoint(request: Request) -> Response:
    """Prometheus 스크래핑 엔드포인트"""
    return Response(
        content=generate_latest(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


# ══════════════════════════════════════
# 앱 초기화 함수
# ══════════════════════════════════════
def setup_prometheus(app) -> None:
    """FastAPI 앱에 Prometheus 메트릭 통합

    Args:
        app: FastAPI 앱 인스턴스
    """
    # 미들웨어 등록
    app.add_middleware(PrometheusMiddleware)

    # /metrics 엔드포인트 등록
    app.add_route("/metrics", metrics_endpoint, methods=["GET"])

    # 앱 메타 정보 설정
    APP_INFO.info(
        {
            "version": "0.5.0",
            "environment": "production",
        }
    )
