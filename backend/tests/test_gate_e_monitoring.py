"""
Gate E 모니터링 대시보드 테스트

테스트 범위:
  - MetricSnapshot: 스냅샷 생성, 임계값 심각도 판정
  - DashboardMetrics: 이력 기록, 조회, 집계 (평균/최대/최소)
  - ServiceHealthEntry: 서비스 상태 기록
  - DashboardAlert: 알림 생성, 확인 처리
  - SystemOverview: 시스템 개요, 업타임 표시
  - DashboardData: 전체 데이터 집계
  - MonitoringDashboard: 종합 대시보드 통합 테스트
"""

from datetime import datetime, timedelta, timezone

import pytest

from core.monitoring.dashboard import (
    AlertSeverity,
    DashboardAlert,
    DashboardData,
    DashboardMetrics,
    MetricSnapshot,
    MonitoringDashboard,
    ServiceHealthEntry,
    ServiceStatus,
    SystemOverview,
)


# ============================================================
# MetricSnapshot 테스트
# ============================================================
class TestMetricSnapshot:
    def test_basic_snapshot(self):
        s = MetricSnapshot(name="cpu_usage", value=45.0, unit="%")
        assert s.name == "cpu_usage"
        assert s.value == 45.0
        assert s.unit == "%"
        assert s.severity == AlertSeverity.INFO

    def test_warning_threshold(self):
        s = MetricSnapshot(name="latency", value=250.0, unit="ms", threshold_warning=200.0, threshold_critical=500.0)
        assert s.severity == AlertSeverity.WARNING

    def test_critical_threshold(self):
        s = MetricSnapshot(name="error_rate", value=10.0, unit="%", threshold_warning=5.0, threshold_critical=8.0)
        assert s.severity == AlertSeverity.CRITICAL

    def test_below_warning_is_info(self):
        s = MetricSnapshot(name="mem", value=30.0, unit="%", threshold_warning=60.0, threshold_critical=90.0)
        assert s.severity == AlertSeverity.INFO

    def test_to_dict(self):
        s = MetricSnapshot(name="cpu", value=50.0, unit="%")
        d = s.to_dict()
        assert d["name"] == "cpu"
        assert d["value"] == 50.0
        assert d["unit"] == "%"
        assert d["severity"] == "INFO"
        assert "timestamp" in d

    def test_no_threshold_always_info(self):
        s = MetricSnapshot(name="count", value=9999.0)
        assert s.severity == AlertSeverity.INFO


