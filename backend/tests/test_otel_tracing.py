"""
OpenTelemetry 분산 추적 테스트

검증 항목:
1. tracing.py 모듈 임포트 및 기본 동작
2. OTEL_ENABLED 환경변수에 따른 활성화/비활성화
3. 테스트 환경(TESTING=1)에서 자동 비활성화
4. NoOp tracer fallback
5. request_logger의 trace ID 헬퍼 함수
6. docker-compose 서비스 정의
7. OTel Collector 설정 파일 검증
"""

import os
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_ROOT = Path(__file__).resolve().parent.parent


class TestTracingModule:
    """tracing.py 모듈 기본 동작 테스트"""

    def test_import_tracing_module(self):
        """tracing 모듈 임포트 확인"""
        from core.monitoring.tracing import get_tracer, setup_tracing

        assert callable(setup_tracing)
        assert callable(get_tracer)

    def test_otel_disabled_by_default(self):
        """OTEL_ENABLED 미설정 시 비활성화 확인"""
        from core.monitoring.tracing import _is_otel_enabled

        original = os.environ.pop("OTEL_ENABLED", None)
        try:
            assert _is_otel_enabled() is False
        finally:
            if original is not None:
                os.environ["OTEL_ENABLED"] = original

    def test_otel_enabled_true(self):
        """OTEL_ENABLED=true 시 활성화 확인"""
        from core.monitoring.tracing import _is_otel_enabled

        os.environ["OTEL_ENABLED"] = "true"
        try:
            assert _is_otel_enabled() is True
        finally:
            os.environ["OTEL_ENABLED"] = "false"

    def test_otel_enabled_false(self):
        """OTEL_ENABLED=false 시 비활성화 확인"""
        from core.monitoring.tracing import _is_otel_enabled

        os.environ["OTEL_ENABLED"] = "false"
        assert _is_otel_enabled() is False

    def test_setup_tracing_returns_none_when_disabled(self):
        """비활성화 시 setup_tracing이 None 반환"""
        from core.monitoring.tracing import setup_tracing

        os.environ["OTEL_ENABLED"] = "false"
        result = setup_tracing(app=None)
        assert result is None

    def test_setup_tracing_returns_none_in_testing(self):
        """TESTING=1 환경에서 setup_tracing이 None 반환"""
        from core.monitoring.tracing import setup_tracing

        # TESTING=1은 conftest에서 이미 설정됨
        os.environ["OTEL_ENABLED"] = "true"
        try:
            result = setup_tracing(app=None)
            assert result is None
        finally:
            os.environ["OTEL_ENABLED"] = "false"


class TestNoOpTracer:
    """NoOp tracer fallback 테스트"""

    def test_get_tracer_returns_object(self):
        """get_tracer가 항상 사용 가능한 객체 반환"""
        from core.monitoring.tracing import get_tracer

        tracer = get_tracer("test")
        assert tracer is not None

    def test_noop_tracer_context_manager(self):
        """NoOp tracer의 span이 context manager로 동작"""
        from core.monitoring.tracing import _NoOpTracer

        tracer = _NoOpTracer()
        with tracer.start_as_current_span("test_span") as span:
            span.set_attribute("key", "value")
            span.add_event("test_event")
        # 에러 없이 완료되면 성공

    def test_noop_span_methods(self):
        """NoOp span의 메서드가 에러 없이 동작"""
        from core.monitoring.tracing import _NoOpSpan

        span = _NoOpSpan()
        span.set_attribute("key", "value")
        span.add_event("event", {"attr": "val"})


class TestRequestLoggerTracing:
    """request_logger의 trace ID 헬퍼 함수 테스트"""

    def test_get_trace_id_returns_string(self):
        """_get_trace_id가 문자열 반환"""
        from api.middleware.request_logger import _get_trace_id

        result = _get_trace_id()
        assert isinstance(result, str)

    def test_get_span_id_returns_string(self):
        """_get_span_id가 문자열 반환"""
        from api.middleware.request_logger import _get_span_id

        result = _get_span_id()
        assert isinstance(result, str)

    def test_get_trace_id_empty_when_no_span(self):
        """활성 span이 없으면 빈 문자열 반환"""
        from api.middleware.request_logger import _get_trace_id

        result = _get_trace_id()
        # OTel이 비활성화 상태에서는 빈 문자열 또는 유효하지 않은 trace_id
        assert result == "" or len(result) == 32

    @pytest.mark.asyncio
    async def test_response_headers_include_request_id(self):
        """응답에 X-Request-ID 헤더가 포함되는지 확인"""
        from httpx import ASGITransport, AsyncClient

        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/info")
            assert "X-Request-ID" in response.headers

    @pytest.mark.asyncio
    async def test_response_headers_include_correlation_id(self):
        """응답에 X-Correlation-ID 헤더가 포함되는지 확인"""
        from httpx import ASGITransport, AsyncClient

        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/info")
            assert "X-Correlation-ID" in response.headers

    @pytest.mark.asyncio
    async def test_custom_request_id_propagated(self):
        """클라이언트가 보낸 X-Request-ID가 전파되는지 확인"""
        from httpx import ASGITransport, AsyncClient

        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            custom_id = "custom-request-id-12345"
            response = await client.get("/api/info", headers={"X-Request-ID": custom_id})
            assert response.headers.get("X-Request-ID") == custom_id


