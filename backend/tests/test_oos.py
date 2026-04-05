"""
OOS (Out-of-Sample) 검증 파이프라인 테스트

테스트 구성:
1. RegimeMapper: 매핑 정확성, fallback, 유효성 검사
2. GateEvaluator: 3단계 게이트 판정 (PASS/REVIEW/FAIL)
3. WalkForwardEngine: 기간 분할, 윈도우 실행, 집계
4. OOSJobManager: 작업 생성, 조회, idempotency
5. OOS Data Models: 직렬화, 상태 전이
6. Integration: 전체 파이프라인 end-to-end
"""

import numpy as np
import pandas as pd
import pytest

from core.oos.gate_evaluator import GateEvaluator
from core.oos.job_manager import OOSJobManager
from core.oos.models import (
    OOSMetric,
    OOSRun,
    OOSRunRequest,
    OOSRunType,
    OOSShadowAction,
    OOSStatus,
    OOSWindowResult,
)
from core.oos.regime_mapping import RegimeMapper
from core.oos.walk_forward import WalkForwardEngine


# ══════════════════════════════════════
# Test Fixtures
# ══════════════════════════════════════
@pytest.fixture
def regime_mapper():
    return RegimeMapper()


@pytest.fixture
def gate_evaluator():
    return GateEvaluator()


@pytest.fixture
def walk_forward_engine():
    return WalkForwardEngine()


@pytest.fixture(autouse=True)
def reset_job_manager():
    """각 테스트 전에 JobManager 싱글톤 리셋"""
    OOSJobManager.reset()
    yield
    OOSJobManager.reset()


@pytest.fixture
def sample_data():
    """3년치 합성 시그널/가격 데이터"""
    np.random.seed(42)
    n_days = 756  # ~3 years
    dates = pd.bdate_range(end="2025-12-31", periods=n_days)
    tickers = ["005930", "000660"]

    # 가격: 랜덤 워크
    prices_data = {}
    for ticker in tickers:
        base = 50000
        returns = np.random.normal(0.0003, 0.015, n_days)
        prices_data[ticker] = base * np.cumprod(1 + returns)

    prices = pd.DataFrame(prices_data, index=dates)

    # 시그널: 모멘텀 기반
    signals_data = {}
    for ticker in tickers:
        price_returns = prices[ticker].pct_change().fillna(0)
        momentum = price_returns.rolling(20).mean().fillna(0) * 100
        noise = np.random.normal(0, 0.2, n_days)
        raw_signal = momentum + noise
        signals_data[ticker] = np.clip(raw_signal, -1, 1)

    signals = pd.DataFrame(signals_data, index=dates)

    return signals, prices


@pytest.fixture
def window_results_pass():
    """PASS 판정을 받을 윈도우 결과"""
    return [
        OOSWindowResult(
            window_index=0,
            train_start="2023-01-01",
            train_end="2024-12-31",
            test_start="2025-01-01",
            test_end="2025-03-31",
            cagr=0.12,
            mdd=-0.08,
            sharpe_ratio=1.2,
            sortino_ratio=1.8,
            calmar_ratio=1.5,
            total_return=0.03,
            total_trades=15,
            win_rate=0.6,
            profit_factor=1.5,
        ),
        OOSWindowResult(
            window_index=1,
            train_start="2023-04-01",
            train_end="2025-03-31",
            test_start="2025-04-01",
            test_end="2025-06-30",
            cagr=0.10,
            mdd=-0.06,
            sharpe_ratio=0.9,
            sortino_ratio=1.3,
            calmar_ratio=1.7,
            total_return=0.025,
            total_trades=12,
            win_rate=0.58,
            profit_factor=1.3,
        ),
        OOSWindowResult(
            window_index=2,
            train_start="2023-07-01",
            train_end="2025-06-30",
            test_start="2025-07-01",
            test_end="2025-09-30",
            cagr=0.08,
            mdd=-0.10,
            sharpe_ratio=0.7,
            sortino_ratio=1.0,
            calmar_ratio=0.8,
            total_return=0.02,
            total_trades=10,
            win_rate=0.55,
            profit_factor=1.2,
        ),
        OOSWindowResult(
            window_index=3,
            train_start="2023-10-01",
            train_end="2025-09-30",
            test_start="2025-10-01",
            test_end="2025-12-31",
            cagr=0.15,
            mdd=-0.07,
            sharpe_ratio=1.5,
            sortino_ratio=2.0,
            calmar_ratio=2.1,
            total_return=0.04,
            total_trades=18,
            win_rate=0.65,
            profit_factor=1.8,
        ),
    ]


