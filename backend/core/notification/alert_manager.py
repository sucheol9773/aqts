"""
알림 관리 서비스 (Alert Manager)

Phase 5: 알림 생성·관리·이력 조회

알림 유형:
  - DAILY_REPORT: 일일 포트폴리오 리포트
  - WEEKLY_REPORT: 주간 성과 리포트
  - MONTHLY_REPORT: 월간 종합 리포트
  - EMERGENCY_REBALANCING: 긴급 리밸런싱 알림
  - SYSTEM_ERROR: 시스템 오류 알림
  - ORDER_EXECUTED: 주문 체결 알림
  - SIGNAL_GENERATED: 투자 시그널 발생 알림
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from config.constants import AlertType
from config.logging import logger


# ══════════════════════════════════════
# 추가 알림 유형 (Phase 5 확장)
# ══════════════════════════════════════
class AlertLevel(str, Enum):
    """알림 심각도"""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class AlertStatus(str, Enum):
    """알림 상태"""

    PENDING = "PENDING"  # 발송 대기
    SENT = "SENT"  # 발송 완료
    FAILED = "FAILED"  # 발송 실패
    READ = "READ"  # 확인됨


# ══════════════════════════════════════
# 알림 데이터 클래스
# ══════════════════════════════════════
@dataclass
class Alert:
    """개별 알림 엔티티"""

    alert_type: AlertType
    level: AlertLevel
    title: str
    message: str
    id: str = field(default_factory=lambda: str(uuid4()))
    status: AlertStatus = AlertStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    sent_at: Optional[datetime] = None
    read_at: Optional[datetime] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """직렬화 가능한 딕셔너리 반환"""
        return {
            "id": self.id,
            "alert_type": self.alert_type.value,
            "level": self.level.value,
            "title": self.title,
            "message": self.message,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "read_at": self.read_at.isoformat() if self.read_at else None,
            "metadata": self.metadata,
        }

    def mark_sent(self) -> None:
        """발송 완료 처리"""
        self.status = AlertStatus.SENT
        self.sent_at = datetime.now(timezone.utc)

    def mark_read(self) -> None:
        """확인 처리"""
        self.status = AlertStatus.READ
        self.read_at = datetime.now(timezone.utc)

    def mark_failed(self) -> None:
        """발송 실패 처리"""
        self.status = AlertStatus.FAILED


# ══════════════════════════════════════
# 알림 템플릿
# ══════════════════════════════════════
ALERT_TEMPLATES = {
    AlertType.DAILY_REPORT: {
        "title": "📊 일일 포트폴리오 리포트",
        "template": (
            "[{date}] 일일 리포트\n"
            "────────────────\n"
            "총 자산: {total_value:,.0f}원\n"
            "일간 수익률: {daily_return:+.2f}%\n"
            "실현 손익: {realized_pnl:+,.0f}원\n"
            "미실현 손익: {unrealized_pnl:+,.0f}원\n"
            "보유 종목: {position_count}개\n"
            "────────────────\n"
            "금일 체결: 매수 {buy_count}건, 매도 {sell_count}건"
        ),
        "level": AlertLevel.INFO,
    },
    AlertType.WEEKLY_REPORT: {
        "title": "📈 주간 성과 리포트",
        "template": (
            "[{start_date} ~ {end_date}] 주간 리포트\n"
            "────────────────\n"
            "총 자산: {total_value:,.0f}원\n"
            "주간 수익률: {weekly_return:+.2f}%\n"
            "주간 실현 손익: {weekly_realized_pnl:+,.0f}원\n"
            "MDD: {mdd:.2f}%\n"
            "Sharpe Ratio: {sharpe:.2f}\n"
            "────────────────\n"
            "주간 리밸런싱: {rebalancing_count}회"
        ),
        "level": AlertLevel.INFO,
    },
    AlertType.MONTHLY_REPORT: {
        "title": "📋 월간 종합 리포트",
        "template": (
            "[{year}년 {month}월] 월간 종합 리포트\n"
            "────────────────\n"
            "총 자산: {total_value:,.0f}원\n"
            "월간 수익률: {monthly_return:+.2f}%\n"
            "누적 수익률: {cumulative_return:+.2f}%\n"
            "CAGR: {cagr:.2f}%\n"
            "월간 MDD: {mdd:.2f}%\n"
            "Sharpe Ratio: {sharpe:.2f}"
        ),
        "level": AlertLevel.INFO,
    },
    AlertType.EMERGENCY_REBALANCING: {
        "title": "🚨 긴급 리밸런싱 알림",
        "template": (
            "긴급 리밸런싱이 실행되었습니다.\n"
            "────────────────\n"
            "사유: {reason}\n"
            "손실률: {loss_rate:.2f}%\n"
            "매도 종목: {sell_count}개\n"
            "매수 종목: {buy_count}개\n"
            "총 거래 수: {total_orders}건\n"
            "────────────────\n"
            "실행 시간: {executed_at}"
        ),
        "level": AlertLevel.CRITICAL,
    },
    AlertType.SYSTEM_ERROR: {
        "title": "⚠️ 시스템 오류",
        "template": (
            "시스템 오류가 발생했습니다.\n"
            "────────────────\n"
            "모듈: {module}\n"
            "오류: {error_message}\n"
            "발생 시각: {occurred_at}\n"
            "────────────────\n"
            "상세: {details}"
        ),
        "level": AlertLevel.ERROR,
    },
}


# ══════════════════════════════════════
# 알림 매니저
# ══════════════════════════════════════
class AlertManager:
    """
    알림 생성·관리·이력 조회 서비스

    알림 생명주기:
      생성(create) → 발송(dispatch) → 확인(mark_read)
    """

    def __init__(self, mongo_collection=None):
        """
        Args:
            mongo_collection: MongoDB 알림 컬렉션 (None이면 메모리 저장)
        """
        self._collection = mongo_collection
        self._in_memory_alerts: list[Alert] = []

    # ── 알림 생성 ──
    def create_alert(
        self,
        alert_type: AlertType,
        level: AlertLevel = AlertLevel.INFO,
        title: str = "",
        message: str = "",
        metadata: Optional[dict] = None,
    ) -> Alert:
        """수동 알림 생성"""
        alert = Alert(
            alert_type=alert_type,
            level=level,
            title=title or f"[{alert_type.value}]",
            message=message,
            metadata=metadata or {},
        )
        self._in_memory_alerts.append(alert)
        logger.info(f"Alert created: [{alert.level.value}] {alert.title}")
        return alert

    def create_from_template(
        self,
        alert_type: AlertType,
        template_data: dict,
        extra_metadata: Optional[dict] = None,
    ) -> Alert:
        """템플릿 기반 알림 생성"""
        template_info = ALERT_TEMPLATES.get(alert_type)
        if not template_info:
            raise ValueError(f"지원하지 않는 알림 유형: {alert_type.value}")

        try:
            message = template_info["template"].format(**template_data)
        except KeyError as e:
            logger.warning(f"Template key missing: {e}")
            message = str(template_data)

        return self.create_alert(
            alert_type=alert_type,
            level=template_info["level"],
            title=template_info["title"],
            message=message,
            metadata={**(extra_metadata or {}), "template_data": template_data},
        )

    # ── 알림 조회 ──
    async def get_alerts(
        self,
        limit: int = 50,
        offset: int = 0,
        alert_type: Optional[AlertType] = None,
        level: Optional[AlertLevel] = None,
        status: Optional[AlertStatus] = None,
    ) -> list[dict]:
        """알림 이력 조회"""
        # MongoDB 사용 가능 시 DB 조회
        if self._collection is not None:
            query = {}
            if alert_type:
                query["alert_type"] = alert_type.value
            if level:
                query["level"] = level.value
            if status:
                query["status"] = status.value

            cursor = self._collection.find(query).sort("created_at", -1).skip(offset).limit(limit)
            return [doc async for doc in cursor]

        # 메모리 폴백
        filtered = self._in_memory_alerts
        if alert_type:
            filtered = [a for a in filtered if a.alert_type == alert_type]
        if level:
            filtered = [a for a in filtered if a.level == level]
        if status:
            filtered = [a for a in filtered if a.status == status]

        filtered.sort(key=lambda a: a.created_at, reverse=True)
        return [a.to_dict() for a in filtered[offset : offset + limit]]

    async def get_alert_by_id(self, alert_id: str) -> Optional[dict]:
        """ID로 알림 조회"""
        if self._collection is not None:
            return await self._collection.find_one({"id": alert_id})

        for alert in self._in_memory_alerts:
            if alert.id == alert_id:
                return alert.to_dict()
        return None

    async def get_unread_count(self) -> int:
        """미확인 알림 수"""
        if self._collection is not None:
            return await self._collection.count_documents({"status": {"$ne": AlertStatus.READ.value}})

        return len([a for a in self._in_memory_alerts if a.status != AlertStatus.READ])

    # ── 알림 상태 변경 ──
    async def mark_alert_read(self, alert_id: str) -> bool:
        """알림 확인 처리"""
        if self._collection is not None:
            result = await self._collection.update_one(
                {"id": alert_id},
                {
                    "$set": {
                        "status": AlertStatus.READ.value,
                        "read_at": datetime.now(timezone.utc).isoformat(),
                    }
                },
            )
            return result.modified_count > 0

        for alert in self._in_memory_alerts:
            if alert.id == alert_id:
                alert.mark_read()
                return True
        return False

    async def mark_all_read(self) -> int:
        """모든 알림 확인 처리"""
        if self._collection is not None:
            result = await self._collection.update_many(
                {"status": {"$ne": AlertStatus.READ.value}},
                {
                    "$set": {
                        "status": AlertStatus.READ.value,
                        "read_at": datetime.now(timezone.utc).isoformat(),
                    }
                },
            )
            return result.modified_count

        count = 0
        for alert in self._in_memory_alerts:
            if alert.status != AlertStatus.READ:
                alert.mark_read()
                count += 1
        return count

    # ── 알림 저장 ──
    async def save_alert(self, alert: Alert) -> None:
        """알림을 MongoDB에 저장"""
        if self._collection is not None:
            await self._collection.insert_one(alert.to_dict())
            logger.debug(f"Alert saved to MongoDB: {alert.id}")
        # 메모리 모드에서는 이미 _in_memory_alerts에 저장됨

    async def get_alert_stats(self) -> dict:
        """알림 통계"""
        if self._collection is not None:
            total = await self._collection.count_documents({})
            unread = await self._collection.count_documents({"status": {"$ne": AlertStatus.READ.value}})
            by_level = {}
            for level in AlertLevel:
                count = await self._collection.count_documents({"level": level.value})
                by_level[level.value] = count
            return {"total": total, "unread": unread, "by_level": by_level}

        total = len(self._in_memory_alerts)
        unread = len([a for a in self._in_memory_alerts if a.status != AlertStatus.READ])
        by_level = {}
        for level in AlertLevel:
            by_level[level.value] = len([a for a in self._in_memory_alerts if a.level == level])
        return {"total": total, "unread": unread, "by_level": by_level}
