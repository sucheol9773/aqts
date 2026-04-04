"""
비밀키 관리 (Secret Manager)

Gate D: 키 로테이션/볼트 사용

기능:
  - 시크릿 등록/조회/로테이션
  - 만료 기한 추적 및 갱신 알림
  - 로테이션 이력 기록 (감사 추적)
  - 환경별 시크릿 분리 (production/demo/test)
  - 시크릿 건강 검사 (만료 임박, 미로테이션 경고)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from config.logging import logger


class SecretType(str, Enum):
    """시크릿 유형"""

    API_KEY = "API_KEY"
    DATABASE_PASSWORD = "DATABASE_PASSWORD"
    JWT_SECRET = "JWT_SECRET"
    BOT_TOKEN = "BOT_TOKEN"
    ENCRYPTION_KEY = "ENCRYPTION_KEY"
    OAUTH_SECRET = "OAUTH_SECRET"


class SecretEnvironment(str, Enum):
    """환경 구분"""

    PRODUCTION = "PRODUCTION"
    DEMO = "DEMO"
    TEST = "TEST"


class SecretStatus(str, Enum):
    """시크릿 상태"""

    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    ROTATED = "ROTATED"  # 로테이션 완료 (이전 키)
    REVOKED = "REVOKED"


@dataclass
class RotationRecord:
    """로테이션 이력"""

    rotation_id: str
    secret_name: str
    rotated_at: datetime
    previous_version: int
    new_version: int
    reason: str
    performed_by: str = "system"

    def to_dict(self) -> dict:
        return {
            "rotation_id": self.rotation_id,
            "secret_name": self.secret_name,
            "rotated_at": self.rotated_at.isoformat(),
            "previous_version": self.previous_version,
            "new_version": self.new_version,
            "reason": self.reason,
            "performed_by": self.performed_by,
        }


@dataclass
class SecretEntry:
    """시크릿 항목 (값은 저장하지 않음 — 메타데이터만)"""

    name: str
    secret_type: SecretType
    environment: SecretEnvironment
    version: int = 1
    status: SecretStatus = SecretStatus.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    last_rotated_at: Optional[datetime] = None
    rotation_interval_days: int = 90  # 기본 90일 로테이션 주기
    description: str = ""

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= self.expires_at

    @property
    def days_until_expiry(self) -> Optional[int]:
        if self.expires_at is None:
            return None
        delta = self.expires_at - datetime.now(timezone.utc)
        return max(0, delta.days)

    @property
    def needs_rotation(self) -> bool:
        """로테이션이 필요한지 (주기 초과)"""
        ref_date = self.last_rotated_at or self.created_at
        elapsed = (datetime.now(timezone.utc) - ref_date).days
        return elapsed >= self.rotation_interval_days

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "secret_type": self.secret_type.value,
            "environment": self.environment.value,
            "version": self.version,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "last_rotated_at": self.last_rotated_at.isoformat() if self.last_rotated_at else None,
            "rotation_interval_days": self.rotation_interval_days,
            "is_expired": self.is_expired,
            "needs_rotation": self.needs_rotation,
            "days_until_expiry": self.days_until_expiry,
        }


class SecretManager:
    """
    비밀키 관리자

    시크릿 메타데이터 관리, 로테이션 추적, 건강 검사.
    실제 시크릿 값은 저장하지 않습니다 (환경변수/볼트에서 관리).
    """

    def __init__(self):
        self._secrets: dict[str, SecretEntry] = {}
        self._rotation_history: list[RotationRecord] = []

    def register(
        self,
        name: str,
        secret_type: SecretType,
        environment: SecretEnvironment,
        rotation_interval_days: int = 90,
        expires_at: Optional[datetime] = None,
        description: str = "",
    ) -> SecretEntry:
        """시크릿 등록"""
        if name in self._secrets:
            raise ValueError(f"Secret '{name}' already registered. Use rotate() to update.")

        entry = SecretEntry(
            name=name,
            secret_type=secret_type,
            environment=environment,
            rotation_interval_days=rotation_interval_days,
            expires_at=expires_at,
            description=description,
        )

        self._secrets[name] = entry
        logger.info(f"Secret registered: {name} ({secret_type.value}, {environment.value})")
        return entry

    def rotate(self, name: str, reason: str = "scheduled", performed_by: str = "system") -> Optional[SecretEntry]:
        """
        시크릿 로테이션

        이전 버전을 ROTATED로 마킹하고 새 버전으로 갱신합니다.
        """
        entry = self._secrets.get(name)
        if not entry:
            logger.warning(f"Cannot rotate: secret '{name}' not found")
            return None

        old_version = entry.version

        # 로테이션 기록
        record = RotationRecord(
            rotation_id=str(uuid4()),
            secret_name=name,
            rotated_at=datetime.now(timezone.utc),
            previous_version=old_version,
            new_version=old_version + 1,
            reason=reason,
            performed_by=performed_by,
        )
        self._rotation_history.append(record)

        # 버전 업데이트
        entry.version = old_version + 1
        entry.last_rotated_at = datetime.now(timezone.utc)
        entry.status = SecretStatus.ACTIVE

        # 만료일 갱신 (있었다면 로테이션 주기만큼 연장)
        if entry.expires_at is not None:
            entry.expires_at = datetime.now(timezone.utc) + timedelta(days=entry.rotation_interval_days)

        logger.info(f"Secret rotated: {name} v{old_version} → v{entry.version} (reason: {reason})")
        return entry

    def revoke(self, name: str) -> Optional[SecretEntry]:
        """시크릿 폐기"""
        entry = self._secrets.get(name)
        if not entry:
            return None

        entry.status = SecretStatus.REVOKED
        logger.warning(f"Secret revoked: {name}")
        return entry

    def get(self, name: str) -> Optional[SecretEntry]:
        """시크릿 메타데이터 조회"""
        return self._secrets.get(name)

    def get_rotation_history(self, name: Optional[str] = None, limit: int = 50) -> list[dict]:
        """로테이션 이력 조회"""
        history = self._rotation_history
        if name:
            history = [r for r in history if r.secret_name == name]
        return [r.to_dict() for r in sorted(history, key=lambda r: r.rotated_at, reverse=True)[:limit]]

    def health_check(self) -> dict:
        """
        시크릿 건강 검사

        Returns:
            expired: 만료된 시크릿 목록
            needs_rotation: 로테이션 필요 시크릿 목록
            expiring_soon: 30일 이내 만료 예정 시크릿 목록
            revoked: 폐기된 시크릿 목록
        """
        expired = []
        needs_rotation = []
        expiring_soon = []
        revoked = []

        for name, entry in self._secrets.items():
            if entry.status == SecretStatus.REVOKED:
                revoked.append(name)
                continue

            if entry.is_expired:
                expired.append(name)
            elif entry.days_until_expiry is not None and entry.days_until_expiry <= 30:
                expiring_soon.append(name)

            if entry.needs_rotation and entry.status == SecretStatus.ACTIVE:
                needs_rotation.append(name)

        healthy = len(expired) == 0 and len(needs_rotation) == 0
        return {
            "healthy": healthy,
            "total_secrets": len(self._secrets),
            "active": sum(1 for e in self._secrets.values() if e.status == SecretStatus.ACTIVE),
            "expired": expired,
            "needs_rotation": needs_rotation,
            "expiring_soon": expiring_soon,
            "revoked": revoked,
            "total_rotations": len(self._rotation_history),
        }

    def get_all(self) -> list[dict]:
        """전체 시크릿 메타데이터"""
        return [e.to_dict() for e in self._secrets.values()]

    @property
    def count(self) -> int:
        return len(self._secrets)
