"""
파라미터 민감도 분석 테스트

모델, 스윕 생성기, 분석기, 엔진, API를 검증합니다.
"""

import unittest

import numpy as np
import pandas as pd
import pytest

from core.param_sensitivity.analyzer import SensitivityAnalyzer
from core.param_sensitivity.engine import ParamSensitivityEngine
from core.param_sensitivity.models import (
    ParamCategory,
    ParamElasticity,
    ParamRange,
    ParamTrialResult,
    SensitivityRun,
    SensitivityRunRequest,
    SensitivityStatus,
    SweepMethod,
)
from core.param_sensitivity.sweep_generator import (
    DEFAULT_PARAM_RANGES,
    SweepGenerator,
)

# ══════════════════════════════════════
# 1. ParamRange 모델 테스트
# ══════════════════════════════════════


class TestParamRange(unittest.TestCase):
    """파라미터 범위 모델 테스트"""

    def test_valid_range(self):
        pr = ParamRange(
            name="test",
            category=ParamCategory.TECHNICAL,
            base_value=14,
            min_value=7,
            max_value=28,
            step=7,
        )
        assert pr.validate() is True

    def test_invalid_min_greater_than_max(self):
        pr = ParamRange(
            name="test",
            category=ParamCategory.TECHNICAL,
            base_value=14,
            min_value=28,
            max_value=7,
        )
        assert pr.validate() is False

    def test_invalid_base_outside_range(self):
        pr = ParamRange(
            name="test",
            category=ParamCategory.TECHNICAL,
            base_value=50,
            min_value=7,
            max_value=28,
        )
        assert pr.validate() is False

    def test_invalid_negative_step(self):
        pr = ParamRange(
            name="test",
            category=ParamCategory.TECHNICAL,
            base_value=14,
            min_value=7,
            max_value=28,
            step=-1,
        )
        assert pr.validate() is False

    def test_grid_values_with_step(self):
        pr = ParamRange(
            name="test",
            category=ParamCategory.TECHNICAL,
            base_value=14,
            min_value=7,
            max_value=28,
            step=7,
        )
        values = pr.grid_values()
        assert values == [7, 14, 21, 28]

    def test_grid_values_without_step(self):
        pr = ParamRange(
            name="test",
            category=ParamCategory.TECHNICAL,
            base_value=5,
            min_value=0,
            max_value=10,
            n_samples=3,
        )
        values = pr.grid_values()
        assert len(values) == 3
        assert values[0] == 0
        assert values[-1] == 10

    def test_grid_values_single_sample(self):
        pr = ParamRange(
            name="test",
            category=ParamCategory.TECHNICAL,
            base_value=5,
            min_value=0,
            max_value=10,
            n_samples=1,
        )
        values = pr.grid_values()
        assert values == [5]


# ══════════════════════════════════════
# 2. ParamTrialResult 테스트
# ══════════════════════════════════════


class TestParamTrialResult(unittest.TestCase):
    """파라미터 trial 결과 테스트"""

    def test_to_dict(self):
        trial = ParamTrialResult(
            param_values={"a": 1.0, "b": 2.0},
            sharpe_ratio=1.5,
            cagr=0.15,
            mdd=-0.10,
        )
        d = trial.to_dict()
        assert d["sharpe_ratio"] == 1.5
        assert d["cagr"] == 0.15
        assert d["param_values"] == {"a": 1.0, "b": 2.0}


# ══════════════════════════════════════
# 3. ParamElasticity 테스트
# ══════════════════════════════════════


class TestParamElasticity(unittest.TestCase):
    """탄성치 모델 테스트"""

    def test_impact_score_calculation(self):
        e = ParamElasticity(
            param_name="test",
            category=ParamCategory.TECHNICAL,
            base_value=14,
            sharpe_elasticity=1.0,
            cagr_elasticity=0.5,
            mdd_elasticity=0.5,
        )
        # 0.4*1.0 + 0.3*0.5 + 0.3*0.5 = 0.7
        assert abs(e.impact_score - 0.7) < 0.001

    def test_to_dict_includes_all_fields(self):
        e = ParamElasticity(
            param_name="rsi_period",
            category=ParamCategory.TECHNICAL,
            base_value=14,
        )
        d = e.to_dict()
        assert "param_name" in d
        assert "impact_score" in d
        assert "monotonicity" in d
        assert "stable_range" in d


# ══════════════════════════════════════
# 4. SensitivityRun 테스트
# ══════════════════════════════════════


