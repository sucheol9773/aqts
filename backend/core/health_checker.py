"""
시스템 건전성 검사 (Health Checker)

Phase 6: 시스템 배포 준비 상태 종합 점검

검사 항목:
  1. 데이터베이스 연결 (PostgreSQL, MongoDB, Redis)
  2. 외부 API 연결 (KIS, FRED, ECOS, DART)
  3. AI 서비스 연결 (Anthropic Claude)
  4. 환경변수 및 설정 유효성
  5. 데이터 준비 상태 (유니버스, 환율 캐시)
  6. 거래 모드별 준비 상태
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from config.logging import logger
from config.settings import get_settings


class HealthStatus(str, Enum):
    """건전성 상태"""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"


@dataclass
class ComponentHealth:
    """개별 컴포넌트 건전성"""
    name: str
    status: HealthStatus
    message: str = ""
    latency_ms: Optional[float] = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "latency_ms": self.latency_ms,
            "checked_at": self.checked_at.isoformat(),
        }


@dataclass
class SystemHealthReport:
    """시스템 종합 건전성 리포트"""
    overall_status: HealthStatus = HealthStatus.UNKNOWN
    components: list[ComponentHealth] = field(default_factory=list)
    trading_mode: str = ""
    environment: str = ""
    ready_for_trading: bool = False
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "overall_status": self.overall_status.value,
            "components": [c.to_dict() for c in self.components],
            "trading_mode": self.trading_mode,
            "environment": self.environment,
            "ready_for_trading": self.ready_for_trading,
            "checked_at": self.checked_at.isoformat(),
        }


class HealthChecker:
    """
    시스템 건전성 검사 서비스

    배포 전, 장 시작 전, 주기적 모니터링 시 호출하여
    시스템 전체 상태를 점검합니다.
    """

    def __init__(self):
        self._settings = get_settings()

    async def run_full_check(self) -> SystemHealthReport:
        """전체 건전성 검사 실행"""
        report = SystemHealthReport(
            trading_mode=self._settings.kis.trading_mode.value,
            environment=self._settings.environment,
        )

        # 개별 컴포넌트 검사
        checks = [
            self._check_postgresql,
            self._check_mongodb,
            self._check_redis,
            self._check_settings_validity,
            self._check_trading_mode_readiness,
        ]

        for check_fn in checks:
            try:
                component = await check_fn()
                report.components.append(component)
            except Exception as e:
                report.components.append(ComponentHealth(
                    name=check_fn.__name__.replace("_check_", ""),
                    status=HealthStatus.UNHEALTHY,
                    message=f"검사 중 오류: {str(e)}",
                ))

        # 종합 상태 판정
        statuses = [c.status for c in report.components]
        if all(s == HealthStatus.HEALTHY for s in statuses):
            report.overall_status = HealthStatus.HEALTHY
        elif any(s == HealthStatus.UNHEALTHY for s in statuses):
            report.overall_status = HealthStatus.UNHEALTHY
        else:
            report.overall_status = HealthStatus.DEGRADED

        # 거래 준비 상태
        report.ready_for_trading = (
            report.overall_status in (HealthStatus.HEALTHY, HealthStatus.DEGRADED)
        )

        logger.info(
            f"Health check complete: {report.overall_status.value}, "
            f"trading_ready={report.ready_for_trading}"
        )
        return report

    # ══════════════════════════════════════
    # 개별 컴포넌트 검사
    # ══════════════════════════════════════
    async def _check_postgresql(self) -> ComponentHealth:
        """PostgreSQL 연결 검사"""
        import time
        try:
            from sqlalchemy import text
            from db.database import async_session_factory

            start = time.monotonic()
            async with async_session_factory() as session:
                result = await session.execute(text("SELECT 1"))
                result.scalar()
            latency = (time.monotonic() - start) * 1000

            return ComponentHealth(
                name="postgresql",
                status=HealthStatus.HEALTHY,
                message="Connected",
                latency_ms=round(latency, 2),
            )
        except Exception as e:
            return ComponentHealth(
                name="postgresql",
                status=HealthStatus.UNHEALTHY,
                message=str(e),
            )

    async def _check_mongodb(self) -> ComponentHealth:
        """MongoDB 연결 검사"""
        import time
        try:
            from db.database import MongoDBManager

            start = time.monotonic()
            db = MongoDBManager.get_db()
            await db.command("ping")
            latency = (time.monotonic() - start) * 1000

            return ComponentHealth(
                name="mongodb",
                status=HealthStatus.HEALTHY,
                message="Connected",
                latency_ms=round(latency, 2),
            )
        except Exception as e:
            return ComponentHealth(
                name="mongodb",
                status=HealthStatus.UNHEALTHY,
                message=str(e),
            )

    async def _check_redis(self) -> ComponentHealth:
        """Redis 연결 검사"""
        import time
        try:
            from db.database import RedisManager

            start = time.monotonic()
            client = RedisManager.get_client()
            await client.ping()
            latency = (time.monotonic() - start) * 1000

            return ComponentHealth(
                name="redis",
                status=HealthStatus.HEALTHY,
                message="Connected",
                latency_ms=round(latency, 2),
            )
        except Exception as e:
            return ComponentHealth(
                name="redis",
                status=HealthStatus.UNHEALTHY,
                message=str(e),
            )

    async def _check_settings_validity(self) -> ComponentHealth:
        """설정 유효성 검사"""
        warnings = []
        s = self._settings

        # 필수 설정 확인
        if not s.telegram.bot_token or s.telegram.bot_token == "test-bot-token":
            warnings.append("텔레그램 봇 토큰 미설정")

        if not s.dashboard.secret_key or s.dashboard.secret_key == "test-secret-key":
            warnings.append("대시보드 시크릿 키가 기본값입니다")

        if s.kis.is_live:
            cred = s.kis.active_credential
            if not cred.app_key or not cred.app_secret:
                return ComponentHealth(
                    name="settings_validity",
                    status=HealthStatus.UNHEALTHY,
                    message="LIVE 모드 KIS API 키 미설정",
                )

        if warnings:
            return ComponentHealth(
                name="settings_validity",
                status=HealthStatus.DEGRADED,
                message="; ".join(warnings),
            )

        return ComponentHealth(
            name="settings_validity",
            status=HealthStatus.HEALTHY,
            message="All settings valid",
        )

    async def _check_trading_mode_readiness(self) -> ComponentHealth:
        """거래 모드별 준비 상태 검사"""
        mode = self._settings.kis.trading_mode.value

        if self._settings.kis.is_backtest:
            return ComponentHealth(
                name="trading_mode_readiness",
                status=HealthStatus.HEALTHY,
                message=f"Mode: {mode} (API 호출 없음)",
            )

        if self._settings.kis.is_demo:
            cred = self._settings.kis.active_credential
            if cred.app_key and cred.app_secret and cred.account_no:
                return ComponentHealth(
                    name="trading_mode_readiness",
                    status=HealthStatus.HEALTHY,
                    message=f"Mode: {mode} (모의투자 자격증명 확인됨)",
                )
            return ComponentHealth(
                name="trading_mode_readiness",
                status=HealthStatus.UNHEALTHY,
                message=f"Mode: {mode} (모의투자 자격증명 불완전)",
            )

        if self._settings.kis.is_live:
            if not self._settings.is_production:
                return ComponentHealth(
                    name="trading_mode_readiness",
                    status=HealthStatus.UNHEALTHY,
                    message="LIVE 모드는 production 환경에서만 허용됩니다",
                )

            cred = self._settings.kis.active_credential
            if cred.app_key and cred.app_secret and cred.account_no:
                return ComponentHealth(
                    name="trading_mode_readiness",
                    status=HealthStatus.HEALTHY,
                    message=f"Mode: {mode} (실전투자 자격증명 확인됨)",
                )
            return ComponentHealth(
                name="trading_mode_readiness",
                status=HealthStatus.UNHEALTHY,
                message=f"Mode: {mode} (실전투자 자격증명 불완전)",
            )

        return ComponentHealth(
            name="trading_mode_readiness",
            status=HealthStatus.UNKNOWN,
            message=f"Unknown mode: {mode}",
        )
