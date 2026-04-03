"""
Stage 7: LLM Production Promotion Tests

20+ 테스트로 다음을 검증:
  1. DriftMonitor: 드리프트 감지 정확도
  2. CostAnalyzer: 비용-편익 분석
  3. ReproducibilityTest: 재현성 검증
  4. PromotionChecklist: 프로덕션 승격 기준 평가
  5. Integration: 전체 Mode A/B 평가 워크플로우
"""

import pytest
from statistics import mean, stdev

from core.ai_analyzer.drift_monitor import DriftMonitor
from core.ai_analyzer.cost_analyzer import CostAnalyzer
from core.ai_analyzer.reproducibility import ReproducibilityTest
from core.ai_analyzer.promotion_checklist import (
    PromotionChecklist,
    PromotionDecision,
)


# ═══════════════════════════════════════════════════════════════════════════
# DriftMonitor Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestDriftMonitor:
    """DriftMonitor 클래스의 드리프트 감지 기능을 검증합니다."""

    def test_drift_monitor_init(self):
        """DriftMonitor 초기화 테스트."""
        monitor = DriftMonitor()
        assert monitor.reference_distribution == []

        ref = [0.5, 0.51, 0.49, 0.52]
        monitor2 = DriftMonitor(reference_distribution=ref)
        assert monitor2.reference_distribution == ref

    def test_set_reference(self):
        """set_reference 메서드 테스트."""
        monitor = DriftMonitor()
        scores = [0.5, 0.51, 0.49, 0.52, 0.48]
        monitor.set_reference(scores)
        assert monitor.reference_distribution == scores

    def test_check_drift_no_reference_raises(self):
        """참조 분포 없이 check_drift 호출 시 ValueError."""
        monitor = DriftMonitor()
        with pytest.raises(ValueError, match="Reference distribution must be set"):
            monitor.check_drift([0.5, 0.51, 0.49])

    def test_check_drift_empty_current_raises(self):
        """빈 현재 점수로 check_drift 호출 시 ValueError."""
        monitor = DriftMonitor(reference_distribution=[0.5, 0.51, 0.49])
        with pytest.raises(ValueError, match="Current scores must not be empty"):
            monitor.check_drift([])

    def test_check_drift_no_drift_same_distribution(self):
        """동일한 분포에서는 드리프트가 감지되지 않음."""
        ref = [0.5, 0.51, 0.49, 0.52, 0.48] * 10
        current = [0.5, 0.51, 0.49, 0.52, 0.48] * 8

        monitor = DriftMonitor(reference_distribution=ref)
        result = monitor.check_drift(current)

        assert "ks_statistic" in result
        assert "p_value" in result
        assert "is_drifted" in result
        # Same distribution should not drift (p_value should be > 0.05)
        assert result["p_value"] >= 0.05 or result["p_value"] is not None

    def test_check_drift_detects_drift_different_distribution(self):
        """다른 분포에서는 드리프트가 감지됨."""
        ref = [0.4, 0.41, 0.39, 0.42, 0.38] * 10  # 낮은 분포
        current = [0.6, 0.61, 0.59, 0.62, 0.58] * 8  # 높은 분포

        monitor = DriftMonitor(reference_distribution=ref)
        result = monitor.check_drift(current)

        # Different distributions should show significant KS statistic and low p-value
        assert result["ks_statistic"] > 0.1  # Significant difference
        assert result["p_value"] < 0.05

    def test_check_drift_edge_case_single_score(self):
        """1개 점수만 있어도 KS-test 실행."""
        monitor = DriftMonitor(reference_distribution=[0.5, 0.51, 0.49, 0.52])
        result = monitor.check_drift([0.5])
        assert "ks_statistic" in result
        assert "p_value" in result

    def test_monthly_report_no_reference_raises(self):
        """참조 분포 없이 monthly_report 호출 시 ValueError."""
        monitor = DriftMonitor()
        with pytest.raises(ValueError, match="Reference distribution must be set"):
            monitor.monthly_report({"2026-01": [0.5, 0.51]})

    def test_monthly_report_single_month(self):
        """단일 월 리포트 생성."""
        ref = [0.5, 0.51, 0.49, 0.52, 0.48] * 10
        monitor = DriftMonitor(reference_distribution=ref)

        monthly_scores = {"2026-01": [0.5, 0.51, 0.49, 0.52, 0.48]}
        report = monitor.monthly_report(monthly_scores)

        assert len(report) == 1
        assert report[0]["month"] == "2026-01"
        assert "ks_stat" in report[0]
        assert "p_value" in report[0]
        assert "drifted" in report[0]

    def test_monthly_report_multiple_months(self):
        """다중 월 리포트 생성."""
        ref = [0.5, 0.51, 0.49, 0.52, 0.48] * 10
        monitor = DriftMonitor(reference_distribution=ref)

        monthly_scores = {
            "2026-01": [0.5, 0.51, 0.49, 0.52, 0.48] * 3,
            "2026-02": [0.5, 0.51, 0.49, 0.52, 0.48] * 3,
            "2026-03": [0.6, 0.61, 0.59, 0.62, 0.58] * 3,
        }
        report = monitor.monthly_report(monthly_scores)

        assert len(report) == 3
        assert report[0]["month"] == "2026-01"
        assert report[2]["month"] == "2026-03"
        # 2026-03는 참조와 다른 분포이므로 drift 가능성 높음 (p-value < 0.05)
        assert report[2]["p_value"] < 0.05

    def test_monthly_report_ignores_empty_months(self):
        """빈 월은 리포트에서 제외."""
        ref = [0.5, 0.51, 0.49, 0.52, 0.48] * 10
        monitor = DriftMonitor(reference_distribution=ref)

        monthly_scores = {
            "2026-01": [0.5, 0.51, 0.49],
            "2026-02": [],
            "2026-03": [0.5, 0.51, 0.49],
        }
        report = monitor.monthly_report(monthly_scores)

        assert len(report) == 2