# ============================================================
# DashboardMetrics 테스트
# ============================================================
class TestDashboardMetrics:
    def test_record_and_get_latest(self):
        dm = DashboardMetrics()
        dm.record(MetricSnapshot(name="cpu", value=10.0))
        dm.record(MetricSnapshot(name="cpu", value=20.0))
        latest = dm.get_latest("cpu")
        assert latest is not None
        assert latest.value == 20.0

    def test_get_latest_nonexistent(self):
        dm = DashboardMetrics()
        assert dm.get_latest("missing") is None

    def test_get_history_filters_by_time(self):
        dm = DashboardMetrics()
        old = MetricSnapshot(name="mem", value=30.0, timestamp=datetime.now(timezone.utc) - timedelta(hours=2))
        recent = MetricSnapshot(name="mem", value=60.0, timestamp=datetime.now(timezone.utc) - timedelta(minutes=5))
        dm.record(old)
        dm.record(recent)
        history = dm.get_history("mem", minutes=60)
        assert len(history) == 1
        assert history[0].value == 60.0

    def test_get_average(self):
        dm = DashboardMetrics()
        now = datetime.now(timezone.utc)
        for v in [10.0, 20.0, 30.0]:
            dm.record(MetricSnapshot(name="test", value=v, timestamp=now))
        avg = dm.get_average("test", minutes=60)
        assert avg == pytest.approx(20.0)

    def test_get_max_min(self):
        dm = DashboardMetrics()
        now = datetime.now(timezone.utc)
        for v in [5.0, 15.0, 10.0]:
            dm.record(MetricSnapshot(name="val", value=v, timestamp=now))
        assert dm.get_max("val") == 15.0
        assert dm.get_min("val") == 5.0

    def test_max_history_eviction(self):
        dm = DashboardMetrics(max_history=3)
        for i in range(5):
            dm.record(MetricSnapshot(name="x", value=float(i)))
        # 최대 3개만 유지
        history = dm._history["x"]
        assert len(history) == 3
        assert history[0].value == 2.0  # 0, 1 제거됨

    def test_list_metrics(self):
        dm = DashboardMetrics()
        dm.record(MetricSnapshot(name="b", value=1.0))
        dm.record(MetricSnapshot(name="a", value=2.0))
        assert dm.list_metrics() == ["a", "b"]

    def test_get_all_latest(self):
        dm = DashboardMetrics()
        dm.record(MetricSnapshot(name="cpu", value=10.0))
        dm.record(MetricSnapshot(name="mem", value=20.0))
        dm.record(MetricSnapshot(name="cpu", value=30.0))
        all_latest = dm.get_all_latest()
        assert len(all_latest) == 2
        names = [s.name for s in all_latest]
        assert "cpu" in names
        assert "mem" in names

    def test_clear_specific(self):
        dm = DashboardMetrics()
        dm.record(MetricSnapshot(name="a", value=1.0))
        dm.record(MetricSnapshot(name="b", value=2.0))
        dm.clear("a")
        assert dm.get_latest("a") is None
        assert dm.get_latest("b") is not None

    def test_clear_all(self):
        dm = DashboardMetrics()
        dm.record(MetricSnapshot(name="a", value=1.0))
        dm.record(MetricSnapshot(name="b", value=2.0))
        dm.clear()
        assert dm.list_metrics() == []

    def test_average_nonexistent(self):
        dm = DashboardMetrics()
        assert dm.get_average("missing") is None
        assert dm.get_max("missing") is None
        assert dm.get_min("missing") is None


# ============================================================
# ServiceHealthEntry 테스트
# ============================================================
class TestServiceHealthEntry:
    def test_basic_entry(self):
        entry = ServiceHealthEntry(name="postgresql", status=ServiceStatus.ONLINE, latency_ms=5.2)
        assert entry.name == "postgresql"
        assert entry.status == ServiceStatus.ONLINE

    def test_to_dict_with_latency(self):
        entry = ServiceHealthEntry(name="redis", status=ServiceStatus.DEGRADED, latency_ms=150.0, message="slow")
        d = entry.to_dict()
        assert d["name"] == "redis"
        assert d["status"] == "DEGRADED"
        assert d["latency_ms"] == 150.0
        assert d["message"] == "slow"

    def test_to_dict_without_optional(self):
        entry = ServiceHealthEntry(name="kis", status=ServiceStatus.OFFLINE)
        d = entry.to_dict()
        assert "latency_ms" not in d
        assert "circuit_state" not in d

    def test_to_dict_with_circuit_state(self):
        entry = ServiceHealthEntry(name="kis", status=ServiceStatus.OFFLINE, circuit_state="OPEN")
        d = entry.to_dict()
        assert d["circuit_state"] == "OPEN"


# ============================================================
# DashboardAlert 테스트
# ============================================================
class TestDashboardAlert:
    def test_basic_alert(self):
        alert = DashboardAlert(severity=AlertSeverity.ERROR, title="DB Down", message="PostgreSQL 연결 실패")
        assert alert.severity == AlertSeverity.ERROR
        assert alert.acknowledged is False

    def test_to_dict(self):
        alert = DashboardAlert(
            severity=AlertSeverity.CRITICAL,
            title="Kill Switch",
            message="긴급 매매 중단",
            source="trading_guard",
        )
        d = alert.to_dict()
        assert d["severity"] == "CRITICAL"
        assert d["title"] == "Kill Switch"
        assert d["source"] == "trading_guard"
        assert d["acknowledged"] is False


