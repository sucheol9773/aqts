"""
Prometheus alert rules 유닛 테스트.

security-integrity-roadmap 의 P0/P1 항목에서 instrument 한 모든 보안/정합성
메트릭에 대해 1:1 알람이 정의되어 있는지, 각 규칙의 스키마가 올바른지 검증한다.

"알람 없는 metric 은 형식적 통제에 불과하다" 는 원칙의 테스트 계층 강제.
"""

from pathlib import Path

import pytest
import yaml

ALERTS_FILE = Path(__file__).resolve().parents[2] / "monitoring" / "prometheus" / "rules" / "aqts_alerts.yml"


# security-integrity-roadmap §9 의 P0/P1 instrument 결과로 추가된 메트릭과
# 이를 감시해야 하는 알람 이름의 대응표. 누락은 실패로 처리한다.
REQUIRED_METRIC_TO_ALERT = {
    "aqts_trading_guard_kill_switch_active": "TradingGuardKillSwitchActive",
    "aqts_trading_guard_blocks_total": "TradingGuardBlocksSpike",
    "aqts_audit_write_failures_total": "AuditWriteFailureStrict",
    "aqts_order_idempotency_store_failure_total": "OrderIdempotencyStoreUnavailable",
    "aqts_token_refresh_from_access_total": "AccessTokenReusedForRefresh",
    "aqts_revocation_backend_failure_total": "RevocationBackendUnavailable",
    "aqts_rate_limit_storage_failure_total": "RateLimitStorageUnavailable",
    "aqts_rate_limit_exceeded_total": "RateLimitExceededSpike",
    "aqts_reconciliation_ledger_diff_abs": "ReconciliationLedgerDiffNonZero",
    "aqts_reconciliation_mismatches_total": "ReconciliationMismatchDetected",
    "aqts_reconciliation_runs_total": "ReconciliationRunnerErrors",
    "aqts_env_bool_nonstandard_total": "EnvBoolNonStandardUsage",
}

ALLOWED_SEVERITIES = {"info", "warning", "critical"}
ALLOWED_DOMAINS = {"security", "integrity", "config"}


@pytest.fixture(scope="module")
def rules_document() -> dict:
    with ALERTS_FILE.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def all_alerts(rules_document) -> list:
    alerts = []
    for group in rules_document["groups"]:
        for rule in group.get("rules", []):
            if "alert" in rule:
                alerts.append((group["name"], rule))
    return alerts


class TestAlertRulesSchema:
    def test_file_exists(self):
        assert ALERTS_FILE.exists(), f"{ALERTS_FILE} 가 존재해야 한다."

    def test_yaml_parses_and_has_groups(self, rules_document):
        assert isinstance(rules_document, dict)
        assert "groups" in rules_document
        assert isinstance(rules_document["groups"], list)
        assert len(rules_document["groups"]) >= 1

    def test_security_integrity_group_exists(self, rules_document):
        names = {g["name"] for g in rules_document["groups"]}
        assert "aqts_security_integrity" in names

    def test_every_alert_has_required_fields(self, all_alerts):
        assert len(all_alerts) > 0
        for group_name, rule in all_alerts:
            assert "alert" in rule, f"{group_name}: alert 키 없음"
            assert rule.get("expr"), f"{rule['alert']}: expr 누락"
            labels = rule.get("labels") or {}
            assert "severity" in labels, f"{rule['alert']}: severity 라벨 누락"
            assert (
                labels["severity"] in ALLOWED_SEVERITIES
            ), f"{rule['alert']}: 유효하지 않은 severity={labels['severity']}"
            annotations = rule.get("annotations") or {}
            assert annotations.get("summary"), f"{rule['alert']}: summary 누락"
            assert annotations.get("description"), f"{rule['alert']}: description 누락"

    def test_security_integrity_alerts_have_domain_label(self, rules_document):
        for group in rules_document["groups"]:
            if group["name"] != "aqts_security_integrity":
                continue
            for rule in group["rules"]:
                labels = rule.get("labels") or {}
                assert "domain" in labels, f"{rule['alert']}: domain 라벨 누락"
                assert labels["domain"] in ALLOWED_DOMAINS


class TestRequiredAlertCoverage:
    """P0/P1 에서 instrument 된 모든 보안/정합성 메트릭이 알람에 연결되어야 한다."""

    @pytest.mark.parametrize(
        "metric_name,alert_name",
        sorted(REQUIRED_METRIC_TO_ALERT.items()),
    )
    def test_metric_is_referenced_by_named_alert(self, all_alerts, metric_name, alert_name):
        matching = [rule for _, rule in all_alerts if rule["alert"] == alert_name]
        assert matching, f"알람 {alert_name} 이 정의되어 있지 않다 (metric={metric_name})"
        rule = matching[0]
        assert metric_name in rule["expr"], f"알람 {alert_name} 의 expr 이 {metric_name} 를 참조하지 않는다"

    def test_trading_guard_kill_switch_is_critical_immediate(self, all_alerts):
        rule = next(r for _, r in all_alerts if r["alert"] == "TradingGuardKillSwitchActive")
        assert rule["labels"]["severity"] == "critical"
        # 즉시 알람: for 가 없거나 0m 여야 한다.
        assert str(rule.get("for", "0m")) in {"0m", "0s", "0"}

    def test_audit_write_failure_strict_is_critical(self, all_alerts):
        rule = next(r for _, r in all_alerts if r["alert"] == "AuditWriteFailureStrict")
        assert rule["labels"]["severity"] == "critical"
        assert 'mode="strict"' in rule["expr"]

    def test_reconciliation_ledger_diff_is_zero_threshold(self, all_alerts):
        rule = next(r for _, r in all_alerts if r["alert"] == "ReconciliationLedgerDiffNonZero")
        assert rule["labels"]["severity"] == "critical"
        assert "aqts_reconciliation_ledger_diff_abs > 0" in rule["expr"]


class TestAlertNameUniqueness:
    def test_no_duplicate_alert_names_within_group(self, rules_document):
        for group in rules_document["groups"]:
            names = [r["alert"] for r in group.get("rules", []) if "alert" in r]
            assert len(names) == len(set(names)), (
                f"그룹 {group['name']} 에 중복 알람 이름 존재: " f"{[n for n in names if names.count(n) > 1]}"
            )