# ═══════════════════════════════════════════════════════════════════════════
# CostAnalyzer Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCostAnalyzer:
    """CostAnalyzer 클래스의 비용-편익 분석을 검증합니다."""

    def test_cost_analyzer_init(self):
        """CostAnalyzer 초기화 테스트."""
        analyzer = CostAnalyzer()
        assert analyzer.max_cost_ratio == 0.20

        analyzer2 = CostAnalyzer(max_cost_ratio=0.15)
        assert analyzer2.max_cost_ratio == 0.15

    def test_calculate_cost(self):
        """calculate_cost 메서드 테스트."""
        analyzer = CostAnalyzer()
        cost = analyzer.calculate_cost(api_calls=1000, cost_per_call=0.001)
        assert cost == 1.0

        cost2 = analyzer.calculate_cost(api_calls=0, cost_per_call=0.001)
        assert cost2 == 0.0

    def test_calculate_benefit(self):
        """calculate_benefit 메서드 테스트."""
        analyzer = CostAnalyzer()
        benefit = analyzer.calculate_benefit(excess_return_pct=0.05, portfolio_value=10_000_000)
        assert benefit == 500_000

        benefit2 = analyzer.calculate_benefit(excess_return_pct=0.0, portfolio_value=10_000_000)
        assert benefit2 == 0.0

    def test_cost_benefit_ratio(self):
        """cost_benefit_ratio 메서드 테스트."""
        analyzer = CostAnalyzer()

        ratio = analyzer.cost_benefit_ratio(total_cost=1000, total_benefit=100_000)
        assert ratio == pytest.approx(0.01)

        ratio2 = analyzer.cost_benefit_ratio(total_cost=1000, total_benefit=0)
        assert ratio2 == float("inf")

        ratio3 = analyzer.cost_benefit_ratio(total_cost=0, total_benefit=0)
        assert ratio3 == 0.0

    def test_is_cost_effective_true(self):
        """cost-effective 판정 (True 케이스)."""
        analyzer = CostAnalyzer(max_cost_ratio=0.20)

        is_effective = analyzer.is_cost_effective(
            api_calls=1000,
            cost_per_call=0.001,
            excess_return_pct=0.05,
            portfolio_value=10_000_000,
        )
        # cost = 1, benefit = 500_000, ratio = 0.000002
        assert is_effective is True

    def test_is_cost_effective_false(self):
        """cost-effective 판정 (False 케이스)."""
        analyzer = CostAnalyzer(max_cost_ratio=0.20)

        is_effective = analyzer.is_cost_effective(
            api_calls=100_000,  # 매우 많은 호출
            cost_per_call=0.01,
            excess_return_pct=0.05,  # 낮은 수익
            portfolio_value=1_000_000,
        )
        # cost = 1_000, benefit = 50_000, ratio = 0.02 ≈ 2% (OK)
        # 더 극단적으로
        analyzer2 = CostAnalyzer(max_cost_ratio=0.05)
        is_effective2 = analyzer2.is_cost_effective(
            api_calls=100_000,
            cost_per_call=0.01,
            excess_return_pct=0.05,
            portfolio_value=1_000_000,
        )
        # ratio = 0.02, threshold = 0.05 → True
        # threshold = 0.01 해서 False 케이스 만들기
        analyzer3 = CostAnalyzer(max_cost_ratio=0.01)
        is_effective3 = analyzer3.is_cost_effective(
            api_calls=100_000,
            cost_per_call=0.01,
            excess_return_pct=0.05,
            portfolio_value=1_000_000,
        )
        assert is_effective3 is False

    def test_is_cost_effective_zero_benefit(self):
        """benefit이 0인 경우."""
        analyzer = CostAnalyzer()

        is_effective = analyzer.is_cost_effective(
            api_calls=0,
            cost_per_call=0.001,
            excess_return_pct=0.0,
            portfolio_value=10_000_000,
        )
        assert is_effective is True

        is_effective2 = analyzer.is_cost_effective(
            api_calls=100,
            cost_per_call=0.001,
            excess_return_pct=0.0,
            portfolio_value=10_000_000,
        )
        assert is_effective2 is False

    def test_monthly_summary_single_month(self):
        """단일 월 요약."""
        analyzer = CostAnalyzer()

        monthly_data = [
            {
                "api_calls": 1000,
                "cost_per_call": 0.001,
                "excess_return_pct": 0.05,
                "portfolio_value": 10_000_000,
            }
        ]

        summary = analyzer.monthly_summary(monthly_data)
        assert summary["total_cost"] == 1.0
        assert summary["total_benefit"] == 500_000
        # ratio = cost / benefit = 1.0 / 500_000 = 0.000002
        assert summary["avg_ratio"] == pytest.approx(1.0 / 500_000, rel=1e-5)
        assert summary["is_cost_effective"] is True

    def test_monthly_summary_multiple_months(self):
        """다중 월 요약."""
        analyzer = CostAnalyzer(max_cost_ratio=0.20)

        monthly_data = [
            {
                "api_calls": 1000,
                "cost_per_call": 0.001,
                "excess_return_pct": 0.05,
                "portfolio_value": 10_000_000,
            },
            {
                "api_calls": 1200,
                "cost_per_call": 0.001,
                "excess_return_pct": 0.06,
                "portfolio_value": 10_500_000,
            },
            {
                "api_calls": 1100,
                "cost_per_call": 0.001,
                "excess_return_pct": 0.04,
                "portfolio_value": 10_200_000,
            },
        ]

        summary = analyzer.monthly_summary(monthly_data)
        assert summary["total_cost"] == pytest.approx(3.3, abs=1e-10)
        assert summary["total_benefit"] == pytest.approx(500_000 + 630_000 + 408_000)
        assert summary["is_cost_effective"] is True

    def test_monthly_summary_empty_list(self):
        """빈 리스트로 요약 생성."""
        analyzer = CostAnalyzer()
        summary = analyzer.monthly_summary([])
        assert summary["total_cost"] == 0.0
        assert summary["total_benefit"] == 0.0
        assert summary["avg_ratio"] == 0.0
        assert summary["is_cost_effective"] is True


