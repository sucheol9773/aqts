"""
Gate C — 알림 채널 검증 + 백업 알림 테스트

테스트 범위:
  1. Telegram 발송 검증 (성공/실패/재시도/레벨필터)
  2. FileNotifier 백업 채널 (파일 기록/크기 제한)
  3. ConsoleNotifier 최후 폴백
  4. NotificationRouter 폴백 동작 (1차→2차→3차)
  5. ChannelHealth 건강 상태 추적
  6. TelegramChannelAdapter 프로토콜 적합성
  7. DispatchResult 결과 추적
"""

import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.constants import AlertType
from core.notification.alert_manager import Alert, AlertLevel, AlertStatus
from core.notification.fallback_notifier import (
    ChannelHealth,
    ChannelStatus,
    ConsoleNotifier,
    DispatchResult,
    FileNotifier,
    NotificationRouter,
)
from core.notification.telegram_adapter import TelegramChannelAdapter
from core.notification.telegram_notifier import TelegramNotifier


# ══════════════════════════════════════
# 테스트 헬퍼
# ══════════════════════════════════════
def _make_alert(
    level: AlertLevel = AlertLevel.WARNING,
    title: str = "Test Alert",
    message: str = "Test message",
) -> Alert:
    """테스트용 Alert 생성"""
    return Alert(
        alert_type=AlertType.SYSTEM_ERROR,
        level=level,
        title=title,
        message=message,
    )


class FakeChannel:
    """테스트용 가짜 채널"""

    def __init__(self, name: str, should_succeed: bool = True, available: bool = True):
        self._name = name
        self._should_succeed = should_succeed
        self._available = available
        self.send_count = 0
        self.last_alert = None

    @property
    def channel_name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return self._available

    async def send(self, alert: Alert) -> bool:
        self.send_count += 1
        self.last_alert = alert
        return self._should_succeed


class ExplodingChannel:
    """예외를 발생시키는 채널"""

    def __init__(self, name: str = "exploding"):
        self._name = name

    @property
    def channel_name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return True

    async def send(self, alert: Alert) -> bool:
        raise ConnectionError("Channel exploded")


# ══════════════════════════════════════════════════════════════
# 1. Telegram 발송 검증
# ══════════════════════════════════════════════════════════════
class TestTelegramDispatch:
    """Telegram 발송 시나리오 검증"""

    @pytest.fixture
    def notifier(self):
        """테스트용 TelegramNotifier"""
        with patch("core.notification.telegram_notifier.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                telegram=MagicMock(bot_token="test-token", chat_id="test-chat", alert_level="ALL")
            )
            return TelegramNotifier()

    async def test_send_message_success(self, notifier):
        """Telegram 메시지 발송 성공"""
        mock_response = MagicMock(status_code=200)
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await notifier.send_message("Hello")
            assert result is True
            mock_client.post.assert_called_once()

    async def test_send_message_failure_retries(self, notifier):
        """Telegram 발송 실패 시 3회 재시도 후 False"""
        mock_response = MagicMock(status_code=500, text="Internal Server Error")
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await notifier.send_message("Hello")
            assert result is False
            assert mock_client.post.call_count == 3  # max_retries=3

    async def test_send_message_network_error_retries(self, notifier):
        """네트워크 오류 시 재시도"""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = ConnectionError("Network unreachable")
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await notifier.send_message("Hello")
            assert result is False
            assert mock_client.post.call_count == 3

    async def test_dispatch_alert_success(self, notifier):
        """Alert 객체 발송 성공 시 SENT 마킹"""
        alert = _make_alert()
        mock_response = MagicMock(status_code=200)
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await notifier.dispatch_alert(alert)
            assert result is True
            assert alert.status == AlertStatus.SENT
            assert alert.sent_at is not None

    async def test_dispatch_alert_failure_marks_failed(self, notifier):
        """Alert 발송 실패 시 FAILED 마킹"""
        alert = _make_alert()
        mock_response = MagicMock(status_code=500, text="Error")
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await notifier.dispatch_alert(alert)
            assert result is False
            assert alert.status == AlertStatus.FAILED

    async def test_level_filter_skips_low_priority(self):
        """레벨 필터: ERROR 필터에서 INFO 알림은 발송하지 않음"""
        with patch("core.notification.telegram_notifier.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                telegram=MagicMock(bot_token="tok", chat_id="cid", alert_level="ERROR")
            )
            notifier = TelegramNotifier()

        alert = _make_alert(level=AlertLevel.INFO)
        result = await notifier.dispatch_alert(alert)
        # 필터링된 알림은 True (발송 불필요), SENT 처리
        assert result is True
        assert alert.status == AlertStatus.SENT

    async def test_message_split_long_text(self, notifier):
        """4096자 초과 메시지 분할 발송"""
        long_text = "A" * 5000
        mock_response = MagicMock(status_code=200)
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await notifier.send_message(long_text)
            assert result is True
            assert mock_client.post.call_count == 2  # 5000 → 2 chunks

    async def test_retry_success_on_second_attempt(self, notifier):
        """첫 번째 실패 후 두 번째 성공"""
        fail_response = MagicMock(status_code=500, text="Error")
        ok_response = MagicMock(status_code=200)
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = [fail_response, ok_response]
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await notifier.send_message("Hello")
            assert result is True
            assert mock_client.post.call_count == 2


