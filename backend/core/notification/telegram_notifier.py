"""
텔레그램 알림 발송 서비스 (Telegram Notifier)

Phase 5: 텔레그램 봇을 통한 실시간 알림 전달

기능:
  - AlertManager에서 생성된 알림을 텔레그램으로 전달
  - 알림 레벨별 필터링 (ALL/IMPORTANT/ERROR)
  - 메시지 포맷팅 (Alert → HTML)
  - 상태 관리 (dispatch_alert → mark_sent_by_id / mark_failed_with_retry)

HTTP 전송은 TelegramTransport 에 위임한다 (SSOT).
"""

from typing import Optional

from config.logging import logger
from config.settings import get_settings
from core.notification.alert_manager import (
    Alert,
    AlertLevel,
    AlertManager,
    AlertStatus,
)
from core.notification.telegram_transport import (
    TelegramTransport,
    create_transport,
)

# 알림 레벨 우선순위 매핑
ALERT_LEVEL_PRIORITY = {
    AlertLevel.INFO: 0,
    AlertLevel.WARNING: 1,
    AlertLevel.ERROR: 2,
    AlertLevel.CRITICAL: 3,
}

# 필터 레벨 최소 우선순위 매핑
FILTER_LEVEL_MAP = {
    "ALL": 0,  # 모든 알림
    "IMPORTANT": 1,  # WARNING 이상
    "ERROR": 2,  # ERROR 이상
}

# 하위호환: 기존 import 경로 유지 (from telegram_notifier import TELEGRAM_MAX_LENGTH)
from core.notification.telegram_transport import TELEGRAM_MAX_LENGTH  # noqa: E402, F401


