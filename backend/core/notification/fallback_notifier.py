"""
백업 알림 채널 (Fallback Notifier)

Gate C: 1차 채널(Telegram) 장애 시 대체 알림 동작

기능:
  - 파일 기반 백업 알림 (JSON Lines 형식)
  - 콘솔 로그 백업 알림 (Loguru 경유)
  - NotificationRouter: 1차 → 백업 자동 폴백
  - 발송 이력 추적 (채널별 성공/실패 통계)
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional, Protocol

from config.logging import logger
from core.notification.alert_manager import Alert, AlertLevel


# ══════════════════════════════════════
# 알림 채널 프로토콜
# ══════════════════════════════════════
class NotificationChannel(Protocol):
    """알림 채널 인터페이스"""

    @property
    def channel_name(self) -> str: ...

    async def send(self, alert: Alert) -> bool: ...

    def is_available(self) -> bool: ...


# ══════════════════════════════════════
# 채널 상태 추적
# ══════════════════════════════════════
class ChannelStatus(str, Enum):
    """채널 상태"""

    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"  # 간헐적 실패
    DOWN = "DOWN"  # 연속 실패


@dataclass
class ChannelHealth:
    """채널 건강 상태 추적"""

    channel_name: str
    total_sent: int = 0
    total_failed: int = 0
    consecutive_failures: int = 0
    last_success_at: Optional[datetime] = None
    last_failure_at: Optional[datetime] = None
    status: ChannelStatus = ChannelStatus.ACTIVE

    # 연속 실패 임계값
    DEGRADED_THRESHOLD: int = 2
    DOWN_THRESHOLD: int = 5

    def record_success(self) -> None:
        """성공 기록"""
        self.total_sent += 1
        self.consecutive_failures = 0
        self.last_success_at = datetime.now(timezone.utc)
        self.status = ChannelStatus.ACTIVE

    def record_failure(self) -> None:
        """실패 기록"""
        self.total_failed += 1
        self.consecutive_failures += 1
        self.last_failure_at = datetime.now(timezone.utc)

        if self.consecutive_failures >= self.DOWN_THRESHOLD:
            self.status = ChannelStatus.DOWN
        elif self.consecutive_failures >= self.DEGRADED_THRESHOLD:
            self.status = ChannelStatus.DEGRADED

    @property
    def success_rate(self) -> float:
        """성공률 (0.0 ~ 1.0)"""
        total = self.total_sent + self.total_failed
        if total == 0:
            return 1.0
        return self.total_sent / total

    def to_dict(self) -> dict:
        return {
            "channel_name": self.channel_name,
            "status": self.status.value,
            "total_sent": self.total_sent,
            "total_failed": self.total_failed,
            "consecutive_failures": self.consecutive_failures,
            "success_rate": round(self.success_rate, 4),
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "last_failure_at": self.last_failure_at.isoformat() if self.last_failure_at else None,
        }


# ══════════════════════════════════════
# 파일 백업 채널
# ══════════════════════════════════════
class FileNotifier:
    """
    파일 기반 백업 알림 채널

    알림을 JSON Lines 파일에 기록합니다.
    Telegram 등 1차 채널 장애 시 알림 유실을 방지합니다.
    """

    def __init__(self, log_dir: str = "logs/alerts", max_file_size_mb: int = 50):
        self._log_dir = Path(log_dir)
        self._max_file_size = max_file_size_mb * 1024 * 1024  # bytes
        self._channel_name = "file"

    @property
    def channel_name(self) -> str:
        return self._channel_name

    def is_available(self) -> bool:
        """파일 시스템 접근 가능 여부"""
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            return True
        except OSError:
            return False

    async def send(self, alert: Alert) -> bool:
        """알림을 파일에 기록"""
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)

            # 날짜별 파일
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            filepath = self._log_dir / f"alerts_{today}.jsonl"

            # 파일 크기 제한 체크
            if filepath.exists() and filepath.stat().st_size >= self._max_file_size:
                filepath = self._log_dir / f"alerts_{today}_{datetime.now(timezone.utc).strftime('%H%M%S')}.jsonl"

            record = {
                **alert.to_dict(),
                "fallback_channel": self._channel_name,
                "written_at": datetime.now(timezone.utc).isoformat(),
            }

            with open(filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

            logger.info(f"Alert written to file: {alert.id} -> {filepath}")
            return True

        except Exception as e:
            logger.error(f"FileNotifier write failed: {e}")
            return False


# ══════════════════════════════════════
# 콘솔 로그 백업 채널
# ══════════════════════════════════════
class ConsoleNotifier:
    """
    콘솔 로그 기반 백업 알림 채널

    Loguru를 통해 알림을 로그로 출력합니다.
    최후의 폴백으로 사용됩니다.
    """

    def __init__(self):
        self._channel_name = "console"

    @property
    def channel_name(self) -> str:
        return self._channel_name

    def is_available(self) -> bool:
        return True  # 항상 사용 가능

    async def send(self, alert: Alert) -> bool:
        """알림을 로그로 출력"""
        try:
            level_map = {
                AlertLevel.INFO: "info",
                AlertLevel.WARNING: "warning",
                AlertLevel.ERROR: "error",
                AlertLevel.CRITICAL: "critical",
            }
            log_level = level_map.get(alert.level, "info")

            log_message = (
                f"[FALLBACK ALERT] [{alert.level.value}] {alert.title}\n"
                f"  Type: {alert.alert_type.value}\n"
                f"  Message: {alert.message}\n"
                f"  ID: {alert.id}\n"
                f"  Created: {alert.created_at.isoformat()}"
            )

            getattr(logger, log_level)(log_message)
            return True

        except Exception as e:
            # 콘솔 출력마저 실패하면 stderr로
            import sys

            print(f"[CRITICAL] ConsoleNotifier failed: {e}", file=sys.stderr)
            return False


# ══════════════════════════════════════
# 알림 라우터 (1차 → 백업 자동 폴백)
# ══════════════════════════════════════
@dataclass
class DispatchResult:
    """발송 결과"""

    success: bool
    channel_used: str
    fallback_used: bool = False
    all_failed: bool = False
    channels_tried: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "channel_used": self.channel_used,
            "fallback_used": self.fallback_used,
            "all_failed": self.all_failed,
            "channels_tried": self.channels_tried,
        }


class NotificationRouter:
    """
    알림 라우터 — 1차 채널 실패 시 백업 채널로 자동 폴백

    채널 우선순위:
      1. Telegram (1차)
      2. File (2차 백업)
      3. Console (최후 폴백, 항상 성공)
    """

    def __init__(self, channels: Optional[list] = None):
        """
        Args:
            channels: 우선순위 순서의 채널 목록 (None이면 기본값 사용)
        """
        self._channels: list = channels or []
        self._health: dict[str, ChannelHealth] = {}

        # 채널별 건강 상태 초기화
        for ch in self._channels:
            self._health[ch.channel_name] = ChannelHealth(channel_name=ch.channel_name)

    def add_channel(self, channel) -> None:
        """채널 추가 (우선순위 최하위)"""
        self._channels.append(channel)
        self._health[channel.channel_name] = ChannelHealth(channel_name=channel.channel_name)

    async def dispatch(self, alert: Alert) -> DispatchResult:
        """
        알림 발송 — 1차 채널부터 순차적으로 시도, 성공 시 중단

        Args:
            alert: 발송할 Alert

        Returns:
            DispatchResult: 발송 결과 (어떤 채널을 사용했는지 포함)
        """
        channels_tried = []

        for channel in self._channels:
            ch_name = channel.channel_name
            channels_tried.append(ch_name)

            # 채널 사용 가능 여부 확인
            if not channel.is_available():
                logger.warning(f"Channel '{ch_name}' is not available, skipping")
                health = self._health.get(ch_name)
                if health:
                    health.record_failure()
                continue

            # 발송 시도
            try:
                success = await channel.send(alert)
            except Exception as e:
                logger.error(f"Channel '{ch_name}' raised exception: {e}")
                success = False

            health = self._health.get(ch_name)
            if health:
                if success:
                    health.record_success()
                else:
                    health.record_failure()

            if success:
                fallback_used = ch_name != self._channels[0].channel_name if self._channels else False
                if fallback_used:
                    logger.warning(
                        f"Alert {alert.id} sent via fallback channel '{ch_name}' " f"(primary channel failed)"
                    )

                return DispatchResult(
                    success=True,
                    channel_used=ch_name,
                    fallback_used=fallback_used,
                    channels_tried=channels_tried,
                )

        # 모든 채널 실패
        logger.critical(f"ALL notification channels failed for alert {alert.id}. " f"Tried: {channels_tried}")
        return DispatchResult(
            success=False,
            channel_used="none",
            all_failed=True,
            channels_tried=channels_tried,
        )

    def get_channel_health(self) -> list[dict]:
        """모든 채널 건강 상태 반환"""
        return [h.to_dict() for h in self._health.values()]

    def get_channel_status(self, channel_name: str) -> Optional[dict]:
        """특정 채널 상태 조회"""
        health = self._health.get(channel_name)
        return health.to_dict() if health else None