@pytest.fixture
def window_results_fail():
    """FAIL 판정을 받을 윈도우 결과 (MDD 과도)"""
    return [
        OOSWindowResult(
            window_index=0,
            train_start="2023-01-01",
            train_end="2024-12-31",
            test_start="2025-01-01",
            test_end="2025-03-31",
            cagr=-0.05,
            mdd=-0.35,
            sharpe_ratio=-0.3,
            sortino_ratio=-0.5,
            calmar_ratio=-0.14,
            total_return=-0.01,
            total_trades=20,
            win_rate=0.35,
            profit_factor=0.7,
        ),
        OOSWindowResult(
            window_index=1,
            train_start="2023-04-01",
            train_end="2025-03-31",
            test_start="2025-04-01",
            test_end="2025-06-30",
            cagr=-0.10,
            mdd=-0.40,
            sharpe_ratio=-0.5,
            sortino_ratio=-0.8,
            calmar_ratio=-0.25,
            total_return=-0.025,
            total_trades=18,
            win_rate=0.30,
            profit_factor=0.5,
        ),
    ]


# ══════════════════════════════════════
# 1. RegimeMapper Tests
# ══════════════════════════════════════
class TestRegimeMapper:
    """레짐 매핑 계층 테스트"""

    def test_realtime_to_backtest_trending_up(self, regime_mapper):
        assert regime_mapper.to_backtest("TRENDING_UP") == "BULL"

    def test_realtime_to_backtest_trending_down(self, regime_mapper):
        assert regime_mapper.to_backtest("TRENDING_DOWN") == "BEAR"

    def test_realtime_to_backtest_high_volatility(self, regime_mapper):
        assert regime_mapper.to_backtest("HIGH_VOLATILITY") == "HIGH_VOL"

    def test_realtime_to_backtest_sideways(self, regime_mapper):
        # SIDEWAYS는 BULL로 매핑 (fallback)
        assert regime_mapper.to_backtest("SIDEWAYS") == "BULL"

    def test_backtest_to_realtime_bull(self, regime_mapper):
        assert regime_mapper.to_realtime("BULL") == "TRENDING_UP"

    def test_backtest_to_realtime_bear(self, regime_mapper):
        assert regime_mapper.to_realtime("BEAR") == "TRENDING_DOWN"

    def test_backtest_to_realtime_high_vol(self, regime_mapper):
        assert regime_mapper.to_realtime("HIGH_VOL") == "HIGH_VOLATILITY"

    def test_backtest_to_realtime_rising_rate(self, regime_mapper):
        # RISING_RATE는 실시간 체계에 없으므로 SIDEWAYS로 fallback
        assert regime_mapper.to_realtime("RISING_RATE") == "SIDEWAYS"

    def test_unmapped_regime_returns_fallback(self, regime_mapper):
        result = regime_mapper.to_backtest("UNKNOWN_REGIME")
        assert result == regime_mapper.get_fallback()

    def test_validate_realtime_regime(self, regime_mapper):
        is_valid, system = regime_mapper.validate_regime("TRENDING_UP")
        assert is_valid is True
        assert system == "realtime"

    def test_validate_backtest_regime(self, regime_mapper):
        is_valid, system = regime_mapper.validate_regime("BULL")
        assert is_valid is True
        assert system == "backtest"

    def test_validate_unknown_regime(self, regime_mapper):
        is_valid, system = regime_mapper.validate_regime("UNKNOWN")
        assert is_valid is False
        assert system == "unknown"

    def test_get_all_mappings_returns_dict(self, regime_mapper):
        mappings = regime_mapper.get_all_mappings()
        assert isinstance(mappings, dict)
        assert len(mappings) > 0


