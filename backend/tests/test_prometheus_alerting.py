"""
Prometheus alerting 전체 커버리지 테스트.

aqts_alerts.yml 에 정의된 **모든** 그룹/규칙이 운영 요건을 충족하는지 검증한다.
test_alert_rules.py 가 security_integrity 그룹의 metric→alert 매핑에 집중하는 반면,
본 테스트는 전체 7개 그룹의 구조적 무결성과 운영 정책 준수를 검증한다.

검증 항목:
  - 그룹 수와 이름 완전성
  - 모든 규칙의 필수 필드 (expr, labels.severity, annotations.summary/description)
  - 심각도별 정책 (critical 은 runbook 필수 등)
  - 그룹별 규칙 수 하한 (회귀 방지)
  - 알림 이름 전역 유니크
  - PromQL expr 기본 구문 검증
"""

from pathlib import Path

import pytest
import yaml

ALERTS_FILE = Path(__file__).resolve().parents[2] / "monitoring" / "prometheus" / "rules" / "aqts_alerts.yml"

# ══════════════════════════════════════════════════════════════
# 기대하는 그룹 구성 — 그룹이 삭제/이름 변경되면 즉시 감지
# ══════════════════════════════════════════════════════════════
EXPECTED_GROUPS = {
    "aqts_availability": 3,  # BackendDown, SystemStatusUnhealthy, ComponentUnhealthy
    "aqts_api_performance": 4,  # HighErrorRate, HighLatencyP95, HighLatencyP99, NoTrafficReceived
    "aqts_circuit_breaker": 3,  # Open, HalfOpen, FailureSpike
    "aqts_data_collection": 2,  # Errors, Slow
    "aqts_trading": 4,  # DailyReturnExtreme, PortfolioValueDrop, NoSignalsGenerated, LowEnsembleConfidence
    "aqts_kis_recovery": 4,  # KISDegraded, KISDegradedProlonged, RecoveryAttemptsSpike, RecoveryStalling
    "aqts_security_integrity": 12,  # P0/P1 security/integrity 규칙
    "aqts_alert_pipeline": 2,  # AlertPipelineFailureRate, AlertPipelineDeadTransitions
}

ALLOWED_SEVERITIES = {"info", "warning", "critical"}

# critical 규칙 중 runbook 이 권장되는 알림 (운영 대응 절차 필수)
CRITICAL_ALERTS_REQUIRING_RUNBOOK = {
    "BackendDown",
    "HighErrorRate",
    "CircuitBreakerOpen",
    "PortfolioValueDrop",
    "KISDegradedProlonged",
    "TradingGuardKillSwitchActive",
    "AuditWriteFailureStrict",
    "OrderIdempotencyStoreUnavailable",
    "RevocationBackendUnavailable",
    "ReconciliationLedgerDiffNonZero",
}


@pytest.fixture(scope="module")
def rules_document() -> dict:
    with ALERTS_FILE.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def all_alerts(rules_document) -> list[tuple[str, dict]]:
    """(group_name, rule_dict) 튜플 리스트"""
    alerts = []
    for group in rules_document["groups"]:
        for rule in group.get("rules", []):
            if "alert" in rule:
                alerts.append((group["name"], rule))
    return alerts


@pytest.fixture(scope="module")
def alert_names(all_alerts) -> list[str]:
    return [rule["alert"] for _, rule in all_alerts]


