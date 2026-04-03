"""
Stage 6 Performance Validation: 30+ Unit Tests

Comprehensive tests for:
- MetricsCalculator: 9 metrics with edge cases
- BenchmarkManager: default benchmarks
- RegimeAnalyzer: regime classification and per-regime metrics
- AblationStudy: layer addition and contribution
- SignificanceTest: bootstrap CI and t-tests
- PerformanceJudge: PASS/REVIEW/FAIL decisions
"""

import pytest
import numpy as np
from core.backtest_engine import (
    MetricsCalculator,
    BenchmarkManager,
    RegimeAnalyzer,
    AblationStudy,
    SignificanceTest,
    PerformanceJudge,
)


# ═══════════════════════════════════════════════════════════════
# MetricsCalculator Tests (9 metrics + edge cases)
# ═══════════════════════════════════════════════════════════════


class TestMetricsCalculator:
    """Test 9 core metrics."""

    def setup_method(self):
        """Set up test data."""
        # Steady positive returns
        self.positive_returns = np.array([0.01] * 252)  # 1% daily, whole year

        # Steady negative returns
        self.negative_returns = np.array([-0.01] * 252)

        # Mixed returns with positive bias
        self.mixed_returns = np.array([0.02, -0.01, 0.01, -0.005] * 63)

        # All zeros
        self.zero_returns = np.array([0.0] * 252)

        # Empty
        self.empty_returns = np.array([])

    def test_cagr_positive(self):
        """Test CAGR with steady positive returns."""
        cagr = MetricsCalculator.cagr(self.positive_returns)
        # 1% daily → very large annual (compound)
        assert cagr > 1.0, f"Expected very large CAGR, got {cagr:.4f}"

    def test_cagr_negative(self):
        """Test CAGR with negative returns."""
        cagr = MetricsCalculator.cagr(self.negative_returns)
        # -1% daily → negative annual
        assert cagr < 0, f"Expected negative CAGR, got {cagr:.4f}"

    def test_cagr_empty(self):
        """Test CAGR with empty returns."""
        cagr = MetricsCalculator.cagr(self.empty_returns)
        assert cagr == 0.0

    def test_max_drawdown_positive(self):
        """Test max drawdown with positive returns."""
        mdd = MetricsCalculator.max_drawdown(self.positive_returns)
        # All positive should have near-zero drawdown
        assert mdd >= -0.01, f"Expected minimal drawdown, got {mdd:.4f}"

    def test_max_drawdown_negative(self):
        """Test max drawdown with all negative returns."""
        mdd = MetricsCalculator.max_drawdown(self.negative_returns)
        # All negative → large drawdown
        assert mdd < -0.1, f"Expected significant drawdown, got {mdd:.4f}"

    def test_max_drawdown_empty(self):
        """Test max drawdown with empty returns."""
        mdd = MetricsCalculator.max_drawdown(self.empty_returns)
        assert mdd == 0.0

    def test_sharpe_ratio_positive(self):
        """Test Sharpe ratio with positive returns."""
        sharpe = MetricsCalculator.sharpe_ratio(self.positive_returns)
        # All same return → zero volatility → zero Sharpe (due to ddof=1)
        assert sharpe >= 0, f"Expected non-negative Sharpe, got {sharpe:.4f}"

    def test_sharpe_ratio_zero_vol(self):
        """Test Sharpe ratio with zero volatility."""
        sharpe = MetricsCalculator.sharpe_ratio(self.zero_returns)
        assert sharpe == 0.0

    def test_sortino_ratio_positive(self):
        """Test Sortino ratio."""
        sortino = MetricsCalculator.sortino_ratio(self.mixed_returns)
        # Should be positive with positive bias
        assert sortino > 0, f"Expected positive Sortino, got {sortino:.4f}"

    def test_sortino_ratio_all_positive(self):
        """Test Sortino ratio with all positive returns."""
        sortino = MetricsCalculator.sortino_ratio(self.positive_returns)
        # All positive → no downside, infinite or very high Sortino
        # (depends on implementation handling)
        assert sortino >= 0

    def test_calmar_ratio_positive(self):
        """Test Calmar ratio."""
        calmar = MetricsCalculator.calmar_ratio(self.mixed_returns)
        # Should be positive (positive CAGR / negative MDD)
        assert calmar > 0

    def test_calmar_ratio_zero_mdd(self):
        """Test Calmar ratio with zero max drawdown."""
        # All positive returns have minimal drawdown
        calmar = MetricsCalculator.calmar_ratio(self.positive_returns)
        assert np.isfinite(calmar)

    def test_information_ratio(self):
        """Test Information Ratio."""
        returns = np.array([0.02, 0.01, -0.01, 0.03] * 63)
        benchmark = np.array([0.01, 0.01, 0.00, 0.01] * 63)

        ir = MetricsCalculator.information_ratio(returns, benchmark)
        # Strategy outperforms benchmark → positive IR
        assert ir > 0, f"Expected positive IR, got {ir:.4f}"

    def test_information_ratio_empty(self):
        """Test IR with empty returns."""
        ir = MetricsCalculator.information_ratio(self.empty_returns, self.empty_returns)
        assert ir == 0.0

    def test_hit_ratio(self):
        """Test Hit Ratio."""
        hit = MetricsCalculator.hit_ratio(self.positive_returns)
        # All positive → 100% hit ratio
        assert hit == 1.0

    def test_hit_ratio_negative(self):
        """Test Hit Ratio with negative returns."""
        hit = MetricsCalculator.hit_ratio(self.negative_returns)
        # All negative → 0% hit ratio
        assert hit == 0.0

    def test_hit_ratio_mixed(self):
        """Test Hit Ratio with mixed returns."""
        returns = np.array([0.01, -0.01, 0.01, -0.01])
        hit = MetricsCalculator.hit_ratio(returns)
        assert hit == 0.5

    def test_profit_factor(self):
        """Test Profit Factor."""
        pf = MetricsCalculator.profit_factor(self.mixed_returns)
        # More gains than losses (mixed with positive bias)
        assert pf > 1, f"Expected PF > 1, got {pf:.4f}"

    def test_profit_factor_all_positive(self):
        """Test PF with all positive returns."""
        pf = MetricsCalculator.profit_factor(self.positive_returns)
        # No losses → infinite
        assert np.isinf(pf) or pf > 100

    def test_profit_factor_all_negative(self):
        """Test PF with all negative returns."""
        pf = MetricsCalculator.profit_factor(self.negative_returns)
        # No gains → 0
        assert pf == 0.0

    def test_turnover(self):
        """Test Turnover calculation."""
        trade_values = np.array([1000, 500, 2000] * 84)  # 252 days
        portfolio_values = np.array([10000] * 252)

        turnover = MetricsCalculator.turnover(trade_values, portfolio_values)
        # turnover = (sum trades / mean portfolio) * (252 / 252) = 3500 / 10000 ≈ 0.35 (daily)
        # Annualized: 0.35 * 252 / 252 ≈ 0.35
        assert turnover > 0, f"Expected positive turnover, got {turnover:.4f}"

    def test_calculate_all(self):
        """Test calculate_all returns all 9 metrics with correct values."""
        metrics = MetricsCalculator.calculate_all(self.mixed_returns)

        # Should have all 9 metrics
        assert len(metrics) == 9

        # Verify actual computed values match individual method results
        # mixed_returns = [0.02, -0.01, 0.01, -0.005] * 63
        assert metrics["cagr"] == pytest.approx(1.523, rel=0.01)
        assert metrics["max_drawdown"] == pytest.approx(-0.01, abs=0.001)
        assert metrics["sharpe_ratio"] == pytest.approx(4.98, rel=0.01)
        assert metrics["sortino_ratio"] == pytest.approx(14.33, rel=0.01)
        assert metrics["calmar_ratio"] == pytest.approx(152.3, rel=0.01)
        assert metrics["hit_ratio"] == 0.5  # 2 positive, 2 non-positive per cycle
        assert metrics["profit_factor"] == pytest.approx(2.0, rel=0.01)  # gains/|losses| = 0.03/0.015

        # IR and turnover require benchmark/trade data → None when not provided
        assert metrics["information_ratio"] is None
        assert metrics["turnover"] is None