class TelegramNotifier:
    """
    텔레그램 알림 발송 서비스

    AlertManager와 연동하여 알림 생성 → 텔레그램 전달 → 상태 업데이트
    전체 흐름을 처리합니다.

    HTTP 전송은 TelegramTransport 에 위임합니다.
    """

    def __init__(
        self,
        alert_manager: Optional[AlertManager] = None,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        alert_level: Optional[str] = None,
        transport: Optional[TelegramTransport] = None,
    ):
        settings = get_settings()
        self._alert_manager = alert_manager or AlertManager()
        self._alert_level = alert_level or settings.telegram.alert_level

        # Transport 주입 또는 자동 생성
        if transport is not None:
            self._transport = transport
        else:
            self._transport = create_transport(
                bot_token=bot_token,
                chat_id=chat_id,
            )

        # 하위호환 프로퍼티 (기존 코드에서 직접 참조하는 곳 대응)
        self._bot_token = self._transport.bot_token
        self._chat_id = self._transport.chat_id

    # ══════════════════════════════════════
    # 메시지 발송 (Transport 위임)
    # ══════════════════════════════════════
    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """
        텔레그램 메시지 발송

        Args:
            text: 발송할 메시지
            parse_mode: 파싱 모드 (HTML / Markdown)

        Returns:
            발송 성공 여부
        """
        return await self._transport.send_text(text, parse_mode=parse_mode)

    # ══════════════════════════════════════
    # 알림 발송 (AlertManager 연동)
    # ══════════════════════════════════════
    async def dispatch_alert(self, alert: Alert) -> bool:
        """
        Alert 객체를 텔레그램으로 발송

        알림 레벨 필터를 통과한 경우에만 실제 발송합니다.
        상태 전이는 AlertManager 의 원자적 메서드(mark_sent_by_id,
        mark_failed_with_retry)를 사용하여 _dispatch_via_router 경로와
        일관성을 유지한다.

        Args:
            alert: 발송할 Alert 객체

        Returns:
            발송 성공 여부
        """
        # PENDING → SENDING 원자 전이 (Router 경로와 동일한 상태 머신 사용)
        await self._alert_manager.claim_for_sending(alert.id)

        # 레벨 필터 체크
        if not self._should_send(alert.level):
            logger.debug(f"Alert filtered out: [{alert.level.value}] " f"filter={self._alert_level}")
            await self._alert_manager.mark_sent_by_id(alert.id)
            return True

        # 텔레그램 메시지 포맷팅
        formatted = self._format_alert(alert)

        # 발송
        success = await self.send_message(formatted)

        # 상태 업데이트 — 원자적 전이 메서드 사용 (save_alert 이중 호출 제거)
        if success:
            await self._alert_manager.mark_sent_by_id(alert.id)
            logger.info(f"Alert dispatched: {alert.id} [{alert.alert_type.value}]")
        else:
            await self._alert_manager.mark_failed_with_retry(
                alert.id,
                error="Telegram send_message failed",
                status_code=None,
            )
            logger.error(f"Alert dispatch failed: {alert.id}")

        return success

    async def dispatch_pending_alerts(self) -> dict:
        """
        대기 중인 알림 일괄 발송

        Returns:
            {"sent": int, "failed": int, "filtered": int}
        """
        pending = await self._alert_manager.get_alerts(limit=100, status=AlertStatus.PENDING)

        result = {"sent": 0, "failed": 0, "filtered": 0}

        for alert_data in pending:
            level_str = alert_data.get("level", "INFO")

            try:
                level = AlertLevel(level_str)
            except ValueError:
                level = AlertLevel.INFO

            if not self._should_send(level):
                result["filtered"] += 1
                continue

            # PENDING → SENDING 원자 전이
            alert_id = alert_data.get("id", "")
            claimed = await self._alert_manager.claim_for_sending(alert_id)
            if not claimed:
                continue

            # 발송
            message = alert_data.get("message", "")
            title = alert_data.get("title", "")
            formatted = f"<b>{title}</b>\n\n{message}"

            success = await self.send_message(formatted)
            if success:
                await self._alert_manager.mark_sent_by_id(alert_id)
                result["sent"] += 1
            else:
                await self._alert_manager.mark_failed_with_retry(
                    alert_id,
                    error="Telegram batch send_message failed",
                    status_code=None,
                )
                result["failed"] += 1

        logger.info(
            f"Batch dispatch complete: "
            f"sent={result['sent']}, failed={result['failed']}, "
            f"filtered={result['filtered']}"
        )
        return result

    # ══════════════════════════════════════
    # 편의 메서드
    # ══════════════════════════════════════
    async def send_daily_report(self, report_data: dict) -> bool:
        """일일 리포트 전송"""
        from config.constants import AlertType

        alert = self._alert_manager.create_from_template(AlertType.DAILY_REPORT, report_data)
        return await self.dispatch_alert(alert)

    async def send_emergency_alert(self, reason: str, details: dict) -> bool:
        """긴급 알림 전송"""
        from config.constants import AlertType

        alert = self._alert_manager.create_from_template(
            AlertType.EMERGENCY_REBALANCING,
            {"reason": reason, **details},
        )
        return await self.dispatch_alert(alert)

    async def send_error_alert(self, module: str, error_message: str, details: str = "") -> bool:
        """시스템 오류 알림 전송"""
        from datetime import datetime, timezone

        from config.constants import AlertType

        alert = self._alert_manager.create_from_template(
            AlertType.SYSTEM_ERROR,
            {
                "module": module,
                "error_message": error_message,
                "occurred_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                "details": details or "상세 정보 없음",
            },
        )
        return await self.dispatch_alert(alert)

    # ══════════════════════════════════════
    # 내부 유틸리티
    # ══════════════════════════════════════
    def _should_send(self, level: AlertLevel) -> bool:
        """알림 레벨 필터 통과 여부 확인"""
        min_priority = FILTER_LEVEL_MAP.get(self._alert_level, 0)
        alert_priority = ALERT_LEVEL_PRIORITY.get(level, 0)
        return alert_priority >= min_priority

    @staticmethod
    def _format_alert(alert: Alert) -> str:
        """Alert를 텔레그램 HTML 메시지로 포맷팅"""
        level_emoji = {
            AlertLevel.INFO: "ℹ️",
            AlertLevel.WARNING: "⚠️",
            AlertLevel.ERROR: "❌",
            AlertLevel.CRITICAL: "🚨",
        }
        emoji = level_emoji.get(alert.level, "📢")

        return (
            f"{emoji} <b>{alert.title}</b>\n"
            f"<code>[{alert.level.value}]</code>\n\n"
            f"{alert.message}\n\n"
            f"<i>{alert.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}</i>"
        )

    @staticmethod
    def _split_message(text: str) -> list[str]:
        """긴 메시지를 텔레그램 최대 길이에 맞게 분할

        하위호환용. 신규 코드는 telegram_transport.split_message() 를 사용한다.
        """
        from core.notification.telegram_transport import split_message

        return split_message(text)