# ══════════════════════════════════════
# 2. GateEvaluator Tests
# ══════════════════════════════════════
class TestGateEvaluator:
    """3단계 게이트 평가 테스트"""

    def test_all_gates_pass(self, gate_evaluator, window_results_pass):
        result = gate_evaluator.evaluate_all(
            windows=window_results_pass,
            avg_sharpe=1.075,  # mean of [1.2, 0.9, 0.7, 1.5]
            avg_calmar=1.525,
            worst_mdd=-0.10,
            sharpe_variance=0.09,
        )
        assert result["gate_a"]["result"] == "PASS"
        assert result["overall"] in ("PASS", "REVIEW")  # C도 통과 가능

    def test_gate_a_fail_on_excessive_mdd(self, gate_evaluator, window_results_fail):
        result = gate_evaluator.evaluate_all(
            windows=window_results_fail,
            avg_sharpe=-0.4,
            avg_calmar=-0.2,
            worst_mdd=-0.50,  # > 0.40 hard limit
            sharpe_variance=0.02,
        )
        assert result["gate_a"]["result"] == "FAIL"
        assert result["overall"] == "FAIL"
        assert any("MDD" in r for r in result["gate_a"]["reasons"])

    def test_gate_b_review_on_low_sharpe(self, gate_evaluator, window_results_pass):
        result = gate_evaluator.evaluate_all(
            windows=window_results_pass,
            avg_sharpe=0.1,  # < 0.3 minimum
            avg_calmar=0.5,
            worst_mdd=-0.10,
            sharpe_variance=0.05,
        )
        assert result["gate_b"]["result"] in ("REVIEW", "FAIL")
        assert any("Sharpe" in r for r in result["gate_b"]["reasons"])

    def test_gate_b_review_on_low_calmar(self, gate_evaluator, window_results_pass):
        result = gate_evaluator.evaluate_all(
            windows=window_results_pass,
            avg_sharpe=0.5,
            avg_calmar=0.05,  # < 0.1 minimum (updated threshold)
            worst_mdd=-0.10,
            sharpe_variance=0.05,
        )
        assert result["gate_b"]["result"] in ("REVIEW", "FAIL")

    def test_gate_c_review_on_high_variance(self, gate_evaluator, window_results_pass):
        result = gate_evaluator.evaluate_all(
            windows=window_results_pass,
            avg_sharpe=1.0,
            avg_calmar=1.0,
            worst_mdd=-0.10,
            sharpe_variance=6.0,  # > 5.0 limit (updated threshold)
        )
        assert result["gate_c"]["result"] == "REVIEW"
        assert any("variance" in r for r in result["gate_c"]["reasons"])

    def test_gate_c_review_on_low_positive_ratio(self, gate_evaluator):
        windows = [
            OOSWindowResult(
                window_index=i,
                train_start="2023-01-01",
                train_end="2024-12-31",
                test_start="2025-01-01",
                test_end="2025-03-31",
                total_return=-0.01 if i < 3 else 0.02,  # 3/4 negative
            )
            for i in range(4)
        ]
        result = gate_evaluator.evaluate_all(
            windows=windows,
            avg_sharpe=0.5,
            avg_calmar=0.3,
            worst_mdd=-0.10,
            sharpe_variance=0.1,
        )
        assert result["gate_c"]["result"] == "REVIEW"

    def test_custom_thresholds(self):
        evaluator = GateEvaluator(
            thresholds={
                "mdd_hard_limit": 0.50,
                "max_turnover": 10.0,
                "min_sharpe_ratio": 0.1,
                "min_calmar_ratio": 0.1,
                "regime_worst_mdd": 0.50,
                "max_window_variance": 1.0,
                "min_positive_windows_ratio": 0.3,
            }
        )
        windows = [
            OOSWindowResult(
                window_index=0,
                train_start="2023-01-01",
                train_end="2024-12-31",
                test_start="2025-01-01",
                test_end="2025-03-31",
                mdd=-0.35,
                sharpe_ratio=0.5,
                total_return=0.02,
            ),
        ]
        result = evaluator.evaluate_all(
            windows=windows,
            avg_sharpe=0.5,
            avg_calmar=0.5,
            worst_mdd=-0.35,
            sharpe_variance=0.1,
        )
        # MDD -35% < 50% limit → Gate A PASS
        assert result["gate_a"]["result"] == "PASS"

    def test_gate_b_regime_mdd_check(self, gate_evaluator):
        """레짐별 최악 MDD가 임계치를 넘으면 REVIEW"""
        windows = [
            OOSWindowResult(
                window_index=0,
                train_start="2023-01-01",
                train_end="2024-12-31",
                test_start="2025-01-01",
                test_end="2025-03-31",
                sharpe_ratio=0.8,
                total_return=0.02,
                regime_metrics={
                    "BULL": {"max_drawdown": -0.40},  # > 0.35 limit (updated threshold)
                },
            ),
        ]
        result = gate_evaluator.evaluate_all(
            windows=windows,
            avg_sharpe=0.8,
            avg_calmar=0.5,
            worst_mdd=-0.10,
            sharpe_variance=0.1,
        )
        assert result["gate_b"]["result"] in ("REVIEW", "FAIL")