# ============================================================
# SystemOverview 테스트
# ============================================================
class TestSystemOverview:
    def test_default_overview(self):
        overview = SystemOverview()
        assert overview.overall_status == ServiceStatus.ONLINE
        assert overview.trading_mode == "BACKTEST"
        assert overview.pipeline_state == "IDLE"

    def test_uptime_display_hours(self):
        overview = SystemOverview(uptime_seconds=7260)  # 2h 1m
        assert overview.uptime_display == "2h 1m"

    def test_uptime_display_days(self):
        overview = SystemOverview(uptime_seconds=90060)  # 25h 1m → 1d 1h 1m
        assert overview.uptime_display == "1d 1h 1m"

    def test_to_dict(self):
        overview = SystemOverview(
            trading_mode="LIVE",
            total_positions=5,
            daily_pnl_percent=1.5,
            portfolio_value=100_000_000.0,
        )
        d = overview.to_dict()
        assert d["trading_mode"] == "LIVE"
        assert d["total_positions"] == 5
        assert d["daily_pnl_percent"] == 1.5
        assert d["portfolio_value"] == 100_000_000.0


# ============================================================
# DashboardData 테스트
# ============================================================
class TestDashboardData:
    def test_empty_data(self):
        data = DashboardData()
        assert data.active_alerts_count == 0
        assert data.critical_alerts_count == 0

    def test_alert_counts(self):
        alerts = [
            DashboardAlert(severity=AlertSeverity.CRITICAL, title="a", message="x"),
            DashboardAlert(severity=AlertSeverity.WARNING, title="b", message="y"),
            DashboardAlert(severity=AlertSeverity.CRITICAL, title="c", message="z", acknowledged=True),
        ]
        data = DashboardData(alerts=alerts)
        assert data.active_alerts_count == 2  # a, b (c acknowledged)
        assert data.critical_alerts_count == 1  # only a (c acknowledged)

    def test_to_dict(self):
        data = DashboardData()
        d = data.to_dict()
        assert "overview" in d
        assert "services" in d
        assert "metrics" in d
        assert "alerts" in d
        assert d["active_alerts"] == 0
        assert d["critical_alerts"] == 0