class TestSensitivityRun(unittest.TestCase):
    """민감도 분석 실행 기록 테스트"""

    def test_to_dict(self):
        run = SensitivityRun(
            run_id="test_001",
            strategy_version="v1.0",
            sweep_method=SweepMethod.GRID,
            param_ranges=[],
            status=SensitivityStatus.COMPLETED,
            base_sharpe=1.0,
            best_sharpe=1.5,
        )
        d = run.to_dict()
        assert d["run_id"] == "test_001"
        assert d["improvement"] == 0.5
        assert d["status"] == "COMPLETED"

    def test_to_summary_dict(self):
        run = SensitivityRun(
            run_id="test_002",
            strategy_version="v1.0",
            sweep_method=SweepMethod.GRID,
            param_ranges=[],
            elasticities=[
                ParamElasticity(
                    param_name="a",
                    category=ParamCategory.TECHNICAL,
                    base_value=1,
                    sharpe_elasticity=2.0,
                ),
                ParamElasticity(
                    param_name="b",
                    category=ParamCategory.COST,
                    base_value=1,
                    sharpe_elasticity=1.0,
                ),
            ],
        )
        s = run.to_summary_dict()
        assert "top_sensitive_params" in s
        assert s["top_sensitive_params"][0] == "a"  # 더 높은 impact

    def test_request_model_validation(self):
        req = SensitivityRunRequest(strategy_version="v1.0")
        assert req.sweep_method == SweepMethod.GRID
        assert len(req.tickers) == 3

    def test_request_model_extra_forbidden(self):
        with pytest.raises(Exception):
            SensitivityRunRequest(
                strategy_version="v1.0",
                unknown_field="value",
            )


# ══════════════════════════════════════
# 5. SweepGenerator 테스트
# ══════════════════════════════════════


class TestSweepGenerator(unittest.TestCase):
    """스윕 생성기 테스트"""

    def _simple_ranges(self):
        return [
            ParamRange(
                name="a",
                category=ParamCategory.TECHNICAL,
                base_value=2,
                min_value=1,
                max_value=3,
                step=1,
            ),
            ParamRange(
                name="b",
                category=ParamCategory.COST,
                base_value=5,
                min_value=4,
                max_value=6,
                step=1,
            ),
        ]

    def test_grid_generation(self):
        gen = SweepGenerator(self._simple_ranges(), method=SweepMethod.GRID)
        trials = gen.generate()
        # a: [1,2,3], b: [4,5,6] → 3×3 = 9
        assert len(trials) == 9

    def test_grid_values_correct(self):
        gen = SweepGenerator(self._simple_ranges(), method=SweepMethod.GRID)
        trials = gen.generate()
        a_vals = {t["a"] for t in trials}
        b_vals = {t["b"] for t in trials}
        assert a_vals == {1, 2, 3}
        assert b_vals == {4, 5, 6}

    def test_random_generation(self):
        gen = SweepGenerator(
            self._simple_ranges(),
            method=SweepMethod.RANDOM,
            max_trials=20,
        )
        trials = gen.generate()
        assert len(trials) == 20

    def test_random_values_in_range(self):
        gen = SweepGenerator(
            self._simple_ranges(),
            method=SweepMethod.RANDOM,
            max_trials=50,
        )
        trials = gen.generate()
        for t in trials:
            assert 1 <= t["a"] <= 3
            assert 4 <= t["b"] <= 6

    def test_oat_generation(self):
        gen = SweepGenerator(self._simple_ranges())
        trials = gen.generate_one_at_a_time()
        # a: [1,2,3] → 2 non-base + b: [4,5,6] → 2 non-base + 1 base = 5
        assert len(trials) == 5

    def test_oat_includes_base(self):
        gen = SweepGenerator(self._simple_ranges())
        trials = gen.generate_one_at_a_time()
        base = gen.base_values
        has_base = any(all(abs(t[k] - v) < 1e-8 for k, v in base.items()) for t in trials)
        assert has_base is True

    def test_base_values(self):
        gen = SweepGenerator(self._simple_ranges())
        assert gen.base_values == {"a": 2, "b": 5}

    def test_param_names(self):
        gen = SweepGenerator(self._simple_ranges())
        assert gen.param_names == ["a", "b"]

    def test_grid_max_trials_cap(self):
        ranges = [
            ParamRange(
                name=f"p{i}",
                category=ParamCategory.TECHNICAL,
                base_value=5,
                min_value=1,
                max_value=10,
                step=1,
            )
            for i in range(5)
        ]
        # 10^5 = 100,000 combinations, capped at 100
        gen = SweepGenerator(ranges, method=SweepMethod.GRID, max_trials=100)
        trials = gen.generate()
        assert len(trials) == 100

    def test_invalid_range_raises(self):
        bad_range = [
            ParamRange(
                name="bad",
                category=ParamCategory.TECHNICAL,
                base_value=5,
                min_value=10,
                max_value=1,
            ),
        ]
        with pytest.raises(ValueError):
            SweepGenerator(bad_range)

    def test_default_param_ranges_valid(self):
        """기본 파라미터 범위가 모두 유효한지 확인"""
        for pr in DEFAULT_PARAM_RANGES:
            assert pr.validate() is True, f"{pr.name} is invalid"


