"""RiskCheck 계약 테스트 (Contract 9)."""

import pytest
from pydantic import ValidationError

from contracts.risk_check import (
    RiskCheckItem, RiskCheckResult, RiskCheckDecision, RiskCheckSeverity,
)


def _item(layer="capital", decision=RiskCheckDecision.PASS, **kw):
    return RiskCheckItem(layer_name=layer, decision=decision, **kw)


def _valid_result(**overrides):
    defaults = dict(
        ticker="005930",
        checks=[_item("env_check"), _item("capital_check"), _item("loss_check")],
        overall_decision=RiskCheckDecision.PASS,
    )
    defaults.update(overrides)
    return defaults


@pytest.mark.smoke
class TestRiskCheckItemValid:
    def test_pass_item(self):
        item = _item()
        assert item.decision == RiskCheckDecision.PASS

    def test_block_item(self):
        item = _item(decision=RiskCheckDecision.BLOCK, severity=RiskCheckSeverity.CRITICAL)
        assert item.severity == RiskCheckSeverity.CRITICAL

    def test_warn_item(self):
        item = _item(decision=RiskCheckDecision.WARN, severity=RiskCheckSeverity.MEDIUM)
        assert item.decision == RiskCheckDecision.WARN

    def test_with_metrics(self):
        item = _item(metric_value=0.05, threshold=0.03, reason="일일 손실 5%")
        assert item.metric_value == 0.05
        assert item.threshold == 0.03

    def test_all_severities(self):
        for sev in RiskCheckSeverity:
            item = _item(severity=sev)
            assert item.severity == sev


@pytest.mark.smoke
class TestRiskCheckResultValid:
    def test_all_pass(self):
        r = RiskCheckResult(**_valid_result())
        assert r.overall_decision == RiskCheckDecision.PASS

    def test_with_block_overall_block(self):
        r = RiskCheckResult(**_valid_result(
            checks=[
                _item("env_check", RiskCheckDecision.PASS),
                _item("capital_check", RiskCheckDecision.BLOCK),
            ],
            overall_decision=RiskCheckDecision.BLOCK,
        ))
        assert r.overall_decision == RiskCheckDecision.BLOCK

    def test_warn_items_with_pass_overall(self):
        r = RiskCheckResult(**_valid_result(
            checks=[
                _item("env_check", RiskCheckDecision.PASS),
                _item("capital_check", RiskCheckDecision.WARN),
            ],
            overall_decision=RiskCheckDecision.PASS,
        ))
        assert r.overall_decision == RiskCheckDecision.PASS

    def test_with_decision_id(self):
        r = RiskCheckResult(**_valid_result(decision_id="dec-456"))
        assert r.decision_id == "dec-456"

    def test_single_check(self):
        r = RiskCheckResult(
            ticker="AAPL",
            checks=[_item("env", RiskCheckDecision.PASS)],
            overall_decision=RiskCheckDecision.PASS,
        )
        assert len(r.checks) == 1


@pytest.mark.smoke
class TestRiskCheckResultInvalid:
    def test_block_item_but_pass_overall(self):
        with pytest.raises(ValidationError, match="BLOCK.*아닙니다"):
            RiskCheckResult(**_valid_result(
                checks=[
                    _item("env", RiskCheckDecision.PASS),
                    _item("capital", RiskCheckDecision.BLOCK),
                ],
                overall_decision=RiskCheckDecision.PASS,
            ))

    def test_empty_checks(self):
        with pytest.raises(ValidationError):
            RiskCheckResult(
                ticker="005930", checks=[],
                overall_decision=RiskCheckDecision.PASS,
            )

    def test_empty_ticker(self):
        with pytest.raises(ValidationError):
            RiskCheckResult(**_valid_result(ticker=""))

    def test_extra_field(self):
        with pytest.raises(ValidationError, match="Extra inputs"):
            RiskCheckResult(**_valid_result(confidence=0.9))

    def test_immutable(self):
        r = RiskCheckResult(**_valid_result())
        with pytest.raises(ValidationError):
            r.overall_decision = RiskCheckDecision.BLOCK

    def test_item_empty_layer_name(self):
        with pytest.raises(ValidationError):
            RiskCheckItem(layer_name="", decision=RiskCheckDecision.PASS)

    def test_item_extra_field(self):
        with pytest.raises(ValidationError, match="Extra inputs"):
            RiskCheckItem(layer_name="test", decision=RiskCheckDecision.PASS, extra="bad")

    def test_item_reason_too_long(self):
        with pytest.raises(ValidationError):
            _item(reason="x" * 501)
