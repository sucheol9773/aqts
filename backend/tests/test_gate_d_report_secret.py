"""
Gate D — 규제 준수 리포트 + 비밀키 관리 테스트

테스트 범위:
  1. ComplianceReportGenerator: 섹션 생성 (감사/보존/PII/리스크)
  2. ComplianceReport: 종합 리포트 생성, 등급 산출
  3. SecretManager: 등록/로테이션/폐기/건강검사
  4. SecretEntry: 만료/로테이션 주기 추적
  5. 통합: 전체 컴플라이언스 파이프라인
"""

from datetime import datetime, timedelta, timezone

import pytest

from core.compliance.compliance_report import (
    ComplianceGrade,
    ComplianceReportGenerator,
    ReportSection,
)
from core.compliance.secret_manager import (
    RotationRecord,
    SecretEntry,
    SecretEnvironment,
    SecretManager,
    SecretStatus,
    SecretType,
)


# ══════════════════════════════════════════════════════════════
# 1. ComplianceReportGenerator 섹션 생성
# ══════════════════════════════════════════════════════════════
class TestReportSections:
    """리포트 섹션 생성"""

    def test_audit_section_pass(self):
        """감사 섹션 — 무결성 통과"""
        gen = ComplianceReportGenerator()
        section = gen.generate_audit_report(
            integrity_result={"valid": True, "total_entries": 100},
            audit_stats={"by_module": {"order_executor": 50}, "by_action": {"ORDER_PLACED": 30}},
        )
        assert section.status == "PASS"
        assert section.metrics["total_entries"] == 100
        assert len(section.findings) == 0

    def test_audit_section_fail_invalid(self):
        """감사 섹션 — 무결성 실패"""
        gen = ComplianceReportGenerator()
        section = gen.generate_audit_report(
            integrity_result={"valid": False, "total_entries": 50, "broken_at_index": 10},
            audit_stats={},
        )
        assert section.status == "FAIL"
        assert any("무결성 위반" in f for f in section.findings)

    def test_audit_section_warning_empty(self):
        """감사 섹션 — 항목 없음 경고"""
        gen = ComplianceReportGenerator()
        section = gen.generate_audit_report(
            integrity_result={"valid": True, "total_entries": 0},
            audit_stats={},
        )
        assert section.status == "WARNING"

    def test_retention_section_pass(self):
        """보존 섹션 — 정상"""
        gen = ComplianceReportGenerator()
        section = gen.generate_retention_report(
            retention_stats={"total_records": 200, "pending_expiry": 0},
            violations=[],
        )
        assert section.status == "PASS"

    def test_retention_section_fail_violations(self):
        """보존 섹션 — 위반 감지"""
        gen = ComplianceReportGenerator()
        section = gen.generate_retention_report(
            retention_stats={"total_records": 200, "pending_expiry": 0},
            violations=[{"record_id": "r1", "violation": "PREMATURE_DELETION"}],
        )
        assert section.status == "FAIL"
        assert any("조기 삭제" in f for f in section.findings)

    def test_retention_section_warning_pending(self):
        """보존 섹션 — 만료 대기"""
        gen = ComplianceReportGenerator()
        section = gen.generate_retention_report(
            retention_stats={"total_records": 200, "pending_expiry": 5},
            violations=[],
        )
        assert section.status == "WARNING"

    def test_pii_section_pass(self):
        """PII 섹션 — 노출 없음"""
        gen = ComplianceReportGenerator()
        section = gen.generate_pii_report(pii_detections=0, settings_violations=[])
        assert section.status == "PASS"

    def test_pii_section_fail_detections(self):
        """PII 섹션 — 노출 감지"""
        gen = ComplianceReportGenerator()
        section = gen.generate_pii_report(
            pii_detections=3,
            settings_violations=[{"field": "password"}],
        )
        assert section.status == "FAIL"
        assert section.metrics["pii_detections"] == 3

    def test_risk_section_pass(self):
        """리스크 섹션 — 정상"""
        gen = ComplianceReportGenerator()
        section = gen.generate_risk_report()
        assert section.status == "PASS"

    def test_risk_section_fail_kill_switch(self):
        """리스크 섹션 — 킬 스위치 활성"""
        gen = ComplianceReportGenerator()
        section = gen.generate_risk_report(kill_switch_active=True)
        assert section.status == "FAIL"

    def test_risk_section_warning_daily_loss(self):
        """리스크 섹션 — 일일 손실 트리거"""
        gen = ComplianceReportGenerator()
        section = gen.generate_risk_report(daily_loss_triggered=True)
        assert section.status == "WARNING"


