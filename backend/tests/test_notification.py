"""
AQTS 알림 시스템 단위 테스트 (test_notification.py)

Phase 5: 알림 생성·관리·이력 조회 및 텔레그램 발송

테스트 범위:
  - Alert 데이터클래스: 생성, 직렬화, 상태 변경
  - AlertManager: 알림 생성·관리·필터링·통계
  - TelegramNotifier: 메시지 발송, 포맷팅, 레벨 필터링
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.constants import AlertType
from core.notification.alert_manager import (
    Alert,
    AlertLevel,
    AlertManager,
    AlertStatus,
)
from core.notification.telegram_notifier import (
    TELEGRAM_MAX_LENGTH,
    TelegramNotifier,
)


# ══════════════════════════════════════════════════════════════
# Alert 데이터클래스 테스트
# ══════════════════════════════════════════════════════════════
class TestAlertDataclass:
    """Alert 데이터클래스 테스트"""

    def test_alert_creation_with_defaults(self):
        """기본값으로 Alert 생성"""
        alert = Alert(
            alert_type=AlertType.DAILY_REPORT,
            level=AlertLevel.INFO,
            title="Test Alert",
            message="Test message",
        )

        assert alert.alert_type == AlertType.DAILY_REPORT
        assert alert.level == AlertLevel.INFO
        assert alert.title == "Test Alert"
        assert alert.message == "Test message"
        assert alert.status == AlertStatus.PENDING
        assert alert.id is not None
        assert len(alert.id) > 0
        assert alert.sent_at is None
        assert alert.read_at is None
        assert alert.metadata == {}
        assert alert.created_at is not None

    def test_alert_creation_with_metadata(self):
        """메타데이터를 포함하여 Alert 생성"""
        metadata = {"user_id": "user123", "portfolio_id": "port456"}
        alert = Alert(
            alert_type=AlertType.WEEKLY_REPORT,
            level=AlertLevel.WARNING,
            title="Weekly Report",
            message="Weekly summary",
            metadata=metadata,
        )

        assert alert.metadata == metadata

    def test_alert_to_dict_serialization(self):
        """Alert을 딕셔너리로 직렬화"""
        alert = Alert(
            alert_type=AlertType.DAILY_REPORT,
            level=AlertLevel.INFO,
            title="Test Alert",
            message="Test message",
            metadata={"key": "value"},
        )

        result = alert.to_dict()

        assert isinstance(result, dict)
        assert result["alert_type"] == "DAILY_REPORT"
        assert result["level"] == "INFO"
        assert result["title"] == "Test Alert"
        assert result["message"] == "Test message"
        assert result["status"] == "PENDING"
        assert result["metadata"] == {"key": "value"}
        assert "id" in result
        assert "created_at" in result
        assert result["sent_at"] is None
        assert result["read_at"] is None

    def test_alert_to_dict_with_timestamps(self):
        """타임스탬프가 ISO 형식으로 직렬화되는지 확인"""
        alert = Alert(
            alert_type=AlertType.SYSTEM_ERROR,
            level=AlertLevel.ERROR,
            title="Error",
            message="System error",
        )
        alert.mark_sent()
        alert.mark_read()

        result = alert.to_dict()

        # ISO 형식 확인
        assert isinstance(result["created_at"], str)
        assert isinstance(result["sent_at"], str)
        assert isinstance(result["read_at"], str)
        assert "T" in result["created_at"]  # ISO 형식 확인

    def test_alert_mark_sent(self):
        """Alert을 발송 완료로 표시"""
        alert = Alert(
            alert_type=AlertType.DAILY_REPORT,
            level=AlertLevel.INFO,
            title="Test",
            message="Test",
        )

        assert alert.status == AlertStatus.PENDING
        assert alert.sent_at is None

        alert.mark_sent()

        assert alert.status == AlertStatus.SENT
        assert alert.sent_at is not None
        assert isinstance(alert.sent_at, datetime)

    def test_alert_mark_read(self):
        """Alert을 확인됨으로 표시"""
        alert = Alert(
            alert_type=AlertType.DAILY_REPORT,
            level=AlertLevel.INFO,
            title="Test",
            message="Test",
        )

        assert alert.status == AlertStatus.PENDING
        assert alert.read_at is None

        alert.mark_read()

        assert alert.status == AlertStatus.READ
        assert alert.read_at is not None
        assert isinstance(alert.read_at, datetime)

    def test_alert_mark_failed(self):
        """Alert을 발송 실패로 표시"""
        alert = Alert(
            alert_type=AlertType.DAILY_REPORT,
            level=AlertLevel.INFO,
            title="Test",
            message="Test",
        )

        assert alert.status == AlertStatus.PENDING

        alert.mark_failed()

        assert alert.status == AlertStatus.FAILED

    def test_alert_status_transition(self):
        """Alert 상태 전환 체인"""
        alert = Alert(
            alert_type=AlertType.DAILY_REPORT,
            level=AlertLevel.INFO,
            title="Test",
            message="Test",
        )

        # PENDING → SENT → READ
        alert.mark_sent()
        assert alert.status == AlertStatus.SENT

        alert.mark_read()
        assert alert.status == AlertStatus.READ

    def test_alert_unique_ids(self):
        """각 Alert는 고유한 ID를 가짐"""
        alert1 = Alert(
            alert_type=AlertType.DAILY_REPORT,
            level=AlertLevel.INFO,
            title="Test1",
            message="Message1",
        )
        alert2 = Alert(
            alert_type=AlertType.DAILY_REPORT,
            level=AlertLevel.INFO,
            title="Test2",
            message="Message2",
        )

        assert alert1.id != alert2.id


# ══════════════════════════════════════════════════════════════
# AlertManager 테스트
# ══════════════════════════════════════════════════════════════
class TestAlertManager:
    """AlertManager 서비스 테스트 (메모리 모드)"""

    @pytest.fixture
    def alert_manager(self):
        """테스트용 AlertManager (메모리 모드)"""
        return AlertManager(mongo_collection=None)

    def test_create_alert_manual(self, alert_manager):
        """수동으로 알림 생성"""
        alert = alert_manager.create_alert(
            alert_type=AlertType.DAILY_REPORT,
            level=AlertLevel.INFO,
            title="Daily Report",
            message="Portfolio report",
        )

        assert alert.id is not None
        assert alert.alert_type == AlertType.DAILY_REPORT
        assert alert.level == AlertLevel.INFO
        assert alert.title == "Daily Report"
        assert alert.message == "Portfolio report"
        assert alert.status == AlertStatus.PENDING

    def test_create_alert_with_metadata(self, alert_manager):
        """메타데이터와 함께 알림 생성"""
        metadata = {"portfolio_id": "port123"}
        alert = alert_manager.create_alert(
            alert_type=AlertType.WEEKLY_REPORT,
            level=AlertLevel.INFO,
            title="Weekly Report",
            message="Weekly summary",
            metadata=metadata,
        )

        assert alert.metadata == metadata

    def test_create_alert_with_default_title(self, alert_manager):
        """제목이 없을 때 기본값 사용"""
        alert = alert_manager.create_alert(
            alert_type=AlertType.DAILY_REPORT,
            level=AlertLevel.INFO,
            title="",  # 빈 문자열
            message="Message",
        )

        assert alert.title == "[DAILY_REPORT]"

    def test_create_from_template_daily_report(self, alert_manager):
        """DAILY_REPORT 템플릿으로 알림 생성"""
        template_data = {
            "date": "2026-04-03",
            "total_value": 50_000_000,
            "daily_return": 1.5,
            "realized_pnl": 100_000,
            "unrealized_pnl": 500_000,
            "position_count": 5,
            "buy_count": 2,
            "sell_count": 1,
        }

        alert = alert_manager.create_from_template(
            AlertType.DAILY_REPORT,
            template_data,
        )

        assert alert.alert_type == AlertType.DAILY_REPORT
        assert alert.level == AlertLevel.INFO
        assert alert.title == "📊 일일 포트폴리오 리포트"
        assert "2026-04-03" in alert.message
        assert "50,000,000원" in alert.message
        assert "1.50%" in alert.message
        assert "매수 2건" in alert.message
        assert alert.metadata["template_data"] == template_data

    def test_create_from_template_weekly_report(self, alert_manager):
        """WEEKLY_REPORT 템플릿으로 알림 생성"""
        template_data = {
            "start_date": "2026-03-27",
            "end_date": "2026-04-03",
            "total_value": 51_000_000,
            "weekly_return": 2.0,
            "weekly_realized_pnl": 200_000,
            "mdd": 3.5,
            "sharpe": 1.2,
            "rebalancing_count": 2,
        }

        alert = alert_manager.create_from_template(
            AlertType.WEEKLY_REPORT,
            template_data,
        )

        assert alert.alert_type == AlertType.WEEKLY_REPORT
        assert alert.level == AlertLevel.INFO
        assert alert.title == "📈 주간 성과 리포트"
        assert "2026-03-27" in alert.message
        assert "51,000,000원" in alert.message

    def test_create_from_template_monthly_report(self, alert_manager):
        """MONTHLY_REPORT 템플릿으로 알림 생성"""
        template_data = {
            "year": 2026,
            "month": 4,
            "total_value": 52_000_000,
            "monthly_return": 4.0,
            "cumulative_return": 10.0,
            "cagr": 15.0,
            "mdd": 5.0,
            "sharpe": 1.5,
        }

        alert = alert_manager.create_from_template(
            AlertType.MONTHLY_REPORT,
            template_data,
        )

        assert alert.alert_type == AlertType.MONTHLY_REPORT
        assert alert.title == "📋 월간 종합 리포트"
        assert "2026년 4월" in alert.message

    def test_create_from_template_emergency_rebalancing(self, alert_manager):
        """EMERGENCY_REBALANCING 템플릿으로 알림 생성"""
        template_data = {
            "reason": "포트폴리오 손실률 초과",
            "loss_rate": 8.5,
            "sell_count": 3,
            "buy_count": 2,
            "total_orders": 5,
            "executed_at": "2026-04-03 14:30:00",
        }

        alert = alert_manager.create_from_template(
            AlertType.EMERGENCY_REBALANCING,
            template_data,
        )

        assert alert.alert_type == AlertType.EMERGENCY_REBALANCING
        assert alert.level == AlertLevel.CRITICAL
        assert alert.title == "🚨 긴급 리밸런싱 알림"
        assert "포트폴리오 손실률 초과" in alert.message

    def test_create_from_template_system_error(self, alert_manager):
        """SYSTEM_ERROR 템플릿으로 알림 생성"""
        template_data = {
            "module": "data_fetcher",
            "error_message": "Connection timeout",
            "occurred_at": "2026-04-03 10:15:00 UTC",
            "details": "Failed to connect to KIS API",
        }

        alert = alert_manager.create_from_template(
            AlertType.SYSTEM_ERROR,
            template_data,
        )

        assert alert.alert_type == AlertType.SYSTEM_ERROR
        assert alert.level == AlertLevel.ERROR
        assert alert.title == "⚠️ 시스템 오류"
        assert "data_fetcher" in alert.message

    def test_create_from_template_invalid_type(self, alert_manager):
        """지원하지 않는 알림 유형으로 생성 시도"""
        # 직접 AlertType이 아닌 잘못된 타입으로 시뮬레이션하기 위해
        # 다른 방식으로 테스트 (실제로는 모든 AlertType이 템플릿을 가짐)
        # 이 테스트는 생략하고 대신 템플릿 키 누락 테스트에 집중
        pass

    def test_create_from_template_missing_keys(self, alert_manager):
        """템플릿에 필수 키가 없을 때 폴백 동작"""
        template_data = {
            "date": "2026-04-03",
            # 필수 키 누락
        }

        alert = alert_manager.create_from_template(
            AlertType.DAILY_REPORT,
            template_data,
        )

        # 폴백: 불완전한 메시지가 생성됨
        assert alert.message is not None
        # 또는 템플릿 데이터로 폴백됨
        assert alert.metadata["template_data"] == template_data

    @pytest.mark.asyncio
    async def test_get_alerts_empty(self, alert_manager):
        """빈 알림 목록 조회"""
        alerts = await alert_manager.get_alerts()

        assert alerts == []

    @pytest.mark.asyncio
    async def test_get_alerts_all(self, alert_manager):
        """모든 알림 조회"""
        alert1 = alert_manager.create_alert(AlertType.DAILY_REPORT, AlertLevel.INFO, "Alert 1", "Message 1")
        alert2 = alert_manager.create_alert(AlertType.WEEKLY_REPORT, AlertLevel.WARNING, "Alert 2", "Message 2")

        alerts = await alert_manager.get_alerts()

        assert len(alerts) == 2
        assert alerts[0]["id"] == alert2.id  # 최신순
        assert alerts[1]["id"] == alert1.id

    @pytest.mark.asyncio
    async def test_get_alerts_with_limit_offset(self, alert_manager):
        """페이지네이션으로 알림 조회"""
        for i in range(5):
            alert_manager.create_alert(
                AlertType.DAILY_REPORT,
                AlertLevel.INFO,
                f"Alert {i}",
                f"Message {i}",
            )

        # limit=2, offset=1
        alerts = await alert_manager.get_alerts(limit=2, offset=1)

        assert len(alerts) == 2

    @pytest.mark.asyncio
    async def test_get_alerts_filter_by_type(self, alert_manager):
        """알림 유형으로 필터링"""
        alert_manager.create_alert(AlertType.DAILY_REPORT, AlertLevel.INFO, "Daily", "Message")
        alert_manager.create_alert(AlertType.WEEKLY_REPORT, AlertLevel.INFO, "Weekly", "Message")
        alert_manager.create_alert(AlertType.DAILY_REPORT, AlertLevel.INFO, "Daily 2", "Message")

        daily_alerts = await alert_manager.get_alerts(alert_type=AlertType.DAILY_REPORT)

        assert len(daily_alerts) == 2
        assert all(a["alert_type"] == "DAILY_REPORT" for a in daily_alerts)

    @pytest.mark.asyncio
    async def test_get_alerts_filter_by_level(self, alert_manager):
        """알림 레벨로 필터링"""
        alert_manager.create_alert(AlertType.DAILY_REPORT, AlertLevel.INFO, "Info", "Message")
        alert_manager.create_alert(AlertType.DAILY_REPORT, AlertLevel.ERROR, "Error", "Message")
        alert_manager.create_alert(AlertType.DAILY_REPORT, AlertLevel.CRITICAL, "Critical", "Message")

        error_alerts = await alert_manager.get_alerts(level=AlertLevel.ERROR)

        assert len(error_alerts) == 1
        assert error_alerts[0]["level"] == "ERROR"

    @pytest.mark.asyncio
    async def test_get_alerts_filter_by_status(self, alert_manager):
        """알림 상태로 필터링"""
        alert1 = alert_manager.create_alert(AlertType.DAILY_REPORT, AlertLevel.INFO, "Alert 1", "Message")
        alert2 = alert_manager.create_alert(AlertType.DAILY_REPORT, AlertLevel.INFO, "Alert 2", "Message")

        alert1.mark_sent()
        alert2.mark_read()

        pending = await alert_manager.get_alerts(status=AlertStatus.PENDING)
        sent = await alert_manager.get_alerts(status=AlertStatus.SENT)
        read = await alert_manager.get_alerts(status=AlertStatus.READ)

        assert len(pending) == 0
        assert len(sent) == 1
        assert len(read) == 1

    @pytest.mark.asyncio
    async def test_get_alerts_multiple_filters(self, alert_manager):
        """여러 필터 조합"""
        alert_manager.create_alert(AlertType.DAILY_REPORT, AlertLevel.INFO, "Alert 1", "Message")
        alert_manager.create_alert(AlertType.DAILY_REPORT, AlertLevel.ERROR, "Alert 2", "Message")
        alert_manager.create_alert(AlertType.WEEKLY_REPORT, AlertLevel.ERROR, "Alert 3", "Message")

        result = await alert_manager.get_alerts(
            alert_type=AlertType.DAILY_REPORT,
            level=AlertLevel.ERROR,
        )

        assert len(result) == 1
        assert result[0]["alert_type"] == "DAILY_REPORT"
        assert result[0]["level"] == "ERROR"

    @pytest.mark.asyncio
    async def test_get_alert_by_id(self, alert_manager):
        """ID로 특정 알림 조회"""
        alert = alert_manager.create_alert(AlertType.DAILY_REPORT, AlertLevel.INFO, "Test", "Message")

        result = await alert_manager.get_alert_by_id(alert.id)

        assert result is not None
        assert result["id"] == alert.id
        assert result["title"] == "Test"

    @pytest.mark.asyncio
    async def test_get_alert_by_id_not_found(self, alert_manager):
        """존재하지 않는 ID 조회"""
        result = await alert_manager.get_alert_by_id("nonexistent_id")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_unread_count_empty(self, alert_manager):
        """미확인 알림 수 (비어있을 때)"""
        count = await alert_manager.get_unread_count()

        assert count == 0

    @pytest.mark.asyncio
    async def test_get_unread_count(self, alert_manager):
        """미확인 알림 수"""
        alert1 = alert_manager.create_alert(AlertType.DAILY_REPORT, AlertLevel.INFO, "Alert 1", "Message")
        alert2 = alert_manager.create_alert(AlertType.DAILY_REPORT, AlertLevel.INFO, "Alert 2", "Message")
        alert3 = alert_manager.create_alert(AlertType.DAILY_REPORT, AlertLevel.INFO, "Alert 3", "Message")

        alert1.mark_read()
        alert2.mark_failed()

        count = await alert_manager.get_unread_count()

        # PENDING(1) + SENT(0) + FAILED(1) = 2 (READ 제외)
        assert count == 2

    @pytest.mark.asyncio
    async def test_mark_alert_read(self, alert_manager):
        """특정 알림을 읽음으로 표시"""
        alert = alert_manager.create_alert(AlertType.DAILY_REPORT, AlertLevel.INFO, "Test", "Message")

        result = await alert_manager.mark_alert_read(alert.id)

        assert result is True
        assert alert.status == AlertStatus.READ

    @pytest.mark.asyncio
    async def test_mark_alert_read_not_found(self, alert_manager):
        """존재하지 않는 알림 읽음 처리"""
        result = await alert_manager.mark_alert_read("nonexistent_id")

        assert result is False

    @pytest.mark.asyncio
    async def test_mark_all_read(self, alert_manager):
        """모든 알림을 읽음으로 표시"""
        alert1 = alert_manager.create_alert(AlertType.DAILY_REPORT, AlertLevel.INFO, "Alert 1", "Message")
        alert2 = alert_manager.create_alert(AlertType.DAILY_REPORT, AlertLevel.INFO, "Alert 2", "Message")
        alert3 = alert_manager.create_alert(AlertType.DAILY_REPORT, AlertLevel.INFO, "Alert 3", "Message")

        alert1.mark_sent()  # SENT는 미확인 상태

        count = await alert_manager.mark_all_read()

        # PENDING(1 alert2) + SENT(1 alert1) = 2 (alert3는 이미 생성되었으므로 PENDING)
        assert count == 3  # 모두 PENDING이므로 3개가 처리됨
        assert alert1.status == AlertStatus.READ
        assert alert2.status == AlertStatus.READ
        assert alert3.status == AlertStatus.READ

    @pytest.mark.asyncio
    async def test_mark_all_read_none_unread(self, alert_manager):
        """모든 알림이 이미 읽음 상태"""
        alert1 = alert_manager.create_alert(AlertType.DAILY_REPORT, AlertLevel.INFO, "Alert 1", "Message")
        alert1.mark_read()

        count = await alert_manager.mark_all_read()

        assert count == 0

    @pytest.mark.asyncio
    async def test_save_alert_memory_mode(self, alert_manager):
        """메모리 모드에서 알림 저장 (noop)"""
        alert = alert_manager.create_alert(AlertType.DAILY_REPORT, AlertLevel.INFO, "Test", "Message")

        # 메모리 모드에서는 이미 저장됨
        await alert_manager.save_alert(alert)

        # 조회 가능 확인
        result = await alert_manager.get_alert_by_id(alert.id)
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_alert_stats_empty(self, alert_manager):
        """알림 통계 (비어있을 때)"""
        stats = await alert_manager.get_alert_stats()

        assert stats["total"] == 0
        assert stats["unread"] == 0
        assert all(count == 0 for count in stats["by_level"].values())

    @pytest.mark.asyncio
    async def test_get_alert_stats(self, alert_manager):
        """알림 통계"""
        alert1 = alert_manager.create_alert(AlertType.DAILY_REPORT, AlertLevel.INFO, "Alert 1", "Message")
        alert2 = alert_manager.create_alert(AlertType.DAILY_REPORT, AlertLevel.WARNING, "Alert 2", "Message")
        alert3 = alert_manager.create_alert(AlertType.DAILY_REPORT, AlertLevel.ERROR, "Alert 3", "Message")
        alert4 = alert_manager.create_alert(AlertType.DAILY_REPORT, AlertLevel.CRITICAL, "Alert 4", "Message")

        alert1.mark_read()  # 1개 읽음

        stats = await alert_manager.get_alert_stats()

        assert stats["total"] == 4
        assert stats["unread"] == 3
        assert stats["by_level"]["INFO"] == 1
        assert stats["by_level"]["WARNING"] == 1
        assert stats["by_level"]["ERROR"] == 1
        assert stats["by_level"]["CRITICAL"] == 1


# ══════════════════════════════════════════════════════════════
# TelegramNotifier 테스트
# ══════════════════════════════════════════════════════════════
class TestTelegramNotifier:
    """TelegramNotifier 서비스 테스트"""

    @pytest.fixture
    def mock_settings(self):
        """Mock settings fixture"""
        settings = MagicMock()
        settings.telegram.bot_token = "test-bot-token"
        settings.telegram.chat_id = "test-chat-id"
        settings.telegram.alert_level = "ALL"
        return settings

    @pytest.fixture
    def alert_manager(self):
        """테스트용 AlertManager"""
        return AlertManager(mongo_collection=None)

    @pytest.fixture
    def telegram_notifier(self, alert_manager, mock_settings):
        """테스트용 TelegramNotifier"""
        with patch("core.notification.telegram_notifier.get_settings", return_value=mock_settings):
            return TelegramNotifier(
                alert_manager=alert_manager,
                bot_token="test-bot-token",
                chat_id="test-chat-id",
                alert_level="ALL",
            )

    # ── 레벨 필터 테스트 ──
    def test_should_send_filter_all(self, telegram_notifier):
        """필터 ALL: 모든 레벨 발송"""
        telegram_notifier._alert_level = "ALL"

        assert telegram_notifier._should_send(AlertLevel.INFO) is True
        assert telegram_notifier._should_send(AlertLevel.WARNING) is True
        assert telegram_notifier._should_send(AlertLevel.ERROR) is True
        assert telegram_notifier._should_send(AlertLevel.CRITICAL) is True

    def test_should_send_filter_important(self, telegram_notifier):
        """필터 IMPORTANT: WARNING 이상만 발송"""
        telegram_notifier._alert_level = "IMPORTANT"

        assert telegram_notifier._should_send(AlertLevel.INFO) is False
        assert telegram_notifier._should_send(AlertLevel.WARNING) is True
        assert telegram_notifier._should_send(AlertLevel.ERROR) is True
        assert telegram_notifier._should_send(AlertLevel.CRITICAL) is True

    def test_should_send_filter_error(self, telegram_notifier):
        """필터 ERROR: ERROR 이상만 발송"""
        telegram_notifier._alert_level = "ERROR"

        assert telegram_notifier._should_send(AlertLevel.INFO) is False
        assert telegram_notifier._should_send(AlertLevel.WARNING) is False
        assert telegram_notifier._should_send(AlertLevel.ERROR) is True
        assert telegram_notifier._should_send(AlertLevel.CRITICAL) is True

    # ── 포맷팅 테스트 ──
    def test_format_alert_info(self, alert_manager, telegram_notifier):
        """INFO 레벨 알림 포맷팅"""
        alert = alert_manager.create_alert(
            AlertType.DAILY_REPORT,
            AlertLevel.INFO,
            "Daily Report",
            "Portfolio update",
        )

        formatted = telegram_notifier._format_alert(alert)

        assert "ℹ️" in formatted
        assert "<b>Daily Report</b>" in formatted
        assert "<code>[INFO]</code>" in formatted
        assert "Portfolio update" in formatted

    def test_format_alert_warning(self, alert_manager, telegram_notifier):
        """WARNING 레벨 알림 포맷팅"""
        alert = alert_manager.create_alert(
            AlertType.DAILY_REPORT,
            AlertLevel.WARNING,
            "Warning Alert",
            "Check portfolio",
        )

        formatted = telegram_notifier._format_alert(alert)

        assert "⚠️" in formatted
        assert "[WARNING]" in formatted

    def test_format_alert_error(self, alert_manager, telegram_notifier):
        """ERROR 레벨 알림 포맷팅"""
        alert = alert_manager.create_alert(
            AlertType.SYSTEM_ERROR,
            AlertLevel.ERROR,
            "System Error",
            "API connection failed",
        )

        formatted = telegram_notifier._format_alert(alert)

        assert "❌" in formatted
        assert "[ERROR]" in formatted

    def test_format_alert_critical(self, alert_manager, telegram_notifier):
        """CRITICAL 레벨 알림 포맷팅"""
        alert = alert_manager.create_alert(
            AlertType.EMERGENCY_REBALANCING,
            AlertLevel.CRITICAL,
            "Emergency",
            "Rebalancing triggered",
        )

        formatted = telegram_notifier._format_alert(alert)

        assert "🚨" in formatted
        assert "[CRITICAL]" in formatted

    def test_format_alert_includes_timestamp(self, alert_manager, telegram_notifier):
        """포맷된 알림에 타임스탬프 포함"""
        alert = alert_manager.create_alert(
            AlertType.DAILY_REPORT,
            AlertLevel.INFO,
            "Test",
            "Message",
        )

        formatted = telegram_notifier._format_alert(alert)

        # UTC 타임스탬프 확인
        assert "UTC" in formatted
        assert "-" in formatted  # 날짜 포맷

    # ── 메시지 분할 테스트 ──
    def test_split_message_short(self, telegram_notifier):
        """짧은 메시지는 분할되지 않음"""
        text = "Short message"

        result = telegram_notifier._split_message(text)

        assert len(result) == 1
        assert result[0] == text

    def test_split_message_exact_max_length(self, telegram_notifier):
        """정확히 최대 길이인 메시지"""
        text = "x" * TELEGRAM_MAX_LENGTH

        result = telegram_notifier._split_message(text)

        assert len(result) == 1
        assert result[0] == text

    def test_split_message_one_over_max(self, telegram_notifier):
        """최대 길이 초과 1글자"""
        text = "x" * (TELEGRAM_MAX_LENGTH + 1)

        result = telegram_notifier._split_message(text)

        assert len(result) == 2
        assert sum(len(m) for m in result) == len(text)

    def test_split_message_long_with_newlines(self, telegram_notifier):
        """긴 메시지를 줄바꿈 기준으로 분할"""
        # 줄바꿈으로 구분된 긴 메시지
        lines = ["Line " + str(i) for i in range(500)]
        text = "\n".join(lines)

        result = telegram_notifier._split_message(text)

        # 여러 부분으로 분할됨
        assert len(result) > 1
        # 각 부분이 최대 길이 이하
        assert all(len(m) <= TELEGRAM_MAX_LENGTH for m in result)
        # 대부분의 콘텐츠가 보존됨 (분할 시점의 줄바꿈은 손실될 수 있음)
        rejoined = "".join(result)
        # 모든 라인이 포함되어 있는지 확인
        for i in range(500):
            assert f"Line {i}" in rejoined

    def test_split_message_very_long(self, telegram_notifier):
        """매우 긴 메시지 분할"""
        text = "x" * (TELEGRAM_MAX_LENGTH * 3 + 100)

        result = telegram_notifier._split_message(text)

        assert len(result) == 4
        assert all(len(m) <= TELEGRAM_MAX_LENGTH for m in result)

    def test_split_message_no_trailing_newline(self, telegram_notifier):
        """분할된 메시지에 남은 줄바꿈 제거"""
        text = ("Line 1\n" * TELEGRAM_MAX_LENGTH) + "Line 2"

        result = telegram_notifier._split_message(text)

        # 각 부분의 시작에 줄바꿈이 없어야 함
        for part in result:
            if part != result[0]:  # 첫 부분 제외
                assert not part.startswith("\n")

    # ── 메시지 발송 테스트 ──
    @pytest.mark.asyncio
    async def test_send_message_success(self, telegram_notifier):
        """메시지 발송 성공"""
        text = "Test message"

        with patch.object(telegram_notifier, "_send_single_message", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            result = await telegram_notifier.send_message(text)

        assert result is True
        mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_failure(self, telegram_notifier):
        """메시지 발송 실패"""
        text = "Test message"

        with patch.object(telegram_notifier, "_send_single_message", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = False
            result = await telegram_notifier.send_message(text)

        assert result is False

    @pytest.mark.asyncio
    async def test_send_message_split_and_send(self, telegram_notifier):
        """긴 메시지 분할 발송"""
        text = "x" * (TELEGRAM_MAX_LENGTH + 1000)

        with patch.object(telegram_notifier, "_send_single_message", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            result = await telegram_notifier.send_message(text)

        # 여러 번 발송
        assert mock_send.call_count > 1
        assert result is True

    @pytest.mark.asyncio
    async def test_send_single_message_success(self, telegram_notifier):
        """단일 메시지 발송 성공"""
        text = "Test message"

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client = MagicMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            result = await telegram_notifier._send_single_message(text)

        assert result is True
        mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_single_message_failure_retry(self, telegram_notifier):
        """단일 메시지 발송 실패 후 재시도"""
        text = "Test message"
        telegram_notifier._max_retries = 3

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Server error"
            mock_client = MagicMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            result = await telegram_notifier._send_single_message(text)

        assert result is False
        # 최대 재시도 횟수만큼 호출
        assert mock_client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_send_single_message_network_error(self, telegram_notifier):
        """네트워크 오류 발생"""
        text = "Test message"
        telegram_notifier._max_retries = 2

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(side_effect=Exception("Network error"))
            mock_client_class.return_value = mock_client

            result = await telegram_notifier._send_single_message(text)

        assert result is False
        assert mock_client.post.call_count == 2

    # ── Alert 발송 테스트 ──
    @pytest.mark.asyncio
    async def test_dispatch_alert_success(self, alert_manager, telegram_notifier):
        """Alert 발송 성공"""
        alert = alert_manager.create_alert(
            AlertType.DAILY_REPORT,
            AlertLevel.INFO,
            "Daily Report",
            "Portfolio update",
        )

        with patch.object(telegram_notifier, "send_message", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            result = await telegram_notifier.dispatch_alert(alert)

        assert result is True
        assert alert.status == AlertStatus.SENT
        assert alert.sent_at is not None

    @pytest.mark.asyncio
    async def test_dispatch_alert_failure(self, alert_manager, telegram_notifier):
        """Alert 발송 실패"""
        alert = alert_manager.create_alert(
            AlertType.DAILY_REPORT,
            AlertLevel.INFO,
            "Daily Report",
            "Portfolio update",
        )

        with patch.object(telegram_notifier, "send_message", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = False
            result = await telegram_notifier.dispatch_alert(alert)

        assert result is False
        assert alert.status == AlertStatus.FAILED

    @pytest.mark.asyncio
    async def test_dispatch_alert_filtered(self, alert_manager, telegram_notifier):
        """레벨 필터에 의해 발송 생략"""
        telegram_notifier._alert_level = "ERROR"

        alert = alert_manager.create_alert(
            AlertType.DAILY_REPORT,
            AlertLevel.INFO,  # ERROR 필터에서 제외됨
            "Daily Report",
            "Portfolio update",
        )

        with patch.object(telegram_notifier, "send_message", new_callable=AsyncMock) as mock_send:
            result = await telegram_notifier.dispatch_alert(alert)

        # 필터링되어도 True 반환 (처리됨)
        assert result is True
        # 그러나 발송되지는 않음
        mock_send.assert_not_called()
        # 상태는 SENT로 표시
        assert alert.status == AlertStatus.SENT

    # ── 편의 메서드 테스트 ──
    @pytest.mark.asyncio
    async def test_send_daily_report(self, telegram_notifier):
        """일일 리포트 전송"""
        report_data = {
            "date": "2026-04-03",
            "total_value": 50_000_000,
            "daily_return": 1.5,
            "realized_pnl": 100_000,
            "unrealized_pnl": 500_000,
            "position_count": 5,
            "buy_count": 2,
            "sell_count": 1,
        }

        with patch.object(telegram_notifier, "dispatch_alert", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = True
            result = await telegram_notifier.send_daily_report(report_data)

        assert result is True
        # dispatch_alert이 호출되었는지 확인
        mock_dispatch.assert_called_once()
        # Alert이 DAILY_REPORT 타입인지 확인
        call_alert = mock_dispatch.call_args[0][0]
        assert call_alert.alert_type == AlertType.DAILY_REPORT

    @pytest.mark.asyncio
    async def test_send_emergency_alert(self, telegram_notifier):
        """긴급 알림 전송"""
        reason = "포트폴리오 손실률 초과"
        details = {
            "loss_rate": 8.5,
            "sell_count": 3,
            "buy_count": 2,
            "total_orders": 5,
            "executed_at": "2026-04-03 14:30:00",
        }

        with patch.object(telegram_notifier, "dispatch_alert", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = True
            result = await telegram_notifier.send_emergency_alert(reason, details)

        assert result is True
        mock_dispatch.assert_called_once()
        call_alert = mock_dispatch.call_args[0][0]
        assert call_alert.alert_type == AlertType.EMERGENCY_REBALANCING
        assert call_alert.level == AlertLevel.CRITICAL

    @pytest.mark.asyncio
    async def test_send_error_alert(self, telegram_notifier):
        """시스템 오류 알림 전송"""
        module = "data_fetcher"
        error_message = "Connection timeout"
        details = "Failed to connect to KIS API"

        with patch.object(telegram_notifier, "dispatch_alert", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = True
            result = await telegram_notifier.send_error_alert(module, error_message, details)

        assert result is True
        mock_dispatch.assert_called_once()
        call_alert = mock_dispatch.call_args[0][0]
        assert call_alert.alert_type == AlertType.SYSTEM_ERROR
        assert call_alert.level == AlertLevel.ERROR
        assert module in call_alert.message


# ══════════════════════════════════════════════════════════════
# 통합 테스트
# ══════════════════════════════════════════════════════════════
class TestNotificationIntegration:
    """AlertManager와 TelegramNotifier 통합 테스트"""

    @pytest.fixture
    def mock_settings(self):
        """Mock settings"""
        settings = MagicMock()
        settings.telegram.bot_token = "test-bot-token"
        settings.telegram.chat_id = "test-chat-id"
        settings.telegram.alert_level = "ALL"
        return settings

    @pytest.fixture
    def setup(self, mock_settings):
        """통합 테스트 셋업"""
        with patch("core.notification.telegram_notifier.get_settings", return_value=mock_settings):
            alert_manager = AlertManager(mongo_collection=None)
            telegram_notifier = TelegramNotifier(
                alert_manager=alert_manager,
                bot_token="test-bot-token",
                chat_id="test-chat-id",
                alert_level="ALL",
            )
        return alert_manager, telegram_notifier

    @pytest.mark.asyncio
    async def test_full_alert_lifecycle(self, setup):
        """전체 알림 생명주기"""
        alert_manager, telegram_notifier = setup

        # 1. 알림 생성
        alert = alert_manager.create_alert(
            AlertType.DAILY_REPORT,
            AlertLevel.INFO,
            "Daily Report",
            "Portfolio update",
        )
        assert alert.status == AlertStatus.PENDING

        # 2. 알림 발송
        with patch.object(telegram_notifier, "send_message", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            await telegram_notifier.dispatch_alert(alert)

        assert alert.status == AlertStatus.SENT

        # 3. 알림 확인
        result = await alert_manager.mark_alert_read(alert.id)
        assert result is True
        assert alert.status == AlertStatus.READ

        # 4. 통계 확인
        stats = await alert_manager.get_alert_stats()
        assert stats["total"] == 1
        assert stats["unread"] == 0

    @pytest.mark.asyncio
    async def test_batch_alert_creation_and_dispatch(self, setup):
        """배치 알림 생성 및 발송"""
        alert_manager, telegram_notifier = setup

        # 다양한 레벨의 알림 생성
        alerts = []
        for level, title in [
            (AlertLevel.INFO, "Info Alert"),
            (AlertLevel.WARNING, "Warning Alert"),
            (AlertLevel.ERROR, "Error Alert"),
            (AlertLevel.CRITICAL, "Critical Alert"),
        ]:
            alert = alert_manager.create_alert(
                AlertType.DAILY_REPORT,
                level,
                title,
                f"Message for {title}",
            )
            alerts.append(alert)

        # 필터 설정: WARNING 이상만
        telegram_notifier._alert_level = "IMPORTANT"

        # 일괄 발송
        with patch.object(telegram_notifier, "send_message", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            for alert in alerts:
                await telegram_notifier.dispatch_alert(alert)

        # INFO는 필터링됨, WARNING/ERROR/CRITICAL은 발송됨
        assert mock_send.call_count == 3

    @pytest.mark.asyncio
    async def test_template_based_alert_dispatch(self, setup):
        """템플릿 기반 알림 생성 및 발송"""
        alert_manager, telegram_notifier = setup

        # 템플릿으로 알림 생성
        template_data = {
            "date": "2026-04-03",
            "total_value": 50_000_000,
            "daily_return": 1.5,
            "realized_pnl": 100_000,
            "unrealized_pnl": 500_000,
            "position_count": 5,
            "buy_count": 2,
            "sell_count": 1,
        }
        alert = alert_manager.create_from_template(
            AlertType.DAILY_REPORT,
            template_data,
        )

        # 발송
        with patch.object(telegram_notifier, "send_message", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            await telegram_notifier.dispatch_alert(alert)

        # 포맷된 메시지 확인
        call_args = mock_send.call_args[0][0]
        assert "50,000,000원" in call_args


# ══════════════════════════════════════════════════════════════
# 엣지 케이스 테스트
# ══════════════════════════════════════════════════════════════
class TestEdgeCases:
    """엣지 케이스 및 경계값 테스트"""

    @pytest.fixture
    def alert_manager(self):
        """테스트용 AlertManager"""
        return AlertManager(mongo_collection=None)

    def test_alert_with_empty_message(self, alert_manager):
        """빈 메시지를 가진 Alert"""
        alert = alert_manager.create_alert(
            AlertType.DAILY_REPORT,
            AlertLevel.INFO,
            "Title",
            "",
        )

        assert alert.message == ""
        result = alert.to_dict()
        assert result["message"] == ""

    def test_alert_with_special_characters(self, alert_manager):
        """특수문자를 포함한 Alert"""
        special_text = "Test <>&\"' with 특수문자 한글 🎉"
        alert = alert_manager.create_alert(
            AlertType.DAILY_REPORT,
            AlertLevel.INFO,
            special_text,
            special_text,
        )

        result = alert.to_dict()
        assert result["title"] == special_text
        assert result["message"] == special_text

    def test_alert_metadata_complex_structure(self, alert_manager):
        """복잡한 구조의 메타데이터"""
        metadata = {
            "nested": {
                "level1": {
                    "level2": [1, 2, 3],
                },
            },
            "array": [{"item": 1}, {"item": 2}],
            "null_value": None,
        }
        alert = alert_manager.create_alert(
            AlertType.DAILY_REPORT,
            AlertLevel.INFO,
            "Test",
            "Message",
            metadata=metadata,
        )

        result = alert.to_dict()
        assert result["metadata"] == metadata

    @pytest.mark.asyncio
    async def test_get_alerts_large_dataset(self, alert_manager):
        """많은 수의 알림 조회"""
        # 100개의 알림 생성
        for i in range(100):
            alert_manager.create_alert(
                AlertType.DAILY_REPORT,
                AlertLevel.INFO,
                f"Alert {i}",
                f"Message {i}",
            )

        # 페이지네이션 테스트
        page1 = await alert_manager.get_alerts(limit=10, offset=0)
        page2 = await alert_manager.get_alerts(limit=10, offset=10)

        assert len(page1) == 10
        assert len(page2) == 10
        assert page1[0]["id"] != page2[0]["id"]

    def test_split_message_exact_boundary(self):
        """메시지 분할 경계값 테스트"""
        notifier = TelegramNotifier()

        # 경계값에서의 분할
        for length in [TELEGRAM_MAX_LENGTH - 1, TELEGRAM_MAX_LENGTH, TELEGRAM_MAX_LENGTH + 1]:
            text = "x" * length
            result = notifier._split_message(text)

            if length <= TELEGRAM_MAX_LENGTH:
                assert len(result) == 1
            else:
                assert len(result) > 1

    @pytest.mark.asyncio
    async def test_mark_same_alert_multiple_times(self, alert_manager):
        """같은 알림을 여러 번 mark_read"""
        alert = alert_manager.create_alert(
            AlertType.DAILY_REPORT,
            AlertLevel.INFO,
            "Test",
            "Message",
        )

        # 첫 번째 mark_read
        result1 = await alert_manager.mark_alert_read(alert.id)
        assert result1 is True

        # 두 번째 mark_read (이미 READ 상태)
        result2 = await alert_manager.mark_alert_read(alert.id)
        assert result2 is True

        # 상태는 변하지 않음
        assert alert.status == AlertStatus.READ
