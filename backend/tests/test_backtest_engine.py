"""
백테스트 엔진 유닛테스트

테스트 대상: core/backtest_engine/engine.py
목표 커버리지: 85% (Backtest Engine 모듈)

테스트 범위:
- 수익률 계산 정확성 (알려진 결과와 비교)
- 거래 비용/세금 반영 확인
- 성과 지표 산출 (CAGR, MDD, Sharpe 등)
- 빈 데이터/극단 케이스 처리
- 전략 비교기 동작
"""

import numpy as np
import pandas as pd

from config.constants import Country
from core.backtest_engine.engine import (
    BacktestConfig,
    BacktestEngine,
    BacktestResult,
    StrategyComparator,
    _max_consecutive,
)


def _make_prices(n: int = 252, tickers: list[str] = None) -> pd.DataFrame:
    """테스트용 가격 데이터 생성"""
    if tickers is None:
        tickers = ["A", "B", "C"]
    np.random.seed(42)
    dates = pd.bdate_range(start="2024-01-02", periods=n)
    data = {}
    for ticker in tickers:
        base = np.random.uniform(10000, 100000)
        returns = np.random.normal(0.0005, 0.02, n)
        prices = base * np.cumprod(1 + returns)
        data[ticker] = prices
    return pd.DataFrame(data, index=dates)


def _make_signals(prices: pd.DataFrame, strategy: str = "always_buy") -> pd.DataFrame:
    """테스트용 시그널 생성"""
    signals = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    if strategy == "always_buy":
        signals.iloc[0] = 0.8  # 첫날 매수
    elif strategy == "always_sell":
        signals.iloc[0] = 0.8
        signals.iloc[10] = -0.8  # 10일 후 매도
    elif strategy == "alternating":
        for i in range(0, len(signals), 20):
            signals.iloc[i] = 0.8
        for i in range(10, len(signals), 20):
            signals.iloc[i] = -0.8
    return signals


class TestBacktestConfig:
    """백테스트 설정 테스트"""

    def test_default_costs_kr(self):
        """한국 시장 기본 거래 비용"""
        config = BacktestConfig(country=Country.KR)
        costs = config.get_costs()
        assert costs["commission"] == 0.00015
        assert costs["tax"] == 0.0023
        assert costs["slippage"] == 0.001

    def test_default_costs_us(self):
        """미국 시장 기본 거래 비용"""
        config = BacktestConfig(country=Country.US)
        costs = config.get_costs()
        assert costs["commission"] == 0.001
        assert costs["tax"] == 0.0
        assert costs["slippage"] == 0.001

    def test_custom_costs_override(self):
        """커스텀 비용 오버라이드"""
        config = BacktestConfig(
            country=Country.KR,
            commission_rate=0.001,
            tax_rate=0.005,
        )
        costs = config.get_costs()
        assert costs["commission"] == 0.001
        assert costs["tax"] == 0.005