# ══════════════════════════════════════════════════════════════
# 2. FileNotifier 백업 채널
# ══════════════════════════════════════════════════════════════
class TestFileNotifier:
    """파일 기반 백업 알림 채널 테스트"""

    async def test_write_alert_to_file(self):
        """알림을 JSONL 파일에 기록"""
        with tempfile.TemporaryDirectory() as tmpdir:
            notifier = FileNotifier(log_dir=tmpdir)
            alert = _make_alert()

            result = await notifier.send(alert)
            assert result is True

            # 파일 존재 확인
            files = os.listdir(tmpdir)
            assert len(files) == 1
            assert files[0].startswith("alerts_")
            assert files[0].endswith(".jsonl")

            # 내용 검증
            with open(os.path.join(tmpdir, files[0]), "r") as f:
                line = f.readline()
                record = json.loads(line)
                assert record["id"] == alert.id
                assert record["title"] == "Test Alert"
                assert record["fallback_channel"] == "file"
                assert "written_at" in record

    async def test_multiple_alerts_appended(self):
        """여러 알림이 같은 파일에 추가"""
        with tempfile.TemporaryDirectory() as tmpdir:
            notifier = FileNotifier(log_dir=tmpdir)

            for i in range(3):
                await notifier.send(_make_alert(title=f"Alert {i}"))

            files = os.listdir(tmpdir)
            assert len(files) == 1

            with open(os.path.join(tmpdir, files[0]), "r") as f:
                lines = f.readlines()
                assert len(lines) == 3

    async def test_file_notifier_is_available(self):
        """파일 시스템 접근 가능"""
        with tempfile.TemporaryDirectory() as tmpdir:
            notifier = FileNotifier(log_dir=tmpdir)
            assert notifier.is_available() is True

    async def test_file_notifier_channel_name(self):
        """채널 이름 확인"""
        notifier = FileNotifier()
        assert notifier.channel_name == "file"

    async def test_file_size_limit_creates_new_file(self):
        """파일 크기 제한 초과 시 새 파일 생성"""
        with tempfile.TemporaryDirectory() as tmpdir:
            notifier = FileNotifier(log_dir=tmpdir, max_file_size_mb=0)  # 0MB = 즉시 새 파일

            # 먼저 첫 파일 생성
            await notifier.send(_make_alert(title="First"))
            # 두 번째는 크기 초과로 새 파일
            await notifier.send(_make_alert(title="Second"))

            files = os.listdir(tmpdir)
            assert len(files) >= 2


# ══════════════════════════════════════════════════════════════
# 3. ConsoleNotifier 최후 폴백
# ══════════════════════════════════════════════════════════════
class TestConsoleNotifier:
    """콘솔 로그 백업 채널 테스트"""

    async def test_send_logs_alert(self):
        """콘솔 출력 성공"""
        notifier = ConsoleNotifier()
        alert = _make_alert(level=AlertLevel.ERROR)

        result = await notifier.send(alert)
        assert result is True

    async def test_always_available(self):
        """항상 사용 가능"""
        notifier = ConsoleNotifier()
        assert notifier.is_available() is True

    async def test_channel_name(self):
        """채널 이름 확인"""
        notifier = ConsoleNotifier()
        assert notifier.channel_name == "console"

    async def test_all_alert_levels(self):
        """모든 알림 레벨 처리 가능"""
        notifier = ConsoleNotifier()
        for level in AlertLevel:
            alert = _make_alert(level=level)
            result = await notifier.send(alert)
            assert result is True