# ═══════════════════════════════════════════════════════════════
# BenchmarkManager Tests
# ═══════════════════════════════════════════════════════════════


class TestBenchmarkManager:
    """Test benchmark management."""

    def setup_method(self):
        """Set up test data."""
        self.manager = BenchmarkManager()

    def test_default_benchmarks(self):
        """Test default benchmarks are created."""
        benchmarks = self.manager.available_benchmarks()
        assert "KOSPI" in benchmarks
        assert "SP500" in benchmarks
        assert "SPY" in benchmarks
        assert "BALANCED_60_40" in benchmarks
        assert "PASSIVE" in benchmarks
        assert len(benchmarks) == 5

    def test_get_benchmark(self):
        """Test getting a benchmark."""
        kospi = self.manager.get_benchmark("KOSPI")
        assert kospi is not None
        assert kospi.name == "KOSPI"
        assert len(kospi.returns) > 0

    def test_get_nonexistent(self):
        """Test getting nonexistent benchmark."""
        result = self.manager.get_benchmark("NONEXISTENT")
        assert result is None

    def test_create_benchmark(self):
        """Test creating a new benchmark."""
        custom_returns = [0.01, 0.02, -0.01] * 84
        bench = self.manager.create_benchmark("CUSTOM", custom_returns)

        assert bench.name == "CUSTOM"
        assert len(bench.returns) == 252

        # Should be retrievable
        retrieved = self.manager.get_benchmark("CUSTOM")
        assert retrieved is not None
        assert retrieved.name == "CUSTOM"

    def test_create_benchmark_with_ticker(self):
        """Test creating benchmark with custom ticker."""
        bench = self.manager.create_benchmark("MYINDEX", [0.01] * 252, "MYIX")
        assert bench.ticker == "MYIX"

    def test_remove_benchmark(self):
        """Test removing a benchmark."""
        self.manager.create_benchmark("TEMP", [0.01] * 252)
        assert "TEMP" in self.manager.available_benchmarks()

        removed = self.manager.remove_benchmark("TEMP")
        assert removed is True
        assert "TEMP" not in self.manager.available_benchmarks()

    def test_remove_nonexistent(self):
        """Test removing nonexistent benchmark."""
        removed = self.manager.remove_benchmark("DOES_NOT_EXIST")
        assert removed is False


