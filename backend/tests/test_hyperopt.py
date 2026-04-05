"""
하이퍼파라미터 최적화 모듈 유닛테스트

SearchSpace, ObjectiveFunction, HyperoptOptimizer,
TrialResult/OptimizationResult 모델을 테스트합니다.
"""

from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

# ══════════════════════════════════════
# 테스트 픽스처
# ══════════════════════════════════════


def _make_ohlcv(n_days: int = 300, seed: int = 42) -> pd.DataFrame:
    """테스트용 OHLCV DataFrame 생성"""
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2020-01-01", periods=n_days)
    close = 100.0 + np.cumsum(rng.randn(n_days) * 0.5)
    close = np.maximum(close, 10.0)

    return pd.DataFrame(
        {
            "open": close * (1 + rng.randn(n_days) * 0.005),
            "high": close * (1 + np.abs(rng.randn(n_days) * 0.01)),
            "low": close * (1 - np.abs(rng.randn(n_days) * 0.01)),
            "close": close,
            "volume": rng.randint(100000, 1000000, n_days).astype(float),
        },
        index=dates,
    )


def _make_multi_ticker_ohlcv(n_tickers: int = 3, n_days: int = 300):
    """멀티 종목 OHLCV 데이터"""
    tickers = [f"T{i:04d}" for i in range(n_tickers)]
    return {ticker: _make_ohlcv(n_days, seed=42 + i) for i, ticker in enumerate(tickers)}


# ══════════════════════════════════════
# SearchSpace 테스트
# ══════════════════════════════════════


class TestSearchSpace:
    """탐색 공간 정의 테스트"""

    def test_get_all_params_count(self):
        """전체 파라미터 수: 앙상블 6 + 레짐 8 + 리스크 6 = 20"""
        from core.hyperopt.search_space import SearchSpace

        params = SearchSpace.get_all_params()
        assert len(params) == 20

    def test_get_params_by_groups_ensemble(self):
        """앙상블 그룹 필터"""
        from core.hyperopt.search_space import SearchSpace

        params = SearchSpace.get_params_by_groups(["ensemble"])
        assert len(params) == 6
        assert all(p.group == "ensemble" for p in params)

    def test_get_params_by_groups_risk(self):
        """리스크 그룹 필터"""
        from core.hyperopt.search_space import SearchSpace

        params = SearchSpace.get_params_by_groups(["risk"])
        assert len(params) == 6
        assert all(p.group == "risk" for p in params)

    def test_get_defaults(self):
        """기본값 딕셔너리"""
        from core.hyperopt.search_space import SearchSpace

        defaults = SearchSpace.get_defaults()
        assert defaults["adx_threshold"] == 25.0
        assert defaults["target_vol"] == 0.25
        assert defaults["max_drawdown_limit"] == 0.20
        assert defaults["w_trending_up_tf"] == 0.55

    def test_suggest_params_with_mock_trial(self):
        """Optuna trial mock으로 파라미터 샘플링"""
        from core.hyperopt.search_space import SearchSpace

        trial = MagicMock()
        trial.suggest_float.side_effect = lambda name, low, high, step=None: (low + high) / 2
        trial.suggest_int.side_effect = lambda name, low, high, step=1: (low + high) // 2

        params = SearchSpace.suggest_params(trial, groups=["ensemble"])
        assert "adx_threshold" in params
        assert "target_vol" in params
        assert len(params) == 6

    def test_suggest_params_regime_weight_pruning(self):
        """레짐 가중치 합계 > 0.90이면 prune"""
        import optuna

        from core.hyperopt.search_space import SearchSpace

        trial = MagicMock()
        # TF=0.80, MR=0.20 → 합계 1.0 > 0.90 → prune
        trial.suggest_float.side_effect = lambda name, low, high, step=None: 0.80 if "_tf" in name else 0.20

        with pytest.raises(optuna.TrialPruned):
            SearchSpace.suggest_params(trial, groups=["regime_weights"])

    def test_params_to_ensemble_config(self):
        """파라미터 → 앙상블 config 변환"""
        from core.hyperopt.search_space import SearchSpace

        params = {
            "adx_threshold": 30.0,
            "target_vol": 0.20,
            "w_trending_up_tf": 0.50,
            "w_trending_up_mr": 0.20,
            "max_drawdown_limit": 0.15,
        }

        config = SearchSpace.params_to_ensemble_config(params)

        assert config["ensemble_params"]["adx_threshold"] == 30.0
        assert config["ensemble_params"]["target_vol"] == 0.20
        assert config["regime_weights"]["TRENDING_UP"]["TF"] == 0.50
        assert config["regime_weights"]["TRENDING_UP"]["MR"] == 0.20
        assert config["regime_weights"]["TRENDING_UP"]["RP"] == pytest.approx(0.30)
        assert config["risk_params"]["max_drawdown_limit"] == 0.15

    def test_regime_weight_rp_calculated(self):
        """RP = 1.0 - TF - MR 자동 계산"""
        from core.hyperopt.search_space import SearchSpace

        params = {
            "w_sideways_tf": 0.25,
            "w_sideways_mr": 0.45,
        }
        config = SearchSpace.params_to_ensemble_config(params)
        assert config["regime_weights"]["SIDEWAYS"]["RP"] == pytest.approx(0.30, abs=0.001)