# ══════════════════════════════════════
# 6. SensitivityAnalyzer 테스트
# ══════════════════════════════════════


class TestSensitivityAnalyzer(unittest.TestCase):
    """민감도 분석기 테스트"""

    def _make_trials(self):
        """OAT 방식 테스트 데이터: param 'x' 변경"""
        base = {"x": 10.0, "y": 5.0}
        return [
            # base
            ParamTrialResult(param_values={"x": 10.0, "y": 5.0}, sharpe_ratio=1.0, cagr=0.10, mdd=-0.15),
            # x varied
            ParamTrialResult(param_values={"x": 5.0, "y": 5.0}, sharpe_ratio=0.8, cagr=0.08, mdd=-0.12),
            ParamTrialResult(param_values={"x": 15.0, "y": 5.0}, sharpe_ratio=1.2, cagr=0.12, mdd=-0.18),
            ParamTrialResult(param_values={"x": 20.0, "y": 5.0}, sharpe_ratio=1.4, cagr=0.14, mdd=-0.20),
            # y varied
            ParamTrialResult(param_values={"x": 10.0, "y": 3.0}, sharpe_ratio=1.1, cagr=0.11, mdd=-0.14),
            ParamTrialResult(param_values={"x": 10.0, "y": 7.0}, sharpe_ratio=0.9, cagr=0.09, mdd=-0.16),
        ], base

    def _make_ranges(self):
        return [
            ParamRange(name="x", category=ParamCategory.TECHNICAL, base_value=10.0, min_value=5.0, max_value=20.0),
            ParamRange(name="y", category=ParamCategory.COST, base_value=5.0, min_value=3.0, max_value=7.0),
        ]

    def test_compute_elasticities_returns_all_params(self):
        trials, base = self._make_trials()
        analyzer = SensitivityAnalyzer(self._make_ranges(), base)
        elasticities = analyzer.compute_elasticities(trials)
        assert len(elasticities) == 2
        names = {e.param_name for e in elasticities}
        assert names == {"x", "y"}

    def test_higher_impact_param_ranked_first(self):
        """'x'는 'y'보다 impact가 높아야 함 (더 큰 Sharpe 변동)"""
        trials, base = self._make_trials()
        analyzer = SensitivityAnalyzer(self._make_ranges(), base)
        elasticities = analyzer.compute_elasticities(trials)
        # elasticities는 impact_score 내림차순으로 정렬됨
        assert elasticities[0].param_name == "x"

    def test_sharpe_range_correct(self):
        trials, base = self._make_trials()
        analyzer = SensitivityAnalyzer(self._make_ranges(), base)
        elasticities = analyzer.compute_elasticities(trials)
        x_elast = next(e for e in elasticities if e.param_name == "x")
        # x의 Sharpe 범위: 0.8 (x=5) ~ 1.4 (x=20) 중 x만 변경된 것 포함 base
        assert x_elast.sharpe_range[0] <= 1.0  # base 포함
        assert x_elast.sharpe_range[1] >= 1.2

    def test_monotonicity_positive_for_x(self):
        """x가 증가하면 Sharpe도 증가 → 양의 단조성"""
        trials, base = self._make_trials()
        analyzer = SensitivityAnalyzer(self._make_ranges(), base)
        elasticities = analyzer.compute_elasticities(trials)
        x_elast = next(e for e in elasticities if e.param_name == "x")
        assert x_elast.monotonicity > 0

    def test_tornado_ranking(self):
        trials, base = self._make_trials()
        analyzer = SensitivityAnalyzer(self._make_ranges(), base)
        elasticities = analyzer.compute_elasticities(trials)
        ranking = analyzer.tornado_ranking(elasticities, metric="sharpe")
        assert len(ranking) == 2
        assert ranking[0]["spread"] >= ranking[1]["spread"]
        assert "param" in ranking[0]
        assert "low" in ranking[0]
        assert "high" in ranking[0]

    def test_empty_trials_returns_empty(self):
        analyzer = SensitivityAnalyzer(self._make_ranges(), {"x": 10.0, "y": 5.0})
        elasticities = analyzer.compute_elasticities([])
        assert elasticities == []

    def test_stable_range_contains_base(self):
        """안정 구간이 기본값을 포함해야 함"""
        trials, base = self._make_trials()
        analyzer = SensitivityAnalyzer(self._make_ranges(), base)
        elasticities = analyzer.compute_elasticities(trials)
        x_elast = next(e for e in elasticities if e.param_name == "x")
        assert x_elast.stable_range[0] <= 10.0
        assert x_elast.stable_range[1] >= 10.0