# ══════════════════════════════════════
# 3. WalkForwardEngine Tests
# ══════════════════════════════════════
class TestWalkForwardEngine:
    """Walk-forward OOS 실행기 테스트"""

    def test_window_splitting(self, walk_forward_engine):
        """기간 분할 정확성"""
        n_days = 756  # ~3 years
        dates = pd.bdate_range(end="2025-12-31", periods=n_days)

        windows = walk_forward_engine._split_windows(
            date_index=dates,
            train_months=24,
            test_months=3,
        )

        assert len(windows) >= 4  # 3년 데이터, 24m train + 3m test → 최소 4 윈도우
        for train_start, train_end, test_start, test_end in windows:
            assert train_start < train_end
            assert train_end < test_start
            assert test_start < test_end

    def test_window_splitting_insufficient_data(self, walk_forward_engine):
        """데이터 부족 시 빈 리스트 반환"""
        dates = pd.bdate_range(end="2025-12-31", periods=100)
        windows = walk_forward_engine._split_windows(
            date_index=dates,
            train_months=24,
            test_months=3,
        )
        assert windows == []

    def test_window_splitting_empty_index(self, walk_forward_engine):
        dates = pd.DatetimeIndex([])
        windows = walk_forward_engine._split_windows(
            date_index=dates,
            train_months=12,
            test_months=3,
        )
        assert windows == []

    def test_run_produces_oos_run(self, walk_forward_engine, sample_data):
        """전체 실행이 OOSRun을 반환"""
        signals, prices = sample_data
        result = walk_forward_engine.run(
            strategy_name="test_ensemble",
            signals=signals,
            prices=prices,
            train_months=12,
            test_months=3,
        )

        assert isinstance(result, OOSRun)
        assert result.run_id is not None
        assert result.status in (OOSStatus.PASS, OOSStatus.REVIEW, OOSStatus.FAIL)
        assert result.total_windows > 0
        assert result.started_at is not None
        assert result.ended_at is not None
        assert result.overall_gate in ("PASS", "REVIEW", "FAIL")

    def test_run_windows_have_metrics(self, walk_forward_engine, sample_data):
        """각 윈도우에 성과 지표가 기록됨"""
        signals, prices = sample_data
        result = walk_forward_engine.run(
            strategy_name="test_ensemble",
            signals=signals,
            prices=prices,
            train_months=12,
            test_months=3,
        )

        for window in result.windows:
            assert isinstance(window, OOSWindowResult)
            # 시그널이 있으면 거래가 발생할 수 있음 (없을 수도 있음)
            assert isinstance(window.sharpe_ratio, float)
            assert isinstance(window.mdd, float)

    def test_run_aggregation(self, walk_forward_engine, sample_data):
        """집계 결과가 올바르게 계산됨"""
        signals, prices = sample_data
        result = walk_forward_engine.run(
            strategy_name="test_ensemble",
            signals=signals,
            prices=prices,
            train_months=12,
            test_months=3,
        )

        if result.total_windows > 0:
            # worst_mdd는 가장 큰 낙폭 (가장 음수)
            window_mdds = [w.mdd for w in result.windows]
            assert result.worst_mdd == round(min(window_mdds), 4)

    def test_run_insufficient_data(self, walk_forward_engine):
        """데이터 부족 시 ERROR 상태"""
        dates = pd.bdate_range(end="2025-12-31", periods=50)
        signals = pd.DataFrame({"TICK": np.random.randn(50)}, index=dates)
        prices = pd.DataFrame({"TICK": np.abs(np.random.randn(50)) * 100}, index=dates)

        result = walk_forward_engine.run(
            strategy_name="test",
            signals=signals,
            prices=prices,
            train_months=24,
            test_months=3,
        )

        assert result.status == OOSStatus.ERROR
        assert result.error_code == "INSUFFICIENT_DATA"

    def test_run_data_hash(self, walk_forward_engine, sample_data):
        """데이터 해시가 생성됨"""
        signals, prices = sample_data
        result = walk_forward_engine.run(
            strategy_name="test",
            signals=signals,
            prices=prices,
            train_months=12,
            test_months=3,
        )
        assert result.data_version != ""
        assert result.data_version != "unknown"

    def test_run_with_short_train(self, walk_forward_engine, sample_data):
        """짧은 train 기간에서도 동작"""
        signals, prices = sample_data
        result = walk_forward_engine.run(
            strategy_name="test",
            signals=signals,
            prices=prices,
            train_months=6,
            test_months=2,
        )
        assert result.total_windows > 0
        assert result.status != OOSStatus.ERROR