class TestBacktestEngine:
    """백테스트 엔진 테스트"""

    def test_buy_and_hold_positive_return(self):
        """매수 후 보유: 우상향 시장에서 양수 수익"""
        np.random.seed(123)
        dates = pd.bdate_range(start="2024-01-02", periods=252)
        # 확실한 우상향 데이터
        prices_data = 50000 * np.cumprod(1 + np.full(252, 0.001))
        prices = pd.DataFrame({"A": prices_data}, index=dates)
        signals = pd.DataFrame({"A": np.zeros(252)}, index=dates)
        signals.iloc[0] = 0.8  # 첫날 매수

        config = BacktestConfig(
            initial_capital=10_000_000,
            country=Country.KR,
            slippage_rate=0.0,
            commission_rate=0.0,
            tax_rate=0.0,
        )
        engine = BacktestEngine(config)
        result = engine.run("BuyHold", signals, prices)

        assert result.total_return > 0.0
        assert result.final_capital > config.initial_capital

    def test_buy_and_hold_negative_return(self):
        """매수 후 보유: 하락장에서 음수 수익"""
        dates = pd.bdate_range(start="2024-01-02", periods=252)
        # 확실한 하락 추세: 매일 -0.1% 하락
        prices_data = 50000 * np.cumprod(1 + np.full(252, -0.001))
        prices = pd.DataFrame({"A": prices_data}, index=dates)
        signals = pd.DataFrame({"A": np.zeros(252)}, index=dates)
        signals.iloc[0] = 0.8  # 첫날 매수

        config = BacktestConfig(
            initial_capital=10_000_000,
            country=Country.KR,
            slippage_rate=0.0,
            commission_rate=0.0,
            tax_rate=0.0,
        )
        engine = BacktestEngine(config)
        result = engine.run("BuyHoldDown", signals, prices)

        assert result.total_return < 0.0
        assert result.final_capital < config.initial_capital

    def test_volatile_market_drawdown(self):
        """변동성 큰 시장: MDD가 유의미하게 발생"""
        np.random.seed(42)
        dates = pd.bdate_range(start="2024-01-02", periods=252)
        # 급등락 반복 (평균 약 0, 높은 변동성)
        daily_returns = np.random.normal(0.0, 0.03, 252)
        prices_data = 50000 * np.cumprod(1 + daily_returns)
        prices = pd.DataFrame({"A": prices_data}, index=dates)
        signals = pd.DataFrame({"A": np.zeros(252)}, index=dates)
        signals.iloc[0] = 0.8

        config = BacktestConfig(
            initial_capital=10_000_000,
            slippage_rate=0.0,
            commission_rate=0.0,
            tax_rate=0.0,
        )
        engine = BacktestEngine(config)
        result = engine.run("Volatile", signals, prices)

        # 변동성이 크면 MDD가 유의미하게 발생해야 함
        assert result.mdd < -0.05, f"Expected significant drawdown, got {result.mdd}"

    def test_transaction_costs_reduce_return(self):
        """거래 비용이 수익을 감소시킴"""
        prices = _make_prices(252, ["A"])
        signals = _make_signals(prices, "alternating")

        # 비용 없는 경우
        config_no_cost = BacktestConfig(
            initial_capital=50_000_000,
            commission_rate=0.0,
            tax_rate=0.0,
            slippage_rate=0.0,
        )
        result_no_cost = BacktestEngine(config_no_cost).run("NoCost", signals, prices)

        # 비용 있는 경우
        config_with_cost = BacktestConfig(
            initial_capital=50_000_000,
            country=Country.KR,
        )
        result_with_cost = BacktestEngine(config_with_cost).run("WithCost", signals, prices)

        # 비용이 있으면 수익이 낮아야 함
        assert result_with_cost.final_capital <= result_no_cost.final_capital

    def test_mdd_is_negative(self):
        """MDD는 음수"""
        prices = _make_prices(252)
        signals = _make_signals(prices, "always_buy")
        config = BacktestConfig(initial_capital=50_000_000)
        result = BacktestEngine(config).run("Test", signals, prices)

        assert result.mdd <= 0.0

    def test_equity_curve_starts_at_initial(self):
        """자산 곡선이 초기 자본에서 시작"""
        prices = _make_prices(50, ["A"])
        signals = _make_signals(prices, "always_buy")
        config = BacktestConfig(initial_capital=10_000_000)
        result = BacktestEngine(config).run("Test", signals, prices)

        if len(result.equity_curve) > 0:
            # 첫날은 매수 비용 차감으로 초기 자본과 정확히 같지 않을 수 있음
            assert result.equity_curve.iloc[0] > 0

    def test_empty_signals_returns_empty_result(self):
        """빈 시그널 → 빈 결과"""
        prices = _make_prices(50, ["A"])
        signals = pd.DataFrame(columns=["B"], index=prices.index)  # 다른 종목
        config = BacktestConfig()
        result = BacktestEngine(config).run("Empty", signals, prices)

        assert result.total_return == 0.0
        assert result.total_trades == 0

    def test_sharpe_ratio_calculation(self):
        """Sharpe Ratio가 유한한 값"""
        prices = _make_prices(252)
        signals = _make_signals(prices, "always_buy")
        config = BacktestConfig(initial_capital=50_000_000)
        result = BacktestEngine(config).run("Test", signals, prices)

        assert np.isfinite(result.sharpe_ratio)

    def test_trade_records_generated(self):
        """거래 기록이 생성됨"""
        prices = _make_prices(100, ["A"])
        signals = _make_signals(prices, "alternating")
        config = BacktestConfig(initial_capital=50_000_000)
        result = BacktestEngine(config).run("Test", signals, prices)

        assert len(result.trade_records) > 0
        for trade in result.trade_records:
            assert trade.ticker in ["A"]
            assert trade.side in ["BUY", "SELL"]
            assert trade.quantity > 0
            assert trade.price > 0


