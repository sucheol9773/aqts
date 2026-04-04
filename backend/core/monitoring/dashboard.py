"""
모니터링 대시보드 (Monitoring Dashboard)

Gate E 요건: 핵심 지표 실시간 확인 가능

주요 기능:
  1. 시스템 상태 종합 개요 (SystemOverview)
  2. 서비스별 상태 추적 (ServiceStatus)
  3. 핵심 메트릭 스냅샷 (MetricSnapshot)
  4. 대시보드 데이터 집계 (DashboardData)
  5. 메트릭 이력 관리 (DashboardMetrics)

통합 대상:
  - HealthChecker: DB/API 연결 상태
  - CircuitBreaker: 외부 서비스 차단 상태
  - TradingGuard: 매매 안전 장치 상태
  - ComplianceReport: 컴플라이언스 등급
  - EmergencyMonitor: 비상 모니터링 상태
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional


class ServiceStatus(str, Enum):
    """서비스 상태"""

    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"
    MAINTENANCE = "MAINTENANCE"


class AlertSeverity(str, Enum):
    """알림 심각도"""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass
class MetricSnapshot:
    """단일 메트릭 스냅샷"""

    name: str
    value: float
    unit: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    threshold_warning: Optional[float] = None
    threshold_critical: Optional[float] = None

    @property
    def severity(self) -> AlertSeverity:
        """임계값 기반 심각도 판정"""
        if self.threshold_critical is not None and self.value >= self.threshold_critical:
            return AlertSeverity.CRITICAL
        if self.threshold_warning is not None and self.value >= self.threshold_warning:
            return AlertSeverity.WARNING
        return AlertSeverity.INFO

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "timestamp": self.timestamp.isoformat(),
            "severity": self.severity.value,
        }


@dataclass
class ServiceHealthEntry:
    """개별 서비스 건전성"""

    name: str
    status: ServiceStatus
    latency_ms: Optional[float] = None
    last_checked: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    message: str = ""
    circuit_state: str = ""  # CLOSED/OPEN/HALF_OPEN

    def to_dict(self) -> dict:
        result = {
            "name": self.name,
            "status": self.status.value,
            "last_checked": self.last_checked.isoformat(),
            "message": self.message,
        }
        if self.latency_ms is not None:
            result["latency_ms"] = self.latency_ms
        if self.circuit_state:
            result["circuit_state"] = self.circuit_state
        return result


@dataclass
class DashboardAlert:
    """대시보드 알림"""

    severity: AlertSeverity
    title: str
    message: str
    source: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    acknowledged: bool = False

    def to_dict(self) -> dict:
        return {
            "severity": self.severity.value,
            "title": self.title,
            "message": self.message,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
            "acknowledged": self.acknowledged,
        }


@dataclass
class SystemOverview:
    """시스템 종합 개요"""

    overall_status: ServiceStatus = ServiceStatus.ONLINE
    trading_mode: str = "BACKTEST"
    pipeline_state: str = "IDLE"
    total_positions: int = 0
    daily_pnl_percent: float = 0.0
    portfolio_value: float = 0.0
    uptime_seconds: float = 0.0
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def uptime_display(self) -> str:
        """사람이 읽기 쉬운 업타임 표시"""
        hours = int(self.uptime_seconds // 3600)
        minutes = int((self.uptime_seconds % 3600) // 60)
        if hours > 24:
            days = hours // 24
            hours = hours % 24
            return f"{days}d {hours}h {minutes}m"
        return f"{hours}h {minutes}m"

    def to_dict(self) -> dict:
        return {
            "overall_status": self.overall_status.value,
            "trading_mode": self.trading_mode,
            "pipeline_state": self.pipeline_state,
            "total_positions": self.total_positions,
            "daily_pnl_percent": self.daily_pnl_percent,
            "portfolio_value": self.portfolio_value,
            "uptime": self.uptime_display,
            "last_updated": self.last_updated.isoformat(),
        }


@dataclass
class DashboardData:
    """대시보드 전체 데이터"""

    overview: SystemOverview = field(default_factory=SystemOverview)
    services: list[ServiceHealthEntry] = field(default_factory=list)
    metrics: list[MetricSnapshot] = field(default_factory=list)
    alerts: list[DashboardAlert] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def active_alerts_count(self) -> int:
        return sum(1 for a in self.alerts if not a.acknowledged)

    @property
    def critical_alerts_count(self) -> int:
        return sum(1 for a in self.alerts if a.severity == AlertSeverity.CRITICAL and not a.acknowledged)

    def to_dict(self) -> dict:
        return {
            "overview": self.overview.to_dict(),
            "services": [s.to_dict() for s in self.services],
            "metrics": [m.to_dict() for m in self.metrics],
            "alerts": [a.to_dict() for a in self.alerts],
            "active_alerts": self.active_alerts_count,
            "critical_alerts": self.critical_alerts_count,
            "generated_at": self.generated_at.isoformat(),
        }


class DashboardMetrics:
    """메트릭 이력 관리 및 집계"""

    def __init__(self, max_history: int = 1440):
        """
        Args:
            max_history: 메트릭당 최대 보존 개수 (기본 1440 = 1분 간격 24시간)
        """
        self._history: dict[str, list[MetricSnapshot]] = {}
        self._max_history = max_history

    def record(self, snapshot: MetricSnapshot) -> None:
        """메트릭 기록"""
        if snapshot.name not in self._history:
            self._history[snapshot.name] = []

        history = self._history[snapshot.name]
        history.append(snapshot)

        # 최대 보존 개수 초과 시 오래된 항목 제거
        if len(history) > self._max_history:
            self._history[snapshot.name] = history[-self._max_history :]

    def get_latest(self, name: str) -> Optional[MetricSnapshot]:
        """특정 메트릭의 최신값"""
        history = self._history.get(name, [])
        return history[-1] if history else None

    def get_history(self, name: str, minutes: int = 60) -> list[MetricSnapshot]:
        """특정 메트릭의 최근 N분 이력"""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        history = self._history.get(name, [])
        return [s for s in history if s.timestamp >= cutoff]

    def get_average(self, name: str, minutes: int = 60) -> Optional[float]:
        """특정 메트릭의 최근 N분 평균"""
        recent = self.get_history(name, minutes)
        if not recent:
            return None
        return sum(s.value for s in recent) / len(recent)

    def get_max(self, name: str, minutes: int = 60) -> Optional[float]:
        """특정 메트릭의 최근 N분 최대값"""
        recent = self.get_history(name, minutes)
        if not recent:
            return None
        return max(s.value for s in recent)

    def get_min(self, name: str, minutes: int = 60) -> Optional[float]:
        """특정 메트릭의 최근 N분 최소값"""
        recent = self.get_history(name, minutes)
        if not recent:
            return None
        return min(s.value for s in recent)

    def list_metrics(self) -> list[str]:
        """등록된 메트릭 이름 목록"""
        return sorted(self._history.keys())

    def get_all_latest(self) -> list[MetricSnapshot]:
        """모든 메트릭의 최신값 목록"""
        result = []
        for name in sorted(self._history.keys()):
            latest = self.get_latest(name)
            if latest:
                result.append(latest)
        return result

    def clear(self, name: Optional[str] = None) -> None:
        """메트릭 이력 초기화"""
        if name:
            self._history.pop(name, None)
        else:
            self._history.clear()


class MonitoringDashboard:
    """
    모니터링 대시보드 — 전체 시스템 상태 집계

    HealthChecker, CircuitBreaker, TradingGuard 등의 상태를
    단일 DashboardData로 집계하여 API/UI에 제공합니다.
    """

    def __init__(self) -> None:
        self._metrics = DashboardMetrics()
        self._alerts: list[DashboardAlert] = []
        self._max_alerts = 500
        self._start_time = datetime.now(timezone.utc)
        self._services: dict[str, ServiceHealthEntry] = {}

    @property
    def metrics(self) -> DashboardMetrics:
        return self._metrics

    def update_service(
        self,
        name: str,
        status: ServiceStatus,
        latency_ms: Optional[float] = None,
        message: str = "",
        circuit_state: str = "",
    ) -> ServiceHealthEntry:
        """서비스 상태 업데이트"""
        entry = ServiceHealthEntry(
            name=name,
            status=status,
            latency_ms=latency_ms,
            message=message,
            circuit_state=circuit_state,
        )
        self._services[name] = entry
        return entry

    def record_metric(
        self,
        name: str,
        value: float,
        unit: str = "",
        threshold_warning: Optional[float] = None,
        threshold_critical: Optional[float] = None,
    ) -> MetricSnapshot:
        """메트릭 기록"""
        snapshot = MetricSnapshot(
            name=name,
            value=value,
            unit=unit,
            threshold_warning=threshold_warning,
            threshold_critical=threshold_critical,
        )
        self._metrics.record(snapshot)

        # 임계값 초과 시 자동 알림 생성
        if snapshot.severity == AlertSeverity.CRITICAL:
            self.add_alert(
                severity=AlertSeverity.CRITICAL,
                title=f"{name} 임계값 초과",
                message=f"{name} = {value}{unit} (임계값: {threshold_critical}{unit})",
                source="metric_monitor",
            )
        elif snapshot.severity == AlertSeverity.WARNING:
            self.add_alert(
                severity=AlertSeverity.WARNING,
                title=f"{name} 주의",
                message=f"{name} = {value}{unit} (경고: {threshold_warning}{unit})",
                source="metric_monitor",
            )

        return snapshot

    def add_alert(
        self,
        severity: AlertSeverity,
        title: str,
        message: str,
        source: str = "",
    ) -> DashboardAlert:
        """알림 추가"""
        alert = DashboardAlert(
            severity=severity,
            title=title,
            message=message,
            source=source,
        )
        self._alerts.append(alert)

        # 최대 보존 개수 초과 시 오래된 항목 제거
        if len(self._alerts) > self._max_alerts:
            self._alerts = self._alerts[-self._max_alerts :]

        return alert

    def acknowledge_alert(self, index: int) -> bool:
        """알림 확인 처리"""
        if 0 <= index < len(self._alerts):
            self._alerts[index].acknowledged = True
            return True
        return False

    def get_active_alerts(self) -> list[DashboardAlert]:
        """미확인 알림 목록"""
        return [a for a in self._alerts if not a.acknowledged]

    def build_overview(
        self,
        trading_mode: str = "BACKTEST",
        pipeline_state: str = "IDLE",
        total_positions: int = 0,
        daily_pnl_percent: float = 0.0,
        portfolio_value: float = 0.0,
    ) -> SystemOverview:
        """시스템 개요 생성"""
        now = datetime.now(timezone.utc)
        uptime = (now - self._start_time).total_seconds()

        # 서비스 상태 기반 전체 상태 판정
        overall = self._determine_overall_status()

        return SystemOverview(
            overall_status=overall,
            trading_mode=trading_mode,
            pipeline_state=pipeline_state,
            total_positions=total_positions,
            daily_pnl_percent=daily_pnl_percent,
            portfolio_value=portfolio_value,
            uptime_seconds=uptime,
            last_updated=now,
        )

    def _determine_overall_status(self) -> ServiceStatus:
        """서비스 상태 기반 전체 상태 판정"""
        if not self._services:
            return ServiceStatus.ONLINE

        statuses = [s.status for s in self._services.values()]

        if any(s == ServiceStatus.OFFLINE for s in statuses):
            return ServiceStatus.OFFLINE
        if any(s == ServiceStatus.DEGRADED for s in statuses):
            return ServiceStatus.DEGRADED
        if any(s == ServiceStatus.MAINTENANCE for s in statuses):
            return ServiceStatus.MAINTENANCE
        return ServiceStatus.ONLINE

    def generate_dashboard(
        self,
        trading_mode: str = "BACKTEST",
        pipeline_state: str = "IDLE",
        total_positions: int = 0,
        daily_pnl_percent: float = 0.0,
        portfolio_value: float = 0.0,
    ) -> DashboardData:
        """전체 대시보드 데이터 생성"""
        overview = self.build_overview(
            trading_mode=trading_mode,
            pipeline_state=pipeline_state,
            total_positions=total_positions,
            daily_pnl_percent=daily_pnl_percent,
            portfolio_value=portfolio_value,
        )

        return DashboardData(
            overview=overview,
            services=list(self._services.values()),
            metrics=self._metrics.get_all_latest(),
            alerts=self._alerts[-50:],  # 최근 50개만 반환
        )

    def get_service_summary(self) -> dict[str, Any]:
        """서비스 상태 요약"""
        summary: dict[str, int] = {
            ServiceStatus.ONLINE.value: 0,
            ServiceStatus.DEGRADED.value: 0,
            ServiceStatus.OFFLINE.value: 0,
            ServiceStatus.MAINTENANCE.value: 0,
        }
        for entry in self._services.values():
            summary[entry.status.value] += 1

        return {
            "total": len(self._services),
            "by_status": summary,
            "services": {name: entry.to_dict() for name, entry in self._services.items()},
        }

    def reset(self) -> None:
        """대시보드 초기화"""
        self._metrics.clear()
        self._alerts.clear()
        self._services.clear()
        self._start_time = datetime.now(timezone.utc)
