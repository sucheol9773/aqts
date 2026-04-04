"""
감사 로그 무결성 검증 (Audit Log Integrity)

Gate D: 모든 주문/변경 기록의 무결성을 보장

기능:
  - SHA-256 해시 체인: 각 로그 항목이 이전 항목의 해시를 포함하여 변조 탐지
  - 무결성 검증: 해시 체인 전체를 순회하며 변조 여부 확인
  - 감사 로그 조회: 날짜/모듈/액션 필터링
  - 통계 제공: 모듈별·액션별 기록 수
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from config.logging import logger


class AuditActionType(str, Enum):
    """감사 로그 액션 유형"""

    ORDER_PLACED = "ORDER_PLACED"
    ORDER_EXECUTED = "ORDER_EXECUTED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    REBALANCING_EXECUTED = "REBALANCING_EXECUTED"
    PROFILE_UPDATED = "PROFILE_UPDATED"
    MODE_CHANGED = "MODE_CHANGED"
    SETTING_CHANGED = "SETTING_CHANGED"
    EMERGENCY_HALT = "EMERGENCY_HALT"
    KILL_SWITCH_ACTIVATED = "KILL_SWITCH_ACTIVATED"
    SYSTEM_START = "SYSTEM_START"
    SYSTEM_SHUTDOWN = "SYSTEM_SHUTDOWN"


@dataclass
class AuditEntry:
    """개별 감사 로그 항목"""

    entry_id: str
    timestamp: datetime
    action_type: str
    module: str
    description: str
    before_state: Optional[dict] = None
    after_state: Optional[dict] = None
    metadata: Optional[dict] = None
    previous_hash: str = ""
    entry_hash: str = ""

    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "timestamp": self.timestamp.isoformat(),
            "action_type": self.action_type,
            "module": self.module,
            "description": self.description,
            "before_state": self.before_state,
            "after_state": self.after_state,
            "metadata": self.metadata,
            "previous_hash": self.previous_hash,
            "entry_hash": self.entry_hash,
        }

    @staticmethod
    def compute_hash(
        entry_id: str,
        timestamp: str,
        action_type: str,
        module: str,
        description: str,
        before_state: Optional[dict],
        after_state: Optional[dict],
        previous_hash: str,
    ) -> str:
        """항목 내용 + 이전 해시로 SHA-256 해시 계산"""
        content = json.dumps(
            {
                "entry_id": entry_id,
                "timestamp": timestamp,
                "action_type": action_type,
                "module": module,
                "description": description,
                "before_state": before_state,
                "after_state": after_state,
                "previous_hash": previous_hash,
            },
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


class AuditIntegrityStore:
    """
    해시 체인 기반 감사 로그 저장소

    각 항목은 이전 항목의 해시를 포함하여 변조를 탐지합니다.
    """

    GENESIS_HASH = "0" * 64  # 첫 항목의 previous_hash

    def __init__(self):
        self._entries: list[AuditEntry] = []
        self._last_hash: str = self.GENESIS_HASH

    def append(
        self,
        action_type: str,
        module: str,
        description: str,
        before_state: Optional[dict] = None,
        after_state: Optional[dict] = None,
        metadata: Optional[dict] = None,
    ) -> AuditEntry:
        """감사 로그 항목 추가 (해시 체인 연결)"""
        entry_id = str(uuid4())
        timestamp = datetime.now(timezone.utc)

        entry_hash = AuditEntry.compute_hash(
            entry_id=entry_id,
            timestamp=timestamp.isoformat(),
            action_type=action_type,
            module=module,
            description=description,
            before_state=before_state,
            after_state=after_state,
            previous_hash=self._last_hash,
        )

        entry = AuditEntry(
            entry_id=entry_id,
            timestamp=timestamp,
            action_type=action_type,
            module=module,
            description=description,
            before_state=before_state,
            after_state=after_state,
            metadata=metadata,
            previous_hash=self._last_hash,
            entry_hash=entry_hash,
        )

        self._entries.append(entry)
        self._last_hash = entry_hash

        logger.debug(f"Audit entry appended: [{action_type}] {module} hash={entry_hash[:12]}...")
        return entry

    def verify_integrity(self) -> "IntegrityResult":
        """
        전체 해시 체인 무결성 검증

        Returns:
            IntegrityResult: 검증 결과 (valid, broken_at_index, details)
        """
        if not self._entries:
            return IntegrityResult(valid=True, total_entries=0)

        expected_prev = self.GENESIS_HASH

        for idx, entry in enumerate(self._entries):
            # 1. previous_hash 연결 검증
            if entry.previous_hash != expected_prev:
                return IntegrityResult(
                    valid=False,
                    total_entries=len(self._entries),
                    broken_at_index=idx,
                    details=f"Chain broken at index {idx}: expected previous_hash={expected_prev[:12]}..., "
                    f"got={entry.previous_hash[:12]}...",
                )

            # 2. entry_hash 재계산 검증
            recomputed = AuditEntry.compute_hash(
                entry_id=entry.entry_id,
                timestamp=entry.timestamp.isoformat(),
                action_type=entry.action_type,
                module=entry.module,
                description=entry.description,
                before_state=entry.before_state,
                after_state=entry.after_state,
                previous_hash=entry.previous_hash,
            )

            if entry.entry_hash != recomputed:
                return IntegrityResult(
                    valid=False,
                    total_entries=len(self._entries),
                    broken_at_index=idx,
                    details=f"Hash mismatch at index {idx}: stored={entry.entry_hash[:12]}..., "
                    f"recomputed={recomputed[:12]}...",
                )

            expected_prev = entry.entry_hash

        return IntegrityResult(valid=True, total_entries=len(self._entries))

    def query(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        module: Optional[str] = None,
        action_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """감사 로그 조회 (필터링)"""
        results = self._entries

        if start_date:
            results = [e for e in results if e.timestamp >= start_date]
        if end_date:
            results = [e for e in results if e.timestamp <= end_date]
        if module:
            results = [e for e in results if e.module == module]
        if action_type:
            results = [e for e in results if e.action_type == action_type]

        return sorted(results, key=lambda e: e.timestamp, reverse=True)[:limit]

    def get_stats(self) -> dict:
        """감사 로그 통계"""
        by_module: dict[str, int] = {}
        by_action: dict[str, int] = {}

        for entry in self._entries:
            by_module[entry.module] = by_module.get(entry.module, 0) + 1
            by_action[entry.action_type] = by_action.get(entry.action_type, 0) + 1

        return {
            "total_entries": len(self._entries),
            "by_module": by_module,
            "by_action": by_action,
            "chain_valid": self.verify_integrity().valid,
            "last_hash": self._last_hash[:16] + "...",
        }

    @property
    def count(self) -> int:
        return len(self._entries)

    @property
    def last_hash(self) -> str:
        return self._last_hash


@dataclass
class IntegrityResult:
    """무결성 검증 결과"""

    valid: bool
    total_entries: int = 0
    broken_at_index: Optional[int] = None
    details: str = ""

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "total_entries": self.total_entries,
            "broken_at_index": self.broken_at_index,
            "details": self.details,
        }