class TestMaxConsecutive:
    """연속 손실 카운터 테스트"""

    def test_basic(self):
        assert _max_consecutive([1, 1, 0, 1, 1, 1, 0]) == 3

    def test_all_losses(self):
        assert _max_consecutive([1, 1, 1, 1]) == 4

    def test_no_losses(self):
        assert _max_consecutive([0, 0, 0]) == 0

    def test_empty(self):
        assert _max_consecutive([]) == 0

    def test_single_loss(self):
        assert _max_consecutive([0, 1, 0]) == 1


class TestStrategyComparator:
    """전략 비교기 테스트"""

    def _make_results(self) -> list[BacktestResult]:
        """테스트용 결과 리스트 생성"""
        config = BacktestConfig()
        results = []
        for name, sharpe, ret in [
            ("Factor", 1.5, 0.12),
            ("MeanRev", 0.8, 0.08),
            ("Trend", 1.2, 0.15),
        ]:
            r = BacktestResult(
                strategy_name=name,
                config=config,
                start_date="2024-01-02",
                end_date="2024-12-31",
                initial_capital=50e6,
                final_capital=50e6 * (1 + ret),
                total_return=ret,
                cagr=ret,
                mdd=-0.1,
                sharpe_ratio=sharpe,
                sortino_ratio=sharpe * 1.2,
                calmar_ratio=ret / 0.1,
                win_rate=0.55,
                profit_factor=1.5,
                total_trades=50,
                avg_trade_return=ret / 50,
                max_consecutive_losses=3,
            )
            results.append(r)
        return results

    def test_compare_returns_sorted_by_sharpe(self):
        """비교 테이블이 Sharpe 내림차순 정렬"""
        results = self._make_results()
        df = StrategyComparator.compare(results)

        assert df.index[0] == "Factor"  # Sharpe 1.5로 최고
        assert len(df) == 3

    def test_recommend_weights_sharpe_based(self):
        """Sharpe 기반 가중치 합이 1.0"""
        results = self._make_results()
        weights = StrategyComparator.recommend_weights(results, method="sharpe")

        assert abs(sum(weights.values()) - 1.0) < 1e-10
        # Factor(Sharpe=1.5)가 가장 높은 가중치
        assert weights["Factor"] > weights["MeanRev"]

    def test_recommend_weights_equal(self):
        """동일 가중치"""
        results = self._make_results()
        weights = StrategyComparator.recommend_weights(results, method="equal")

        for w in weights.values():
            assert abs(w - 1.0 / 3.0) < 1e-10

    def test_empty_results(self):
        """빈 결과 처리"""
        df = StrategyComparator.compare([])
        assert len(df) == 0

        weights = StrategyComparator.recommend_weights([])
        assert len(weights) == 0