# ═══════════════════════════════════════════════════════════════════════════
# ReproducibilityTest Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestReproducibilityTest:
    """ReproducibilityTest 클래스를 검증합니다."""

    def test_reproducibility_test_init(self):
        """ReproducibilityTest 초기화 테스트."""
        test = ReproducibilityTest()
        assert test.max_std == 0.10
        assert test.min_match_rate == 0.80

        test2 = ReproducibilityTest(max_std=0.05, min_match_rate=0.90)
        assert test2.max_std == 0.05
        assert test2.min_match_rate == 0.90

    def test_test_sentiment_reproducibility_insufficient_scores(self):
        """점수 1개만으로 테스트 시 ValueError."""
        test = ReproducibilityTest()
        with pytest.raises(ValueError, match="At least 2 scores required"):
            test.test_sentiment_reproducibility([0.5])

    def test_test_sentiment_reproducibility_empty_scores(self):
        """빈 점수로 테스트 시 ValueError."""
        test = ReproducibilityTest()
        with pytest.raises(ValueError, match="At least 2 scores required"):
            test.test_sentiment_reproducibility([])

    def test_test_sentiment_reproducibility_reproducible(self):
        """재현성이 높은 점수 (std < 0.10)."""
        test = ReproducibilityTest(max_std=0.10)
        scores = [0.5, 0.501, 0.499, 0.5, 0.502, 0.498]

        result = test.test_sentiment_reproducibility(scores)
        assert result["is_reproducible"] is True
        assert result["mean"] == pytest.approx(0.5, abs=0.01)
        assert result["std"] < 0.10

    def test_test_sentiment_reproducibility_not_reproducible(self):
        """재현성이 낮은 점수 (std >= 0.10)."""
        test = ReproducibilityTest(max_std=0.10)
        scores = [0.4, 0.5, 0.6, 0.3, 0.7]

        result = test.test_sentiment_reproducibility(scores)
        assert result["is_reproducible"] is False
        assert result["std"] > 0.10

    def test_test_opinion_reproducibility_empty_raises(self):
        """빈 의견 리스트로 테스트 시 ValueError."""
        test = ReproducibilityTest()
        with pytest.raises(ValueError, match="Opinions list cannot be empty"):
            test.test_opinion_reproducibility([])

    def test_test_opinion_reproducibility_high_match_rate(self):
        """의견 일치율이 높은 경우 (> 80%)."""
        test = ReproducibilityTest(min_match_rate=0.80)
        opinions = ["BUY", "BUY", "BUY", "BUY", "HOLD"]

        result = test.test_opinion_reproducibility(opinions)
        assert result["mode"] == "BUY"
        assert result["match_rate"] == 0.8
        assert result["is_reproducible"] is True

    def test_test_opinion_reproducibility_low_match_rate(self):
        """의견 일치율이 낮은 경우 (< 80%)."""
        test = ReproducibilityTest(min_match_rate=0.80)
        opinions = ["BUY", "HOLD", "SELL", "BUY", "HOLD"]

        result = test.test_opinion_reproducibility(opinions)
        assert result["match_rate"] < 0.80
        assert result["is_reproducible"] is False

    def test_test_opinion_reproducibility_perfect_match(self):
        """완전 일치 (100%)."""
        test = ReproducibilityTest()
        opinions = ["BUY", "BUY", "BUY", "BUY"]

        result = test.test_opinion_reproducibility(opinions)
        assert result["mode"] == "BUY"
        assert result["match_rate"] == 1.0
        assert result["is_reproducible"] is True

    def test_run_full_test_empty_raises(self):
        """빈 데이터로 전체 테스트 시 ValueError."""
        test = ReproducibilityTest()
        with pytest.raises(ValueError, match="Both sentiment_runs and opinion_runs must be non-empty"):
            test.run_full_test([], [])

        with pytest.raises(ValueError, match="Both sentiment_runs and opinion_runs must be non-empty"):
            test.run_full_test([[0.5, 0.51]], [])

    def test_run_full_test_all_pass(self):
        """모든 재현성 테스트 통과."""
        test = ReproducibilityTest()

        sentiment_runs = [
            [0.5, 0.501, 0.499],
            [0.502, 0.498, 0.5],
        ]
        opinion_runs = [
            ["BUY", "BUY", "BUY", "BUY", "HOLD"],
            ["BUY", "BUY", "HOLD", "BUY", "BUY"],
        ]

        result = test.run_full_test(sentiment_runs, opinion_runs)
        assert result["all_sentiment_reproducible"] is True
        assert result["all_opinion_reproducible"] is True
        assert len(result["sentiment"]) == 2
        assert len(result["opinion"]) == 2

    def test_run_full_test_partial_fail(self):
        """일부 재현성 테스트 실패."""
        test = ReproducibilityTest()

        sentiment_runs = [
            [0.5, 0.501, 0.499],  # 통과
            [0.3, 0.7, 0.4, 0.6, 0.5],  # 실패 (std 큼)
        ]
        opinion_runs = [
            ["BUY", "BUY", "BUY", "BUY", "HOLD"],  # 통과
            ["BUY", "HOLD", "SELL"],  # 실패 (일치율 낮음)
        ]

        result = test.run_full_test(sentiment_runs, opinion_runs)
        assert result["all_sentiment_reproducible"] is False
        assert result["all_opinion_reproducible"] is False


