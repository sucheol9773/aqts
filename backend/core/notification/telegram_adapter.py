"""
TelegramNotifier → NotificationChannel 어댑터

기존 TelegramNotifier를 NotificationRouter에서 사용할 수 있도록
NotificationChannel 프로토콜에 맞춰 래핑합니다.
"""

from typing import Optional

from core.notification.alert_manager import Alert, AlertManager
from core.notification.telegram_notifier import TelegramNotifier


class TelegramChannelAdapter:
    """
    TelegramNotifier를 NotificationChannel 프로토콜에 맞춘 어댑터

    기존 TelegramNotifier의 dispatch_alert 로직을 send()로 위임하되,
    AlertManager 상태 업데이트는 라우터 레벨에서 처리하도록 분리합니다.
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        alert_level: Optional[str] = None,
        alert_manager: Optional[AlertManager] = None,
    ):
        self._notifier = TelegramNotifier(
            alert_manager=alert_manager,
            bot_token=bot_token,
            chat_id=chat_id,
            alert_level=alert_level,
        )
        self._channel_name = "telegram"

    @property
    def channel_name(self) -> str:
        return self._channel_name

    def is_available(self) -> bool:
        """봇 토큰과 채팅 ID가 설정되어 있으면 사용 가능"""
        return bool(self._notifier._bot_token) and bool(self._notifier._chat_id)

    async def send(self, alert: Alert) -> bool:
        """
        Alert를 텔레그램으로 발송

        레벨 필터를 통과하지 못하면 True를 반환합니다 (필터링 = 발송 불필요).
        """
        # 레벨 필터 체크
        if not self._notifier._should_send(alert.level):
            return True  # 필터링됨 = 발송 불필요

        formatted = self._notifier._format_alert(alert)
        return await self._notifier.send_message(formatted)
