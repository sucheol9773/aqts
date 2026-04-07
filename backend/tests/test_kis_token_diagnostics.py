"""
KISTokenManager._issue_token 의 진단 로깅 + 예외 wrapping 검증.

목적:
    실제 운영 환경에서 startup 시점에 KIS 토큰 발급이 실패하면 tenacity RetryError
    로 감싸여 원래 HTTPStatusError 의 status code / KIS error_code 가 묻히는 회귀가
    있었다. 이 테스트는 다음을 검증한다:

    1. RetryError 로 감싸인 HTTPStatusError 가 풀려서 status code, KIS error_code,
       error_description 이 로그에 명시적으로 출력된다.
    2. 시크릿(app_key/app_secret) 은 로그에 절대 노출되지 않는다.
    3. 최종 raise 되는 예외는 KISAPIError 로 일관된다 (호출자 main lifespan 의
       'KIS 토큰 초기화 실패 (degraded)' 경로 호환).
    4. Timeout / 일반 HTTPError 도 적절한 예외 타입과 함께 로깅된다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from loguru import logger

from core.data_collector.kis_client import KISAPIError, KISTokenManager


@pytest.fixture
def loguru_capture():
    """loguru 로그를 메모리 버퍼에 캡처. capsys 는 loguru sink 를 가로채지 못함."""
    messages: list[str] = []
    sink_id = logger.add(lambda msg: messages.append(str(msg)), level="DEBUG")
    yield messages
    logger.remove(sink_id)


def _make_manager(monkeypatch=None) -> KISTokenManager:
    """retry_count=2 로 줄인 KISTokenManager 인스턴스 생성."""
    manager = KISTokenManager()
    # 테스트 속도를 위해 retry 수를 1회로 강제 (재시도 wait 없이 즉시 실패)
    manager._settings = MagicMock(
        base_url="https://example.invalid",
        api_timeout=1.0,
        api_retry_count=1,
        is_live=False,
        is_backtest=False,
        app_key="TEST_APP_KEY_36CHARS_xxxxxxxxxxxxxxxx",
        app_secret="TEST_APP_SECRET_180CHARS_" + "y" * 155,
    )
    return manager


def _http_status_error(status: int, body: str) -> httpx.HTTPStatusError:
    """주어진 status code + body 를 가진 HTTPStatusError 인스턴스 생성."""
    request = httpx.Request("POST", "https://example.invalid/oauth2/tokenP")
    response = httpx.Response(status_code=status, content=body.encode("utf-8"), request=request)
    return httpx.HTTPStatusError(message=f"HTTP {status}", request=request, response=response)


class TestKISTokenIssueDiagnostics:
    """토큰 발급 실패 시 진단 로깅 + 예외 wrapping 동작 검증."""

    @pytest.mark.asyncio
    async def test_kis_rate_limit_egw00133_logs_status_and_error_code(self, loguru_capture):
        """EGW00133 (1분 1회 제한) 응답이 명시적으로 로그에 남는지."""
        manager = _make_manager()

        # 1분 1회 토큰 발급 제한 응답 (KIS 실제 응답 형식)
        body = '{"error_code":"EGW00133","error_description":"기간내 발급된 access_token이 있습니다."}'
        http_err = _http_status_error(403, body)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=http_err)
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            with pytest.raises(KISAPIError) as exc_info:
                await manager._issue_token()

        # 1) 예외 자체에 KIS error_code/description 이 매핑되었는지
        assert exc_info.value.code == "EGW00133"
        assert "access_token" in exc_info.value.message

        # 2) 로그(stderr)에 status code, error_code, error_description 모두 명시
        log_text = "\n".join(loguru_capture)
        assert "HTTP 403" in log_text
        assert "EGW00133" in log_text
        assert "access_token" in log_text  # 한국어 description 일부

        # 3) 시크릿이 로그에 노출되지 않음
        assert "TEST_APP_KEY_36CHARS" not in log_text
        assert "TEST_APP_SECRET" not in log_text

    @pytest.mark.asyncio
    async def test_unauthorized_401_logs_http_status(self, loguru_capture):
        """401 (잘못된 credential) 도 HTTP status 가 로그에 남는지."""
        manager = _make_manager()
        body = '{"error_code":"EGW00121","error_description":"appkey or appsecret is invalid."}'
        http_err = _http_status_error(401, body)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=http_err)
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            with pytest.raises(KISAPIError) as exc_info:
                await manager._issue_token()

        assert exc_info.value.code == "EGW00121"
        log_text = "\n".join(loguru_capture)
        assert "HTTP 401" in log_text
        assert "EGW00121" in log_text

    @pytest.mark.asyncio
    async def test_timeout_logs_timeout_type(self, loguru_capture):
        """ReadTimeout 발생 시 timeout 타입이 로그에 남는지."""
        manager = _make_manager()
        timeout_err = httpx.ReadTimeout("Read operation timed out")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=timeout_err)
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            with pytest.raises(KISAPIError) as exc_info:
                await manager._issue_token()

        assert exc_info.value.code == "ReadTimeout"
        log_text = "\n".join(loguru_capture)
        assert "timeout" in log_text.lower()
        assert "ReadTimeout" in log_text

    @pytest.mark.asyncio
    async def test_unparseable_body_falls_back_to_http_status_code(self, loguru_capture):
        """KIS 가 비표준 응답(예: HTML 에러 페이지)을 줘도 status code 는 보존되는지."""
        manager = _make_manager()
        http_err = _http_status_error(503, "<html><body>Service Unavailable</body></html>")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=http_err)
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            with pytest.raises(KISAPIError) as exc_info:
                await manager._issue_token()

        # error_code 파싱 실패 → HTTP 상태로 fallback
        assert exc_info.value.code == "HTTP503"
        log_text = "\n".join(loguru_capture)
        assert "HTTP 503" in log_text

    def test_parse_kis_error_body_extracts_fields(self):
        """KIS 에러 body 파서 단위 테스트."""
        code, desc = KISTokenManager._parse_kis_error_body('{"error_code":"EGW00133","error_description":"limit"}')
        assert code == "EGW00133"
        assert desc == "limit"

    def test_parse_kis_error_body_returns_none_on_invalid_json(self):
        """JSON 이 아닌 body 는 (None, None) 반환."""
        code, desc = KISTokenManager._parse_kis_error_body("not a json")
        assert code is None
        assert desc is None

    def test_parse_kis_error_body_returns_none_on_non_dict_payload(self):
        """배열 등 dict 가 아닌 payload 는 (None, None) 반환."""
        code, desc = KISTokenManager._parse_kis_error_body("[1,2,3]")
        assert code is None
        assert desc is None