# ══════════════════════════════════════
# 7. ParamSensitivityEngine 테스트
# ══════════════════════════════════════


class TestParamSensitivityEngine(unittest.TestCase):
    """민감도 엔진 통합 테스트"""

    def _sample_data(self, days=60, tickers=None):
        tickers = tickers or ["A", "B"]
        np.random.seed(42)
        dates = pd.bdate_range("2025-01-01", periods=days)
        prices = pd.DataFrame(
            {t: 100 * np.cumprod(1 + np.random.normal(0.001, 0.02, days)) for t in tickers},
            index=dates,
        )
        signals = pd.DataFrame(
            {t: np.random.uniform(-0.5, 0.5, days) for t in tickers},
            index=dates,
        )
        return signals, prices

    def test_engine_run_completes(self):
        """엔진 실행이 COMPLETED 상태로 종료"""
        signals, prices = self._sample_data()
        # 작은 범위로 제한하여 빠르게 실행
        ranges = [
            ParamRange(
                name="commission_rate",
                category=ParamCategory.COST,
                base_value=0.00015,
                min_value=0.0,
                max_value=0.001,
                step=0.0005,
            ),
        ]
        engine = ParamSensitivityEngine(param_ranges=ranges)
        run = engine.run("v_test", signals, prices, use_oat=True)

        assert run.status == SensitivityStatus.COMPLETED
        assert run.total_trials > 0
        assert run.completed_trials == run.total_trials
        assert len(run.trial_results) > 0

    def test_engine_finds_best_params(self):
        """최적 파라미터가 기록되어야 함"""
        signals, prices = self._sample_data()
        ranges = [
            ParamRange(
                name="slippage_rate",
                category=ParamCategory.COST,
                base_value=0.001,
                min_value=0.0,
                max_value=0.005,
                step=0.0025,
            ),
        ]
        engine = ParamSensitivityEngine(param_ranges=ranges)
        run = engine.run("v_test", signals, prices, use_oat=True)

        assert run.best_params is not None
        assert "slippage_rate" in run.best_params

    def test_engine_computes_elasticities(self):
        """탄성치가 계산되어야 함"""
        signals, prices = self._sample_data()
        ranges = [
            ParamRange(
                name="commission_rate",
                category=ParamCategory.COST,
                base_value=0.00015,
                min_value=0.0,
                max_value=0.001,
                step=0.0005,
            ),
        ]
        engine = ParamSensitivityEngine(param_ranges=ranges)
        run = engine.run("v_test", signals, prices, use_oat=True)

        assert len(run.elasticities) == 1
        assert run.elasticities[0].param_name == "commission_rate"

    def test_engine_base_sharpe_recorded(self):
        """기본 Sharpe가 기록되어야 함"""
        signals, prices = self._sample_data()
        ranges = [
            ParamRange(
                name="commission_rate",
                category=ParamCategory.COST,
                base_value=0.00015,
                min_value=0.0,
                max_value=0.001,
                step=0.0005,
            ),
        ]
        engine = ParamSensitivityEngine(param_ranges=ranges)
        run = engine.run("v_test", signals, prices, use_oat=True)

        # base_sharpe는 0이 아닐 수 있지만 기록되어야 함
        assert isinstance(run.base_sharpe, float)

    def test_engine_multiple_params(self):
        """복수 파라미터 OAT 분석"""
        signals, prices = self._sample_data()
        ranges = [
            ParamRange(
                name="commission_rate",
                category=ParamCategory.COST,
                base_value=0.00015,
                min_value=0.0,
                max_value=0.0003,
                step=0.00015,
            ),
            ParamRange(
                name="slippage_rate",
                category=ParamCategory.COST,
                base_value=0.001,
                min_value=0.0,
                max_value=0.002,
                step=0.001,
            ),
        ]
        engine = ParamSensitivityEngine(param_ranges=ranges)
        run = engine.run("v_test", signals, prices, use_oat=True)

        assert len(run.elasticities) == 2
        assert run.status == SensitivityStatus.COMPLETED

    def test_engine_grid_mode(self):
        """Grid 방식 실행"""
        signals, prices = self._sample_data()
        ranges = [
            ParamRange(
                name="commission_rate",
                category=ParamCategory.COST,
                base_value=0.00015,
                min_value=0.0,
                max_value=0.0003,
                step=0.00015,
            ),
        ]
        engine = ParamSensitivityEngine(
            param_ranges=ranges,
            sweep_method=SweepMethod.GRID,
        )
        run = engine.run("v_test", signals, prices, use_oat=False)

        assert run.status == SensitivityStatus.COMPLETED
        assert run.total_trials > 0