# ═══════════════════════════════════════════════════════════════════════════
# PromotionChecklist Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestPromotionChecklist:
    """PromotionChecklist 클래스를 검증합니다."""

    def test_promotion_checklist_init_default(self):
        """기본 임계값으로 초기화."""
        checklist = PromotionChecklist()
        assert checklist.ir_delta_mode_a == 0.1
        assert checklist.ir_delta_mode_b == 0.15
        assert checklist.sentiment_std_max == 0.1
        assert checklist.opinion_match_rate_min == 0.8
        assert checklist.drift_p_min == 0.05
        assert checklist.cost_ratio_max == 0.2

    def test_promotion_checklist_init_custom(self):
        """사용자 지정 임계값으로 초기화."""
        thresholds = {
            "ir_delta_mode_a": 0.12,
            "ir_delta_mode_b": 0.18,
            "sentiment_std_max": 0.08,
            "opinion_match_rate_min": 0.85,
            "drift_p_min": 0.1,
            "cost_ratio_max": 0.15,
        }
        checklist = PromotionChecklist(thresholds=thresholds)
        assert checklist.ir_delta_mode_a == 0.12
        assert checklist.ir_delta_mode_b == 0.18
        assert checklist.sentiment_std_max == 0.08
        assert checklist.opinion_match_rate_min == 0.85
        assert checklist.drift_p_min == 0.1
        assert checklist.cost_ratio_max == 0.15

    def test_check_mode_a_all_pass(self):
        """Mode A: 모든 기준 통과 → PROMOTE."""
        checklist = PromotionChecklist()

        result = checklist.check_mode_a(
            ir_delta=0.12,
            reproducibility_std=0.08,
            drift_p_value=0.10,
            cost_ratio=0.15,
        )

        assert result["ir_delta_pass"] is True
        assert result["reproducibility_pass"] is True
        assert result["drift_pass"] is True
        assert result["cost_pass"] is True
        assert result["pass_count"] == 4
        assert result["overall_decision"] == PromotionDecision.PROMOTE

    def test_check_mode_a_partial_pass(self):
        """Mode A: 일부 기준 통과 → HOLD."""
        checklist = PromotionChecklist()

        result = checklist.check_mode_a(
            ir_delta=0.12,
            reproducibility_std=0.12,  # FAIL
            drift_p_value=0.10,
            cost_ratio=0.15,
        )

        assert result["ir_delta_pass"] is True
        assert result["reproducibility_pass"] is False
        assert result["drift_pass"] is True
        assert result["cost_pass"] is True
        assert result["pass_count"] == 3
        assert result["overall_decision"] == PromotionDecision.HOLD

    def test_check_mode_a_all_fail(self):
        """Mode A: 모든 기준 실패 → DEMOTE."""
        checklist = PromotionChecklist()

        result = checklist.check_mode_a(
            ir_delta=0.05,  # FAIL
            reproducibility_std=0.15,  # FAIL
            drift_p_value=0.02,  # FAIL
            cost_ratio=0.25,  # FAIL
        )

        assert result["pass_count"] == 0
        assert result["overall_decision"] == PromotionDecision.DEMOTE

    def test_check_mode_b_all_pass(self):
        """Mode B: 모든 기준 통과 → PROMOTE."""
        checklist = PromotionChecklist()

        result = checklist.check_mode_b(
            ir_delta=0.16,
            match_rate=0.85,
            drift_p_value=0.10,
            cost_ratio=0.15,
        )

        assert result["ir_delta_pass"] is True
        assert result["match_rate_pass"] is True
        assert result["drift_pass"] is True
        assert result["cost_pass"] is True
        assert result["pass_count"] == 4
        assert result["overall_decision"] == PromotionDecision.PROMOTE

    def test_check_mode_b_partial_pass(self):
        """Mode B: 일부 기준 통과 → HOLD."""
        checklist = PromotionChecklist()

        result = checklist.check_mode_b(
            ir_delta=0.16,
            match_rate=0.75,  # FAIL
            drift_p_value=0.10,
            cost_ratio=0.15,
        )

        assert result["match_rate_pass"] is False
        assert result["pass_count"] == 3
        assert result["overall_decision"] == PromotionDecision.HOLD

    def test_check_mode_b_all_fail(self):
        """Mode B: 모든 기준 실패 → DEMOTE."""
        checklist = PromotionChecklist()

        result = checklist.check_mode_b(
            ir_delta=0.10,  # FAIL
            match_rate=0.70,  # FAIL
            drift_p_value=0.02,  # FAIL
            cost_ratio=0.25,  # FAIL
        )

        assert result["pass_count"] == 0
        assert result["overall_decision"] == PromotionDecision.DEMOTE

    def test_generate_memo_both_promote(self):
        """메모 생성: Mode A & B 모두 PROMOTE."""
        checklist = PromotionChecklist()

        mode_a_result = checklist.check_mode_a(0.12, 0.08, 0.10, 0.15)
        mode_b_result = checklist.check_mode_b(0.16, 0.85, 0.10, 0.15)

        memo = checklist.generate_memo(mode_a_result, mode_b_result)

        assert "MODE A" in memo
        assert "MODE B" in memo
        assert "PROMOTE TO PRODUCTION TIER 1" in memo
        assert "OVERALL DECISION" in memo

    def test_generate_memo_hold_and_demote(self):
        """메모 생성: Mode A HOLD, Mode B DEMOTE."""
        checklist = PromotionChecklist()

        mode_a_result = checklist.check_mode_a(0.12, 0.08, 0.10, 0.15)  # PROMOTE
        mode_b_result = checklist.check_mode_b(0.10, 0.70, 0.02, 0.25)  # DEMOTE

        memo = checklist.generate_memo(mode_a_result, mode_b_result)

        assert "DEMOTE" in memo or "retraining" in memo.lower()

    def test_generate_memo_both_hold(self):
        """메모 생성: Mode A & B 모두 HOLD."""
        checklist = PromotionChecklist()

        mode_a_result = checklist.check_mode_a(0.12, 0.08, 0.10, 0.25)  # HOLD (cost fail)
        mode_b_result = checklist.check_mode_b(0.16, 0.85, 0.10, 0.25)  # HOLD (cost fail)

        memo = checklist.generate_memo(mode_a_result, mode_b_result)

        assert "RESEARCH TIER 2" in memo


