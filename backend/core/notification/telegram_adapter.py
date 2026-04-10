"""
TelegramTransport → NotificationChannel 어댑터

TelegramTransport 를 NotificationRouter 에서 사용할 수 있도록
NotificationChannel 프로토콜에 맞춰 래핑합니다.

HTTP 전송은 TelegramTransport 에 위임하고,
포맷팅/필터링은 TelegramNotifier 의 static 유틸을 재사용합니다.
"""

from typing import Optional

from core.notification.alert_manager import Alert, AlertLevel, AlertManager
from core.notification.telegram_notifier import (
    ALERT_LEVEL_PRIORITY,
    FILTER_LEVEL_MAP,
    TelegramNotifier,
)
from core.notification.telegram_transport import (
    TelegramTransport,
    create_transport,
)


class TelegramChannelAdapter:
    """
    TelegramTransport 를 NotificationChannel 프로토콜에 맞춘 어댑터

    Transport 가 주입되면 직접 사용하고, 없으면 create_transport() 로
    자동 생성한다. 포맷팅과 레벨 필터링은 TelegramNotifier 의 static
    메서드를 재사용하여 중복을 제거한다.
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        alert_level: Optional[str] = None,
        alert_manager: Optional[AlertManager] = None,
        transport: Optional[TelegramTransport] = None,
    ):
        # Transport 주입 또는 자동 생성
        if transport is not None:
            self._transport = transport
        else:
            self._transport = create_transport(
                bot_token=bot_token,
                chat_id=chat_id,
            )

        from config.settings import get_settings

        settings = get_settings()
        self._alert_level = alert_level or settings.telegram.alert_level
        self._channel_name = "telegram"

    @property
    def channel_name(self) -> str:
        return self._channel_name

    def is_available(self) -> bool:
        """봇 토큰과 채팅 ID가 설정되어 있으면 사용 가능"""
        return self._transport.is_configured()

    async def send(self, alert: Alert) -> bool:
        """
        Alert를 텔레그램으로 발송

        레벨 필터를 통과하지 못하면 True를 반환합니다 (필터링 = 발송 불필요).
        """
        # 레벨 필터 체크
        if not self._should_send(alert.level):
            return True  # 필터링됨 = 발송 불필요

        formatted = TelegramNotifier._format_alert(alert)
        return await self._transport.send_text(formatted)

    def _should_send(self, level: AlertLevel) -> bool:
        """알림 레벨 필터 통과 여부 확인"""
        min_priority = FILTER_LEVEL_MAP.get(self._alert_level, 0)
        alert_priority = ALERT_LEVEL_PRIORITY.get(level, 0)
        return alert_priority >= min_priority