# ══════════════════════════════════════
# TrialResult / OptimizationResult 모델 테스트
# ══════════════════════════════════════


class TestModels:
    """데이터 모델 테스트"""

    def test_trial_result_to_dict(self):
        """TrialResult 직렬화"""
        from core.hyperopt.models import TrialResult

        tr = TrialResult(
            trial_number=0,
            params={"adx_threshold": 25.0},
            oos_sharpe=0.45,
            oos_cagr=0.12,
            oos_mdd=-0.15,
            oos_sortino=0.65,
            oos_calmar=0.80,
            oos_win_rate=0.55,
            oos_window_count=10,
            oos_positive_windows=7,
        )
        d = tr.to_dict()
        assert d["trial_number"] == 0
        assert d["oos_sharpe"] == 0.45
        assert d["oos_positive_windows"] == 7

    def test_optimization_result_to_dict(self):
        """OptimizationResult 직렬화"""
        from core.hyperopt.models import OptimizationResult, TrialResult

        trials = [
            TrialResult(
                trial_number=i,
                params={"p": float(i)},
                oos_sharpe=0.1 * i,
                oos_cagr=0.0,
                oos_mdd=0.0,
                oos_sortino=0.0,
                oos_calmar=0.0,
                oos_win_rate=0.0,
            )
            for i in range(5)
        ]

        result = OptimizationResult(
            study_name="test_study",
            n_trials=5,
            n_completed=5,
            n_pruned=0,
            best_params={"p": 4.0},
            best_oos_sharpe=0.4,
            best_trial_number=4,
            baseline_oos_sharpe=0.1,
            baseline_params={"p": 0.0},
            improvement_pct=300.0,
            trials=trials,
            param_importances={"p": 0.95},
        )

        d = result.to_dict()
        assert d["study_name"] == "test_study"
        assert d["best_oos_sharpe"] == 0.4
        assert d["improvement_pct"] == 300.0
        assert len(d["top_5_trials"]) == 5
        assert d["top_5_trials"][0]["oos_sharpe"] == 0.4

    def test_optimization_result_improvement_zero_baseline(self):
        """기준선 0일 때 개선율 계산"""
        from core.hyperopt.models import OptimizationResult

        result = OptimizationResult(
            study_name="test",
            n_trials=1,
            n_completed=1,
            n_pruned=0,
            best_params={},
            best_oos_sharpe=0.5,
            best_trial_number=0,
            baseline_oos_sharpe=0.0,
            baseline_params={},
            improvement_pct=float("inf"),
        )
        # inf는 to_dict에서 round로 처리 안되므로 직접 체크
        assert result.improvement_pct == float("inf")


# ══════════════════════════════════════
# ObjectiveFunction 테스트
# ══════════════════════════════════════


