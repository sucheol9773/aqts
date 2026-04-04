"""
규제 준수 리포트 자동 생성 (Regulatory Compliance Reports)

Gate D: 규제 리포트 자동 생성

기능:
  - 거래 활동 요약 리포트 (일별/월별)
  - 리스크 한도 준수 현황
  - 감사 로그 무결성 현황
  - 보존 정책 준수 현황
  - PII 노출 검사 결과
  - 종합 컴플라이언스 점수 산출
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from config.logging import logger


class ReportType(str, Enum):
    """리포트 유형"""

    DAILY_TRADING = "DAILY_TRADING"
    MONTHLY_SUMMARY = "MONTHLY_SUMMARY"
    RISK_COMPLIANCE = "RISK_COMPLIANCE"
    AUDIT_INTEGRITY = "AUDIT_INTEGRITY"
    RETENTION_STATUS = "RETENTION_STATUS"
    PII_SCAN = "PII_SCAN"
    COMPREHENSIVE = "COMPREHENSIVE"


class ComplianceGrade(str, Enum):
    """컴플라이언스 등급"""

    COMPLIANT = "COMPLIANT"  # 모든 항목 통과
    MINOR_ISSUES = "MINOR_ISSUES"  # 경미한 문제 (조치 권고)
    NON_COMPLIANT = "NON_COMPLIANT"  # 비준수 (즉시 조치 필요)


@dataclass
class ReportSection:
    """리포트 섹션"""

    title: str
    status: str  # PASS / WARNING / FAIL
    findings: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "status": self.status,
            "findings": self.findings,
            "metrics": self.metrics,
        }


@dataclass
class ComplianceReport:
    """컴플라이언스 리포트"""

    report_id: str
    report_type: ReportType
    generated_at: datetime
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    sections: list[ReportSection] = field(default_factory=list)
    overall_grade: ComplianceGrade = ComplianceGrade.COMPLIANT
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "report_type": self.report_type.value,
            "generated_at": self.generated_at.isoformat(),
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "sections": [s.to_dict() for s in self.sections],
            "overall_grade": self.overall_grade.value,
            "summary": self.summary,
        }

    @property
    def pass_count(self) -> int:
        return sum(1 for s in self.sections if s.status == "PASS")

    @property
    def fail_count(self) -> int:
        return sum(1 for s in self.sections if s.status == "FAIL")

    @property
    def warning_count(self) -> int:
        return sum(1 for s in self.sections if s.status == "WARNING")


class ComplianceReportGenerator:
    """
    컴플라이언스 리포트 생성기

    각 검사 모듈의 결과를 수집하여 종합 리포트를 생성합니다.
    """

    def __init__(self):
        self._reports: list[ComplianceReport] = []

    def generate_audit_report(
        self,
        integrity_result: dict,
        audit_stats: dict,
    ) -> ReportSection:
        """감사 로그 무결성 섹션 생성"""
        is_valid = integrity_result.get("valid", False)
        total = integrity_result.get("total_entries", 0)

        findings = []
        if not is_valid:
            findings.append(f"해시 체인 무결성 위반 감지 (인덱스 {integrity_result.get('broken_at_index')})")
        if total == 0:
            findings.append("감사 로그 항목이 없습니다")

        return ReportSection(
            title="감사 로그 무결성",
            status="PASS" if is_valid and total > 0 else "FAIL" if not is_valid else "WARNING",
            findings=findings,
            metrics={
                "total_entries": total,
                "chain_valid": is_valid,
                "by_module": audit_stats.get("by_module", {}),
                "by_action": audit_stats.get("by_action", {}),
            },
        )

    def generate_retention_report(
        self,
        retention_stats: dict,
        violations: list[dict],
    ) -> ReportSection:
        """보존 정책 준수 섹션 생성"""
        has_violations = len(violations) > 0
        pending_expiry = retention_stats.get("pending_expiry", 0)

        findings = []
        if has_violations:
            findings.append(f"조기 삭제 위반 {len(violations)}건 감지")
        if pending_expiry > 0:
            findings.append(f"만료 대기 기록 {pending_expiry}건 (아카이브 필요)")

        return ReportSection(
            title="거래 기록 보존 정책",
            status="FAIL" if has_violations else "WARNING" if pending_expiry > 0 else "PASS",
            findings=findings,
            metrics={
                "total_records": retention_stats.get("total_records", 0),
                "violations": len(violations),
                "pending_expiry": pending_expiry,
                "by_status": retention_stats.get("by_status", {}),
            },
        )

    def generate_pii_report(
        self,
        pii_detections: int,
        settings_violations: list[dict],
    ) -> ReportSection:
        """PII 보호 섹션 생성"""
        findings = []
        if pii_detections > 0:
            findings.append(f"PII 노출 {pii_detections}건 감지 (마스킹 필요)")
        if settings_violations:
            findings.append(f"Settings 민감 필드 노출 {len(settings_violations)}건")

        has_issues = pii_detections > 0 or len(settings_violations) > 0
        return ReportSection(
            title="개인정보 보호",
            status="FAIL" if has_issues else "PASS",
            findings=findings,
            metrics={
                "pii_detections": pii_detections,
                "settings_violations": len(settings_violations),
            },
        )

    def generate_risk_report(
        self,
        daily_loss_triggered: bool = False,
        mdd_triggered: bool = False,
        kill_switch_active: bool = False,
        trading_halted: bool = False,
    ) -> ReportSection:
        """리스크 한도 준수 섹션 생성"""
        findings = []
        if daily_loss_triggered:
            findings.append("일일 손실 한도 트리거 발생")
        if mdd_triggered:
            findings.append("최대 낙폭(MDD) 한도 트리거 발생")
        if kill_switch_active:
            findings.append("킬 스위치 활성화됨")
        if trading_halted:
            findings.append("매매 중단 상태")

        has_critical = kill_switch_active or mdd_triggered
        has_warning = daily_loss_triggered or trading_halted

        status = "FAIL" if has_critical else "WARNING" if has_warning else "PASS"
        return ReportSection(
            title="리스크 한도 준수",
            status=status,
            findings=findings,
            metrics={
                "daily_loss_triggered": daily_loss_triggered,
                "mdd_triggered": mdd_triggered,
                "kill_switch_active": kill_switch_active,
                "trading_halted": trading_halted,
            },
        )

    def generate_comprehensive_report(
        self,
        report_id: str,
        sections: list[ReportSection],
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
    ) -> ComplianceReport:
        """종합 컴플라이언스 리포트 생성"""
        now = datetime.now(timezone.utc)

        # 종합 등급 산출
        fail_count = sum(1 for s in sections if s.status == "FAIL")
        warning_count = sum(1 for s in sections if s.status == "WARNING")

        if fail_count > 0:
            grade = ComplianceGrade.NON_COMPLIANT
        elif warning_count > 0:
            grade = ComplianceGrade.MINOR_ISSUES
        else:
            grade = ComplianceGrade.COMPLIANT

        total = len(sections)
        pass_count = sum(1 for s in sections if s.status == "PASS")

        summary = (
            f"검사 {total}개 항목 중 PASS {pass_count}, WARNING {warning_count}, FAIL {fail_count}. "
            f"종합 등급: {grade.value}"
        )

        report = ComplianceReport(
            report_id=report_id,
            report_type=ReportType.COMPREHENSIVE,
            generated_at=now,
            period_start=period_start,
            period_end=period_end,
            sections=sections,
            overall_grade=grade,
            summary=summary,
        )

        self._reports.append(report)
        logger.info(f"Compliance report generated: {report_id} grade={grade.value}")
        return report

    def get_latest_report(self) -> Optional[ComplianceReport]:
        """최신 리포트 반환"""
        if not self._reports:
            return None
        return self._reports[-1]

    def get_reports(self, limit: int = 10) -> list[ComplianceReport]:
        """리포트 이력 조회"""
        return sorted(self._reports, key=lambda r: r.generated_at, reverse=True)[:limit]

    @property
    def report_count(self) -> int:
        return len(self._reports)