# ══════════════════════════════════════════════════════════════
# 2. ComplianceReport 종합 리포트
# ══════════════════════════════════════════════════════════════
class TestComplianceReport:
    """종합 리포트"""

    def test_compliant_grade(self):
        """모두 PASS → COMPLIANT"""
        gen = ComplianceReportGenerator()
        sections = [
            ReportSection(title="Audit", status="PASS"),
            ReportSection(title="Retention", status="PASS"),
            ReportSection(title="PII", status="PASS"),
        ]
        report = gen.generate_comprehensive_report("r-001", sections)
        assert report.overall_grade == ComplianceGrade.COMPLIANT
        assert report.pass_count == 3
        assert report.fail_count == 0

    def test_minor_issues_grade(self):
        """WARNING 포함 → MINOR_ISSUES"""
        gen = ComplianceReportGenerator()
        sections = [
            ReportSection(title="Audit", status="PASS"),
            ReportSection(title="Retention", status="WARNING"),
        ]
        report = gen.generate_comprehensive_report("r-002", sections)
        assert report.overall_grade == ComplianceGrade.MINOR_ISSUES
        assert report.warning_count == 1

    def test_non_compliant_grade(self):
        """FAIL 포함 → NON_COMPLIANT"""
        gen = ComplianceReportGenerator()
        sections = [
            ReportSection(title="Audit", status="FAIL"),
            ReportSection(title="Retention", status="PASS"),
        ]
        report = gen.generate_comprehensive_report("r-003", sections)
        assert report.overall_grade == ComplianceGrade.NON_COMPLIANT

    def test_report_to_dict(self):
        """리포트 직렬화"""
        gen = ComplianceReportGenerator()
        sections = [ReportSection(title="Test", status="PASS")]
        report = gen.generate_comprehensive_report("r-004", sections)
        d = report.to_dict()
        assert d["report_id"] == "r-004"
        assert d["report_type"] == "COMPREHENSIVE"
        assert d["overall_grade"] == "COMPLIANT"
        assert len(d["sections"]) == 1

    def test_report_with_period(self):
        """기간 지정 리포트"""
        gen = ComplianceReportGenerator()
        start = datetime(2026, 3, 1, tzinfo=timezone.utc)
        end = datetime(2026, 3, 31, tzinfo=timezone.utc)
        report = gen.generate_comprehensive_report(
            "r-005",
            [ReportSection(title="T", status="PASS")],
            period_start=start,
            period_end=end,
        )
        assert report.period_start == start
        assert report.period_end == end

    def test_get_latest_report(self):
        """최신 리포트 조회"""
        gen = ComplianceReportGenerator()
        gen.generate_comprehensive_report("r-1", [ReportSection(title="T", status="PASS")])
        gen.generate_comprehensive_report("r-2", [ReportSection(title="T", status="PASS")])
        latest = gen.get_latest_report()
        assert latest is not None
        assert latest.report_id == "r-2"

    def test_get_reports_history(self):
        """리포트 이력"""
        gen = ComplianceReportGenerator()
        for i in range(5):
            gen.generate_comprehensive_report(f"r-{i}", [ReportSection(title="T", status="PASS")])
        reports = gen.get_reports(limit=3)
        assert len(reports) == 3

    def test_empty_latest_report(self):
        """리포트 없을 때"""
        gen = ComplianceReportGenerator()
        assert gen.get_latest_report() is None

    def test_report_summary_text(self):
        """요약 텍스트 포함"""
        gen = ComplianceReportGenerator()
        report = gen.generate_comprehensive_report(
            "r-006",
            [ReportSection(title="A", status="PASS"), ReportSection(title="B", status="FAIL")],
        )
        assert "PASS 1" in report.summary
        assert "FAIL 1" in report.summary

    def test_report_section_to_dict(self):
        """섹션 직렬화"""
        section = ReportSection(title="Test", status="PASS", findings=["ok"], metrics={"count": 1})
        d = section.to_dict()
        assert d["title"] == "Test"
        assert d["findings"] == ["ok"]