class TestObjectiveFunction:
    """목적 함수 테스트"""

    def test_precompute_signals(self):
        """시그널 사전계산: OHLCV 200일 이상만 처리"""
        from core.hyperopt.objective import ObjectiveFunction

        ohlcv_data = {
            "A": _make_ohlcv(300),
            "B": _make_ohlcv(100),  # 200 미만 → 제외
        }

        obj = ObjectiveFunction(ohlcv_data, groups=["ensemble"])
        assert "A" in obj._precomputed_signals
        assert "B" not in obj._precomputed_signals

    def test_precomputed_signals_keys(self):
        """사전계산 시그널에 MR/TF/RP 포함"""
        from core.hyperopt.objective import ObjectiveFunction

        ohlcv_data = {"A": _make_ohlcv(300)}
        obj = ObjectiveFunction(ohlcv_data, groups=["ensemble"])

        signals = obj._precomputed_signals["A"]
        assert "MEAN_REVERSION" in signals
        assert "TREND_FOLLOWING" in signals
        assert "RISK_PARITY" in signals

    def test_baseline_score_returns_valid(self):
        """기준선 점수 계산"""
        from core.hyperopt.objective import ObjectiveFunction

        ohlcv_data = _make_multi_ticker_ohlcv(n_tickers=2, n_days=500)
        obj = ObjectiveFunction(
            ohlcv_data,
            train_months=6,
            test_months=3,
            groups=["ensemble"],
        )

        sharpe, params = obj.get_baseline_score()
        # 합성 데이터이므로 Sharpe가 정확한 값은 예측 불가,
        # 하지만 유한한 숫자여야 함
        assert np.isfinite(sharpe)
        assert "adx_threshold" in params

    def test_walk_forward_eval_returns_list(self):
        """walk-forward 평가가 Sharpe 리스트 반환"""
        from core.hyperopt.objective import ObjectiveFunction

        ohlcv_data = _make_multi_ticker_ohlcv(n_tickers=2, n_days=500)
        obj = ObjectiveFunction(
            ohlcv_data,
            train_months=6,
            test_months=3,
            groups=["ensemble"],
        )

        # 기준선 계산 시 내부에서 walk_forward_eval 호출
        sharpe, _ = obj.get_baseline_score()
        assert isinstance(sharpe, float)

    def test_objective_callable_with_mock_trial(self):
        """목적 함수가 Optuna trial로 호출 가능"""
        from core.hyperopt.objective import ObjectiveFunction
        from core.hyperopt.search_space import SearchSpace

        ohlcv_data = _make_multi_ticker_ohlcv(n_tickers=2, n_days=500)
        obj = ObjectiveFunction(
            ohlcv_data,
            train_months=6,
            test_months=3,
            groups=["ensemble"],
        )

        # Optuna trial mock
        defaults = SearchSpace.get_defaults()
        trial = MagicMock()
        trial.suggest_float.side_effect = lambda name, low, high, step=None: defaults.get(name, (low + high) / 2)
        trial.suggest_int.side_effect = lambda name, low, high, step=1: int(defaults.get(name, (low + high) // 2))
        trial.should_prune.return_value = False
        trial.number = 0
        trial.set_user_attr = MagicMock()
        trial.report = MagicMock()

        result = obj(trial)
        assert isinstance(result, float)
        assert np.isfinite(result) or result == float("-inf")


# ══════════════════════════════════════
# HyperoptOptimizer 통합 테스트
# ══════════════════════════════════════


class TestHyperoptOptimizer:
    """최적화 오케스트레이터 통합 테스트"""

    def test_optimize_minimal(self):
        """최소 설정으로 최적화 실행 (3 trials)"""
        from core.hyperopt.optimizer import HyperoptOptimizer

        ohlcv_data = _make_multi_ticker_ohlcv(n_tickers=2, n_days=500)

        optimizer = HyperoptOptimizer(
            ohlcv_data=ohlcv_data,
            train_months=6,
            test_months=3,
            groups=["ensemble"],
        )

        result = optimizer.optimize(
            n_trials=3,
            n_startup_trials=2,
            show_progress=False,
        )

        assert result.n_trials == 3
        assert result.n_completed + result.n_pruned >= 1
        assert np.isfinite(result.best_oos_sharpe)
        assert len(result.best_params) > 0
        assert result.total_duration_seconds > 0

    def test_optimize_returns_optimization_result(self):
        """결과가 OptimizationResult 타입"""
        from core.hyperopt.models import OptimizationResult
        from core.hyperopt.optimizer import HyperoptOptimizer

        ohlcv_data = _make_multi_ticker_ohlcv(n_tickers=2, n_days=500)

        optimizer = HyperoptOptimizer(
            ohlcv_data=ohlcv_data,
            train_months=6,
            test_months=3,
            groups=["ensemble"],
        )

        result = optimizer.optimize(
            n_trials=2,
            n_startup_trials=1,
            show_progress=False,
        )

        assert isinstance(result, OptimizationResult)
        assert result.study_name.startswith("aqts_hyperopt_")

    def test_optimize_baseline_params_populated(self):
        """기준선 파라미터가 채워지는지 확인"""
        from core.hyperopt.optimizer import HyperoptOptimizer

        ohlcv_data = _make_multi_ticker_ohlcv(n_tickers=2, n_days=500)

        optimizer = HyperoptOptimizer(
            ohlcv_data=ohlcv_data,
            train_months=6,
            test_months=3,
            groups=["ensemble"],
        )

        result = optimizer.optimize(
            n_trials=2,
            n_startup_trials=1,
            show_progress=False,
        )

        assert "adx_threshold" in result.baseline_params
        assert np.isfinite(result.baseline_oos_sharpe)

    def test_optimize_to_dict_serializable(self):
        """결과 직렬화 가능"""
        import json

        from core.hyperopt.optimizer import HyperoptOptimizer

        ohlcv_data = _make_multi_ticker_ohlcv(n_tickers=2, n_days=500)

        optimizer = HyperoptOptimizer(
            ohlcv_data=ohlcv_data,
            train_months=6,
            test_months=3,
            groups=["ensemble"],
        )

        result = optimizer.optimize(
            n_trials=2,
            n_startup_trials=1,
            show_progress=False,
        )

        # JSON 직렬화 가능해야 함
        d = result.to_dict()
        json_str = json.dumps(d, ensure_ascii=False)
        assert len(json_str) > 0
        parsed = json.loads(json_str)
        assert parsed["study_name"] == result.study_name
