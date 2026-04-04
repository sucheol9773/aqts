"""
거래 기록 보존 정책 (Record Retention Policy)

Gate D: 거래 기록 5년 보존 설정

기능:
  - 보존 정책 정의 (카테고리별 보존 기간)
  - 기록 등록 및 보존 기한 자동 계산
  - 만료 기록 식별 (삭제 대상)
  - 보존 상태 추적 (ACTIVE/EXPIRED/ARCHIVED/DELETED)
  - 정책 위반 감지 (조기 삭제 방지)
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from config.logging import logger


class RetentionCategory(str, Enum):
    """보존 카테고리"""

    TRADE_ORDER = "TRADE_ORDER"  # 주문 기록
    TRADE_EXECUTION = "TRADE_EXECUTION"  # 체결 기록
    PORTFOLIO_SNAPSHOT = "PORTFOLIO_SNAPSHOT"  # 포트폴리오 스냅샷
    DECISION_RECORD = "DECISION_RECORD"  # 의사결정 기록
    REBALANCING = "REBALANCING"  # 리밸런싱 기록
    RISK_EVENT = "RISK_EVENT"  # 리스크 이벤트
    AUDIT_LOG = "AUDIT_LOG"  # 감사 로그
    SYSTEM_CONFIG = "SYSTEM_CONFIG"  # 시스템 설정 변경


class RetentionStatus(str, Enum):
    """보존 상태"""

    ACTIVE = "ACTIVE"  # 보존 중
    EXPIRED = "EXPIRED"  # 보존 기한 만료
    ARCHIVED = "ARCHIVED"  # 아카이브됨
    DELETED = "DELETED"  # 삭제됨


# 법정 최소 보존 기간 (일)
DEFAULT_RETENTION_DAYS = {
    RetentionCategory.TRADE_ORDER: 5 * 365,  # 5년
    RetentionCategory.TRADE_EXECUTION: 5 * 365,  # 5년
    RetentionCategory.PORTFOLIO_SNAPSHOT: 5 * 365,  # 5년
    RetentionCategory.DECISION_RECORD: 5 * 365,  # 5년
    RetentionCategory.REBALANCING: 5 * 365,  # 5년
    RetentionCategory.RISK_EVENT: 5 * 365,  # 5년
    RetentionCategory.AUDIT_LOG: 10 * 365,  # 10년 (감사 로그는 더 길게)
    RetentionCategory.SYSTEM_CONFIG: 3 * 365,  # 3년
}


@dataclass
class RetentionPolicy:
    """보존 정책 정의"""

    category: RetentionCategory
    retention_days: int
    description: str = ""
    requires_archive: bool = True  # 삭제 전 아카이브 필수 여부

    @property
    def retention_years(self) -> float:
        return round(self.retention_days / 365, 1)

    def to_dict(self) -> dict:
        return {
            "category": self.category.value,
            "retention_days": self.retention_days,
            "retention_years": self.retention_years,
            "description": self.description,
            "requires_archive": self.requires_archive,
        }


@dataclass
class RetentionRecord:
    """보존 대상 기록"""

    record_id: str
    category: RetentionCategory
    created_at: datetime
    expires_at: datetime
    status: RetentionStatus = RetentionStatus.ACTIVE
    source_table: str = ""
    source_id: str = ""
    archived_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expires_at

    @property
    def days_until_expiry(self) -> int:
        delta = self.expires_at - datetime.now(timezone.utc)
        return max(0, delta.days)

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "category": self.category.value,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "status": self.status.value,
            "source_table": self.source_table,
            "source_id": self.source_id,
            "is_expired": self.is_expired,
            "days_until_expiry": self.days_until_expiry,
        }


class RetentionStore:
    """
    거래 기록 보존 관리 저장소

    기록 등록, 만료 확인, 아카이브/삭제 관리
    """

    def __init__(self, custom_policies: Optional[dict[RetentionCategory, int]] = None):
        """
        Args:
            custom_policies: 카테고리별 커스텀 보존 기간 (일). 기본값 오버라이드.
        """
        self._policies: dict[RetentionCategory, RetentionPolicy] = {}
        self._records: list[RetentionRecord] = []

        # 기본 정책 초기화
        for category, days in DEFAULT_RETENTION_DAYS.items():
            actual_days = (custom_policies or {}).get(category, days)
            self._policies[category] = RetentionPolicy(
                category=category,
                retention_days=actual_days,
                description=f"{category.value} records ({actual_days // 365}년 보존)",
            )

    def register_record(
        self,
        category: RetentionCategory,
        source_table: str = "",
        source_id: str = "",
        created_at: Optional[datetime] = None,
    ) -> RetentionRecord:
        """기록 등록 (보존 기한 자동 계산)"""
        policy = self._policies.get(category)
        if not policy:
            raise ValueError(f"No retention policy for category: {category.value}")

        now = created_at or datetime.now(timezone.utc)
        expires_at = now + timedelta(days=policy.retention_days)

        record = RetentionRecord(
            record_id=str(uuid4()),
            category=category,
            created_at=now,
            expires_at=expires_at,
            source_table=source_table,
            source_id=source_id,
        )

        self._records.append(record)
        logger.debug(f"Retention record registered: {record.record_id} expires={expires_at.date()}")
        return record

    def get_expired_records(self) -> list[RetentionRecord]:
        """만료된 기록 목록 (삭제 대상)"""
        return [r for r in self._records if r.is_expired and r.status == RetentionStatus.ACTIVE]

    def archive_record(self, record_id: str) -> Optional[RetentionRecord]:
        """기록 아카이브 처리"""
        record = self._find_record(record_id)
        if not record:
            return None

        record.status = RetentionStatus.ARCHIVED
        record.archived_at = datetime.now(timezone.utc)
        logger.info(f"Record archived: {record_id}")
        return record

    def delete_record(self, record_id: str) -> Optional[RetentionRecord]:
        """
        기록 삭제 처리

        아카이브 필수 정책인 경우 아카이브 없이 삭제 불가.
        보존 기한 전 삭제 시도는 차단.
        """
        record = self._find_record(record_id)
        if not record:
            return None

        # 보존 기한 전 삭제 방지
        if not record.is_expired:
            logger.warning(
                f"Cannot delete record {record_id}: retention period not expired "
                f"(expires={record.expires_at.date()})"
            )
            return None

        # 아카이브 필수 정책 확인
        policy = self._policies.get(record.category)
        if policy and policy.requires_archive and record.status != RetentionStatus.ARCHIVED:
            logger.warning(f"Cannot delete record {record_id}: must be archived first (policy requires archive)")
            return None

        record.status = RetentionStatus.DELETED
        record.deleted_at = datetime.now(timezone.utc)
        logger.info(f"Record deleted: {record_id}")
        return record

    def validate_no_premature_deletion(self) -> list[dict]:
        """조기 삭제 정책 위반 감지"""
        violations = []
        for record in self._records:
            if record.status == RetentionStatus.DELETED and record.deleted_at:
                if record.deleted_at < record.expires_at:
                    violations.append(
                        {
                            "record_id": record.record_id,
                            "category": record.category.value,
                            "deleted_at": record.deleted_at.isoformat(),
                            "expires_at": record.expires_at.isoformat(),
                            "violation": "PREMATURE_DELETION",
                        }
                    )
        return violations

    def get_policies(self) -> list[dict]:
        """전체 보존 정책 목록"""
        return [p.to_dict() for p in self._policies.values()]

    def get_stats(self) -> dict:
        """보존 현황 통계"""
        by_status: dict[str, int] = {}
        by_category: dict[str, int] = {}

        for record in self._records:
            by_status[record.status.value] = by_status.get(record.status.value, 0) + 1
            by_category[record.category.value] = by_category.get(record.category.value, 0) + 1

        expired_count = len(self.get_expired_records())

        return {
            "total_records": len(self._records),
            "by_status": by_status,
            "by_category": by_category,
            "pending_expiry": expired_count,
            "violations": len(self.validate_no_premature_deletion()),
        }

    def _find_record(self, record_id: str) -> Optional[RetentionRecord]:
        for record in self._records:
            if record.record_id == record_id:
                return record
        return None

    @property
    def count(self) -> int:
        return len(self._records)