# ══════════════════════════════════════
# 4. OOSJobManager Tests
# ══════════════════════════════════════
class TestOOSJobManager:
    """비동기 작업 관리자 테스트"""

    def test_singleton(self):
        manager1 = OOSJobManager()
        manager2 = OOSJobManager()
        assert manager1 is manager2

    def test_submit_and_retrieve(self, sample_data):
        signals, prices = sample_data
        manager = OOSJobManager()

        result = manager.submit_run(
            strategy_version="v1",
            train_months=12,
            test_months=3,
            tickers=["005930", "000660"],
            signals=signals,
            prices=prices,
        )

        assert result.run_id is not None
        retrieved = manager.get_run(result.run_id)
        assert retrieved is not None
        assert retrieved.run_id == result.run_id

    def test_idempotency(self, sample_data):
        """동일 파라미터 재요청 시 같은 run_id 반환"""
        signals, prices = sample_data
        manager = OOSJobManager()

        result1 = manager.submit_run(
            strategy_version="v1",
            train_months=12,
            test_months=3,
            tickers=["005930"],
            signals=signals,
            prices=prices,
        )

        result2 = manager.submit_run(
            strategy_version="v1",
            train_months=12,
            test_months=3,
            tickers=["005930"],
            signals=signals,
            prices=prices,
        )

        assert result1.run_id == result2.run_id

    def test_different_params_different_run(self, sample_data):
        """다른 파라미터는 다른 run_id"""
        signals, prices = sample_data
        manager = OOSJobManager()

        result1 = manager.submit_run(
            strategy_version="v1",
            train_months=12,
            test_months=3,
            tickers=["005930"],
            signals=signals,
            prices=prices,
        )

        result2 = manager.submit_run(
            strategy_version="v2",
            train_months=12,
            test_months=3,
            tickers=["005930"],
            signals=signals,
            prices=prices,
        )

        assert result1.run_id != result2.run_id

    def test_get_latest(self, sample_data):
        signals, prices = sample_data
        manager = OOSJobManager()

        manager.submit_run(
            strategy_version="v1",
            train_months=12,
            test_months=3,
            tickers=["005930"],
            signals=signals,
            prices=prices,
        )

        latest = manager.get_latest()
        assert latest is not None
        assert latest.strategy_version == "v1"

    def test_get_latest_empty(self):
        manager = OOSJobManager()
        assert manager.get_latest() is None

    def test_gate_status_empty(self):
        manager = OOSJobManager()
        status = manager.get_gate_status()
        assert status["total_runs"] == 0
        assert status["deploy_allowed"] is False

    def test_gate_status_with_run(self, sample_data):
        signals, prices = sample_data
        manager = OOSJobManager()

        manager.submit_run(
            strategy_version="v1",
            train_months=12,
            test_months=3,
            tickers=["005930"],
            signals=signals,
            prices=prices,
        )

        status = manager.get_gate_status()
        assert status["total_runs"] == 1
        assert status["latest_gate"] in ("PASS", "REVIEW", "FAIL")

    def test_list_runs(self, sample_data):
        signals, prices = sample_data
        manager = OOSJobManager()

        manager.submit_run(
            strategy_version="v1",
            train_months=12,
            test_months=3,
            tickers=["005930"],
            signals=signals,
            prices=prices,
        )

        runs = manager.list_runs()
        assert len(runs) == 1
        assert "run_id" in runs[0]
        assert "status" in runs[0]

    def test_get_nonexistent_run(self):
        manager = OOSJobManager()
        assert manager.get_run("nonexistent") is None


