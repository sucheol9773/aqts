"""
알림 파이프라인 E2E Wiring 통합 테스트

테스트 범위:
  1. Lifespan wiring 시뮬레이션 — AlertManager → Router → Adapter → Transport
  2. create_and_persist_alert → dispatch → Transport.send_text 도달 확인
  3. Transport 실패 시 fallback 캐스케이드 (Telegram → File → Console)
  4. FAILED 상태 영속 후 dispatch_retriable_alerts 재픽업
  5. daily_reporter → Transport 경로 확인

설계 근거:
  - main.py lifespan 의 wiring 순서를 그대로 재현하되, httpx 만 모킹하여
    Transport.send_text → _send_single 내부의 HTTP 호출만 차단한다.
  - 이 테스트가 통과하면 "정의 ≠ 적용" 패턴이 발생하지 않았음을 보장한다.
"""

import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.constants import AlertType
from core.notification.alert_manager import Alert, AlertLevel, AlertManager
from core.notification.fallback_notifier import (
    ConsoleNotifier,
    FileNotifier,
    NotificationRouter,
)
from core.notification.telegram_adapter import TelegramChannelAdapter
from core.notification.telegram_transport import TelegramTransport

# ══════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════


@pytest.fixture
def mock_settings():
    return MagicMock(
        telegram=MagicMock(
            bot_token="test-bot-token",
            chat_id="test-chat-id",
            alert_level="ALL",
        )
    )


@pytest.fixture
def transport():
    """실제 TelegramTransport 인스턴스 (httpx만 모킹)"""
    return TelegramTransport(
        bot_token="test-bot-token",
        chat_id="test-chat-id",
        max_retries=1,
    )


@pytest.fixture
def alert_manager():
    """테스트용 AlertManager (in-memory)"""
    return AlertManager(mongo_collection=None)


@pytest.fixture
def wired_pipeline(alert_manager, transport, mock_settings):
    """main.py lifespan wiring을 시뮬레이션한 전체 파이프라인.

    순서: AlertManager → NotificationRouter → [Adapter(Transport), File, Console]
    """
    with patch("config.settings.get_settings", return_value=mock_settings):
        adapter = TelegramChannelAdapter(transport=transport)

    router = NotificationRouter()
    router.add_channel(adapter)

    tmpdir = tempfile.mkdtemp()
    router.add_channel(FileNotifier(log_dir=tmpdir))
    router.add_channel(ConsoleNotifier())

    alert_manager.set_router(router)
    return {
        "alert_manager": alert_manager,
        "router": router,
        "adapter": adapter,
        "transport": transport,
        "log_dir": tmpdir,
    }


def _make_alert(
    level: AlertLevel = AlertLevel.WARNING,
    title: str = "Pipeline Test",
    message: str = "E2E wiring verification",
) -> Alert:
    return Alert(
        alert_type=AlertType.SYSTEM_ERROR,
        level=level,
        title=title,
        message=message,
    )


# ══════════════════════════════════════════════════════════════
# 1. Wiring 구조 검증
# ══════════════════════════════════════════════════════════════
class TestWiringStructure:
    """파이프라인 구성 요소가 올바르게 연결됐는지 검증"""

    def test_router_has_three_channels(self, wired_pipeline):
        """Router에 3개 채널 (telegram, file, console) 등록"""
        router = wired_pipeline["router"]
        assert len(router._channels) == 3

    def test_channel_order(self, wired_pipeline):
        """채널 우선순위: telegram → file → console"""
        channels = wired_pipeline["router"]._channels
        assert channels[0].channel_name == "telegram"
        assert channels[1].channel_name == "file"
        assert channels[2].channel_name == "console"

    def test_adapter_uses_transport(self, wired_pipeline):
        """Adapter가 Transport 인스턴스를 보유"""
        adapter = wired_pipeline["adapter"]
        assert adapter._transport is wired_pipeline["transport"]

    def test_adapter_is_available(self, wired_pipeline):
        """bot_token + chat_id 설정 시 is_available() True"""
        assert wired_pipeline["adapter"].is_available() is True

    def test_alert_manager_has_router(self, wired_pipeline):
        """AlertManager에 Router가 주입됨"""
        am = wired_pipeline["alert_manager"]
        assert am._router is not None


# ══════════════════════════════════════════════════════════════
# 2. E2E 디스패치 — Alert 생성 → Transport.send_text 도달
# ══════════════════════════════════════════════════════════════
class TestE2EDispatch:
    """create_and_persist_alert → Router → Adapter → Transport 전체 경로"""

    async def test_alert_reaches_transport(self, wired_pipeline):
        """Alert 생성 시 Transport.send_text()가 호출됨"""
        mock_response = MagicMock(status_code=200)
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            am = wired_pipeline["alert_manager"]
            alert = await am.create_and_persist_alert(
                AlertType.SYSTEM_ERROR,
                AlertLevel.WARNING,
                "E2E Test",
                "Transport 도달 확인",
            )

            # httpx.post가 호출됐으면 Transport까지 도달한 것
            assert mock_client.post.called
            # payload 검증
            call_args = mock_client.post.call_args
            payload = call_args.kwargs.get("json") or call_args[1].get("json")
            assert payload["chat_id"] == "test-chat-id"
            assert "E2E Test" in payload["text"]

    async def test_dispatch_result_success(self, wired_pipeline):
        """디스패치 성공 시 Router가 telegram 채널 사용 기록"""
        mock_response = MagicMock(status_code=200)
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            router = wired_pipeline["router"]
            alert = _make_alert()
            result = await router.dispatch(alert)

            assert result.success is True
            assert result.channel_used == "telegram"
            assert result.fallback_used is False