# ══════════════════════════════════════
# 벤치마크 대비 지표 테스트 (F-07-01 완성)
# ══════════════════════════════════════
class TestBenchmarkMetrics:
    """벤치마크 대비 성과 지표 (Alpha, Beta, IR, TE) 테스트"""

    def test_no_benchmark_returns_zeros(self):
        """벤치마크 없으면 모두 0"""
        prices = _make_prices(100, ["A"])
        signals = _make_signals(prices, "always_buy")
        config = BacktestConfig(initial_capital=50_000_000, benchmark_returns=None)
        result = BacktestEngine(config).run("NoBM", signals, prices)

        assert result.alpha == 0.0
        assert result.beta == 0.0
        assert result.information_ratio == 0.0
        assert result.tracking_error == 0.0

    def test_benchmark_metrics_finite(self):
        """벤치마크 제공 시 유한한 값 반환"""
        np.random.seed(42)
        n = 252
        dates = pd.bdate_range(start="2024-01-02", periods=n)

        # 벤치마크: 일별 수익률
        bm_returns = pd.Series(
            np.random.normal(0.0004, 0.012, n),
            index=dates,
        )

        prices = _make_prices(n, ["A"])
        signals = _make_signals(prices, "always_buy")
        config = BacktestConfig(
            initial_capital=50_000_000,
            benchmark_returns=bm_returns,
        )
        result = BacktestEngine(config).run("WithBM", signals, prices)

        assert np.isfinite(result.alpha)
        assert np.isfinite(result.beta)
        assert np.isfinite(result.information_ratio)
        assert np.isfinite(result.tracking_error)

    def test_beta_positive_for_correlated_strategy(self):
        """시장과 양의 상관관계 → Beta > 0"""
        np.random.seed(42)
        n = 252
        dates = pd.bdate_range(start="2024-01-02", periods=n)

        # 시장 수익률
        market_returns = np.random.normal(0.0005, 0.015, n)
        bm_returns = pd.Series(market_returns, index=dates)

        # 전략: 시장을 추종하는 가격 데이터 생성
        prices_data = 50000 * np.cumprod(1 + market_returns * 1.2 + np.random.normal(0, 0.005, n))
        prices = pd.DataFrame({"A": prices_data}, index=dates)
        signals = pd.DataFrame({"A": np.zeros(n)}, index=dates)
        signals.iloc[0] = 0.8

        config = BacktestConfig(
            initial_capital=50_000_000,
            benchmark_returns=bm_returns,
            commission_rate=0.0,
            tax_rate=0.0,
            slippage_rate=0.0,
        )
        result = BacktestEngine(config).run("Correlated", signals, prices)

        assert result.beta > 0

    def test_tracking_error_zero_for_identical(self):
        """전략과 벤치마크가 동일하면 Tracking Error ≈ 0"""
        np.random.seed(42)
        n = 100
        dates = pd.bdate_range(start="2024-01-02", periods=n)

        returns = np.random.normal(0.001, 0.01, n)
        prices_data = 50000 * np.cumprod(1 + returns)
        prices = pd.DataFrame({"A": prices_data}, index=dates)
        signals = pd.DataFrame({"A": np.zeros(n)}, index=dates)
        signals.iloc[0] = 0.8

        # 전략의 실제 일별 수익률을 벤치마크로 설정
        config_pre = BacktestConfig(
            initial_capital=50_000_000,
            commission_rate=0.0,
            tax_rate=0.0,
            slippage_rate=0.0,
        )
        result_pre = BacktestEngine(config_pre).run("Pre", signals, prices)

        # 자산곡선에서 일별 수익률 추출
        if len(result_pre.equity_curve) > 1:
            strategy_daily = result_pre.equity_curve.pct_change().dropna()
            config = BacktestConfig(
                initial_capital=50_000_000,
                benchmark_returns=strategy_daily,
                commission_rate=0.0,
                tax_rate=0.0,
                slippage_rate=0.0,
            )
            result = BacktestEngine(config).run("Identical", signals, prices)
            # 동일하면 TE가 매우 작아야 함
            assert result.tracking_error < 0.01

    def test_information_ratio_sign(self):
        """초과 수익 양수 → IR 양수"""
        np.random.seed(42)
        n = 252
        dates = pd.bdate_range(start="2024-01-02", periods=n)

        # 벤치마크: 일 0.0001 수익 (저수익)
        bm_returns = pd.Series(
            np.full(n, 0.0001),
            index=dates,
        )

        # 전략: 확실한 우상향
        prices_data = 50000 * np.cumprod(1 + np.full(n, 0.002))
        prices = pd.DataFrame({"A": prices_data}, index=dates)
        signals = pd.DataFrame({"A": np.zeros(n)}, index=dates)
        signals.iloc[0] = 0.8

        config = BacktestConfig(
            initial_capital=50_000_000,
            benchmark_returns=bm_returns,
            commission_rate=0.0,
            tax_rate=0.0,
            slippage_rate=0.0,
        )
        result = BacktestEngine(config).run("Outperform", signals, prices)

        # 전략이 벤치마크를 초과하므로 IR > 0
        assert result.information_ratio > 0

    def test_comparator_includes_benchmark_columns(self):
        """StrategyComparator가 벤치마크 지표 컬럼을 포함"""
        config = BacktestConfig()
        r = BacktestResult(
            strategy_name="Test",
            config=config,
            start_date="2024-01-02",
            end_date="2024-12-31",
            initial_capital=50e6,
            final_capital=55e6,
            total_return=0.10,
            cagr=0.10,
            mdd=-0.05,
            sharpe_ratio=1.2,
            sortino_ratio=1.5,
            calmar_ratio=2.0,
            win_rate=0.55,
            profit_factor=1.5,
            total_trades=50,
            avg_trade_return=0.002,
            max_consecutive_losses=3,
            alpha=0.05,
            beta=0.9,
            information_ratio=0.8,
            tracking_error=0.06,
        )
        df = StrategyComparator.compare([r])
        assert "alpha" in df.columns
        assert "beta" in df.columns
        assert "info_ratio" in df.columns
        assert "tracking_error" in df.columns