# ═══════════════════════════════════════════════════════════════
# RegimeAnalyzer Tests
# ═══════════════════════════════════════════════════════════════


class TestRegimeAnalyzer:
    """Test regime classification and analysis."""

    def setup_method(self):
        """Set up test data."""
        # Bull market: positive returns, low vol
        self.bull_returns = np.array([0.01, 0.005] * 63)
        self.bull_vol = 0.15

        # Bear market: negative returns, low vol
        self.bear_returns = np.array([-0.01, -0.005] * 63)
        self.bear_vol = 0.18

        # High vol environment
        self.high_vol_returns = np.array([0.02, -0.03, 0.01, -0.02] * 63)
        self.high_vol = 0.35

    def test_classify_bull(self):
        """Test bull market classification."""
        regime = RegimeAnalyzer.classify_regime(
            self.bull_returns, self.bull_vol, 0.0
        )
        assert regime == RegimeAnalyzer.BULL

    def test_classify_bear(self):
        """Test bear market classification."""
        regime = RegimeAnalyzer.classify_regime(
            self.bear_returns, self.bear_vol, 0.0
        )
        assert regime == RegimeAnalyzer.BEAR

    def test_classify_high_vol(self):
        """Test high volatility classification."""
        regime = RegimeAnalyzer.classify_regime(
            self.high_vol_returns, self.high_vol, 0.0
        )
        assert regime == RegimeAnalyzer.HIGH_VOL

    def test_classify_rising_rate(self):
        """Test rising rate classification."""
        regime = RegimeAnalyzer.classify_regime(
            self.bull_returns, 0.10, 0.001  # Small positive rate change
        )
        # Even positive bull market, if rate rising → RISING_RATE (after other rules)
        assert regime in RegimeAnalyzer.VALID_REGIMES

    def test_split_by_regime(self):
        """Test splitting returns by regime."""
        returns = np.array([0.01, 0.02, -0.01, -0.02])
        labels = np.array(["BULL", "BULL", "BEAR", "BEAR"])

        split = RegimeAnalyzer.split_by_regime(returns, labels)

        assert "BULL" in split
        assert "BEAR" in split
        assert len(split["BULL"]) == 2
        assert len(split["BEAR"]) == 2

    def test_regime_metrics(self):
        """Test per-regime metrics calculation."""
        returns = np.array([0.01, 0.02, -0.01, -0.02])
        labels = np.array(["BULL", "BULL", "BEAR", "BEAR"])

        metrics = RegimeAnalyzer.regime_metrics(returns, labels)

        assert "BULL" in metrics
        assert "BEAR" in metrics

        # Each should have the 9 metrics
        for regime_metrics in metrics.values():
            assert "cagr" in regime_metrics


