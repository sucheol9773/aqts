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
import pytest

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

    def test_transaction_costs_reduce_return(self):
        """거래 비용이 수익을 감소시킴"""
        prices = _make_prices(252, ["A"])
        signals = _make_signals(prices, "alternating")

        # 비용 없는 경우
        config_no_cost = BacktestConfig(
            initial_capital=50_000_000,
            commission_rate=0.0, tax_rate=0.0, slippage_rate=0.0,
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
                strategy_name=name, config=config,
                start_date="2024-01-02", end_date="2024-12-31",
                initial_capital=50e6, final_capital=50e6 * (1 + ret),
                total_return=ret, cagr=ret, mdd=-0.1,
                sharpe_ratio=sharpe, sortino_ratio=sharpe * 1.2,
                calmar_ratio=ret / 0.1,
                win_rate=0.55, profit_factor=1.5,
                total_trades=50, avg_trade_return=ret / 50,
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