# ══════════════════════════════════════════════════════════════
# 3. SecretManager 비밀키 관리
# ══════════════════════════════════════════════════════════════
class TestSecretManager:
    """비밀키 관리"""

    def test_register_secret(self):
        """시크릿 등록"""
        mgr = SecretManager()
        entry = mgr.register(
            name="KIS_APP_KEY",
            secret_type=SecretType.API_KEY,
            environment=SecretEnvironment.PRODUCTION,
        )
        assert entry.name == "KIS_APP_KEY"
        assert entry.version == 1
        assert entry.status == SecretStatus.ACTIVE
        assert mgr.count == 1

    def test_register_duplicate_raises(self):
        """중복 등록 차단"""
        mgr = SecretManager()
        mgr.register("KEY", SecretType.API_KEY, SecretEnvironment.DEMO)
        with pytest.raises(ValueError, match="already registered"):
            mgr.register("KEY", SecretType.API_KEY, SecretEnvironment.DEMO)

    def test_rotate_secret(self):
        """시크릿 로테이션"""
        mgr = SecretManager()
        mgr.register("DB_PASS", SecretType.DATABASE_PASSWORD, SecretEnvironment.PRODUCTION)
        entry = mgr.rotate("DB_PASS", reason="scheduled")
        assert entry is not None
        assert entry.version == 2
        assert entry.last_rotated_at is not None

    def test_rotate_nonexistent_returns_none(self):
        """존재하지 않는 시크릿 로테이션"""
        mgr = SecretManager()
        assert mgr.rotate("GHOST") is None

    def test_rotation_history(self):
        """로테이션 이력 추적"""
        mgr = SecretManager()
        mgr.register("KEY", SecretType.API_KEY, SecretEnvironment.PRODUCTION)
        mgr.rotate("KEY", reason="monthly")
        mgr.rotate("KEY", reason="quarterly")

        history = mgr.get_rotation_history("KEY")
        assert len(history) == 2
        assert history[0]["new_version"] == 3  # 최신순

    def test_revoke_secret(self):
        """시크릿 폐기"""
        mgr = SecretManager()
        mgr.register("OLD_KEY", SecretType.API_KEY, SecretEnvironment.DEMO)
        entry = mgr.revoke("OLD_KEY")
        assert entry is not None
        assert entry.status == SecretStatus.REVOKED

    def test_health_check_healthy(self):
        """건강 검사 — 정상"""
        mgr = SecretManager()
        mgr.register("KEY1", SecretType.API_KEY, SecretEnvironment.PRODUCTION, rotation_interval_days=365)
        mgr.register("KEY2", SecretType.JWT_SECRET, SecretEnvironment.PRODUCTION, rotation_interval_days=365)
        health = mgr.health_check()
        assert health["healthy"] is True
        assert health["total_secrets"] == 2
        assert health["active"] == 2

    def test_health_check_needs_rotation(self):
        """건강 검사 — 로테이션 필요"""
        mgr = SecretManager()
        entry = mgr.register("KEY", SecretType.API_KEY, SecretEnvironment.PRODUCTION, rotation_interval_days=30)
        # 생성일을 60일 전으로 조작
        entry.created_at = datetime.now(timezone.utc) - timedelta(days=60)
        health = mgr.health_check()
        assert health["healthy"] is False
        assert "KEY" in health["needs_rotation"]

    def test_health_check_expired(self):
        """건강 검사 — 만료"""
        mgr = SecretManager()
        past = datetime.now(timezone.utc) - timedelta(days=1)
        mgr.register(
            "KEY",
            SecretType.API_KEY,
            SecretEnvironment.PRODUCTION,
            expires_at=past,
            rotation_interval_days=365,
        )
        health = mgr.health_check()
        assert "KEY" in health["expired"]

    def test_health_check_expiring_soon(self):
        """건강 검사 — 곧 만료"""
        mgr = SecretManager()
        soon = datetime.now(timezone.utc) + timedelta(days=15)
        mgr.register(
            "KEY",
            SecretType.API_KEY,
            SecretEnvironment.PRODUCTION,
            expires_at=soon,
            rotation_interval_days=365,
        )
        health = mgr.health_check()
        assert "KEY" in health["expiring_soon"]

    def test_get_all(self):
        """전체 목록 조회"""
        mgr = SecretManager()
        mgr.register("A", SecretType.API_KEY, SecretEnvironment.PRODUCTION, rotation_interval_days=365)
        mgr.register("B", SecretType.JWT_SECRET, SecretEnvironment.DEMO, rotation_interval_days=365)
        all_secrets = mgr.get_all()
        assert len(all_secrets) == 2

    def test_rotation_extends_expiry(self):
        """로테이션 시 만료일 갱신"""
        mgr = SecretManager()
        future = datetime.now(timezone.utc) + timedelta(days=10)
        mgr.register(
            "KEY",
            SecretType.API_KEY,
            SecretEnvironment.PRODUCTION,
            expires_at=future,
            rotation_interval_days=90,
        )
        old_expiry = mgr.get("KEY").expires_at
        mgr.rotate("KEY")
        new_expiry = mgr.get("KEY").expires_at
        assert new_expiry > old_expiry