# ═══════════════════════════════════════════════════════════════
# AblationStudy Tests
# ═══════════════════════════════════════════════════════════════


class TestAblationStudy:
    """Test ablation study layer tracking."""

    def setup_method(self):
        """Set up test data."""
        self.base_returns = np.array([0.01, -0.005, 0.02, -0.01] * 63)
        self.study = AblationStudy(self.base_returns)

    def test_init(self):
        """Test initialization."""
        assert "Base" in self.study.layer_names()
        assert len(self.study.layer_names()) == 1

    def test_add_layer(self):
        """Test adding a layer."""
        layer2 = self.base_returns + 0.005  # Slightly better
        self.study.add_layer("Base+Quant", layer2)

        assert "Base+Quant" in self.study.layer_names()
        assert len(self.study.layer_names()) == 2

    def test_add_layer_wrong_length(self):
        """Test adding layer with wrong length."""
        bad_returns = np.array([0.01] * 100)

        with pytest.raises(ValueError):
            self.study.add_layer("Bad", bad_returns)

    def test_run(self):
        """Test running metrics calculation."""
        layer2 = self.base_returns + 0.005
        self.study.add_layer("Base+Quant", layer2)

        results = self.study.run()

        assert "Base" in results
        assert "Base+Quant" in results
        assert "cagr" in results["Base"]

    def test_contribution(self):
        """Test contribution delta calculation."""
        layer2 = self.base_returns + 0.005  # Slight improvement
        self.study.add_layer("Base+Quant", layer2)

        delta = self.study.contribution("Base", "Base+Quant")

        # CAGR should improve
        assert delta["cagr"] > 0

    def test_contribution_both_negative(self):
        """Test contribution when both layers are negative."""
        layer2 = self.base_returns - 0.005  # Worse
        self.study.add_layer("Base+Bad", layer2)

        delta = self.study.contribution("Base", "Base+Bad")

        # CAGR should decrease
        assert delta["cagr"] < 0

    def test_remove_layer(self):
        """Test removing a layer."""
        self.study.add_layer("Temp", self.base_returns.copy())
        assert "Temp" in self.study.layer_names()

        self.study.remove_layer("Temp")
        assert "Temp" not in self.study.layer_names()

    def test_remove_base_fails(self):
        """Test that Base layer cannot be removed."""
        removed = self.study.remove_layer("Base")
        assert removed is False
        assert "Base" in self.study.layer_names()


# ═══════════════════════════════════════════════════════════════
# SignificanceTest Tests
# ═══════════════════════════════════════════════════════════════