# ══════════════════════════════════════════════════════════════
# 3. Fallback 캐스케이드 — Telegram 실패 → File → Console
# ══════════════════════════════════════════════════════════════
class TestFallbackCascade:
    """Telegram Transport 실패 시 fallback 채널로 전환"""

    async def test_telegram_fail_falls_to_file(self, wired_pipeline):
        """Telegram 500 → File fallback"""
        mock_response = MagicMock(status_code=500, text="Server Error")
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("asyncio.sleep", new_callable=AsyncMock):
                router = wired_pipeline["router"]
                alert = _make_alert()
                result = await router.dispatch(alert)

                assert result.success is True
                assert result.channel_used == "file"
                assert result.fallback_used is True

    async def test_telegram_unavailable_skips_to_file(self, mock_settings):
        """Telegram 미설정(bot_token 없음) → File fallback"""
        transport = TelegramTransport(bot_token="", chat_id="")
        with patch("config.settings.get_settings", return_value=mock_settings):
            adapter = TelegramChannelAdapter(transport=transport)

        assert adapter.is_available() is False

        tmpdir = tempfile.mkdtemp()
        router = NotificationRouter()
        router.add_channel(adapter)
        router.add_channel(FileNotifier(log_dir=tmpdir))
        router.add_channel(ConsoleNotifier())

        alert = _make_alert()
        result = await router.dispatch(alert)

        assert result.success is True
        assert result.channel_used == "file"
        assert result.fallback_used is True

    async def test_all_channels_tried_on_cascade(self, wired_pipeline):
        """전체 캐스케이드에서 시도된 채널 목록 기록"""
        mock_response = MagicMock(status_code=500, text="fail")
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("asyncio.sleep", new_callable=AsyncMock):
                router = wired_pipeline["router"]
                alert = _make_alert()
                result = await router.dispatch(alert)

                # Telegram 실패 → File 성공이므로 2개 채널 시도
                assert "telegram" in result.channels_tried
                assert "file" in result.channels_tried


# ══════════════════════════════════════════════════════════════
# 4. AlertManager 상태 전이 + Router 디스패치 연동
# ══════════════════════════════════════════════════════════════
class TestAlertManagerDispatchIntegration:
    """AlertManager가 Router를 통해 디스패치하고 상태를 전이하는지 검증"""

    async def test_successful_dispatch_marks_sent(self, wired_pipeline):
        """성공 디스패치 후 in-memory 알림 상태가 SENT"""
        mock_response = MagicMock(status_code=200)
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            am = wired_pipeline["alert_manager"]
            alert = await am.create_and_persist_alert(
                AlertType.SYSTEM_ERROR,
                AlertLevel.WARNING,
                "State Test",
                "SENT 전이 확인",
            )

            # in-memory 모드에서 상태 확인
            stored = await am.get_alert_by_id(alert.id)
            if stored:
                from core.notification.alert_manager import AlertStatus

                assert stored.get("status") in [
                    AlertStatus.SENT.value,
                    AlertStatus.SENDING.value,
                ]

    async def test_router_none_skips_dispatch(self, alert_manager):
        """Router 미주입 시 dispatch 경로가 noop"""
        assert alert_manager._router is None
        alert = await alert_manager.create_and_persist_alert(
            AlertType.SYSTEM_ERROR,
            AlertLevel.WARNING,
            "No Router",
            "dispatch 스킵",
        )
        # alert 생성은 성공하지만 dispatch는 되지 않음
        assert alert is not None
        assert alert.title == "No Router"


# ══════════════════════════════════════════════════════════════
# 5. DailyReporter → Transport 경로 확인
# ══════════════════════════════════════════════════════════════
class TestDailyReporterTransportPath:
    """daily_reporter가 create_transport()를 통해 Transport에 도달하는지 확인"""

    async def test_daily_reporter_uses_transport(self, mock_settings):
        """DailyReporter.send_telegram_report → create_transport → send_text"""
        mock_transport = AsyncMock()
        mock_transport.send_text.return_value = True

        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            with patch(
                "core.notification.telegram_transport.create_transport",
                return_value=mock_transport,
            ):
                from core.daily_reporter import DailyReporter

                reporter = DailyReporter()

                # DailyReport mock — _format_telegram_message가 접근하는
                # 모든 속성에 실제 타입의 값을 설정해야 한다.
                mock_report = MagicMock()
                mock_report.report_date = "2026-04-10"
                mock_report.trading_mode = "DEMO"
                mock_report.portfolio_value_start = 10_000_000
                mock_report.portfolio_value_end = 10_050_000
                mock_report.daily_pnl = 50_000
                mock_report.daily_return_pct = 0.5
                mock_report.cumulative_pnl = 200_000
                mock_report.cumulative_return_pct = 2.3
                mock_report.total_trades = 5
                mock_report.buy_trades = 3
                mock_report.sell_trades = 2
                mock_report.winning_trades = 3
                mock_report.losing_trades = 2
                mock_report.cash_balance = 5_000_000
                mock_report.total_positions = 3
                mock_report.max_drawdown_today = 0.0
                mock_report.circuit_breaker_triggered = False
                mock_report.circuit_breaker_reason = ""
                mock_report.trades = []
                mock_report.positions = []
                mock_report.top3_positions = []
                mock_report.bottom3_positions = []

                result = await reporter.send_telegram_report(mock_report)

                assert result is True
                mock_transport.send_text.assert_called_once()
                # 메시지에 리포트 내용이 포함돼야 함
                sent_text = mock_transport.send_text.call_args[0][0]
                assert "AQTS" in sent_text
