"""
TelegramTransport SSOT 유닛테스트

테스트 범위:
  1. TelegramTransport 기본 동작 (send_text, _send_single)
  2. 재시도 로직 (지수 backoff, 최대 재시도)
  3. 메시지 분할 (split_message)
  4. is_configured() 설정 검증
  5. create_transport() 팩토리
  6. 하위호환 (TelegramNotifier.send_message → Transport 위임)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.notification.telegram_transport import (
    TELEGRAM_MAX_LENGTH,
    TelegramTransport,
    create_transport,
    split_message,
)


# ══════════════════════════════════════════════════════════════
# 1. TelegramTransport 기본 동작
# ══════════════════════════════════════════════════════════════
class TestTelegramTransportBasic:
    """TelegramTransport 인스턴스 생성 및 기본 프로퍼티"""

    def test_properties(self):
        """bot_token, chat_id 프로퍼티 접근"""
        t = TelegramTransport(bot_token="tok123", chat_id="cid456")
        assert t.bot_token == "tok123"
        assert t.chat_id == "cid456"

    def test_is_configured_true(self):
        """토큰+채팅ID 모두 설정 시 True"""
        t = TelegramTransport(bot_token="tok", chat_id="cid")
        assert t.is_configured() is True

    def test_is_configured_false_no_token(self):
        """토큰 미설정 시 False"""
        t = TelegramTransport(bot_token="", chat_id="cid")
        assert t.is_configured() is False

    def test_is_configured_false_no_chat_id(self):
        """채팅ID 미설정 시 False"""
        t = TelegramTransport(bot_token="tok", chat_id="")
        assert t.is_configured() is False

    def test_is_configured_false_both_empty(self):
        """둘 다 빈 문자열이면 False"""
        t = TelegramTransport(bot_token="", chat_id="")
        assert t.is_configured() is False


# ══════════════════════════════════════════════════════════════
# 2. send_text 성공/실패
# ══════════════════════════════════════════════════════════════
class TestSendText:
    """send_text() HTTP 전송 테스트 (httpx 모킹)"""

    @pytest.fixture
    def transport(self):
        return TelegramTransport(bot_token="test-token", chat_id="test-chat", max_retries=2)

    async def test_send_text_success(self, transport):
        """정상 전송 (200 응답)"""
        mock_response = MagicMock(status_code=200)
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await transport.send_text("hello")
            assert result is True
            mock_client.post.assert_called_once()

            # payload 검증
            call_args = mock_client.post.call_args
            payload = call_args.kwargs.get("json") or call_args[1].get("json")
            assert payload["chat_id"] == "test-chat"
            assert payload["text"] == "hello"
            assert payload["parse_mode"] == "HTML"

    async def test_send_text_custom_parse_mode(self, transport):
        """parse_mode 커스텀 전달"""
        mock_response = MagicMock(status_code=200)
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await transport.send_text("hello", parse_mode="Markdown")
            payload = mock_client.post.call_args.kwargs.get("json") or mock_client.post.call_args[1].get("json")
            assert payload["parse_mode"] == "Markdown"

    async def test_send_text_failure_after_retries(self, transport):
        """모든 재시도 실패 시 False"""
        mock_response = MagicMock(status_code=500, text="Internal Server Error")
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await transport.send_text("fail")
                assert result is False
                # max_retries=2 → 2회 시도
                assert mock_client.post.call_count == 2

    async def test_send_text_exception_retry(self, transport):
        """네트워크 예외 시 재시도 후 실패"""
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = ConnectionError("network error")
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await transport.send_text("error")
                assert result is False
                assert mock_client.post.call_count == 2

    async def test_send_text_retry_then_success(self):
        """1차 실패 후 2차 성공"""
        t = TelegramTransport(bot_token="tok", chat_id="cid", max_retries=3)
        fail_resp = MagicMock(status_code=500, text="fail")
        ok_resp = MagicMock(status_code=200)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = [fail_resp, ok_resp]
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await t.send_text("retry-ok")
                assert result is True
                assert mock_client.post.call_count == 2


# ══════════════════════════════════════════════════════════════
# 3. 메시지 분할
# ══════════════════════════════════════════════════════════════
class TestSplitMessage:
    """split_message() 유틸리티"""

    def test_short_message_no_split(self):
        """짧은 메시지는 분할하지 않음"""
        result = split_message("hello")
        assert result == ["hello"]

    def test_exact_max_length(self):
        """정확히 max_length인 메시지는 분할하지 않음"""
        msg = "a" * TELEGRAM_MAX_LENGTH
        result = split_message(msg)
        assert len(result) == 1
        assert result[0] == msg

    def test_split_on_newline(self):
        """줄바꿈 기준 분할"""
        line = "x" * 2000
        msg = f"{line}\n{line}\n{line}"
        result = split_message(msg)
        assert len(result) >= 2
        for chunk in result:
            assert len(chunk) <= TELEGRAM_MAX_LENGTH

    def test_forced_split_no_newline(self):
        """줄바꿈 없는 긴 메시지 — 강제 절단"""
        msg = "a" * (TELEGRAM_MAX_LENGTH + 100)
        result = split_message(msg)
        assert len(result) == 2
        assert len(result[0]) == TELEGRAM_MAX_LENGTH
        assert len(result[1]) == 100

    def test_custom_max_length(self):
        """커스텀 max_length"""
        result = split_message("aaa\nbbb\nccc", max_length=5)
        assert all(len(c) <= 5 for c in result)

    def test_empty_message(self):
        """빈 메시지"""
        result = split_message("")
        assert result == [""]

    async def test_multi_chunk_send_text(self):
        """분할된 메시지가 순차 전송되는지 확인"""
        t = TelegramTransport(bot_token="tok", chat_id="cid")
        # 각 줄이 4096을 초과하도록 구성 → 최소 3개 청크
        line = "x" * 4000
        msg = f"{line}\n{line}\n{line}"
        chunks = split_message(msg)
        expected_chunks = len(chunks)
        assert expected_chunks >= 3, f"expected >= 3 chunks, got {expected_chunks}"

        mock_response = MagicMock(status_code=200)
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result = await t.send_text(msg)
                assert result is True
                assert mock_client.post.call_count == expected_chunks
                # 청크 간 딜레이 (N-1)회
                assert mock_sleep.call_count == expected_chunks - 1


# ══════════════════════════════════════════════════════════════
# 4. create_transport 팩토리
# ══════════════════════════════════════════════════════════════
class TestCreateTransport:
    """create_transport() 팩토리 함수"""

    def test_explicit_values(self):
        """명시적 값 우선"""
        with patch("config.settings.get_settings") as ms:
            ms.return_value = MagicMock(telegram=MagicMock(bot_token="default-tok", chat_id="default-cid"))
            t = create_transport(bot_token="my-tok", chat_id="my-cid")
        assert t.bot_token == "my-tok"
        assert t.chat_id == "my-cid"

    def test_fallback_to_settings(self):
        """None이면 settings fallback"""
        with patch("config.settings.get_settings") as ms:
            ms.return_value = MagicMock(telegram=MagicMock(bot_token="cfg-tok", chat_id="cfg-cid"))
            t = create_transport()
        assert t.bot_token == "cfg-tok"
        assert t.chat_id == "cfg-cid"

    def test_empty_string_preserved(self):
        """빈 문자열은 settings fallback하지 않음 (None만 fallback)"""
        with patch("config.settings.get_settings") as ms:
            ms.return_value = MagicMock(telegram=MagicMock(bot_token="cfg-tok", chat_id="cfg-cid"))
            t = create_transport(bot_token="", chat_id="")
        assert t.bot_token == ""
        assert t.chat_id == ""

    def test_kwargs_forwarded(self):
        """추가 kwargs가 TelegramTransport에 전달"""
        with patch("config.settings.get_settings") as ms:
            ms.return_value = MagicMock(telegram=MagicMock(bot_token="tok", chat_id="cid"))
            t = create_transport(max_retries=5, timeout=30.0)
        assert t._max_retries == 5
        assert t._timeout == 30.0


# ══════════════════════════════════════════════════════════════
# 5. TelegramNotifier → Transport 위임 하위호환
# ══════════════════════════════════════════════════════════════
class TestNotifierTransportDelegation:
    """TelegramNotifier.send_message()이 Transport.send_text()로 위임되는지 확인"""

    async def test_send_message_delegates(self):
        """send_message → transport.send_text 위임"""
        from core.notification.telegram_notifier import TelegramNotifier

        mock_transport = MagicMock()
        mock_transport.send_text = AsyncMock(return_value=True)
        mock_transport.bot_token = "tok"
        mock_transport.chat_id = "cid"

        with patch("core.notification.telegram_notifier.get_settings") as ms:
            ms.return_value = MagicMock(telegram=MagicMock(bot_token="tok", chat_id="cid", alert_level="ALL"))
            notifier = TelegramNotifier(transport=mock_transport)

        result = await notifier.send_message("test text", parse_mode="Markdown")
        assert result is True
        mock_transport.send_text.assert_called_once_with("test text", parse_mode="Markdown")

    async def test_notifier_backward_compat_properties(self):
        """_bot_token, _chat_id 하위호환 프로퍼티"""
        from core.notification.telegram_notifier import TelegramNotifier

        mock_transport = MagicMock()
        mock_transport.bot_token = "my-bot-tok"
        mock_transport.chat_id = "my-chat-id"

        with patch("core.notification.telegram_notifier.get_settings") as ms:
            ms.return_value = MagicMock(telegram=MagicMock(bot_token="tok", chat_id="cid", alert_level="ALL"))
            notifier = TelegramNotifier(transport=mock_transport)

        assert notifier._bot_token == "my-bot-tok"
        assert notifier._chat_id == "my-chat-id"

    def test_split_message_backward_compat(self):
        """TelegramNotifier._split_message → transport.split_message 위임"""
        from core.notification.telegram_notifier import TelegramNotifier

        result = TelegramNotifier._split_message("short")
        assert result == ["short"]

    def test_max_length_reexport(self):
        """TELEGRAM_MAX_LENGTH 가 telegram_notifier 에서도 접근 가능"""
        from core.notification.telegram_notifier import (
            TELEGRAM_MAX_LENGTH as NOTIFIER_MAX,
        )

        assert NOTIFIER_MAX == 4096


# ══════════════════════════════════════════════════════════════
# 6. TelegramChannelAdapter → Transport 직접 사용
# ══════════════════════════════════════════════════════════════
class TestAdapterTransportDirect:
    """TelegramChannelAdapter가 Transport를 직접 사용하는지 확인"""

    async def test_adapter_uses_transport_directly(self):
        """Adapter.send() → Transport.send_text() 직접 호출"""
        from core.notification.telegram_adapter import TelegramChannelAdapter

        mock_transport = MagicMock()
        mock_transport.send_text = AsyncMock(return_value=True)
        mock_transport.is_configured.return_value = True

        with patch("config.settings.get_settings") as ms:
            ms.return_value = MagicMock(telegram=MagicMock(alert_level="ALL"))
            adapter = TelegramChannelAdapter(transport=mock_transport)

        from config.constants import AlertType
        from core.notification.alert_manager import Alert, AlertLevel

        alert = Alert(
            alert_type=AlertType.SYSTEM_ERROR,
            level=AlertLevel.WARNING,
            title="Test",
            message="msg",
        )

        result = await adapter.send(alert)
        assert result is True
        mock_transport.send_text.assert_called_once()

    async def test_adapter_is_available_delegates(self):
        """is_available() → transport.is_configured() 위임"""
        from core.notification.telegram_adapter import TelegramChannelAdapter

        mock_transport = MagicMock()
        mock_transport.is_configured.return_value = False

        with patch("config.settings.get_settings") as ms:
            ms.return_value = MagicMock(telegram=MagicMock(alert_level="ALL"))
            adapter = TelegramChannelAdapter(transport=mock_transport)

        assert adapter.is_available() is False
        mock_transport.is_configured.assert_called_once()
