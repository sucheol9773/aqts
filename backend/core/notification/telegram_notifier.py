"""
텔레그램 알림 발송 서비스 (Telegram Notifier)

Phase 5: 텔레그램 봇을 통한 실시간 알림 전달

기능:
  - AlertManager에서 생성된 알림을 텔레그램으로 전달
  - 알림 레벨별 필터링 (ALL/IMPORTANT/ERROR)
  - 발송 실패 시 재시도 (최대 3회)
  - 메시지 길이 제한 (4096자) 자동 분할
"""

import asyncio
from typing import Optional

import httpx

from config.logging import logger
from config.settings import get_settings
from core.notification.alert_manager import (
    Alert,
    AlertLevel,
    AlertManager,
    AlertStatus,
)


# 텔레그램 메시지 최대 길이
TELEGRAM_MAX_LENGTH = 4096

# 알림 레벨 우선순위 매핑
ALERT_LEVEL_PRIORITY = {
    AlertLevel.INFO: 0,
    AlertLevel.WARNING: 1,
    AlertLevel.ERROR: 2,
    AlertLevel.CRITICAL: 3,
}

# 필터 레벨 최소 우선순위 매핑
FILTER_LEVEL_MAP = {
    "ALL": 0,         # 모든 알림
    "IMPORTANT": 1,   # WARNING 이상
    "ERROR": 2,       # ERROR 이상
}


class TelegramNotifier:
    """
    텔레그램 알림 발송 서비스

    AlertManager와 연동하여 알림 생성 → 텔레그램 전달 → 상태 업데이트
    전체 흐름을 처리합니다.
    """

    TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"

    def __init__(
        self,
        alert_manager: Optional[AlertManager] = None,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        alert_level: Optional[str] = None,
    ):
        settings = get_settings()
        self._alert_manager = alert_manager or AlertManager()
        self._bot_token = bot_token or settings.telegram.bot_token
        self._chat_id = chat_id or settings.telegram.chat_id
        self._alert_level = alert_level or settings.telegram.alert_level
        self._base_url = self.TELEGRAM_API_BASE.format(token=self._bot_token)
        self._max_retries = 3

    # ══════════════════════════════════════
    # 메시지 발송
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
        # 메시지 길이 초과 시 분할
        messages = self._split_message(text)

        for msg in messages:
            success = await self._send_single_message(msg, parse_mode)
            if not success:
                return False
            # 연속 발송 시 딜레이
            if len(messages) > 1:
                await asyncio.sleep(0.5)

        return True

    async def _send_single_message(
        self, text: str, parse_mode: str = "HTML"
    ) -> bool:
        """단일 메시지 발송 (재시도 포함)"""
        url = f"{self._base_url}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }

        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.post(url, json=payload)
                    if response.status_code == 200:
                        return True

                    logger.warning(
                        f"Telegram send failed (attempt {attempt}): "
                        f"status={response.status_code}, body={response.text}"
                    )
            except Exception as e:
                logger.warning(
                    f"Telegram send error (attempt {attempt}): {e}"
                )

            if attempt < self._max_retries:
                await asyncio.sleep(1.0 * attempt)

        logger.error("Telegram message send failed after max retries")
        return False

    # ══════════════════════════════════════
    # 알림 발송 (AlertManager 연동)
    # ══════════════════════════════════════
    async def dispatch_alert(self, alert: Alert) -> bool:
        """
        Alert 객체를 텔레그램으로 발송

        알림 레벨 필터를 통과한 경우에만 실제 발송합니다.

        Args:
            alert: 발송할 Alert 객체

        Returns:
            발송 성공 여부
        """
        # 레벨 필터 체크
        if not self._should_send(alert.level):
            logger.debug(
                f"Alert filtered out: [{alert.level.value}] "
                f"filter={self._alert_level}"
            )
            alert.mark_sent()  # 필터링된 경우에도 SENT 처리
            return True

        # 텔레그램 메시지 포맷팅
        formatted = self._format_alert(alert)

        # 발송
        success = await self.send_message(formatted)

        # 상태 업데이트
        if success:
            alert.mark_sent()
            await self._alert_manager.save_alert(alert)
            logger.info(f"Alert dispatched: {alert.id} [{alert.alert_type.value}]")
        else:
            alert.mark_failed()
            await self._alert_manager.save_alert(alert)
            logger.error(f"Alert dispatch failed: {alert.id}")

        return success

    async def dispatch_pending_alerts(self) -> dict:
        """
        대기 중인 알림 일괄 발송

        Returns:
            {"sent": int, "failed": int, "filtered": int}
        """
        pending = await self._alert_manager.get_alerts(
            limit=100, status=AlertStatus.PENDING
        )

        result = {"sent": 0, "failed": 0, "filtered": 0}

        for alert_data in pending:
            alert_type_str = alert_data.get("alert_type", "")
            level_str = alert_data.get("level", "INFO")

            try:
                level = AlertLevel(level_str)
            except ValueError:
                level = AlertLevel.INFO

            if not self._should_send(level):
                result["filtered"] += 1
                continue

            # 발송
            message = alert_data.get("message", "")
            title = alert_data.get("title", "")
            formatted = f"<b>{title}</b>\n\n{message}"

            success = await self.send_message(formatted)
            if success:
                alert_id = alert_data.get("id", "")
                await self._alert_manager.mark_alert_read(alert_id)
                result["sent"] += 1
            else:
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

        alert = self._alert_manager.create_from_template(
            AlertType.DAILY_REPORT, report_data
        )
        return await self.dispatch_alert(alert)

    async def send_emergency_alert(self, reason: str, details: dict) -> bool:
        """긴급 알림 전송"""
        from config.constants import AlertType

        alert = self._alert_manager.create_from_template(
            AlertType.EMERGENCY_REBALANCING,
            {"reason": reason, **details},
        )
        return await self.dispatch_alert(alert)

    async def send_error_alert(
        self, module: str, error_message: str, details: str = ""
    ) -> bool:
        """시스템 오류 알림 전송"""
        from config.constants import AlertType
        from datetime import datetime, timezone

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
        """긴 메시지를 텔레그램 최대 길이에 맞게 분할"""
        if len(text) <= TELEGRAM_MAX_LENGTH:
            return [text]

        messages = []
        while text:
            if len(text) <= TELEGRAM_MAX_LENGTH:
                messages.append(text)
                break

            # 줄바꿈 기준으로 분할
            split_idx = text.rfind("\n", 0, TELEGRAM_MAX_LENGTH)
            if split_idx == -1:
                split_idx = TELEGRAM_MAX_LENGTH

            messages.append(text[:split_idx])
            text = text[split_idx:].lstrip("\n")

        return messages