# ══════════════════════════════════════════════════════════════
# 4. SecretEntry 속성
# ══════════════════════════════════════════════════════════════
class TestSecretEntry:
    """SecretEntry 속성 테스트"""

    def test_to_dict(self):
        """직렬화"""
        entry = SecretEntry(
            name="TEST",
            secret_type=SecretType.API_KEY,
            environment=SecretEnvironment.TEST,
            rotation_interval_days=365,
        )
        d = entry.to_dict()
        assert d["name"] == "TEST"
        assert d["secret_type"] == "API_KEY"
        assert d["needs_rotation"] is False  # 방금 생성

    def test_needs_rotation_after_interval(self):
        """로테이션 주기 초과"""
        entry = SecretEntry(
            name="TEST",
            secret_type=SecretType.API_KEY,
            environment=SecretEnvironment.TEST,
            rotation_interval_days=30,
            created_at=datetime.now(timezone.utc) - timedelta(days=60),
        )
        assert entry.needs_rotation is True

    def test_not_expired_without_expiry(self):
        """만료일 없으면 만료 안 됨"""
        entry = SecretEntry(
            name="TEST",
            secret_type=SecretType.API_KEY,
            environment=SecretEnvironment.TEST,
        )
        assert entry.is_expired is False
        assert entry.days_until_expiry is None

    def test_rotation_record_to_dict(self):
        """RotationRecord 직렬화"""
        record = RotationRecord(
            rotation_id="rot-1",
            secret_name="KEY",
            rotated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            previous_version=1,
            new_version=2,
            reason="scheduled",
        )
        d = record.to_dict()
        assert d["rotation_id"] == "rot-1"
        assert d["new_version"] == 2


# ══════════════════════════════════════════════════════════════
# 5. 통합 시나리오
# ══════════════════════════════════════════════════════════════
class TestGateDIntegration:
    """Gate D 종합 통합"""

    def test_full_compliance_pipeline(self):
        """전체 컴플라이언스 파이프라인: 모든 검사 → 종합 리포트"""
        gen = ComplianceReportGenerator()

        # 각 섹션 생성
        audit_section = gen.generate_audit_report(
            integrity_result={"valid": True, "total_entries": 500},
            audit_stats={"by_module": {"order_executor": 300}},
        )
        retention_section = gen.generate_retention_report(
            retention_stats={"total_records": 1000, "pending_expiry": 0},
            violations=[],
        )
        pii_section = gen.generate_pii_report(pii_detections=0, settings_violations=[])
        risk_section = gen.generate_risk_report()

        # 종합 리포트
        report = gen.generate_comprehensive_report(
            "comprehensive-001",
            [audit_section, retention_section, pii_section, risk_section],
            period_start=datetime(2026, 3, 1, tzinfo=timezone.utc),
            period_end=datetime(2026, 3, 31, tzinfo=timezone.utc),
        )

        assert report.overall_grade == ComplianceGrade.COMPLIANT
        assert report.pass_count == 4
        assert report.fail_count == 0
        assert gen.report_count == 1

    def test_secret_lifecycle(self):
        """시크릿 전체 수명주기"""
        mgr = SecretManager()

        # 등록
        mgr.register("KIS_APP_KEY", SecretType.API_KEY, SecretEnvironment.PRODUCTION, rotation_interval_days=365)
        mgr.register(
            "DB_PASSWORD", SecretType.DATABASE_PASSWORD, SecretEnvironment.PRODUCTION, rotation_interval_days=365
        )
        mgr.register("JWT_SECRET", SecretType.JWT_SECRET, SecretEnvironment.PRODUCTION, rotation_interval_days=365)

        # 로테이션
        mgr.rotate("KIS_APP_KEY", reason="quarterly")
        mgr.rotate("DB_PASSWORD", reason="quarterly")

        # 폐기
        mgr.revoke("JWT_SECRET")

        # 건강 검사
        health = mgr.health_check()
        assert health["total_secrets"] == 3
        assert health["active"] == 2
        assert "JWT_SECRET" in health["revoked"]
        assert len(mgr.get_rotation_history()) == 2

    def test_noncompliant_report_with_issues(self):
        """문제 있는 종합 리포트"""
        gen = ComplianceReportGenerator()

        sections = [
            gen.generate_audit_report(
                integrity_result={"valid": False, "total_entries": 50, "broken_at_index": 10},
                audit_stats={},
            ),
            gen.generate_pii_report(pii_detections=5, settings_violations=[{"field": "pw"}]),
            gen.generate_risk_report(kill_switch_active=True),
        ]

        report = gen.generate_comprehensive_report("issue-001", sections)
        assert report.overall_grade == ComplianceGrade.NON_COMPLIANT
        assert report.fail_count == 3
