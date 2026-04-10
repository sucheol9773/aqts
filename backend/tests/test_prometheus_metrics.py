"""
Prometheus 메트릭 모듈 테스트

테스트 대상:
  1. PrometheusMiddleware — HTTP 요청 메트릭 수집
  2. metrics_endpoint — /metrics 엔드포인트 응답
  3. setup_prometheus — 앱 초기화
  4. _normalize_path — 경로 정규화
  5. 메트릭 객체 — Counter, Gauge, Histogram 동작
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.monitoring.metrics import (
    CIRCUIT_BREAKER_FAILURES,
    CIRCUIT_BREAKER_STATE,
    COMPONENT_HEALTH,
    DAILY_RETURN_PCT,
    DATA_COLLECTION_DURATION,
    DATA_COLLECTION_ERRORS,
    ENSEMBLE_CONFIDENCE,
    HTTP_HEAVY_REQUEST_DURATION,
    HTTP_REQUEST_DURATION,
    HTTP_REQUEST_TOTAL,
    HTTP_REQUESTS_IN_PROGRESS,
    ORDERS_TOTAL,
    PORTFOLIO_VALUE,
    SIGNAL_GENERATED,
    SYSTEM_STATUS,
    PrometheusMiddleware,
    metrics_endpoint,
    setup_prometheus,
)


class TestPrometheusMiddleware:
    """PrometheusMiddleware 테스트"""

    def test_normalize_path_simple(self):
        """일반 경로는 그대로 반환"""
        result = PrometheusMiddleware._normalize_path("/api/system/health")
        assert result == "/api/system/health"

    def test_normalize_path_numeric_id(self):
        """숫자 ID는 {id}로 치환"""
        result = PrometheusMiddleware._normalize_path("/api/alerts/12345")
        assert result == "/api/alerts/{id}"

    def test_normalize_path_uuid(self):
        """UUID 길이 문자열은 {id}로 치환"""
        result = PrometheusMiddleware._normalize_path("/api/orders/550e8400-e29b-41d4-a716-446655440000")
        assert result == "/api/orders/{id}"

    def test_normalize_path_short_string(self):
        """짧은 문자열은 그대로 유지"""
        result = PrometheusMiddleware._normalize_path("/api/market/KR")
        assert result == "/api/market/KR"

    def test_skip_paths(self):
        """메트릭 수집 제외 경로 목록 확인"""
        assert "/metrics" in PrometheusMiddleware.SKIP_PATHS
        assert "/api/system/health" in PrometheusMiddleware.SKIP_PATHS

    def test_heavy_path_prefixes(self):
        """heavy endpoint 접두사 목록 확인"""
        prefixes = PrometheusMiddleware.HEAVY_PATH_PREFIXES
        assert "/api/system/pipeline" in prefixes
        assert "/api/system/backtest" in prefixes
        assert "/api/system/oos/run" in prefixes
        assert "/api/ensemble/batch" in prefixes
        assert "/param_sensitivity/run" in prefixes

    @pytest.mark.asyncio
    async def test_dispatch_heavy_endpoint_uses_separate_histogram(self):
        """heavy endpoint는 HTTP_HEAVY_REQUEST_DURATION에 기록"""
        middleware = PrometheusMiddleware(app=MagicMock())
        request = MagicMock()
        request.url.path = "/api/system/pipeline"
        request.method = "POST"

        mock_response = MagicMock()
        mock_response.status_code = 200
        call_next = AsyncMock(return_value=mock_response)

        with (
            patch("core.monitoring.metrics.HTTP_HEAVY_REQUEST_DURATION") as mock_heavy,
            patch("core.monitoring.metrics.HTTP_REQUEST_DURATION") as mock_light,
        ):
            await middleware.dispatch(request, call_next)
            mock_heavy.labels.assert_called_once()
            mock_light.labels.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_light_endpoint_uses_standard_histogram(self):
        """일반 endpoint는 HTTP_REQUEST_DURATION에 기록"""
        middleware = PrometheusMiddleware(app=MagicMock())
        request = MagicMock()
        request.url.path = "/api/portfolio"
        request.method = "GET"

        mock_response = MagicMock()
        mock_response.status_code = 200
        call_next = AsyncMock(return_value=mock_response)

        with (
            patch("core.monitoring.metrics.HTTP_HEAVY_REQUEST_DURATION") as mock_heavy,
            patch("core.monitoring.metrics.HTTP_REQUEST_DURATION") as mock_light,
        ):
            await middleware.dispatch(request, call_next)
            mock_light.labels.assert_called_once()
            mock_heavy.labels.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_skips_metrics_path(self):
        """제외 경로는 메트릭 수집 없이 통과"""
        middleware = PrometheusMiddleware(app=MagicMock())
        request = MagicMock()
        request.url.path = "/metrics"
        request.method = "GET"

        mock_response = MagicMock()
        call_next = AsyncMock(return_value=mock_response)

        result = await middleware.dispatch(request, call_next)
        assert result == mock_response
        call_next.assert_called_once_with(request)

    @pytest.mark.asyncio
    async def test_dispatch_records_metrics(self):
        """일반 요청에 대해 메트릭이 기록됨"""
        middleware = PrometheusMiddleware(app=MagicMock())
        request = MagicMock()
        request.url.path = "/api/portfolio"
        request.method = "GET"

        mock_response = MagicMock()
        mock_response.status_code = 200
        call_next = AsyncMock(return_value=mock_response)

        result = await middleware.dispatch(request, call_next)
        assert result == mock_response


class TestMetricsEndpoint:
    """메트릭 엔드포인트 테스트"""

    @pytest.mark.asyncio
    async def test_metrics_returns_prometheus_format(self):
        """메트릭 엔드포인트가 Prometheus 텍스트 포맷을 반환"""
        request = MagicMock()
        response = await metrics_endpoint(request)
        assert response.status_code == 200
        assert "text/plain" in response.media_type
        body = response.body.decode()
        assert "aqts_" in body

    @pytest.mark.asyncio
    async def test_metrics_contains_http_metrics(self):
        """HTTP 메트릭이 포함되어 있는지 확인"""
        request = MagicMock()
        response = await metrics_endpoint(request)
        body = response.body.decode()
        assert "aqts_http_request_duration_seconds" in body
        assert "aqts_http_requests_total" in body


class TestSetupPrometheus:
    """setup_prometheus 초기화 테스트"""

    def test_setup_adds_middleware_and_route(self):
        """미들웨어와 /metrics 라우트가 등록됨"""
        mock_app = MagicMock()
        setup_prometheus(mock_app)

        mock_app.add_middleware.assert_called_once_with(PrometheusMiddleware)
        mock_app.add_route.assert_called_once()
        route_args = mock_app.add_route.call_args
        assert route_args[0][0] == "/metrics"
        assert route_args[1]["methods"] == ["GET"]


class TestMetricObjects:
    """메트릭 객체 기본 동작 테스트"""

    def test_system_status_gauge(self):
        """SYSTEM_STATUS 게이지 설정"""
        SYSTEM_STATUS.set(1.0)
        # prometheus_client는 내부 상태를 유지, 예외 없이 동작하면 OK

    def test_component_health_gauge(self):
        """COMPONENT_HEALTH 레이블 게이지"""
        COMPONENT_HEALTH.labels(component="postgresql").set(1.0)
        COMPONENT_HEALTH.labels(component="redis").set(0.5)
        COMPONENT_HEALTH.labels(component="mongodb").set(0.0)

    def test_orders_counter(self):
        """ORDERS_TOTAL 카운터 증가"""
        ORDERS_TOTAL.labels(side="BUY", status="filled").inc()
        ORDERS_TOTAL.labels(side="SELL", status="rejected").inc()

    def test_portfolio_value_gauge(self):
        """PORTFOLIO_VALUE 게이지 설정"""
        PORTFOLIO_VALUE.set(100_000_000)

    def test_daily_return_gauge(self):
        """DAILY_RETURN_PCT 게이지 설정"""
        DAILY_RETURN_PCT.set(1.25)

    def test_signal_counter(self):
        """SIGNAL_GENERATED 카운터"""
        SIGNAL_GENERATED.labels(strategy="ENSEMBLE", direction="BUY").inc()

    def test_ensemble_confidence(self):
        """ENSEMBLE_CONFIDENCE 게이지"""
        ENSEMBLE_CONFIDENCE.set(0.75)

    def test_circuit_breaker_state(self):
        """CIRCUIT_BREAKER_STATE 게이지"""
        CIRCUIT_BREAKER_STATE.labels(service="kis_api").set(0)
        CIRCUIT_BREAKER_STATE.labels(service="fred_api").set(1)

    def test_circuit_breaker_failures(self):
        """CIRCUIT_BREAKER_FAILURES 카운터"""
        CIRCUIT_BREAKER_FAILURES.labels(service="kis_api").inc()

    def test_data_collection_duration(self):
        """DATA_COLLECTION_DURATION 히스토그램"""
        DATA_COLLECTION_DURATION.labels(source="kis").observe(5.2)

    def test_data_collection_errors(self):
        """DATA_COLLECTION_ERRORS 카운터"""
        DATA_COLLECTION_ERRORS.labels(source="fred").inc()

    def test_http_heavy_request_duration(self):
        """HTTP_HEAVY_REQUEST_DURATION 히스토그램"""
        HTTP_HEAVY_REQUEST_DURATION.labels(method="POST", endpoint="/api/system/pipeline", status_code="200").observe(
            5.2
        )

    def test_http_request_duration(self):
        """HTTP_REQUEST_DURATION 히스토그램"""
        HTTP_REQUEST_DURATION.labels(method="GET", endpoint="/api/portfolio", status_code="200").observe(0.15)

    def test_http_request_total(self):
        """HTTP_REQUEST_TOTAL 카운터"""
        HTTP_REQUEST_TOTAL.labels(method="POST", endpoint="/api/orders", status_code="201").inc()

    def test_http_requests_in_progress(self):
        """HTTP_REQUESTS_IN_PROGRESS 게이지"""
        HTTP_REQUESTS_IN_PROGRESS.labels(method="GET").inc()
        HTTP_REQUESTS_IN_PROGRESS.labels(method="GET").dec()


class TestJsonLogging:
    """JSON 로그 포맷 테스트"""

    def test_json_sink_produces_valid_json(self):
        """_json_sink가 유효한 JSON을 출력하는지 확인"""
        import json
        from io import StringIO

        from config.logging import _json_sink

        # loguru의 Message 객체를 시뮬레이션
        mock_record = {
            "time": MagicMock(),
            "level": MagicMock(),
            "name": "test_module",
            "function": "test_func",
            "line": 42,
            "message": "Test message",
            "extra": {"request_id": "abc-123"},
            "exception": None,
        }
        mock_record["time"].strftime.return_value = "2026-04-06T12:00:00.000000+0900"
        mock_record["level"].name = "INFO"

        mock_message = MagicMock()
        mock_message.record = mock_record

        captured = StringIO()
        with patch("sys.stdout", captured):
            _json_sink(mock_message)

        output = captured.getvalue().strip()
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "Test message"
        assert parsed["extra"]["request_id"] == "abc-123"

    def test_json_sink_with_exception(self):
        """예외 정보가 JSON에 포함되는지 확인"""
        import json
        from io import StringIO

        from config.logging import _json_sink

        mock_exception = MagicMock()
        mock_exception.type = ValueError
        mock_exception.value = "something went wrong"

        mock_record = {
            "time": MagicMock(),
            "level": MagicMock(),
            "name": "test",
            "function": "test_func",
            "line": 10,
            "message": "Error occurred",
            "extra": {},
            "exception": mock_exception,
        }
        mock_record["time"].strftime.return_value = "2026-04-06T12:00:00.000000+0900"
        mock_record["level"].name = "ERROR"

        mock_message = MagicMock()
        mock_message.record = mock_record

        captured = StringIO()
        with patch("sys.stdout", captured):
            _json_sink(mock_message)

        output = captured.getvalue().strip()
        parsed = json.loads(output)
        assert parsed["exception"]["type"] == "ValueError"
        assert "something went wrong" in parsed["exception"]["value"]
