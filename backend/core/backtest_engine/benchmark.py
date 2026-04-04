"""
Stage 6 Performance Validation: Benchmark Management

Manages benchmark definitions and provides default benchmarks:
- KOSPI: Korean Composite Stock Price Index
- SP500: S&P 500 Index
- SPY: SPDR S&P 500 ETF returns
- BALANCED_60_40: 60/40 equity/bond portfolio
- PASSIVE: Passive diversified portfolio
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np


@dataclass
class Benchmark:
    """Benchmark definition with daily returns."""

    name: str
    ticker: str
    returns: List[float]

    def __post_init__(self):
        """Validate benchmark data."""
        if not self.returns:
            raise ValueError(f"Benchmark {self.name} has empty returns")
        self.returns = list(self.returns)


class BenchmarkManager:
    """Manage benchmark creation, retrieval, and defaults."""

    def __init__(self):
        """Initialize with default benchmarks."""
        self._benchmarks: Dict[str, Benchmark] = {}
        self._initialize_defaults()

    def _initialize_defaults(self):
        """Initialize 5 default benchmarks with synthetic daily returns."""
        # KOSPI: Korean market, slightly lower volatility
        kospi_returns = self._generate_synthetic_returns(
            n_days=252,
            annual_return=0.07,
            annual_vol=0.18,
            seed=42,
        )
        self._benchmarks["KOSPI"] = Benchmark(
            name="KOSPI",
            ticker="KOSPI",
            returns=kospi_returns,
        )

        # S&P 500: Large cap US equities
        sp500_returns = self._generate_synthetic_returns(
            n_days=252,
            annual_return=0.10,
            annual_vol=0.15,
            seed=43,
        )
        self._benchmarks["SP500"] = Benchmark(
            name="S&P 500",
            ticker="^GSPC",
            returns=sp500_returns,
        )

        # SPY: S&P 500 ETF
        spy_returns = self._generate_synthetic_returns(
            n_days=252,
            annual_return=0.095,
            annual_vol=0.16,
            seed=44,
        )
        self._benchmarks["SPY"] = Benchmark(
            name="SPY ETF",
            ticker="SPY",
            returns=spy_returns,
        )

        # BALANCED_60_40: 60% equities, 40% bonds
        eq_returns = self._generate_synthetic_returns(0.10, 0.15, 252, 45)
        bd_returns = self._generate_synthetic_returns(0.03, 0.05, 252, 46)
        balanced_returns = [0.6 * e + 0.4 * b for e, b in zip(eq_returns, bd_returns)]
        self._benchmarks["BALANCED_60_40"] = Benchmark(
            name="60/40 Balanced",
            ticker="BALANCED_60_40",
            returns=balanced_returns,
        )

        # PASSIVE: Diversified passive portfolio
        passive_returns = self._generate_synthetic_returns(
            n_days=252,
            annual_return=0.06,
            annual_vol=0.10,
            seed=47,
        )
        self._benchmarks["PASSIVE"] = Benchmark(
            name="Passive Diversified",
            ticker="PASSIVE",
            returns=passive_returns,
        )

    @staticmethod
    def _generate_synthetic_returns(
        annual_return: float,
        annual_vol: float,
        n_days: int = 252,
        seed: Optional[int] = None,
    ) -> List[float]:
        """
        Generate synthetic daily returns from annual parameters.

        Uses normal distribution with drift.

        Args:
            annual_return: Annual expected return
            annual_vol: Annual volatility
            n_days: Number of daily returns
            seed: Random seed for reproducibility

        Returns:
            List of daily fractional returns
        """
        if seed is not None:
            np.random.seed(seed)

        daily_return = annual_return / n_days
        daily_vol = annual_vol / np.sqrt(n_days)

        returns = np.random.normal(daily_return, daily_vol, n_days)
        return returns.tolist()

    def create_benchmark(self, name: str, returns: List[float], ticker: Optional[str] = None) -> Benchmark:
        """
        Create and register a new benchmark.

        Args:
            name: Benchmark name
            returns: List of daily fractional returns
            ticker: Optional ticker symbol

        Returns:
            Benchmark object
        """
        if ticker is None:
            ticker = name.upper().replace(" ", "_")

        benchmark = Benchmark(name=name, ticker=ticker, returns=returns)
        self._benchmarks[name] = benchmark
        return benchmark

    def get_benchmark(self, name: str) -> Optional[Benchmark]:
        """
        Retrieve a benchmark by name.

        Args:
            name: Benchmark name

        Returns:
            Benchmark object or None if not found
        """
        return self._benchmarks.get(name)

    def available_benchmarks(self) -> List[str]:
        """
        List all available benchmark names.

        Returns:
            List of benchmark names
        """
        return list(self._benchmarks.keys())

    def remove_benchmark(self, name: str) -> bool:
        """
        Remove a benchmark.

        Args:
            name: Benchmark name

        Returns:
            True if removed, False if not found
        """
        if name in self._benchmarks:
            del self._benchmarks[name]
            return True
        return False