# ══════════════════════════════════════════════════════════════
# 1. 그룹 구조 검증
# ══════════════════════════════════════════════════════════════
class TestGroupStructure:
    """7개 그룹이 모두 존재하고, 각 그룹의 규칙 수가 기대 하한 이상인지 검증"""

    def test_all_expected_groups_exist(self, rules_document):
        actual_names = {g["name"] for g in rules_document["groups"]}
        for expected_name in EXPECTED_GROUPS:
            assert expected_name in actual_names, (
                f"그룹 '{expected_name}' 이 aqts_alerts.yml 에 없음. " f"실제 그룹: {sorted(actual_names)}"
            )

    @pytest.mark.parametrize(
        "group_name,min_rules",
        sorted(EXPECTED_GROUPS.items()),
    )
    def test_group_has_minimum_rules(self, rules_document, group_name, min_rules):
        group = next(
            (g for g in rules_document["groups"] if g["name"] == group_name),
            None,
        )
        assert group is not None, f"그룹 '{group_name}' 를 찾을 수 없음"
        actual_count = len([r for r in group.get("rules", []) if "alert" in r])
        assert actual_count >= min_rules, (
            f"그룹 '{group_name}': 규칙 {actual_count}개 < 기대 하한 {min_rules}개. "
            f"규칙이 삭제되었다면 EXPECTED_GROUPS 를 업데이트해야 한다."
        )

    def test_total_alert_count(self, all_alerts):
        """전체 규칙 수가 34개 이상 (회귀 방지 하한)"""
        assert len(all_alerts) >= 34, (
            f"전체 알림 규칙 {len(all_alerts)}개 < 34개 하한. " f"규칙이 삭제되었는지 확인 필요."
        )


# ══════════════════════════════════════════════════════════════
# 2. 규칙별 필수 필드 검증
# ══════════════════════════════════════════════════════════════
class TestRuleFields:
    """모든 규칙이 expr, severity, summary, description 을 갖추었는지 검증"""

    def test_every_alert_has_expr(self, all_alerts):
        for group_name, rule in all_alerts:
            assert rule.get("expr"), f"[{group_name}] {rule['alert']}: expr 이 비어있음"

    def test_every_alert_has_valid_severity(self, all_alerts):
        for group_name, rule in all_alerts:
            labels = rule.get("labels") or {}
            severity = labels.get("severity")
            assert severity in ALLOWED_SEVERITIES, (
                f"[{group_name}] {rule['alert']}: " f"severity='{severity}' 는 {ALLOWED_SEVERITIES} 에 포함되지 않음"
            )

    def test_every_alert_has_summary_and_description(self, all_alerts):
        for group_name, rule in all_alerts:
            annotations = rule.get("annotations") or {}
            assert annotations.get("summary"), f"[{group_name}] {rule['alert']}: summary 누락"
            assert annotations.get("description"), f"[{group_name}] {rule['alert']}: description 누락"

    def test_expr_is_nonempty_string(self, all_alerts):
        """expr 이 단순 빈 문자열이나 공백만이 아닌지 검증"""
        for group_name, rule in all_alerts:
            expr = str(rule.get("expr", "")).strip()
            assert len(expr) > 0, f"[{group_name}] {rule['alert']}: expr 이 빈 문자열"


# ══════════════════════════════════════════════════════════════
# 3. 심각도 정책 검증
# ══════════════════════════════════════════════════════════════
class TestSeverityPolicy:
    """심각도별 운영 정책 준수 여부"""

    def test_critical_alerts_have_runbook_where_required(self, all_alerts):
        """핵심 critical 알림에 runbook 참조가 있어야 한다"""
        for group_name, rule in all_alerts:
            if rule["alert"] in CRITICAL_ALERTS_REQUIRING_RUNBOOK:
                annotations = rule.get("annotations") or {}
                assert annotations.get("runbook"), (
                    f"[{group_name}] {rule['alert']}: " f"critical 등급이지만 runbook 이 없음"
                )

    # 에스컬레이션 규칙: 의도적으로 긴 'for' 를 사용하는 critical 알림
    # KISDegradedProlonged: KISDegraded(warning, 2m) 이후 10분 추가 지속 시 critical 승격
    ESCALATION_EXCEPTIONS = {"KISDegradedProlonged"}

    def test_critical_for_duration_is_short(self, all_alerts):
        """critical 규칙의 'for' 는 5분 이하여야 한다 (에스컬레이션 예외 제외)"""
        for group_name, rule in all_alerts:
            labels = rule.get("labels") or {}
            if labels.get("severity") != "critical":
                continue
            if rule["alert"] in self.ESCALATION_EXCEPTIONS:
                continue
            for_value = str(rule.get("for", "0m"))
            # "30s", "0m", "1m", "2m" 등 파싱
            if for_value.endswith("s"):
                seconds = int(for_value[:-1])
            elif for_value.endswith("m"):
                seconds = int(for_value[:-1]) * 60
            elif for_value.endswith("h"):
                seconds = int(for_value[:-1]) * 3600
            else:
                seconds = int(for_value) if for_value.isdigit() else 0
            assert seconds <= 300, (
                f"[{group_name}] {rule['alert']}: " f"critical 규칙의 for={for_value} 이 5분(300s) 초과"
            )