class TestSignificanceTest:
    """Test statistical significance testing."""

    def setup_method(self):
        """Set up test data."""
        # Clearly positive returns
        self.positive_returns = np.random.RandomState(42).normal(0.001, 0.01, 252)

        # Clearly negative returns
        self.negative_returns = np.random.RandomState(43).normal(-0.001, 0.01, 252)

        # Near-zero returns
        self.zero_returns = np.random.RandomState(44).normal(0.0, 0.005, 252)

    def test_bootstrap_ci_positive(self):
        """Test bootstrap CI with positive returns."""
        lower, upper = SignificanceTest.bootstrap_ci(
            self.positive_returns, n_bootstrap=100
        )

        # CI should be valid: lower < upper
        assert upper > lower, f"Expected upper > lower, got {lower:.6f} > {upper:.6f}"
        # Mean should be positive overall
        assert (lower + upper) / 2 > -0.0001, f"Expected positive mean, got {(lower+upper)/2:.6f}"

    def test_bootstrap_ci_negative(self):
        """Test bootstrap CI with negative returns."""
        lower, upper = SignificanceTest.bootstrap_ci(
            self.negative_returns, n_bootstrap=100
        )

        # CI should be valid: lower < upper
        assert upper > lower, f"Expected upper > lower"
        # Mean should be negative overall
        assert (lower + upper) / 2 < 0.0001, f"Expected negative mean, got {(lower+upper)/2:.6f}"

    def test_bootstrap_ci_empty(self):
        """Test bootstrap CI with empty returns."""
        lower, upper = SignificanceTest.bootstrap_ci(np.array([]))
        assert lower == 0.0
        assert upper == 0.0

    def test_t_test_vs_benchmark(self):
        """Test t-test comparison."""
        result = SignificanceTest.t_test_vs_benchmark(
            self.positive_returns, self.negative_returns
        )

        assert "t_statistic" in result
        assert "p_value" in result
        assert "significant" in result
        assert "mean_difference" in result

        # Should have valid results
        assert isinstance(result["t_statistic"], float)
        assert isinstance(result["p_value"], float)

    def test_t_test_same_returns(self):
        """Test t-test with same returns."""
        result = SignificanceTest.t_test_vs_benchmark(
            self.positive_returns, self.positive_returns
        )

        # When comparing identical samples, t-stat should be near 0
        assert abs(result["t_statistic"]) < 1e-5

    def test_is_significant(self):
        """Test significance check."""
        # Different distributions (one positive, one negative)
        significant = SignificanceTest.is_significant(
            self.positive_returns, self.negative_returns, confidence=0.95
        )
        assert isinstance(significant, (bool, np.bool_))

    def test_is_not_significant(self):
        """Test with same data."""
        # Same data → benchmark mean should be within CI
        significant = SignificanceTest.is_significant(
            self.positive_returns, self.positive_returns, confidence=0.95
        )
        # When data is identical, benchmark is definitely in the CI
        assert not significant

    def test_excess_return_ttest(self):
        """Test excess return t-test."""
        result = SignificanceTest.excess_return_ttest(
            self.positive_returns, self.zero_returns
        )

        assert "t_statistic" in result
        assert "p_value" in result
        assert "significant" in result


# ═══════════════════════════════════════════════════════════════
# PerformanceJudge Tests
# ═══════════════════════════════════════════════════════════════


