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
    """알림 상태.

    상태 전이 (Commit 1 에서 SENDING/DEAD 추가):
        PENDING  → SENDING (claim_for_sending, atomic)
        SENDING  → SENT    (mark_sent_by_id)
        SENDING  → FAILED  (mark_failed_with_retry, attempts < max)
        SENDING  → DEAD    (mark_failed_with_retry, attempts >= max)
        FAILED   → PENDING (스케줄러 재픽업, Commit 3 에서 구현)
        *        → READ    (mark_alert_read, 운영자 확인)

    DEAD 는 최대 재시도 초과 상태로, 메타알림(`AlertPipelineFailureRate`)의
    1차 타겟이자 운영자 수동 개입이 필요한 terminal 상태다.
    """

    PENDING = "PENDING"  # 발송 대기
    SENDING = "SENDING"  # 발송 중 (atomic claim, race 방지)
    SENT = "SENT"  # 발송 완료
    FAILED = "FAILED"  # 일시 실패 (재시도 대상)
    DEAD = "DEAD"  # 최대 재시도 초과 (운영자 수동 개입 필요)
    READ = "READ"  # 확인됨


# ══════════════════════════════════════
# 상수 (재시도/에러 길이)
# ══════════════════════════════════════
# 알림 발송 실패 시 `last_send_error` 에 기록할 최대 문자열 길이.
# 텔레그램 메시지 한계(4096자) 및 Mongo 인덱스 효율을 고려한 값.
# 향후 운영 관찰에 따라 조정 가능하도록 모듈 상수로 분리.
MAX_ALERT_ERROR_LEN = 500


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
    # ── 재시도 추적 (Commit 1 신규) ──
    # metadata dict 에 넣지 않고 1급 필드로 올린 이유:
    #   (a) MongoDB 쿼리 필터(`send_attempts: {$lt: N}`)에서 직접 사용
    #   (b) 타입 안정성
    #   (c) Prometheus 라벨로 직접 노출되는 값
    send_attempts: int = 0
    last_send_error: Optional[str] = None
    last_send_attempt_at: Optional[datetime] = None
    last_send_status_code: Optional[int] = None

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
            "send_attempts": self.send_attempts,
            "last_send_error": self.last_send_error,
            "last_send_attempt_at": (self.last_send_attempt_at.isoformat() if self.last_send_attempt_at else None),
            "last_send_status_code": self.last_send_status_code,
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

    def set_collection(self, mongo_collection) -> None:
        """런타임에 MongoDB 컬렉션을 주입한다.

        모듈 레벨 싱글톤(`api.routes.alerts._alert_manager`)은 import 시점에
        DB 가 아직 연결되지 않은 상태로 생성되므로, FastAPI startup 단계에서
        이 메서드로 컬렉션을 주입한다.
        """
        self._collection = mongo_collection
        logger.info(
            f"AlertManager: MongoDB 컬렉션 주입 완료 ({'enabled' if mongo_collection is not None else 'disabled'})"
        )

    async def create_and_persist_alert(
        self,
        alert_type: AlertType,
        level: AlertLevel = AlertLevel.INFO,
        title: str = "",
        message: str = "",
        metadata: Optional[dict] = None,
    ) -> Alert:
        """알림을 생성하고, 컬렉션이 주입되어 있으면 MongoDB에 영속화한다.

        - `_collection` 이 None 이면 in-memory 만 저장하고 반환 (회귀 동작 호환).
        - `_collection` 이 있으면 `save_alert` 까지 호출하여 영속화한다.
        - DB 쓰기 실패는 예외를 그대로 올린다 (호출자가 try/except 로 swallow 하도록).
        """
        alert = self.create_alert(
            alert_type=alert_type,
            level=level,
            title=title,
            message=message,
            metadata=metadata,
        )
        if self._collection is not None:
            await self.save_alert(alert)
        return alert

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
        """알림을 MongoDB 에 upsert 저장 (id 기준, 멱등).

        Commit 1 에서 `insert_one` → `update_one(upsert=True)` 로 전환.
        기존 `insert_one` 은 `telegram_notifier.dispatch_alert` 및
        `create_and_persist_alert` 경로에서 동일 id 로 중복 호출될 수 있어
        중복 행을 만들 수 있었다. upsert 로 전환하여 멱등성을 보장한다.
        """
        if self._collection is not None:
            await self._collection.update_one(
                {"id": alert.id},
                {"$set": alert.to_dict()},
                upsert=True,
            )
            logger.debug(f"Alert upserted to MongoDB: {alert.id}")
        # 메모리 모드에서는 이미 _in_memory_alerts에 저장됨

    # ── 재시도 상태 전이 (Commit 1 신규) ──
    async def claim_for_sending(self, alert_id: str) -> bool:
        """PENDING → SENDING 원자적 전이 + send_attempts 증가.

        다중 워커/스케줄러 환경에서 동일 Alert 가 중복 발송되는 것을
        방지하기 위한 atomic claim. MongoDB 의 단일 document update 는
        atomic 이 보장되므로 정확히 한 워커만 True 를 받는다.

        Returns:
            True:  claim 성공 (호출자가 발송 로직 진행)
            False: 이미 다른 워커가 claim 했거나 상태가 PENDING 이 아님
        """
        now_iso = datetime.now(timezone.utc).isoformat()

        if self._collection is not None:
            result = await self._collection.update_one(
                {"id": alert_id, "status": AlertStatus.PENDING.value},
                {
                    "$set": {
                        "status": AlertStatus.SENDING.value,
                        "last_send_attempt_at": now_iso,
                    },
                    "$inc": {"send_attempts": 1},
                },
            )
            return result.modified_count == 1

        # 메모리 모드: 단일 프로세스라 race 없음. 단순 검사.
        for alert in self._in_memory_alerts:
            if alert.id == alert_id and alert.status == AlertStatus.PENDING:
                alert.status = AlertStatus.SENDING
                alert.send_attempts += 1
                alert.last_send_attempt_at = datetime.now(timezone.utc)
                return True
        return False

    async def mark_sent_by_id(self, alert_id: str) -> bool:
        """SENDING → SENT 전이. dispatch 성공 시 호출.

        SENDING 상태가 아닌 Alert 는 전이하지 않고 False 반환
        (이중 전이 방지).
        """
        now = datetime.now(timezone.utc)

        if self._collection is not None:
            result = await self._collection.update_one(
                {"id": alert_id, "status": AlertStatus.SENDING.value},
                {
                    "$set": {
                        "status": AlertStatus.SENT.value,
                        "sent_at": now.isoformat(),
                        "last_send_error": None,
                    }
                },
            )
            return result.modified_count == 1

        for alert in self._in_memory_alerts:
            if alert.id == alert_id and alert.status == AlertStatus.SENDING:
                alert.status = AlertStatus.SENT
                alert.sent_at = now
                alert.last_send_error = None
                return True
        return False

    async def mark_failed_with_retry(
        self,
        alert_id: str,
        error: str,
        status_code: Optional[int] = None,
        max_attempts: int = 3,
    ) -> AlertStatus:
        """SENDING → FAILED(재시도 가능) 또는 DEAD(최대 초과) 전이.

        경계: `send_attempts >= max_attempts` 이면 DEAD, 미만이면 FAILED.
        `claim_for_sending` 이 이미 `$inc: 1` 을 적용했으므로 세 번째
        시도 실패 시점의 `send_attempts` 값은 3 이고, 이는 gte 3 으로
        DEAD 에 전이된다 ("3번 시도 후 포기" 의 직관).

        Args:
            alert_id: 대상 Alert id
            error: 실패 사유 문자열 (MAX_ALERT_ERROR_LEN 자에서 절단)
            status_code: HTTP 상태 코드 (해당하는 경우)
            max_attempts: 최대 재시도 횟수 (기본 3)

        Returns:
            최종 상태 (FAILED 또는 DEAD). DEAD 로 전이된 경우 호출자가
            메타알림/운영 로그 트리거를 수행해야 한다.
        """
        truncated_error = error[:MAX_ALERT_ERROR_LEN] if error else ""
        now_iso = datetime.now(timezone.utc).isoformat()

        if self._collection is not None:
            doc = await self._collection.find_one({"id": alert_id})
            if doc is None:
                # 존재하지 않는 Alert 에 대한 실패 전이 요청 — 논리 오류.
                # 호출자가 무시할 수 있도록 FAILED 를 반환하되 로그 남김.
                logger.warning(f"mark_failed_with_retry: alert not found id={alert_id}")
                return AlertStatus.FAILED

            attempts = int(doc.get("send_attempts", 0))
            new_status = AlertStatus.DEAD if attempts >= max_attempts else AlertStatus.FAILED
            await self._collection.update_one(
                {"id": alert_id},
                {
                    "$set": {
                        "status": new_status.value,
                        "last_send_error": truncated_error,
                        "last_send_status_code": status_code,
                        "last_send_attempt_at": now_iso,
                    }
                },
            )
            return new_status

        for alert in self._in_memory_alerts:
            if alert.id == alert_id:
                new_status = AlertStatus.DEAD if alert.send_attempts >= max_attempts else AlertStatus.FAILED
                alert.status = new_status
                alert.last_send_error = truncated_error
                alert.last_send_status_code = status_code
                alert.last_send_attempt_at = datetime.now(timezone.utc)
                return new_status

        logger.warning(f"mark_failed_with_retry: alert not found in memory id={alert_id}")
        return AlertStatus.FAILED

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