# ============================================================
# MonitoringDashboard 통합 테스트
# ============================================================
class TestMonitoringDashboard:
    def test_update_service(self):
        dash = MonitoringDashboard()
        entry = dash.update_service("postgresql", ServiceStatus.ONLINE, latency_ms=3.5)
        assert entry.name == "postgresql"
        assert entry.status == ServiceStatus.ONLINE

    def test_update_service_overwrite(self):
        dash = MonitoringDashboard()
        dash.update_service("redis", ServiceStatus.ONLINE)
        dash.update_service("redis", ServiceStatus.DEGRADED, message="slow")
        summary = dash.get_service_summary()
        assert summary["services"]["redis"]["status"] == "DEGRADED"

    def test_record_metric(self):
        dash = MonitoringDashboard()
        snap = dash.record_metric("cpu_usage", 45.0, unit="%")
        assert snap.value == 45.0
        assert dash.metrics.get_latest("cpu_usage").value == 45.0

    def test_record_metric_auto_alert_critical(self):
        dash = MonitoringDashboard()
        dash.record_metric("error_rate", 12.0, unit="%", threshold_warning=5.0, threshold_critical=10.0)
        alerts = dash.get_active_alerts()
        assert len(alerts) == 1
        assert alerts[0].severity == AlertSeverity.CRITICAL

    def test_record_metric_auto_alert_warning(self):
        dash = MonitoringDashboard()
        dash.record_metric("latency", 250.0, unit="ms", threshold_warning=200.0, threshold_critical=500.0)
        alerts = dash.get_active_alerts()
        assert len(alerts) == 1
        assert alerts[0].severity == AlertSeverity.WARNING

    def test_record_metric_no_alert_below_threshold(self):
        dash = MonitoringDashboard()
        dash.record_metric("cpu", 30.0, unit="%", threshold_warning=60.0, threshold_critical=90.0)
        assert len(dash.get_active_alerts()) == 0

    def test_add_alert(self):
        dash = MonitoringDashboard()
        alert = dash.add_alert(AlertSeverity.ERROR, "DB Error", "PostgreSQL timeout", source="health_checker")
        assert alert.severity == AlertSeverity.ERROR
        assert alert.source == "health_checker"

    def test_acknowledge_alert(self):
        dash = MonitoringDashboard()
        dash.add_alert(AlertSeverity.WARNING, "Test", "msg")
        assert len(dash.get_active_alerts()) == 1
        result = dash.acknowledge_alert(0)
        assert result is True
        assert len(dash.get_active_alerts()) == 0

    def test_acknowledge_invalid_index(self):
        dash = MonitoringDashboard()
        assert dash.acknowledge_alert(99) is False

    def test_overall_status_all_online(self):
        dash = MonitoringDashboard()
        dash.update_service("pg", ServiceStatus.ONLINE)
        dash.update_service("redis", ServiceStatus.ONLINE)
        overview = dash.build_overview()
        assert overview.overall_status == ServiceStatus.ONLINE

    def test_overall_status_degraded(self):
        dash = MonitoringDashboard()
        dash.update_service("pg", ServiceStatus.ONLINE)
        dash.update_service("redis", ServiceStatus.DEGRADED)
        overview = dash.build_overview()
        assert overview.overall_status == ServiceStatus.DEGRADED

    def test_overall_status_offline(self):
        dash = MonitoringDashboard()
        dash.update_service("pg", ServiceStatus.ONLINE)
        dash.update_service("kis", ServiceStatus.OFFLINE)
        overview = dash.build_overview()
        assert overview.overall_status == ServiceStatus.OFFLINE

    def test_overall_status_no_services(self):
        dash = MonitoringDashboard()
        overview = dash.build_overview()
        assert overview.overall_status == ServiceStatus.ONLINE

    def test_generate_dashboard(self):
        dash = MonitoringDashboard()
        dash.update_service("pg", ServiceStatus.ONLINE, latency_ms=5.0)
        dash.record_metric("cpu", 40.0, unit="%")
        dash.add_alert(AlertSeverity.INFO, "System start", "Boot complete")
        data = dash.generate_dashboard(trading_mode="DEMO", total_positions=3, portfolio_value=50_000_000.0)
        assert isinstance(data, DashboardData)
        assert data.overview.trading_mode == "DEMO"
        assert data.overview.total_positions == 3
        assert len(data.services) == 1
        assert len(data.metrics) == 1
        assert len(data.alerts) == 1

    def test_generate_dashboard_to_dict(self):
        dash = MonitoringDashboard()
        data = dash.generate_dashboard()
        d = data.to_dict()
        assert isinstance(d, dict)
        assert "overview" in d
        assert "generated_at" in d

    def test_service_summary(self):
        dash = MonitoringDashboard()
        dash.update_service("pg", ServiceStatus.ONLINE)
        dash.update_service("redis", ServiceStatus.ONLINE)
        dash.update_service("kis", ServiceStatus.OFFLINE)
        summary = dash.get_service_summary()
        assert summary["total"] == 3
        assert summary["by_status"]["ONLINE"] == 2
        assert summary["by_status"]["OFFLINE"] == 1

    def test_reset(self):
        dash = MonitoringDashboard()
        dash.update_service("pg", ServiceStatus.ONLINE)
        dash.record_metric("cpu", 50.0)
        dash.add_alert(AlertSeverity.INFO, "test", "msg")
        dash.reset()
        assert dash.get_service_summary()["total"] == 0
        assert dash.metrics.list_metrics() == []
        assert len(dash.get_active_alerts()) == 0

    def test_uptime_increases(self):
        dash = MonitoringDashboard()
        overview = dash.build_overview()
        assert overview.uptime_seconds >= 0

    def test_max_alerts_eviction(self):
        dash = MonitoringDashboard()
        dash._max_alerts = 5
        for i in range(10):
            dash.add_alert(AlertSeverity.INFO, f"alert_{i}", f"msg_{i}")
        assert len(dash._alerts) == 5
        assert dash._alerts[0].title == "alert_5"  # 0~4 제거됨