# ══════════════════════════════════════════════════════════════
# 4. NotificationRouter 폴백 동작
# ══════════════════════════════════════════════════════════════
class TestNotificationRouter:
    """NotificationRouter 폴백 로직 테스트"""

    async def test_primary_channel_success(self):
        """1차 채널 성공 시 백업 채널 호출 안 함"""
        primary = FakeChannel("telegram", should_succeed=True)
        backup = FakeChannel("file", should_succeed=True)

        router = NotificationRouter(channels=[primary, backup])
        result = await router.dispatch(_make_alert())

        assert result.success is True
        assert result.channel_used == "telegram"
        assert result.fallback_used is False
        assert primary.send_count == 1
        assert backup.send_count == 0

    async def test_fallback_on_primary_failure(self):
        """1차 실패 시 2차 백업으로 폴백"""
        primary = FakeChannel("telegram", should_succeed=False)
        backup = FakeChannel("file", should_succeed=True)

        router = NotificationRouter(channels=[primary, backup])
        result = await router.dispatch(_make_alert())

        assert result.success is True
        assert result.channel_used == "file"
        assert result.fallback_used is True
        assert primary.send_count == 1
        assert backup.send_count == 1

    async def test_cascade_to_third_channel(self):
        """1차+2차 실패 시 3차(콘솔)로 폴백"""
        primary = FakeChannel("telegram", should_succeed=False)
        secondary = FakeChannel("file", should_succeed=False)
        tertiary = FakeChannel("console", should_succeed=True)

        router = NotificationRouter(channels=[primary, secondary, tertiary])
        result = await router.dispatch(_make_alert())

        assert result.success is True
        assert result.channel_used == "console"
        assert result.fallback_used is True
        assert result.channels_tried == ["telegram", "file", "console"]

    async def test_all_channels_fail(self):
        """모든 채널 실패"""
        primary = FakeChannel("telegram", should_succeed=False)
        backup = FakeChannel("file", should_succeed=False)

        router = NotificationRouter(channels=[primary, backup])
        result = await router.dispatch(_make_alert())

        assert result.success is False
        assert result.all_failed is True
        assert result.channel_used == "none"
        assert result.channels_tried == ["telegram", "file"]

    async def test_skip_unavailable_channel(self):
        """사용 불가 채널은 건너뜀"""
        primary = FakeChannel("telegram", available=False)
        backup = FakeChannel("file", should_succeed=True)

        router = NotificationRouter(channels=[primary, backup])
        result = await router.dispatch(_make_alert())

        assert result.success is True
        assert result.channel_used == "file"
        assert primary.send_count == 0
        assert backup.send_count == 1

    async def test_exception_handling_in_channel(self):
        """채널에서 예외 발생 시 다음 채널로 진행"""
        exploding = ExplodingChannel("telegram")
        backup = FakeChannel("file", should_succeed=True)

        router = NotificationRouter(channels=[exploding, backup])
        result = await router.dispatch(_make_alert())

        assert result.success is True
        assert result.channel_used == "file"
        assert result.fallback_used is True

    async def test_empty_router_fails(self):
        """채널이 없으면 실패"""
        router = NotificationRouter(channels=[])
        result = await router.dispatch(_make_alert())

        assert result.success is False
        assert result.all_failed is True

    async def test_add_channel(self):
        """동적 채널 추가"""
        router = NotificationRouter(channels=[])
        ch = FakeChannel("dynamic", should_succeed=True)
        router.add_channel(ch)

        result = await router.dispatch(_make_alert())
        assert result.success is True
        assert result.channel_used == "dynamic"

    async def test_channel_health_tracking(self):
        """채널 건강 상태 추적"""
        primary = FakeChannel("telegram", should_succeed=False)
        backup = FakeChannel("file", should_succeed=True)
        router = NotificationRouter(channels=[primary, backup])

        # 3번 발송 (매번 telegram 실패 → file 성공)
        for _ in range(3):
            await router.dispatch(_make_alert())

        health = router.get_channel_health()
        telegram_health = next(h for h in health if h["channel_name"] == "telegram")
        file_health = next(h for h in health if h["channel_name"] == "file")

        assert telegram_health["total_failed"] == 3
        assert telegram_health["total_sent"] == 0
        assert telegram_health["consecutive_failures"] == 3
        assert telegram_health["status"] == "DEGRADED"

        assert file_health["total_sent"] == 3
        assert file_health["total_failed"] == 0
        assert file_health["status"] == "ACTIVE"

    async def test_get_channel_status(self):
        """특정 채널 상태 조회"""
        ch = FakeChannel("test", should_succeed=True)
        router = NotificationRouter(channels=[ch])
        await router.dispatch(_make_alert())

        status = router.get_channel_status("test")
        assert status is not None
        assert status["total_sent"] == 1

    async def test_get_nonexistent_channel_status(self):
        """존재하지 않는 채널 조회"""
        router = NotificationRouter(channels=[])
        assert router.get_channel_status("ghost") is None


