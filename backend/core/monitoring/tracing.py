"""
AQTS OpenTelemetry 분산 추적 설정

FastAPI, SQLAlchemy, httpx, Redis에 대한 자동 계측(instrumentation)과
OTLP Collector 내보내기를 설정합니다.

환경변수:
    OTEL_ENABLED: true/false (기본: false — 프로덕션에서만 활성화)
    OTEL_SERVICE_NAME: 서비스 이름 (기본: aqts-backend)
    OTEL_EXPORTER_OTLP_ENDPOINT: OTLP Collector 주소 (기본: http://otel-collector:4317)
    OTEL_TRACES_SAMPLER_ARG: 샘플링 비율 (기본: 1.0 = 전수)
"""

import os
from typing import Optional

from loguru import logger


def _is_otel_enabled() -> bool:
    """OpenTelemetry 활성화 여부 확인"""
    return os.environ.get("OTEL_ENABLED", "false").lower() == "true"


def setup_tracing(app=None) -> Optional[object]:
    """OpenTelemetry 분산 추적을 초기화합니다.

    OTEL_ENABLED=true일 때만 활성화됩니다.
    테스트 환경(TESTING=1)에서는 자동 비활성화됩니다.

    Args:
        app: FastAPI 앱 인스턴스 (자동 계측용)

    Returns:
        TracerProvider 또는 None (비활성화 시)
    """
    if os.environ.get("TESTING") == "1":
        logger.debug("OpenTelemetry disabled in test environment")
        return None

    if not _is_otel_enabled():
        logger.info("OpenTelemetry disabled (OTEL_ENABLED != true)")
        return None

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as e:
        logger.warning(f"OpenTelemetry 패키지 미설치: {e}")
        return None

    service_name = os.environ.get("OTEL_SERVICE_NAME", "aqts-backend")
    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
    sample_rate = float(os.environ.get("OTEL_TRACES_SAMPLER_ARG", "1.0"))

    # Resource: 서비스 메타데이터
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": "0.5.0",
            "deployment.environment": os.environ.get("ENVIRONMENT", "development"),
        }
    )

    # TracerProvider 설정
    provider = TracerProvider(resource=resource)

    # OTLP gRPC Exporter → OTel Collector
    exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
    processor = BatchSpanProcessor(exporter)
    provider.add_span_processor(processor)

    # 글로벌 TracerProvider 등록
    trace.set_tracer_provider(provider)

    # ── 자동 계측 (Instrumentation) ──
    _instrument_fastapi(app)
    _instrument_sqlalchemy()
    _instrument_httpx()
    _instrument_redis()

    logger.info(
        f"OpenTelemetry initialized: service={service_name}, " f"endpoint={otlp_endpoint}, sample_rate={sample_rate}"
    )

    return provider


def _instrument_fastapi(app):
    """FastAPI 자동 계측"""
    if app is None:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app,
            excluded_urls="health,healthz,ready,metrics",
        )
        logger.debug("FastAPI instrumentation enabled")
    except Exception as e:
        logger.warning(f"FastAPI instrumentation failed: {e}")


def _instrument_sqlalchemy():
    """SQLAlchemy 자동 계측"""
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument()
        logger.debug("SQLAlchemy instrumentation enabled")
    except Exception as e:
        logger.warning(f"SQLAlchemy instrumentation failed: {e}")


def _instrument_httpx():
    """httpx HTTP 클라이언트 자동 계측"""
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
        logger.debug("httpx instrumentation enabled")
    except Exception as e:
        logger.warning(f"httpx instrumentation failed: {e}")


def _instrument_redis():
    """Redis 자동 계측"""
    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        RedisInstrumentor().instrument()
        logger.debug("Redis instrumentation enabled")
    except Exception as e:
        logger.warning(f"Redis instrumentation failed: {e}")


def get_tracer(name: str = "aqts"):
    """현재 TracerProvider에서 Tracer 인스턴스를 가져옵니다.

    수동 span 생성 시 사용:
        tracer = get_tracer("my_module")
        with tracer.start_as_current_span("operation_name"):
            ...

    Args:
        name: Tracer 이름 (모듈 구분용)

    Returns:
        Tracer 인스턴스 (OTel 비활성화 시 NoOp tracer)
    """
    try:
        from opentelemetry import trace

        return trace.get_tracer(name)
    except ImportError:
        return _NoOpTracer()


class _NoOpTracer:
    """OTel 미설치 시 NoOp 대체"""

    def start_as_current_span(self, name, **kwargs):
        return _NoOpSpan()


class _NoOpSpan:
    """NoOp span context manager"""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def set_attribute(self, key, value):
        pass

    def add_event(self, name, attributes=None):
        pass
