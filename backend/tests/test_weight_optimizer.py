"""
가중치 자동 최적화 테스트 (F-04-02)

WeightOptimizer의 종합 단위 테스트

테스트 범위:
- Sharpe 비율 비례 가중치
- 리스크 조정 복합 지표 가중치
- 최소 분산 가중치
- 제약 조건 (최소/최대/감성 상한)
- 평활화 (지수이동평균)
- Walk-Forward 최적화
- 시간 가중 평균
- 앙상블 엔진 연동
- 빈 입력/엣지 케이스
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from config.constants import RiskProfile
from core.backtest_engine.engine import BacktestConfig, BacktestResult
from core.weight_optimizer import OptimizationResult, WeightOptimizer


# ══════════════════════════════════════
# 테스트 픽스처
# ══════════════════════════════════════
def _make_result(
    name: str,
    sharpe: float = 1.0,
    sortino: float = 1.2,
    calmar: float = 0.8,
    mdd: float = -0.15,
    cagr: float = 0.10,
    total_return: float = 0.20,
    win_rate: float = 0.55,
    profit_factor: float = 1.5,
    alpha: float = 0.02,
    beta: float = 1.0,
    equity_curve: pd.Series | None = None,
) -> BacktestResult:
    """테스트용 BacktestResult 팩토리"""
    if equity_curve is None:
        dates = pd.date_range("2024-01-01", periods=252, freq="B")
        equity_curve = pd.Series(
            np.cumsum(np.random.randn(252) * 0.01) + 100, index=dates
        )

    return BacktestResult(
        strategy_name=name,
        config=BacktestConfig(),
        start_date="2024-01-01",
        end_date="2024-12-31",
        initial_capital=50_000_000,
        final_capital=50_000_000 * (1 + total_return),
        total_return=total_return,
        cagr=cagr,
        mdd=mdd,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        calmar_ratio=calmar,
        win_rate=win_rate,
        profit_factor=profit_factor,
        total_trades=100,
        avg_trade_return=0.002,
        max_consecutive_losses=5,
        alpha=alpha,
        beta=beta,
        equity_curve=equity_curve,
    )


def _make_three_results() -> list[BacktestResult]:
    """3개 전략 표준 테스트 세트"""
    return [
        _make_result("FACTOR", sharpe=1.5, calmar=1.0, mdd=-0.10),
        _make_result("TREND_FOLLOWING", sharpe=0.8, calmar=0.5, mdd=-0.20),
        _make_result("RISK_PARITY", sharpe=1.2, calmar=0.9, mdd=-0.12),
    ]


# ══════════════════════════════════════
# Sharpe 가중치 테스트
# ══════════════════════════════════════
class TestSharpeWeights:
    """Sharpe 비율 비례 가중치 테스트"""

    def test_proportional_to_sharpe(self):
        """Sharpe가 높은 전략이 높은 가중치"""
        results = _make_three_results()
        opt = WeightOptimizer()
        result = opt.optimize(results, method="sharpe")

        assert result.new_weights["FACTOR"] > result.new_weights["TREND_FOLLOWING"]

    def test_weights_sum_to_one(self):
        """가중치 합계 = 1.0"""
        results = _make_three_results()
        opt = WeightOptimizer()
        result = opt.optimize(results, method="sharpe")

        active = {k: v for k, v in result.new_weights.items() if v > 0}
        assert abs(sum(active.values()) - 1.0) < 0.01

    def test_zero_sharpe_equal_weights(self):
        """모든 Sharpe가 0이면 동일 가중"""
        results = [
            _make_result("A", sharpe=0.0),
            _make_result("B", sharpe=-0.5),
        ]
        opt = WeightOptimizer()
        result = opt.optimize(results, method="sharpe")

        active = {k: v for k, v in result.new_weights.items() if v > 0}
        weights = list(active.values())
        assert len(weights) == 2
        # 동일 가중이므로 차이가 작아야 함
        assert abs(weights[0] - weights[1]) < 0.15

    def test_negative_sharpe_clamped(self):
        """음수 Sharpe는 0으로 처리"""
        results = [
            _make_result("GOOD", sharpe=2.0),
            _make_result("BAD", sharpe=-1.0),
        ]
        opt = WeightOptimizer()
        metrics = opt._extract_metrics(results)
        raw = opt._sharpe_weights(metrics)

        assert raw["BAD"] == 0.0
        assert raw["GOOD"] == 1.0


# ══════════════════════════════════════
# 리스크 조정 가중치 테스트
# ══════════════════════════════════════
class TestRiskAdjustedWeights:
    """리스크 조정 복합 지표 가중치 테스트"""

    def test_favors_low_mdd(self):
        """MDD가 낮은 전략이 유리"""
        results = [
            _make_result("STABLE", sharpe=1.0, calmar=1.0, mdd=-0.05),
            _make_result("VOLATILE", sharpe=1.0, calmar=0.3, mdd=-0.40),
        ]
        opt = WeightOptimizer()
        result = opt.optimize(results, method="risk_adjusted")

        assert result.new_weights["STABLE"] > result.new_weights["VOLATILE"]

    def test_combines_sharpe_and_calmar(self):
        """Sharpe와 Calmar를 모두 반영"""
        results = [
            _make_result("HIGH_SHARPE", sharpe=2.0, calmar=0.5, mdd=-0.20),
            _make_result("HIGH_CALMAR", sharpe=1.0, calmar=2.0, mdd=-0.05),
        ]
        opt = WeightOptimizer()
        result = opt.optimize(results, method="risk_adjusted")

        # 두 전략 모두 0이 아닌 가중치를 가져야 함
        assert result.new_weights["HIGH_SHARPE"] > 0
        assert result.new_weights["HIGH_CALMAR"] > 0

    def test_method_stored(self):
        """최적화 방식이 결과에 기록됨"""
        results = _make_three_results()
        opt = WeightOptimizer()
        result = opt.optimize(results, method="risk_adjusted")

        assert result.method == "risk_adjusted"


# ══════════════════════════════════════
# 최소 분산 가중치 테스트
# ══════════════════════════════════════
class TestMinVarianceWeights:
    """최소 분산 가중치 테스트"""

    def test_non_negative_weights(self):
        """가중치가 모두 비음수"""
        results = _make_three_results()
        opt = WeightOptimizer()
        result = opt.optimize(results, method="min_variance")

        for w in result.new_weights.values():
            assert w >= 0.0

    def test_diversified(self):
        """단일 전략에 편중되지 않음"""
        np.random.seed(42)
        results = _make_three_results()
        opt = WeightOptimizer()
        result = opt.optimize(results, method="min_variance")

        active = {k: v for k, v in result.new_weights.items() if v > 0}
        assert len(active) >= 2  # 최소 2개 전략에 분산

    def test_fallback_with_insufficient_data(self):
        """데이터 부족 시 동일 가중 폴백"""
        # equity_curve가 2개 미만인 결과
        short_curve = pd.Series([100.0], index=pd.date_range("2024-01-01", periods=1))
        results = [
            _make_result("A", equity_curve=short_curve),
            _make_result("B", equity_curve=short_curve),
        ]
        raw = WeightOptimizer._min_variance_weights(results)

        assert abs(raw["A"] - raw["B"]) < 0.01


# ══════════════════════════════════════
# 제약 조건 테스트
# ══════════════════════════════════════
class TestConstraints:
    """가중치 제약 조건 테스트"""

    def test_min_weight_enforced(self):
        """최소 가중치 5% 보장"""
        opt = WeightOptimizer()
        weights = {"A": 0.01, "B": 0.99}
        constrained = opt._apply_constraints(weights)

        assert constrained["A"] >= opt.MIN_WEIGHT

    def test_max_weight_enforced(self):
        """최대 가중치 40% 제한"""
        opt = WeightOptimizer()
        weights = {"A": 0.95, "B": 0.05}
        constrained = opt._apply_constraints(weights)

        assert constrained["A"] <= opt.MAX_WEIGHT

    def test_sentiment_cap(self):
        """감성 시그널 최대 25% 제한"""
        opt = WeightOptimizer()
        weights = {"SENTIMENT": 0.50, "FACTOR": 0.50}
        constrained = opt._apply_constraints(weights)

        assert constrained["SENTIMENT"] <= opt.SENTIMENT_MAX

    def test_zero_weight_preserved(self):
        """0 가중치는 유지"""
        opt = WeightOptimizer()
        weights = {"A": 0.0, "B": 0.50, "C": 0.50}
        constrained = opt._apply_constraints(weights)

        assert constrained["A"] == 0.0

    def test_normalization_after_constraints(self):
        """제약 적용 후 정규화"""
        results = _make_three_results()
        opt = WeightOptimizer()
        result = opt.optimize(results, method="sharpe")

        active = {k: v for k, v in result.new_weights.items() if v > 0}
        assert abs(sum(active.values()) - 1.0) < 0.01


# ══════════════════════════════════════
# 평활화 테스트
# ══════════════════════════════════════
class TestSmoothing:
    """지수이동평균 평활화 테스트"""

    def test_smoothing_blends_old_and_new(self):
        """이전/신규 가중치 혼합"""
        opt = WeightOptimizer()
        old = {"A": 0.60, "B": 0.40}
        new = {"A": 0.30, "B": 0.70}

        smoothed = opt._smooth_weights(old, new)
        alpha = opt.SMOOTHING_ALPHA

        expected_a = alpha * 0.30 + (1 - alpha) * 0.60
        assert abs(smoothed["A"] - expected_a) < 1e-6

    def test_smoothing_prevents_drastic_change(self):
        """급격한 변동 방지"""
        results = _make_three_results()
        opt = WeightOptimizer()

        current = {"FACTOR": 0.10, "TREND_FOLLOWING": 0.80, "RISK_PARITY": 0.10}
        result = opt.optimize(results, method="sharpe", current_weights=current)

        # 현재 80%인 TREND_FOLLOWING이 한 번에 극단적으로 줄지 않음
        assert result.new_weights.get("TREND_FOLLOWING", 0) > 0.15

    def test_no_smoothing_without_current(self):
        """현재 가중치 없으면 평활화 미적용"""
        results = _make_three_results()
        opt = WeightOptimizer()
        result = opt.optimize(results, method="sharpe", current_weights=None)

        # 결과가 정상적으로 반환
        assert sum(v for v in result.new_weights.values() if v > 0) > 0.99


# ══════════════════════════════════════
# Walk-Forward 최적화 테스트
# ══════════════════════════════════════
class TestWalkForward:
    """Walk-Forward 최적화 테스트"""

    def test_basic_walk_forward(self):
        """기본 Walk-Forward 실행"""
        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=200, freq="B")
        prices = pd.DataFrame(
            {
                "AAPL": np.cumsum(np.random.randn(200) * 0.5) + 150,
                "GOOGL": np.cumsum(np.random.randn(200) * 0.5) + 100,
            },
            index=dates,
        )

        signals_factor = pd.DataFrame(
            np.random.uniform(-0.5, 0.5, (200, 2)),
            index=dates,
            columns=["AAPL", "GOOGL"],
        )
        signals_trend = pd.DataFrame(
            np.random.uniform(-0.5, 0.5, (200, 2)),
            index=dates,
            columns=["AAPL", "GOOGL"],
        )

        opt = WeightOptimizer()
        result = opt.walk_forward_optimize(
            strategy_signals={"FACTOR": signals_factor, "TREND": signals_trend},
            prices=prices,
            window=100,
            step=50,
        )

        assert result.method.startswith("walk_forward")
        assert len(result.new_weights) > 0

    def test_insufficient_data(self):
        """데이터 부족 시 빈 결과"""
        dates = pd.date_range("2024-01-01", periods=10, freq="B")
        prices = pd.DataFrame({"A": range(10)}, index=dates)

        opt = WeightOptimizer()
        result = opt.walk_forward_optimize(
            strategy_signals={},
            prices=prices,
            window=120,
        )

        assert result.new_weights == {}


# ══════════════════════════════════════
# 시간 가중 평균 테스트
# ══════════════════════════════════════
class TestTimeWeightedAverage:
    """시간 가중 평균 테스트"""

    def test_recent_window_higher_weight(self):
        """최근 윈도우에 높은 비중"""
        opt = WeightOptimizer()

        # 초기: A=1.0, 최근: A=0.0, B=1.0
        series = [
            {"A": 1.0, "B": 0.0},  # 과거
            {"A": 0.0, "B": 1.0},  # 최근
        ]
        avg = opt._time_weighted_average(series)

        # 최근 윈도우(B=1.0)가 더 높은 비중
        assert avg["B"] > avg["A"]

    def test_empty_series(self):
        """빈 시리즈"""
        opt = WeightOptimizer()
        avg = opt._time_weighted_average([])
        assert avg == {}

    def test_single_window(self):
        """단일 윈도우"""
        opt = WeightOptimizer()
        avg = opt._time_weighted_average([{"A": 0.6, "B": 0.4}])
        assert abs(avg["A"] - 0.6) < 1e-6


# ══════════════════════════════════════
# OptimizationResult 테스트
# ══════════════════════════════════════
class TestOptimizationResult:
    """최적화 결과 데이터 테스트"""

    def test_weight_changes(self):
        """가중치 변화량 계산"""
        result = OptimizationResult(
            method="sharpe",
            risk_profile="BALANCED",
            old_weights={"A": 0.30, "B": 0.70},
            new_weights={"A": 0.50, "B": 0.50},
            strategy_metrics={},
        )
        changes = result.weight_changes
        assert abs(changes["A"] - 0.20) < 1e-6
        assert abs(changes["B"] - (-0.20)) < 1e-6

    def test_weight_changes_new_strategy(self):
        """신규 전략 추가 시 변화량"""
        result = OptimizationResult(
            method="sharpe",
            risk_profile="BALANCED",
            old_weights={"A": 1.0},
            new_weights={"A": 0.7, "C": 0.3},
            strategy_metrics={},
        )
        changes = result.weight_changes
        assert abs(changes["C"] - 0.3) < 1e-6


# ══════════════════════════════════════
# 빈 입력 테스트
# ══════════════════════════════════════
class TestEdgeCases:
    """엣지 케이스 테스트"""

    def test_empty_results(self):
        """빈 결과 리스트"""
        opt = WeightOptimizer()
        result = opt.optimize([], method="sharpe")
        assert result.new_weights == {}

    def test_single_strategy(self):
        """단일 전략"""
        results = [_make_result("ONLY_ONE", sharpe=1.5)]
        opt = WeightOptimizer()
        result = opt.optimize(results, method="sharpe")

        active = {k: v for k, v in result.new_weights.items() if v > 0}
        assert len(active) == 1
        assert abs(list(active.values())[0] - 1.0) < 0.01

    def test_optimization_score(self):
        """최적화 점수 산출"""
        results = _make_three_results()
        opt = WeightOptimizer()
        result = opt.optimize(results, method="sharpe")

        assert result.optimization_score > 0

    def test_backtest_period(self):
        """백테스트 기간 문자열"""
        results = _make_three_results()
        opt = WeightOptimizer()
        result = opt.optimize(results, method="sharpe")

        assert "2024" in result.backtest_period

    def test_risk_profile_stored(self):
        """리스크 프로필 저장"""
        opt = WeightOptimizer(risk_profile=RiskProfile.AGGRESSIVE)
        result = opt.optimize(_make_three_results())

        assert result.risk_profile == "AGGRESSIVE"


# ══════════════════════════════════════
# 앙상블 엔진 연동 테스트
# ══════════════════════════════════════
class TestEnsembleIntegration:
    """앙상블 엔진 연동 테스트"""

    @pytest.mark.asyncio
    async def test_optimize_and_apply(self):
        """최적화 후 앙상블 엔진 반영"""
        results = _make_three_results()
        opt = WeightOptimizer()

        # Mock ensemble engine
        engine = MagicMock()
        engine.get_weights = AsyncMock(
            return_value={"FACTOR": 0.33, "TREND_FOLLOWING": 0.34, "RISK_PARITY": 0.33}
        )
        engine.recalibrate_weights = AsyncMock(return_value={})
        engine._weights = None

        result = await opt.optimize_and_apply(results, engine)

        assert engine.recalibrate_weights.called
        assert engine._weights is not None
        assert len(engine._weights) > 0

    @pytest.mark.asyncio
    async def test_optimize_and_apply_empty(self):
        """빈 결과로 최적화 시 엔진 변경 없음"""
        opt = WeightOptimizer()
        engine = MagicMock()
        engine.get_weights = AsyncMock(return_value={"A": 0.5, "B": 0.5})
        engine.recalibrate_weights = AsyncMock()

        result = await opt.optimize_and_apply([], engine)

        assert not engine.recalibrate_weights.called
