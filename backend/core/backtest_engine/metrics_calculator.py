"""
Stage 6 Performance Validation: 9 Core Metrics Calculator

Calculates compound metrics from daily returns:
- CAGR: Compound Annual Growth Rate
- Max Drawdown: Largest peak-to-trough decline
- Sharpe Ratio: Excess return per unit of volatility
- Sortino Ratio: Excess return per unit of downside volatility
- Calmar Ratio: CAGR / |Max Drawdown|
- Information Ratio: Excess return / Tracking error
- Hit Ratio: Fraction of positive daily returns
- Profit Factor: Sum of gains / |Sum of losses|
- Turnover: Annual portfolio turnover ratio

All returns are daily fractional returns (e.g., 0.01 = 1%).
Annualization factor defaults to 252 (trading days/year).
"""

import numpy as np
from typing import List, Optional, Dict, Union


class MetricsCalculator:
    """Calculate 9 performance metrics from daily returns."""

    @staticmethod
    def cagr(returns: Union[List[float], np.ndarray], periods: int = 252) -> float:
        """
        Compound Annual Growth Rate.

        Formula: (1 + total_return) ^ (periods / n_periods) - 1
        where total_return = prod(1 + r_i) - 1

        Args:
            returns: List/array of daily fractional returns
            periods: Annualization factor (default 252 trading days)

        Returns:
            Float CAGR (e.g., 0.10 = 10% annual)
        """
        returns = np.asarray(returns)
        if len(returns) == 0:
            return 0.0

        # Cumulative return: product of (1 + daily_return)
        cumulative_return = np.prod(1 + returns) - 1

        # Number of periods (assuming each return is 1 trading day)
        n_periods = len(returns) / periods

        if n_periods <= 0:
            return 0.0

        cagr = (1 + cumulative_return) ** (1 / n_periods) - 1
        return float(cagr)

    @staticmethod
    def max_drawdown(returns: Union[List[float], np.ndarray]) -> float:
        """
        Maximum Drawdown.

        Formula: min(trough / peak - 1) for all rolling peaks/troughs
        Returns as negative value (e.g., -0.20 = -20%).

        Args:
            returns: List/array of daily fractional returns

        Returns:
            Float max drawdown, always ≤ 0 (e.g., -0.25 = 25% drawdown)
        """
        returns = np.asarray(returns)
        if len(returns) == 0:
            return 0.0

        # Cumulative growth: cumprod of (1 + returns)
        cumulative = np.cumprod(1 + returns)

        # Running maximum (peak)
        running_max = np.maximum.accumulate(cumulative)

        # Drawdown at each point
        drawdown = (cumulative - running_max) / running_max

        # Maximum drawdown (most negative)
        return float(np.min(drawdown))

    @staticmethod
    def sharpe_ratio(
        returns: Union[List[float], np.ndarray],
        risk_free_rate: float = 0.0,
        periods: int = 252,
    ) -> float:
        """
        Sharpe Ratio.

        Formula: (mean_return - risk_free_rate) / std_return * sqrt(periods)
        Annualized excess return per unit volatility.

        Args:
            returns: List/array of daily fractional returns
            risk_free_rate: Daily risk-free rate (default 0.0)
            periods: Annualization factor (default 252)

        Returns:
            Float Sharpe ratio (e.g., 1.5)
        """
        returns = np.asarray(returns)
        if len(returns) == 0:
            return 0.0

        mean_return = np.mean(returns)
        std_return = np.std(returns, ddof=1) if len(returns) > 1 else 0.0

        if std_return == 0:
            return 0.0

        sharpe = (mean_return - risk_free_rate) / std_return * np.sqrt(periods)
        return float(sharpe)

    @staticmethod
    def sortino_ratio(
        returns: Union[List[float], np.ndarray],
        risk_free_rate: float = 0.0,
        periods: int = 252,
    ) -> float:
        """
        Sortino Ratio.

        Formula: (mean_return - risk_free_rate) / downside_std * sqrt(periods)
        Annualized excess return per unit downside volatility (only negative returns).

        Args:
            returns: List/array of daily fractional returns
            risk_free_rate: Daily risk-free rate (default 0.0)
            periods: Annualization factor (default 252)

        Returns:
            Float Sortino ratio (e.g., 2.0)
        """
        returns = np.asarray(returns)
        if len(returns) == 0:
            return 0.0

        mean_return = np.mean(returns)

        # Downside returns: only values < risk_free_rate
        downside_returns = np.minimum(returns - risk_free_rate, 0)
        downside_std = np.std(downside_returns, ddof=1) if len(returns) > 1 else 0.0

        if downside_std == 0:
            return 0.0

        sortino = (mean_return - risk_free_rate) / downside_std * np.sqrt(periods)
        return float(sortino)

    @staticmethod
    def calmar_ratio(returns: Union[List[float], np.ndarray], periods: int = 252) -> float:
        """
        Calmar Ratio.

        Formula: CAGR / |Max Drawdown|
        Return relative to maximum drawdown risk.

        Args:
            returns: List/array of daily fractional returns
            periods: Annualization factor (default 252)

        Returns:
            Float Calmar ratio (e.g., 0.8)
        """
        cagr_val = MetricsCalculator.cagr(returns, periods)
        mdd = MetricsCalculator.max_drawdown(returns)

        if mdd == 0 or abs(mdd) < 1e-10:
            return 0.0

        calmar = cagr_val / abs(mdd)
        return float(calmar)

    @staticmethod
    def information_ratio(
        returns: Union[List[float], np.ndarray],
        benchmark_returns: Union[List[float], np.ndarray],
        periods: int = 252,
    ) -> float:
        """
        Information Ratio.

        Formula: (mean(excess_return)) / std(excess_return) * sqrt(periods)
        where excess_return = returns - benchmark_returns

        Args:
            returns: List/array of daily fractional returns
            benchmark_returns: List/array of benchmark daily returns
            periods: Annualization factor (default 252)

        Returns:
            Float Information ratio (e.g., 0.50)
        """
        returns = np.asarray(returns)
        benchmark_returns = np.asarray(benchmark_returns)

        if len(returns) == 0 or len(benchmark_returns) == 0:
            return 0.0

        # Ensure same length
        min_len = min(len(returns), len(benchmark_returns))
        returns = returns[:min_len]
        benchmark_returns = benchmark_returns[:min_len]

        excess_return = returns - benchmark_returns
        mean_excess = np.mean(excess_return)
        std_excess = np.std(excess_return, ddof=1) if len(excess_return) > 1 else 0.0

        if std_excess == 0:
            return 0.0

        ir = mean_excess / std_excess * np.sqrt(periods)
        return float(ir)

    @staticmethod
    def hit_ratio(returns: Union[List[float], np.ndarray]) -> float:
        """
        Hit Ratio.

        Formula: count(returns > 0) / len(returns)
        Fraction of positive daily returns.

        Args:
            returns: List/array of daily fractional returns

        Returns:
            Float hit ratio in [0, 1] (e.g., 0.55 = 55% positive days)
        """
        returns = np.asarray(returns)
        if len(returns) == 0:
            return 0.0

        positive_count = np.sum(returns > 0)
        return float(positive_count / len(returns))

    @staticmethod
    def profit_factor(returns: Union[List[float], np.ndarray]) -> float:
        """
        Profit Factor.

        Formula: sum(returns[returns > 0]) / |sum(returns[returns < 0])|
        Ratio of total gains to total losses (always positive).

        Args:
            returns: List/array of daily fractional returns

        Returns:
            Float profit factor (e.g., 1.5)
        """
        returns = np.asarray(returns)
        if len(returns) == 0:
            return 0.0

        gains = np.sum(returns[returns > 0])
        losses = np.sum(returns[returns < 0])

        if abs(losses) < 1e-10:
            return 0.0 if gains <= 0 else float("inf")

        return float(gains / abs(losses))

    @staticmethod
    def turnover(
        trade_values: Union[List[float], np.ndarray],
        portfolio_value: Union[List[float], np.ndarray],
        periods: int = 252,
    ) -> float:
        """
        Annual Turnover Ratio.

        Formula: (sum(abs(trade_values)) / mean(portfolio_value)) * (periods / n_days)
        Annual rate of portfolio turnover as a percentage.

        Args:
            trade_values: List/array of daily absolute trade values
            portfolio_value: List/array of daily portfolio values
            periods: Annualization factor (default 252)

        Returns:
            Float annual turnover (e.g., 2.5 = 250% annual)
        """
        trade_values = np.asarray(trade_values)
        portfolio_value = np.asarray(portfolio_value)

        if len(portfolio_value) == 0 or len(trade_values) == 0:
            return 0.0

        # Ensure same length
        min_len = min(len(trade_values), len(portfolio_value))
        trade_values = trade_values[:min_len]
        portfolio_value = portfolio_value[:min_len]

        mean_portfolio = np.mean(portfolio_value)
        if mean_portfolio <= 0:
            return 0.0

        total_trades = np.sum(np.abs(trade_values))
        daily_turnover = total_trades / mean_portfolio

        # Annualize
        n_days = len(trade_values)
        annual_turnover = daily_turnover * (periods / n_days)

        return float(annual_turnover)

    @classmethod
    def calculate_all(
        cls,
        returns: Union[List[float], np.ndarray],
        benchmark_returns: Optional[Union[List[float], np.ndarray]] = None,
        trade_values: Optional[Union[List[float], np.ndarray]] = None,
        portfolio_value: Optional[Union[List[float], np.ndarray]] = None,
        periods: int = 252,
    ) -> Dict[str, float]:
        """
        Calculate all 9 metrics at once.

        Args:
            returns: List/array of daily fractional returns
            benchmark_returns: Optional benchmark returns for IR calculation
            trade_values: Optional daily trade values for turnover
            portfolio_value: Optional daily portfolio values for turnover
            periods: Annualization factor (default 252)

        Returns:
            Dict with keys: cagr, max_drawdown, sharpe_ratio, sortino_ratio,
                          calmar_ratio, information_ratio, hit_ratio,
                          profit_factor, turnover
        """
        metrics = {
            "cagr": cls.cagr(returns, periods),
            "max_drawdown": cls.max_drawdown(returns),
            "sharpe_ratio": cls.sharpe_ratio(returns, periods=periods),
            "sortino_ratio": cls.sortino_ratio(returns, periods=periods),
            "calmar_ratio": cls.calmar_ratio(returns, periods),
            "hit_ratio": cls.hit_ratio(returns),
            "profit_factor": cls.profit_factor(returns),
        }

        # Information ratio (requires benchmark)
        if benchmark_returns is not None:
            metrics["information_ratio"] = cls.information_ratio(
                returns, benchmark_returns, periods
            )
        else:
            metrics["information_ratio"] = None

        # Turnover (requires trade_values and portfolio_value)
        if trade_values is not None and portfolio_value is not None:
            metrics["turnover"] = cls.turnover(trade_values, portfolio_value, periods)
        else:
            metrics["turnover"] = None

        return metrics