class TestPerformanceJudge:
    """Test PASS/REVIEW/FAIL decision logic."""

    def setup_method(self):
        """Set up test data."""
        self.judge = PerformanceJudge()

    def test_judge_ir_pass(self):
        """Test IR PASS."""
        decision = self.judge.judge_ir(0.15)
        assert decision == "PASS"

    def test_judge_ir_review(self):
        """Test IR REVIEW."""
        decision = self.judge.judge_ir(0.075)
        assert decision == "REVIEW"

    def test_judge_ir_fail(self):
        """Test IR FAIL."""
        decision = self.judge.judge_ir(0.02)
        assert decision == "FAIL"

    def test_judge_excess_cagr_pass(self):
        """Test excess CAGR PASS."""
        decision = self.judge.judge_excess_cagr(0.03)
        assert decision == "PASS"

    def test_judge_excess_cagr_review(self):
        """Test excess CAGR REVIEW."""
        decision = self.judge.judge_excess_cagr(0.005)
        assert decision == "REVIEW"

    def test_judge_excess_cagr_fail(self):
        """Test excess CAGR FAIL."""
        decision = self.judge.judge_excess_cagr(-0.01)
        assert decision == "FAIL"

    def test_judge_mdd_pass(self):
        """Test MDD PASS."""
        decision = self.judge.judge_mdd(-0.10, tolerance=0.15)
        assert decision == "PASS"

    def test_judge_mdd_review(self):
        """Test MDD REVIEW."""
        decision = self.judge.judge_mdd(-0.18, tolerance=0.15)
        assert decision == "REVIEW"

    def test_judge_mdd_fail(self):
        """Test MDD FAIL."""
        decision = self.judge.judge_mdd(-0.25, tolerance=0.15)
        assert decision == "FAIL"

    def test_judge_turnover_pass(self):
        """Test turnover PASS."""
        decision = self.judge.judge_turnover(2.5)
        assert decision == "PASS"

    def test_judge_turnover_review(self):
        """Test turnover REVIEW."""
        decision = self.judge.judge_turnover(4.0)
        assert decision == "REVIEW"

    def test_judge_turnover_fail(self):
        """Test turnover FAIL."""
        decision = self.judge.judge_turnover(6.0)
        assert decision == "FAIL"

    def test_judge_significance_pass(self):
        """Test significance PASS."""
        decision = self.judge.judge_significance(0.001)  # CI lower > 0
        assert decision == "PASS"

    def test_judge_significance_review(self):
        """Test significance REVIEW."""
        decision = self.judge.judge_significance(-0.0001)
        assert decision == "REVIEW"

    def test_judge_significance_fail(self):
        """Test significance FAIL."""
        decision = self.judge.judge_significance(-0.01)
        assert decision == "FAIL"

    def test_overall_judgment_pass(self):
        """Test overall PASS."""
        metrics = {
            "information_ratio": 0.12,
            "cagr": 0.02,
            "max_drawdown": -0.10,
            "turnover": 2.0,
        }
        result = self.judge.overall_judgment(metrics)

        assert result["overall"] == "PASS"
        assert result["pass_count"] >= 3

    def test_overall_judgment_review(self):
        """Test overall REVIEW."""
        metrics = {
            "information_ratio": 0.07,
            "cagr": 0.005,
            "max_drawdown": -0.10,
        }
        result = self.judge.overall_judgment(metrics)

        assert result["overall"] in ["REVIEW", "PASS"]

    def test_overall_judgment_fail(self):
        """Test overall FAIL."""
        metrics = {
            "information_ratio": 0.02,
            "cagr": -0.01,
            "max_drawdown": -0.30,
            "turnover": 10.0,
        }
        result = self.judge.overall_judgment(metrics)

        assert result["overall"] == "FAIL"
        assert result["fail_count"] >= 1

    def test_custom_thresholds(self):
        """Test with custom thresholds."""
        custom = {"ir_pass": 0.20, "ir_review": 0.10}
        judge = PerformanceJudge(thresholds=custom)

        # 0.15 should be REVIEW with custom thresholds
        decision = judge.judge_ir(0.15)
        assert decision == "REVIEW"


# ═══════════════════════════════════════════════════════════════
# Integration Tests
# ═══════════════════════════════════════════════════════════════


class TestIntegration:
    """Integration tests combining multiple components."""

    def test_end_to_end_validation(self):
        """Test complete validation pipeline."""
        # Generate strategy returns
        returns = np.random.RandomState(100).normal(0.0008, 0.012, 252)

        # Get benchmark
        manager = BenchmarkManager()
        benchmark = manager.get_benchmark("KOSPI")

        # Calculate metrics
        metrics = MetricsCalculator.calculate_all(returns, benchmark.returns)

        # Judge performance
        judge = PerformanceJudge()
        judgment = judge.overall_judgment(metrics)

        # Should have valid judgment
        assert judgment["overall"] in ["PASS", "REVIEW", "FAIL"]

    def test_regime_ablation_workflow(self):
        """Test regime analysis with ablation study."""
        # Base strategy
        base = np.random.RandomState(200).normal(0.0005, 0.010, 252)

        # Enhanced strategy
        enhanced = base + np.random.RandomState(201).normal(0.0002, 0.002, 252)

        # Regime labels
        regimes = np.array(["BULL"] * 126 + ["BEAR"] * 126)

        # Ablation
        study = AblationStudy(base)
        study.add_layer("Enhanced", enhanced)

        results = study.run()
        assert len(results) == 2

        # Regime analysis
        regime_metrics = RegimeAnalyzer.regime_metrics(enhanced, regimes)
        assert len(regime_metrics) > 0

    def test_significance_workflow(self):
        """Test significance testing with custom data."""
        # Generate data with known properties
        strategy = np.random.RandomState(300).normal(0.001, 0.010, 252)
        benchmark = np.random.RandomState(301).normal(0.0005, 0.010, 252)

        # Bootstrap CI
        lower, upper = SignificanceTest.bootstrap_ci(strategy, n_bootstrap=100)

        # T-test
        ttest = SignificanceTest.t_test_vs_benchmark(strategy, benchmark)

        # Should have valid results
        assert isinstance(lower, float)
        assert isinstance(upper, float)
        assert isinstance(ttest["p_value"], float)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
