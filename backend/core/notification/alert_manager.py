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
from typing import Any, Optional
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
        # NotificationRouter 인스턴스 (lifespan 에서 set_router 로 주입).
        # 순환 import 회피를 위해 타입은 Any 로 선언한다.
        # 주입 여부가 곧 "즉시 디스패치 활성" 플래그 역할을 한다 (Commit 2).
        self._router: Optional[Any] = None

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

    def set_router(self, router: Optional[Any]) -> None:
        """런타임에 NotificationRouter 를 주입한다 (Commit 2).

        `set_collection` 과 동형의 setter. import 시점 싱글톤 패턴을 유지하기
        위해 생성자 주입 대신 setter 를 사용한다.

        주입 시점 이후 `create_and_persist_alert` 는 저장 직후 router 를 통해
        즉시 디스패치를 수행한다. `None` 을 주입하면 디스패치 경로가 비활성화
        되어 in-memory 또는 단순 영속화 경로만 동작한다 (테스트 격리 용도).

        운영 중 재주입 가능 — 예를 들어 Telegram 토큰 로테이션 후 router 를
        재조립하여 무중단으로 교체할 수 있다.
        """
        self._router = router
        logger.info(f"AlertManager: NotificationRouter 주입 완료 ({'enabled' if router is not None else 'disabled'})")

    async def create_and_persist_alert(
        self,
        alert_type: AlertType,
        level: AlertLevel = AlertLevel.INFO,
        title: str = "",
        message: str = "",
        metadata: Optional[dict] = None,
    ) -> Alert:
        """알림을 생성하고, 컬렉션이 주입되어 있으면 MongoDB에 영속화한다.

        Commit 2: router 가 주입되어 있으면 저장 직후 즉시 디스패치를 시도한다.

        파이프라인 계약:
          1. create_alert (in-memory) — 항상 실행
          2. save_alert (MongoDB) — collection 주입 시
          3. _dispatch_via_router — router 주입 시, 예외 swallow

        예외 정책:
          - DB 쓰기 실패는 그대로 raise (호출자가 try/except 로 swallow 하도록)
          - router.dispatch 실패는 내부에서 swallow + FAILED 영속화
            이유: 관찰성 채널 장애가 원인 이벤트(KIS 복원 실패 등) 처리 경로를
            막으면 안 됨. NotificationRouter 가 이미 Telegram → File → Console
            캐스케이드를 수행하므로 최소 한 채널까지는 도달함. FAILED 영속화는
            Commit 3 의 스케줄러가 재픽업하여 at-least-once 보장.
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
        if self._router is not None:
            await self._dispatch_via_router(alert)
        return alert

    async def _dispatch_via_router(self, alert: Alert) -> None:
        """router 를 통해 alert 를 디스패치하고 상태 전이를 수행한다 (Commit 2).

        흐름:
          1. claim_for_sending(alert.id) 로 PENDING → SENDING 원자 전이.
             실패(False) 시 dispatch 스킵 — 이미 다른 워커가 claim 했거나
             상태가 PENDING 이 아닌 경우 (e.g. in-memory 모드에서 재호출).
          2. router.dispatch(alert) await.
          3. DispatchResult.success 에 따라 mark_sent_by_id 또는
             mark_failed_with_retry 호출.
          4. router.dispatch 자체가 예외를 raise 하면 그 예외 메시지로
             mark_failed_with_retry 호출 후 swallow.

        이 메서드는 예외를 raise 하지 않는다. 호출자(`create_and_persist_alert`)
        의 계약을 유지하기 위함이다.
        """
        try:
            claimed = await self.claim_for_sending(alert.id)
            if not claimed:
                logger.debug(f"dispatch skipped, alert not in PENDING state: {alert.id}")
                return

            try:
                result = await self._router.dispatch(alert)
            except Exception as exc:  # noqa: BLE001 — router 내부 예외 격리 필요
                logger.warning(f"NotificationRouter.dispatch raised: alert_id={alert.id} error={exc}")
                await self.mark_failed_with_retry(
                    alert.id,
                    error=str(exc),
                    status_code=None,
                )
                return

            if getattr(result, "success", False):
                await self.mark_sent_by_id(alert.id)
            else:
                channels_tried = getattr(result, "channels_tried", [])
                error_msg = f"all channels failed: {channels_tried}"
                logger.warning(
                    f"NotificationRouter.dispatch all channels failed: "
                    f"alert_id={alert.id} channels={channels_tried}"
                )
                await self.mark_failed_with_retry(
                    alert.id,
                    error=error_msg,
                    status_code=None,
                )
        except Exception as exc:  # noqa: BLE001 — 최상위 swallow 가드
            # claim_for_sending / mark_* 자체가 예외를 낸 경우에도
            # 원인 이벤트 처리 경로를 막지 않기 위해 최종 swallow.
            logger.warning(f"_dispatch_via_router unexpected error: alert_id={alert.id} error={exc}")

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

    # ── 재시도 픽업 루프 (Commit 3 신규) ──
    async def find_retriable_alerts(
        self,
        now: Optional[datetime] = None,
        max_attempts: int = 3,
        limit: int = 100,
    ) -> list[dict]:
        """백오프 만기된 FAILED 알림을 조회한다 (Commit 3).

        Commit 1 의 상태 머신 docstring 에서 예약한 `FAILED → PENDING`
        재픽업 경로의 첫 단계. 본 메서드는 조회만 수행하며 상태를
        변경하지 않는다. 호출자(`dispatch_retriable_alerts`)가 각
        document 에 대해 개별 원자 전이(`requeue_failed_to_pending`)
        를 수행한다.

        조회 조건:
          1. status == FAILED
          2. send_attempts < max_attempts
             (send_attempts >= max_attempts 는 이미 DEAD 이거나 곧
              DEAD 로 전이될 상태이므로 재시도 대상이 아님)
          3. last_send_attempt_at + RETRY_BACKOFF_SECONDS[send_attempts]
             <= now
             (백오프 미만의 알림은 아직 재시도 금지)

        백오프 필터는 MongoDB 서버 사이드 `$expr` 대신 파이썬에서
        2차 필터링한다. 근거:
          - FAILED 알림의 정상 cardinality 는 분당 수 건 수준.
          - `$expr` + 날짜 문자열 파싱은 Mongo 드라이버 호환성
            이슈가 있고, 감사 시 쿼리를 재현하기 어렵다.
          - 파이썬측 필터는 `RETRY_BACKOFF_SECONDS` 상수와 직결되어
            운영 이해가 쉬움.

        Args:
            now: 기준 시각 (기본값 현재 UTC). 테스트에서 주입 가능.
            max_attempts: DEAD 경계. retry_policy.MAX_SEND_ATTEMPTS 와
                동일해야 한다 (기본 3).
            limit: 1회 픽업 최대 건수. MongoDB cursor 부하 제한.

        Returns:
            재시도 대상 Alert document (dict) 리스트. 빈 리스트 가능.
        """
        from core.notification.retry_policy import RETRY_BACKOFF_SECONDS

        current = now or datetime.now(timezone.utc)

        def _is_ready(doc: dict) -> bool:
            attempts = int(doc.get("send_attempts", 0))
            # attempts==0 이면 아직 한 번도 시도되지 않은 FAILED
            # (논리적으로는 불가능하지만, 잘못된 데이터 방어).
            # attempts>=max 이면 이미 DEAD 경계이므로 재시도 대상 아님.
            if not (0 < attempts < max_attempts):
                return False
            last_iso = doc.get("last_send_attempt_at")
            if not last_iso:
                # 기록이 없는 FAILED 는 즉시 재시도 허용 (보수적).
                return True
            try:
                last_dt = datetime.fromisoformat(last_iso)
            except (TypeError, ValueError):
                return True
            wait_s = RETRY_BACKOFF_SECONDS.get(attempts)
            if wait_s is None:
                # 범위 밖 — retry_policy.backoff_seconds_for 와 동일
                # clamp 정책.
                wait_s = RETRY_BACKOFF_SECONDS[max(RETRY_BACKOFF_SECONDS.keys())]
            return (current - last_dt).total_seconds() >= wait_s

        if self._collection is not None:
            cursor = (
                self._collection.find(
                    {
                        "status": AlertStatus.FAILED.value,
                        "send_attempts": {"$lt": max_attempts, "$gt": 0},
                    }
                )
                .sort("last_send_attempt_at", 1)
                .limit(limit)
            )
            docs = [doc async for doc in cursor]
            return [d for d in docs if _is_ready(d)]

        # 메모리 모드
        candidates = [
            a for a in self._in_memory_alerts if a.status == AlertStatus.FAILED and 0 < a.send_attempts < max_attempts
        ]
        candidates.sort(key=lambda a: a.last_send_attempt_at or datetime.min.replace(tzinfo=timezone.utc))
        result = []
        for a in candidates[:limit]:
            if _is_ready(a.to_dict()):
                result.append(a.to_dict())
        return result

    async def requeue_failed_to_pending(self, alert_id: str) -> bool:
        """FAILED → PENDING 원자 전이 (Commit 3).

        `find_retriable_alerts` 가 반환한 document 에 대해 호출한다.
        조건(`status == FAILED`) 을 update filter 에 포함하여 다른
        워커가 이미 재큐잉했거나 DEAD 로 전이된 경우 False 를
        반환한다 (이중 재시도 방지).

        전이 성공 후에는 `claim_for_sending` 이 PENDING → SENDING
        으로 한 번 더 원자 전이하므로, 두 단계 합산 최종 상태는
        항상 정확히 한 워커에게만 SENDING 을 부여한다.

        주의: 이 메서드는 `send_attempts` 를 변경하지 않는다. 증가는
        `claim_for_sending` 의 `$inc` 에서 일어난다. 즉 재시도 1 회당
        attempts 는 정확히 1 만큼 증가한다.
        """
        if self._collection is not None:
            result = await self._collection.update_one(
                {"id": alert_id, "status": AlertStatus.FAILED.value},
                {"$set": {"status": AlertStatus.PENDING.value}},
            )
            return result.modified_count == 1

        for alert in self._in_memory_alerts:
            if alert.id == alert_id and alert.status == AlertStatus.FAILED:
                alert.status = AlertStatus.PENDING
                return True
        return False

    async def dispatch_retriable_alerts(self, max_attempts: int = 3, limit: int = 100) -> dict:
        """백오프 만기된 FAILED 알림을 재픽업하여 재발송한다 (Commit 3).

        본 메서드가 Commit 3 의 at-least-once 전달 보장의 엔트리포인트다.
        `main.py` lifespan 의 주기 태스크에서 호출된다.

        흐름:
          1. find_retriable_alerts — FAILED + 백오프 만기 조회
          2. requeue_failed_to_pending — 원자 전이 (실패 시 skip)
          3. Alert 재구성 — dict → Alert (metadata 유지)
          4. _dispatch_via_router — Commit 2 의 디스패치 경로 재사용
             (claim_for_sending → router.dispatch → mark_sent/failed)
          5. mark_failed_with_retry 의 반환값이 DEAD 이면 카운트
             (호출자는 get_alerts 로 확인 가능)

        설계 — Path A 원칙 연장:
          `_dispatch_via_router` 를 재사용하므로 Commit 2 의 경로와
          100% 동형이다. 즉시 디스패치(create_and_persist_alert)와
          재시도 디스패치가 서로 다른 코드 경로를 타지 않는다. 이는
          메트릭 관측과 버그 탐지 시 일관된 경로 분석을 가능하게 한다.

          DEAD 전이 카운터(`ALERT_RETRY_DEAD_TOTAL`)는 이 메서드 내부
          에서 직접 증가시키지 않고, `_dispatch_via_router` 내부의
          `mark_failed_with_retry` 반환값을 관찰하는 것이 이상적이나,
          현재 구조상 반환값이 소실되므로 본 메서드에서 상태를 재조회
          하여 DEAD 로 전이된 건만 카운트한다.

        예외 정책:
          루프 전체를 try/except 로 감싸지 않는다 — 개별 alert 처리의
          예외는 `_dispatch_via_router` 에서 이미 swallow 되므로 본
          루프까지 전파되지 않는다. 본 메서드가 raise 하는 경우는
          find_retriable_alerts 의 DB 조회 실패뿐이며, 그 경우는
          상위(lifespan 루프)에서 다음 iteration 까지 대기하는 것이
          올바른 동작이다.

        Args:
            max_attempts: retry_policy.MAX_SEND_ATTEMPTS 와 동일 (기본 3).
            limit: 1회 호출당 최대 처리 건수.

        Returns:
            {"dispatched": n, "skipped": m, "dead": k}
              - dispatched: _dispatch_via_router 가 호출된 건수
              - skipped: requeue_failed_to_pending 이 False 반환 (경합)
              - dead: 이번 호출에서 DEAD 로 전이된 건수
        """
        from core.monitoring.metrics import ALERT_RETRY_DEAD_TOTAL

        if self._router is None:
            # Router 미주입 시 noop — Commit 2 의 "주입 = 활성" 원칙.
            return {"dispatched": 0, "skipped": 0, "dead": 0}

        candidates = await self.find_retriable_alerts(max_attempts=max_attempts, limit=limit)
        stats = {"dispatched": 0, "skipped": 0, "dead": 0}

        for doc in candidates:
            alert_id = doc.get("id")
            if not alert_id:
                stats["skipped"] += 1
                continue

            requeued = await self.requeue_failed_to_pending(alert_id)
            if not requeued:
                stats["skipped"] += 1
                continue

            alert_obj = self._alert_from_doc(doc)
            if alert_obj is None:
                stats["skipped"] += 1
                continue

            await self._dispatch_via_router(alert_obj)
            stats["dispatched"] += 1

            # 디스패치 후 DEAD 전이 여부 재확인 — Prometheus 카운터 갱신.
            post = await self.get_alert_by_id(alert_id)
            if post and post.get("status") == AlertStatus.DEAD.value:
                stats["dead"] += 1
                ALERT_RETRY_DEAD_TOTAL.inc()

        return stats

    def _alert_from_doc(self, doc: dict) -> Optional[Alert]:
        """Mongo document → Alert 재구성 (Commit 3 헬퍼).

        Router 는 Alert 인스턴스를 받으므로 dict 를 다시 dataclass 로
        복원해야 한다. 실패 시 None 반환 — 호출자는 skip 처리.

        `send_attempts` 등 재시도 필드도 보존하여, DEAD 경계 판정이
        정확히 수행되도록 한다.
        """
        try:
            alert_type = AlertType(doc["alert_type"])
            level = AlertLevel(doc.get("level", "INFO"))
        except (KeyError, ValueError) as exc:
            logger.warning(f"_alert_from_doc: invalid type/level in doc id={doc.get('id')}: {exc}")
            return None

        try:
            created_at_iso = doc.get("created_at")
            created_at = datetime.fromisoformat(created_at_iso) if created_at_iso else datetime.now(timezone.utc)
        except (TypeError, ValueError):
            created_at = datetime.now(timezone.utc)

        last_attempt_iso = doc.get("last_send_attempt_at")
        try:
            last_attempt = datetime.fromisoformat(last_attempt_iso) if last_attempt_iso else None
        except (TypeError, ValueError):
            last_attempt = None

        return Alert(
            alert_type=alert_type,
            level=level,
            title=doc.get("title", ""),
            message=doc.get("message", ""),
            id=doc.get("id", str(uuid4())),
            status=AlertStatus(doc.get("status", AlertStatus.PENDING.value)),
            created_at=created_at,
            metadata=doc.get("metadata", {}) or {},
            send_attempts=int(doc.get("send_attempts", 0)),
            last_send_error=doc.get("last_send_error"),
            last_send_attempt_at=last_attempt,
            last_send_status_code=doc.get("last_send_status_code"),
        )

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