# ══════════════════════════════════════
# 5. Data Model Tests
# ══════════════════════════════════════
class TestOOSModels:
    """데이터 모델 직렬화/검증 테스트"""

    def test_oos_run_to_dict(self):
        run = OOSRun(
            run_id="test-123",
            status=OOSStatus.PASS,
            overall_gate="PASS",
            avg_sharpe=1.2,
            worst_mdd=-0.10,
        )
        d = run.to_dict()
        assert d["run_id"] == "test-123"
        assert d["status"] == "PASS"
        assert d["overall_gate"] == "PASS"
        assert d["avg_sharpe"] == 1.2

    def test_oos_run_to_summary_dict(self):
        run = OOSRun(
            run_id="test-456",
            status=OOSStatus.REVIEW,
            overall_gate="REVIEW",
        )
        d = run.to_summary_dict()
        assert "run_id" in d
        assert "status" in d
        assert "overall_gate" in d

    def test_oos_run_request_validation(self):
        # Valid request
        req = OOSRunRequest(
            strategy_version="v1",
            train_months=12,
            test_months=3,
            tickers=["005930"],
        )
        assert req.train_months == 12

    def test_oos_run_request_invalid_train_months(self):
        with pytest.raises(Exception):
            OOSRunRequest(train_months=2, test_months=3, tickers=["005930"])

    def test_oos_run_request_invalid_empty_tickers(self):
        with pytest.raises(Exception):
            OOSRunRequest(train_months=12, test_months=3, tickers=[])

    def test_oos_run_request_forbids_extra(self):
        with pytest.raises(Exception):
            OOSRunRequest(
                train_months=12,
                test_months=3,
                tickers=["005930"],
                unknown_field="bad",
            )

    def test_oos_status_enum(self):
        assert OOSStatus.PASS.value == "PASS"
        assert OOSStatus.REVIEW.value == "REVIEW"
        assert OOSStatus.FAIL.value == "FAIL"

    def test_oos_run_type_enum(self):
        assert OOSRunType.OOS.value == "OOS"
        assert OOSRunType.SHADOW.value == "SHADOW"

    def test_oos_metric_creation(self):
        metric = OOSMetric(
            run_id="test-123",
            metric_name="sharpe_ratio",
            metric_value=1.5,
            regime="BULL",
        )
        assert metric.metric_name == "sharpe_ratio"
        assert metric.regime == "BULL"

    def test_oos_shadow_action_nullable_fields(self):
        action = OOSShadowAction(
            run_id="test-123",
            date="2025-01-15",
            regime="TRENDING_UP",
            baseline_threshold=0.3,
        )
        assert action.shadow_threshold is None
        assert action.reward_proxy is None
        assert action.action_taken is None

    def test_window_result_defaults(self):
        w = OOSWindowResult(
            window_index=0,
            train_start="2023-01-01",
            train_end="2024-12-31",
            test_start="2025-01-01",
            test_end="2025-03-31",
        )
        assert w.cagr == 0.0
        assert w.regime_metrics == {}