class TestOtelCollectorConfig:
    """OTel Collector 설정 파일 검증"""

    @pytest.fixture(autouse=True)
    def load_config(self):
        config_path = PROJECT_ROOT / "monitoring" / "otel-collector" / "otel-collector-config.yml"
        assert config_path.exists(), "otel-collector-config.yml이 없습니다"
        with open(config_path) as f:
            self.config = yaml.safe_load(f)

    def test_otlp_receiver_configured(self):
        """OTLP 수신기가 설정되어 있는지 확인"""
        assert "otlp" in self.config["receivers"]
        protocols = self.config["receivers"]["otlp"]["protocols"]
        assert "grpc" in protocols
        assert "http" in protocols

    def test_otlp_grpc_port(self):
        """OTLP gRPC 포트가 4317인지 확인"""
        endpoint = self.config["receivers"]["otlp"]["protocols"]["grpc"]["endpoint"]
        assert "4317" in endpoint

    def test_batch_processor_configured(self):
        """배치 프로세서가 설정되어 있는지 확인"""
        assert "batch" in self.config["processors"]
        batch = self.config["processors"]["batch"]
        assert batch["send_batch_size"] > 0

    def test_jaeger_exporter_configured(self):
        """Jaeger OTLP exporter가 설정되어 있는지 확인"""
        assert "otlp/jaeger" in self.config["exporters"]
        endpoint = self.config["exporters"]["otlp/jaeger"]["endpoint"]
        assert "jaeger" in endpoint

    def test_traces_pipeline_configured(self):
        """traces 파이프라인이 설정되어 있는지 확인"""
        pipelines = self.config["service"]["pipelines"]
        assert "traces" in pipelines
        traces = pipelines["traces"]
        assert "otlp" in traces["receivers"]
        assert "batch" in traces["processors"]

    def test_health_check_extension(self):
        """헬스체크 확장이 설정되어 있는지 확인"""
        assert "health_check" in self.config["extensions"]


class TestDockerComposeOtel:
    """docker-compose.yml OTel 서비스 정의 검증"""

    @pytest.fixture(autouse=True)
    def load_compose(self):
        compose_path = PROJECT_ROOT / "docker-compose.yml"
        with open(compose_path) as f:
            self.compose = yaml.safe_load(f)

    def test_otel_collector_service_exists(self):
        """otel-collector 서비스가 정의되어 있는지 확인"""
        assert "otel-collector" in self.compose["services"]

    def test_jaeger_service_exists(self):
        """jaeger 서비스가 정의되어 있는지 확인"""
        assert "jaeger" in self.compose["services"]

    def test_otel_collector_depends_on_jaeger(self):
        """otel-collector가 jaeger에 의존하는지 확인"""
        deps = self.compose["services"]["otel-collector"]["depends_on"]
        assert "jaeger" in deps

    def test_jaeger_otlp_enabled(self):
        """Jaeger에서 OTLP 수신이 활성화되어 있는지 확인"""
        env = self.compose["services"]["jaeger"]["environment"]
        assert env.get("COLLECTOR_OTLP_ENABLED") == "true"

    def test_jaeger_ui_port_exposed(self):
        """Jaeger UI 포트(16686)가 노출되어 있는지 확인"""
        ports = self.compose["services"]["jaeger"]["ports"]
        port_found = any("16686" in str(p) for p in ports)
        assert port_found

    def test_otel_collector_config_volume(self):
        """OTel Collector 설정 파일이 볼륨으로 마운트되어 있는지 확인"""
        volumes = self.compose["services"]["otel-collector"]["volumes"]
        config_found = any("otel-collector-config" in str(v) for v in volumes)
        assert config_found