# ══════════════════════════════════════════════════════════════
# 4. 알림 이름 전역 유니크
# ══════════════════════════════════════════════════════════════
class TestAlertNameUniqueness:
    def test_no_duplicate_alert_names_globally(self, alert_names):
        seen = {}
        for name in alert_names:
            if name in seen:
                pytest.fail(f"알림 이름 '{name}' 이 중복 정의됨")
            seen[name] = True


# ══════════════════════════════════════════════════════════════
# 5. 그룹별 특화 검증
# ══════════════════════════════════════════════════════════════
class TestGroupSpecific:
    """각 그룹의 핵심 규칙이 존재하는지 이름으로 검증"""

    @pytest.mark.parametrize(
        "alert_name",
        [
            "BackendDown",
            "SystemStatusUnhealthy",
            "ComponentUnhealthy",
        ],
    )
    def test_availability_alerts_exist(self, alert_names, alert_name):
        assert alert_name in alert_names

    @pytest.mark.parametrize(
        "alert_name",
        [
            "HighErrorRate",
            "HighLatencyP95",
            "HighLatencyP99",
            "NoTrafficReceived",
        ],
    )
    def test_api_performance_alerts_exist(self, alert_names, alert_name):
        assert alert_name in alert_names

    @pytest.mark.parametrize(
        "alert_name",
        [
            "CircuitBreakerOpen",
            "CircuitBreakerHalfOpen",
            "CircuitBreakerFailureSpike",
        ],
    )
    def test_circuit_breaker_alerts_exist(self, alert_names, alert_name):
        assert alert_name in alert_names

    @pytest.mark.parametrize(
        "alert_name",
        [
            "KISDegraded",
            "KISDegradedProlonged",
            "KISRecoveryAttemptsSpike",
            "KISRecoveryStalling",
        ],
    )
    def test_kis_recovery_alerts_exist(self, alert_names, alert_name):
        assert alert_name in alert_names

    @pytest.mark.parametrize(
        "alert_name",
        [
            "AlertPipelineFailureRate",
            "AlertPipelineDeadTransitions",
        ],
    )
    def test_alert_pipeline_alerts_exist(self, alert_names, alert_name):
        assert alert_name in alert_names

    def test_backend_down_is_critical_and_immediate(self, all_alerts):
        """BackendDown 은 critical 이고 1분 이내로 발화해야 한다"""
        rule = next(r for _, r in all_alerts if r["alert"] == "BackendDown")
        assert rule["labels"]["severity"] == "critical"
        for_value = str(rule.get("for", "0m"))
        assert for_value in {"0m", "0s", "1m", "30s", "0"}, f"BackendDown for={for_value} 는 1분 이내여야 함"

    def test_kis_degraded_prolonged_escalates_severity(self, all_alerts):
        """KISDegraded(warning) → KISDegradedProlonged(critical) 에스컬레이션"""
        degraded = next(r for _, r in all_alerts if r["alert"] == "KISDegraded")
        prolonged = next(r for _, r in all_alerts if r["alert"] == "KISDegradedProlonged")
        assert degraded["labels"]["severity"] == "warning"
        assert prolonged["labels"]["severity"] == "critical"