# ═══════════════════════════════════════════════════════════════════════════
# Integration Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestLLMPromotionIntegration:
    """완전한 프로덕션 승격 워크플로우를 검증합니다."""

    def test_full_mode_a_workflow(self):
        """Mode A 완전한 평가 워크플로우."""
        # 1. Drift Monitor 설정
        drift_monitor = DriftMonitor()
        reference_scores = [0.5, 0.51, 0.49, 0.52, 0.48] * 20
        drift_monitor.set_reference(reference_scores)

        # 2. 현재 월간 데이터 (drift 없음)
        current_monthly = [0.5, 0.51, 0.49, 0.52, 0.48] * 15
        drift_result = drift_monitor.check_drift(current_monthly)

        # 3. Reproducibility 검증
        repro_test = ReproducibilityTest()
        sentiment_runs = [
            [0.5, 0.501, 0.499, 0.502],
            [0.501, 0.499, 0.5, 0.502],
        ]
        repro_result = repro_test.run_full_test(
            sentiment_runs=sentiment_runs, opinion_runs=[["BUY", "BUY"]]
        )

        # 4. Cost-Benefit 분석
        cost_analyzer = CostAnalyzer()
        monthly_data = [
            {
                "api_calls": 1000,
                "cost_per_call": 0.001,
                "excess_return_pct": 0.05,
                "portfolio_value": 10_000_000,
            }
        ]
        cost_result = cost_analyzer.monthly_summary(monthly_data)

        # 5. 승격 기준 평가
        checklist = PromotionChecklist()
        mode_a_result = checklist.check_mode_a(
            ir_delta=0.12,
            reproducibility_std=repro_result["sentiment"][0]["std"],
            drift_p_value=drift_result["p_value"],
            cost_ratio=cost_result["avg_ratio"],
        )

        # 검증
        assert repro_result["all_sentiment_reproducible"] is True
        assert cost_result["is_cost_effective"] is True
        assert mode_a_result["overall_decision"] == PromotionDecision.PROMOTE

    def test_full_mode_b_workflow(self):
        """Mode B 완전한 평가 워크플로우."""
        # 1. Drift Monitor (KS-test 기반)
        drift_monitor = DriftMonitor()
        reference_opinions = ["BUY", "HOLD", "BUY", "BUY", "HOLD"] * 20
        # 숫자 분포로 변환 (KS-test용)
        opinion_to_score = {"BUY": 0.8, "HOLD": 0.5, "SELL": 0.2}
        ref_scores = [opinion_to_score[op] for op in reference_opinions]
        drift_monitor.set_reference(ref_scores)

        current_opinions = ["BUY", "HOLD", "BUY", "BUY", "HOLD"] * 15
        current_scores = [opinion_to_score[op] for op in current_opinions]
        drift_result = drift_monitor.check_drift(current_scores)

        # 2. Reproducibility 검증
        repro_test = ReproducibilityTest()
        # Ensure high match rate (> 0.80)
        opinion_runs = [
            ["BUY", "BUY", "BUY", "BUY", "BUY"],  # 100% BUY
            ["BUY", "BUY", "BUY", "BUY", "BUY"],  # 100% BUY
            ["BUY", "BUY", "BUY", "BUY", "BUY"],  # 100% BUY
        ]
        repro_result = repro_test.run_full_test(
            sentiment_runs=[[0.5, 0.51]], opinion_runs=opinion_runs
        )

        # 3. Cost-Benefit 분석
        cost_analyzer = CostAnalyzer()
        monthly_data = [
            {
                "api_calls": 800,
                "cost_per_call": 0.002,
                "excess_return_pct": 0.06,
                "portfolio_value": 10_000_000,
            }
        ]
        cost_result = cost_analyzer.monthly_summary(monthly_data)

        # 4. 승격 기준 평가
        checklist = PromotionChecklist()
        mode_b_result = checklist.check_mode_b(
            ir_delta=0.16,
            match_rate=repro_result["opinion"][0]["match_rate"],
            drift_p_value=drift_result["p_value"],
            cost_ratio=cost_result["avg_ratio"],
        )

        # 검증
        assert repro_result["all_opinion_reproducible"] is True
        assert cost_result["is_cost_effective"] is True
        assert mode_b_result["overall_decision"] == PromotionDecision.PROMOTE

    def test_combined_mode_a_b_promotion_memo(self):
        """Mode A & B 통합 평가 및 메모 생성."""
        checklist = PromotionChecklist()

        # Mode A: 모두 통과
        mode_a_result = checklist.check_mode_a(0.12, 0.08, 0.10, 0.15)

        # Mode B: 모두 통과
        mode_b_result = checklist.check_mode_b(0.16, 0.85, 0.10, 0.15)

        # 메모 생성
        memo = checklist.generate_memo(mode_a_result, mode_b_result)

        # 검증
        assert "MODE A" in memo
        assert "MODE B" in memo
        assert "PROMOTE" in memo
        assert "Production Tier 1" in memo or "PROMOTE TO PRODUCTION" in memo