# ══════════════════════════════════════
# 8. 통합 시나리오 테스트
# ══════════════════════════════════════


class TestSensitivityIntegration(unittest.TestCase):
    """민감도 분석 E2E 통합 테스트"""

    def test_full_pipeline_oat(self):
        """전체 파이프라인: 범위 정의 → 스윕 → 백테스트 → 탄성치 → 토네이도"""
        np.random.seed(42)
        dates = pd.bdate_range("2025-01-01", periods=60)
        prices = pd.DataFrame(
            {"AAPL": 150 * np.cumprod(1 + np.random.normal(0.001, 0.015, 60))},
            index=dates,
        )
        signals = pd.DataFrame(
            {"AAPL": np.random.uniform(-0.3, 0.3, 60)},
            index=dates,
        )

        ranges = [
            ParamRange(
                name="commission_rate",
                category=ParamCategory.COST,
                base_value=0.00015,
                min_value=0.0,
                max_value=0.0006,
                step=0.0003,
            ),
            ParamRange(
                name="rsi_oversold",
                category=ParamCategory.SIGNAL_THRESHOLD,
                base_value=30,
                min_value=20,
                max_value=40,
                step=10,
            ),
        ]

        engine = ParamSensitivityEngine(param_ranges=ranges)
        run = engine.run("v_integration", signals, prices, use_oat=True)

        # 실행 완료
        assert run.status == SensitivityStatus.COMPLETED
        assert run.completed_trials == run.total_trials

        # 탄성치 결과
        assert len(run.elasticities) == 2

        # 토네이도 차트 데이터
        analyzer = SensitivityAnalyzer(
            param_ranges=ranges,
            base_values={pr.name: pr.base_value for pr in ranges},
        )
        tornado = analyzer.tornado_ranking(run.elasticities, metric="sharpe")
        assert len(tornado) == 2
        assert all("spread" in t for t in tornado)

        # 결과 직렬화
        summary = run.to_summary_dict()
        assert "top_sensitive_params" in summary

        detail = run.to_dict()
        assert "elasticities" in detail
        assert len(detail["elasticities"]) == 2

    def test_cost_sensitivity_intuition(self):
        """비용 관련 파라미터 직관 검증: 비용 증가 → Sharpe 감소"""
        np.random.seed(42)
        dates = pd.bdate_range("2025-01-01", periods=120)
        prices = pd.DataFrame(
            {"X": 100 * np.cumprod(1 + np.random.normal(0.001, 0.02, 120))},
            index=dates,
        )
        signals = pd.DataFrame(
            {"X": np.random.uniform(-0.4, 0.4, 120)},
            index=dates,
        )

        ranges = [
            ParamRange(
                name="commission_rate",
                category=ParamCategory.COST,
                base_value=0.00015,
                min_value=0.0,
                max_value=0.003,
                step=0.001,
            ),
        ]

        engine = ParamSensitivityEngine(param_ranges=ranges)
        run = engine.run("v_cost_test", signals, prices, use_oat=True)

        assert run.status == SensitivityStatus.COMPLETED

        # commission 0.0 vs 0.003의 Sharpe 비교
        low_cost = [t for t in run.trial_results if abs(t.param_values.get("commission_rate", 0) - 0.0) < 1e-8]
        high_cost = [t for t in run.trial_results if abs(t.param_values.get("commission_rate", 0) - 0.003) < 1e-8]

        if low_cost and high_cost:
            # 비용이 없을 때 Sharpe가 높거나 같아야 함 (일반적 직관)
            assert low_cost[0].sharpe_ratio >= high_cost[0].sharpe_ratio