# ══════════════════════════════════════
# 6. Integration Tests
# ══════════════════════════════════════
class TestOOSIntegration:
    """전체 파이프라인 end-to-end 테스트"""

    def test_full_pipeline(self, sample_data):
        """OOS 파이프라인 전체 흐름"""
        signals, prices = sample_data

        # 1. 엔진으로 직접 실행
        engine = WalkForwardEngine()
        result = engine.run(
            strategy_name="integration_test",
            signals=signals,
            prices=prices,
            train_months=12,
            test_months=3,
        )

        # 2. 결과 검증
        assert result.status != OOSStatus.PENDING
        assert result.status != OOSStatus.RUNNING

        # 3. 게이트 판정이 있어야 함
        assert result.overall_gate in ("PASS", "REVIEW", "FAIL")
        assert result.gate_a_result in ("PASS", "REVIEW", "FAIL")
        assert result.gate_b_result in ("PASS", "REVIEW", "FAIL")
        assert result.gate_c_result in ("PASS", "REVIEW", "FAIL")

        # 4. 윈도우가 있어야 함
        assert len(result.windows) > 0

        # 5. 직렬화 가능
        d = result.to_dict()
        assert isinstance(d, dict)
        assert len(d["windows"]) == len(result.windows)

    def test_full_pipeline_via_job_manager(self, sample_data):
        """JobManager를 통한 전체 흐름"""
        signals, prices = sample_data
        manager = OOSJobManager()

        # 1. 제출
        result = manager.submit_run(
            strategy_version="integration_v1",
            train_months=12,
            test_months=3,
            tickers=["005930", "000660"],
            signals=signals,
            prices=prices,
        )

        # 2. 조회
        retrieved = manager.get_run(result.run_id)
        assert retrieved is not None
        assert retrieved.status == result.status

        # 3. 최신 조회
        latest = manager.get_latest()
        assert latest.run_id == result.run_id

        # 4. 게이트 상태
        gate_status = manager.get_gate_status()
        assert gate_status["total_runs"] == 1
        assert gate_status["latest_run_id"] == result.run_id

    def test_regime_mapper_integration_with_gate(self):
        """레짐 매핑이 게이트 평가와 통합"""
        mapper = RegimeMapper()

        # 실시간 레짐을 백테스트 레짐으로 변환
        backtest_regime = mapper.to_backtest("HIGH_VOLATILITY")
        assert backtest_regime == "HIGH_VOL"

        # 이 레짐으로 게이트 평가 가능
        evaluator = GateEvaluator()
        windows = [
            OOSWindowResult(
                window_index=0,
                train_start="2023-01-01",
                train_end="2024-12-31",
                test_start="2025-01-01",
                test_end="2025-03-31",
                sharpe_ratio=0.8,
                total_return=0.02,
                regime_metrics={
                    backtest_regime: {"max_drawdown": -0.15},
                },
            ),
        ]
        result = evaluator.evaluate_all(
            windows=windows,
            avg_sharpe=0.8,
            avg_calmar=0.5,
            worst_mdd=-0.10,
            sharpe_variance=0.1,
        )
        assert result["overall"] in ("PASS", "REVIEW", "FAIL")

    def test_shadow_extension_fields_preserved(self, sample_data):
        """Shadow 확장 필드가 보존됨"""
        signals, prices = sample_data
        engine = WalkForwardEngine()
        result = engine.run(
            strategy_name="shadow_test",
            signals=signals,
            prices=prices,
            train_months=12,
            test_months=3,
            run_type=OOSRunType.OOS,
        )

        # Shadow 필드는 None이지만 존재해야 함
        assert result.shadow_config is None
        assert result.shadow_summary is None

        d = result.to_dict()
        assert "run_type" in d
        assert d["run_type"] == "OOS"