# ══════════════════════════════════════════════════════════════
# 5. ChannelHealth 건강 상태 추적
# ══════════════════════════════════════════════════════════════
class TestChannelHealth:
    """ChannelHealth 상태 추적 테스트"""

    def test_initial_state(self):
        """초기 상태는 ACTIVE"""
        health = ChannelHealth(channel_name="test")
        assert health.status == ChannelStatus.ACTIVE
        assert health.consecutive_failures == 0
        assert health.total_sent == 0
        assert health.total_failed == 0

    def test_record_success_resets_failures(self):
        """성공 기록 시 연속 실패 초기화"""
        health = ChannelHealth(channel_name="test")
        health.consecutive_failures = 3
        health.status = ChannelStatus.DEGRADED

        health.record_success()
        assert health.consecutive_failures == 0
        assert health.status == ChannelStatus.ACTIVE
        assert health.total_sent == 1
        assert health.last_success_at is not None

    def test_degraded_after_threshold(self):
        """연속 실패 2회 → DEGRADED"""
        health = ChannelHealth(channel_name="test")
        health.record_failure()
        assert health.status == ChannelStatus.ACTIVE

        health.record_failure()
        assert health.status == ChannelStatus.DEGRADED

    def test_down_after_threshold(self):
        """연속 실패 5회 → DOWN"""
        health = ChannelHealth(channel_name="test")
        for _ in range(5):
            health.record_failure()
        assert health.status == ChannelStatus.DOWN
        assert health.consecutive_failures == 5

    def test_success_rate_calculation(self):
        """성공률 계산"""
        health = ChannelHealth(channel_name="test")
        assert health.success_rate == 1.0  # 전송 없음 → 100%

        health.record_success()
        health.record_success()
        health.record_failure()
        assert health.success_rate == pytest.approx(2 / 3, abs=0.01)

    def test_to_dict(self):
        """직렬화"""
        health = ChannelHealth(channel_name="telegram")
        health.record_success()
        d = health.to_dict()

        assert d["channel_name"] == "telegram"
        assert d["status"] == "ACTIVE"
        assert d["total_sent"] == 1
        assert d["success_rate"] == 1.0
        assert d["last_success_at"] is not None

    def test_recovery_from_down(self):
        """DOWN 상태에서 성공 시 ACTIVE 복구"""
        health = ChannelHealth(channel_name="test")
        for _ in range(5):
            health.record_failure()
        assert health.status == ChannelStatus.DOWN

        health.record_success()
        assert health.status == ChannelStatus.ACTIVE
        assert health.consecutive_failures == 0


# ══════════════════════════════════════════════════════════════
# 6. TelegramChannelAdapter 프로토콜 적합성
# ══════════════════════════════════════════════════════════════
class TestTelegramChannelAdapter:
    """TelegramChannelAdapter 테스트"""

    def test_channel_name(self):
        """채널 이름"""
        with patch("config.settings.get_settings") as ms:
            ms.return_value = MagicMock(telegram=MagicMock(bot_token="tok", chat_id="cid", alert_level="ALL"))
            adapter = TelegramChannelAdapter(bot_token="tok", chat_id="cid")
        assert adapter.channel_name == "telegram"

    def test_is_available_with_token(self):
        """토큰+채팅ID 설정 시 사용 가능"""
        with patch("config.settings.get_settings") as ms:
            ms.return_value = MagicMock(telegram=MagicMock(bot_token="tok", chat_id="cid", alert_level="ALL"))
            adapter = TelegramChannelAdapter(bot_token="tok", chat_id="cid")
        assert adapter.is_available() is True

    def test_is_unavailable_without_token(self):
        """토큰 미설정 시 사용 불가"""
        with patch("config.settings.get_settings") as ms:
            ms.return_value = MagicMock(telegram=MagicMock(bot_token="", chat_id="cid", alert_level="ALL"))
            adapter = TelegramChannelAdapter(bot_token="", chat_id="cid")
        assert adapter.is_available() is False

    async def test_send_delegates_to_transport(self):
        """send() → TelegramTransport.send_text() 위임"""
        with patch("config.settings.get_settings") as ms:
            ms.return_value = MagicMock(telegram=MagicMock(bot_token="tok", chat_id="cid", alert_level="ALL"))
            adapter = TelegramChannelAdapter(bot_token="tok", chat_id="cid")

        mock_response = MagicMock(status_code=200)
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await adapter.send(_make_alert())
            assert result is True

    async def test_filtered_alert_returns_true(self):
        """필터링된 알림은 True 반환"""
        with patch("config.settings.get_settings") as ms:
            ms.return_value = MagicMock(telegram=MagicMock(bot_token="tok", chat_id="cid", alert_level="ERROR"))
            adapter = TelegramChannelAdapter(bot_token="tok", chat_id="cid", alert_level="ERROR")

        result = await adapter.send(_make_alert(level=AlertLevel.INFO))
        assert result is True  # 필터링됨 = 발송 불필요