# ============================================================
# 통합 시나리오 테스트
# ============================================================
class TestMonitoringIntegration:
    def test_full_dashboard_lifecycle(self):
        """전체 대시보드 라이프사이클: 서비스 등록 → 메트릭 기록 → 알림 → 대시보드 생성"""
        dash = MonitoringDashboard()

        # 서비스 등록
        dash.update_service("postgresql", ServiceStatus.ONLINE, latency_ms=3.0)
        dash.update_service("mongodb", ServiceStatus.ONLINE, latency_ms=5.0)
        dash.update_service("redis", ServiceStatus.ONLINE, latency_ms=1.0)
        dash.update_service("kis_api", ServiceStatus.ONLINE, circuit_state="CLOSED")

        # 메트릭 기록
        dash.record_metric("api_latency_p95", 150.0, unit="ms", threshold_warning=200.0, threshold_critical=500.0)
        dash.record_metric("error_rate", 0.5, unit="%", threshold_warning=3.0, threshold_critical=5.0)
        dash.record_metric("active_positions", 8.0)
        dash.record_metric("daily_pnl", 1.2, unit="%")

        # 알림 없어야 함 (임계값 미달)
        assert len(dash.get_active_alerts()) == 0

        # 대시보드 생성
        data = dash.generate_dashboard(
            trading_mode="LIVE",
            pipeline_state="RUNNING",
            total_positions=8,
            daily_pnl_percent=1.2,
            portfolio_value=100_000_000.0,
        )

        assert data.overview.overall_status == ServiceStatus.ONLINE
        assert data.overview.trading_mode == "LIVE"
        assert len(data.services) == 4
        assert len(data.metrics) == 4
        assert data.active_alerts_count == 0

    def test_degraded_scenario(self):
        """서비스 장애 시나리오: KIS API 장애 → 서킷 오픈 → 알림 발생"""
        dash = MonitoringDashboard()

        dash.update_service("postgresql", ServiceStatus.ONLINE)
        dash.update_service("kis_api", ServiceStatus.OFFLINE, circuit_state="OPEN", message="Connection timeout")

        dash.add_alert(
            AlertSeverity.CRITICAL,
            "KIS API 연결 장애",
            "5회 연속 실패, 서킷 브레이커 OPEN",
            source="circuit_breaker",
        )

        data = dash.generate_dashboard(trading_mode="LIVE", pipeline_state="HALTED")

        assert data.overview.overall_status == ServiceStatus.OFFLINE
        assert data.overview.pipeline_state == "HALTED"
        assert data.critical_alerts_count == 1

    def test_metric_threshold_triggers_alert(self):
        """메트릭 임계값 초과 시 자동 알림 발생"""
        dash = MonitoringDashboard()

        # 정상 범위
        dash.record_metric("error_rate", 2.0, unit="%", threshold_warning=5.0, threshold_critical=10.0)
        assert len(dash.get_active_alerts()) == 0

        # 경고 범위
        dash.record_metric("error_rate", 7.0, unit="%", threshold_warning=5.0, threshold_critical=10.0)
        assert len(dash.get_active_alerts()) == 1

        # 위험 범위
        dash.record_metric("error_rate", 15.0, unit="%", threshold_warning=5.0, threshold_critical=10.0)
        assert len(dash.get_active_alerts()) == 2

    def test_dashboard_data_serialization(self):
        """DashboardData → dict 직렬화 완전성 검증"""
        dash = MonitoringDashboard()
        dash.update_service("pg", ServiceStatus.ONLINE, latency_ms=5.0)
        dash.record_metric("cpu", 45.0, unit="%")
        dash.add_alert(AlertSeverity.WARNING, "High CPU", "CPU 사용률 주의")

        data = dash.generate_dashboard(trading_mode="DEMO")
        d = data.to_dict()

        # 최상위 키 검증
        assert set(d.keys()) == {
            "overview",
            "services",
            "metrics",
            "alerts",
            "active_alerts",
            "critical_alerts",
            "generated_at",
        }

        # 서비스 직렬화
        assert len(d["services"]) == 1
        assert d["services"][0]["name"] == "pg"

        # 메트릭 직렬화
        assert len(d["metrics"]) == 1
        assert d["metrics"][0]["name"] == "cpu"

        # 알림 직렬화
        assert len(d["alerts"]) == 1
        assert d["alerts"][0]["severity"] == "WARNING"