# ══════════════════════════════════════════════════════════════
# 7. DispatchResult 결과 추적
# ══════════════════════════════════════════════════════════════
class TestDispatchResult:
    """DispatchResult 테스트"""

    def test_to_dict(self):
        """직렬화"""
        result = DispatchResult(
            success=True,
            channel_used="telegram",
            fallback_used=False,
            channels_tried=["telegram"],
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["channel_used"] == "telegram"
        assert d["fallback_used"] is False
        assert d["channels_tried"] == ["telegram"]

    def test_all_failed_result(self):
        """전체 실패 결과"""
        result = DispatchResult(
            success=False,
            channel_used="none",
            all_failed=True,
            channels_tried=["telegram", "file", "console"],
        )
        assert result.all_failed is True
        assert len(result.channels_tried) == 3


# ══════════════════════════════════════════════════════════════
# 8. 통합 시나리오
# ══════════════════════════════════════════════════════════════
class TestNotificationIntegration:
    """통합 시나리오 테스트"""

    async def test_full_fallback_chain_telegram_file_console(self):
        """전체 폴백 체인: Telegram→File→Console"""
        with tempfile.TemporaryDirectory() as tmpdir:
            telegram = FakeChannel("telegram", should_succeed=False)
            file_ch = FileNotifier(log_dir=tmpdir)
            console = ConsoleNotifier()

            router = NotificationRouter(channels=[telegram, file_ch, console])
            alert = _make_alert(level=AlertLevel.CRITICAL, title="Critical Error")

            result = await router.dispatch(alert)

            assert result.success is True
            assert result.channel_used == "file"
            assert result.fallback_used is True

            # 파일에 기록 확인
            files = os.listdir(tmpdir)
            assert len(files) == 1

    async def test_telegram_down_console_fallback(self):
        """Telegram+File 모두 실패 → Console 폴백"""
        telegram = FakeChannel("telegram", should_succeed=False)
        file_ch = FakeChannel("file", should_succeed=False)
        console = ConsoleNotifier()

        router = NotificationRouter(channels=[telegram, file_ch, console])
        result = await router.dispatch(_make_alert())

        assert result.success is True
        assert result.channel_used == "console"
        assert result.channels_tried == ["telegram", "file", "console"]

    async def test_health_degrades_after_repeated_failures(self):
        """반복 실패 시 채널 건강 상태 악화"""
        primary = FakeChannel("telegram", should_succeed=False)
        backup = FakeChannel("file", should_succeed=True)
        router = NotificationRouter(channels=[primary, backup])

        for _ in range(5):
            await router.dispatch(_make_alert())

        health = router.get_channel_health()
        telegram_h = next(h for h in health if h["channel_name"] == "telegram")
        assert telegram_h["status"] == "DOWN"
        assert telegram_h["consecutive_failures"] == 5

    async def test_multiple_alerts_different_levels(self):
        """다양한 레벨의 알림 발송"""
        primary = FakeChannel("telegram", should_succeed=True)
        router = NotificationRouter(channels=[primary])

        for level in [AlertLevel.INFO, AlertLevel.WARNING, AlertLevel.ERROR, AlertLevel.CRITICAL]:
            result = await router.dispatch(_make_alert(level=level))
            assert result.success is True

        assert primary.send_count == 4
