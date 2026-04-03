"""
Stage 6 Performance Validation: Statistical Significance Testing

Determine if returns are statistically significantly different from benchmark:
- Bootstrap confidence intervals for mean return
- T-test vs benchmark
- Significance testing at 95% confidence level
"""

import numpy as np
from typing import List, Tuple, Dict, Union
from scipy import stats


class SignificanceTest:
    """Test statistical significance of strategy returns."""

    @staticmethod
    def bootstrap_ci(
        returns: Union[List[float], np.ndarray],
        n_bootstrap: int = 1000,
        confidence: float = 0.95,
    ) -> Tuple[float, float]:
        """
        Bootstrap confidence interval for mean return.

        Resamples returns with replacement n_bootstrap times and
        calculates percentile-based confidence interval.

        Args:
            returns: List/array of daily fractional returns
            n_bootstrap: Number of bootstrap samples (default 1000)
            confidence: Confidence level (default 0.95 = 95%)

        Returns:
            Tuple (lower_bound, upper_bound) for mean return
        """
        returns = np.asarray(returns)
        if len(returns) == 0:
            return 0.0, 0.0

        bootstrap_means = []

        for _ in range(n_bootstrap):
            # Resample with replacement
            sample = np.random.choice(returns, size=len(returns), replace=True)
            bootstrap_means.append(np.mean(sample))

        bootstrap_means = np.array(bootstrap_means)

        # Percentile-based CI
        alpha = 1 - confidence
        lower_percentile = alpha / 2 * 100
        upper_percentile = (1 - alpha / 2) * 100

        lower = float(np.percentile(bootstrap_means, lower_percentile))
        upper = float(np.percentile(bootstrap_means, upper_percentile))

        return lower, upper

    @staticmethod
    def t_test_vs_benchmark(
        returns: Union[List[float], np.ndarray],
        benchmark_returns: Union[List[float], np.ndarray],
    ) -> Dict[str, float]:
        """
        Independent t-test comparing returns to benchmark.

        Tests null hypothesis: mean(returns) = mean(benchmark_returns)

        Args:
            returns: List/array of daily fractional returns
            benchmark_returns: List/array of benchmark daily returns

        Returns:
            Dict with keys:
            - t_statistic: t-test statistic
            - p_value: two-tailed p-value
            - significant: bool (True if p_value < 0.05)
            - mean_difference: mean(returns) - mean(benchmark)
        """
        returns = np.asarray(returns)
        benchmark_returns = np.asarray(benchmark_returns)

        if len(returns) == 0 or len(benchmark_returns) == 0:
            return {
                "t_statistic": 0.0,
                "p_value": 1.0,
                "significant": False,
                "mean_difference": 0.0,
            }

        # Independent samples t-test
        t_stat, p_val = stats.ttest_ind(returns, benchmark_returns)

        mean_diff = float(np.mean(returns) - np.mean(benchmark_returns))

        return {
            "t_statistic": float(t_stat),
            "p_value": float(p_val),
            "significant": p_val < 0.05,
            "mean_difference": mean_diff,
        }

    @staticmethod
    def is_significant(
        returns: Union[List[float], np.ndarray],
        benchmark_returns: Union[List[float], np.ndarray],
        confidence: float = 0.95,
    ) -> bool:
        """
        Simple significance check: is mean return significantly different from benchmark?

        Uses bootstrap CI approach. Returns True if benchmark mean
        falls outside the CI of the strategy mean.

        Args:
            returns: List/array of daily fractional returns
            benchmark_returns: List/array of benchmark daily returns
            confidence: Confidence level (default 0.95)

        Returns:
            True if statistically significant (benchmark outside CI)
        """
        benchmark_mean = np.mean(benchmark_returns)
        lower, upper = SignificanceTest.bootstrap_ci(returns, confidence=confidence)

        # Significant if benchmark mean is outside the CI
        return benchmark_mean < lower or benchmark_mean > upper

    @staticmethod
    def excess_return_ttest(
        returns: Union[List[float], np.ndarray],
        benchmark_returns: Union[List[float], np.ndarray],
    ) -> Dict[str, float]:
        """
        T-test on excess returns (returns - benchmark_returns).

        Tests if excess return is significantly different from 0.

        Args:
            returns: List/array of daily fractional returns
            benchmark_returns: List/array of benchmark daily returns

        Returns:
            Dict with t_statistic, p_value, significant
        """
        returns = np.asarray(returns)
        benchmark_returns = np.asarray(benchmark_returns)

        if len(returns) == 0 or len(benchmark_returns) == 0:
            return {
                "t_statistic": 0.0,
                "p_value": 1.0,
                "significant": False,
            }

        # Ensure same length
        min_len = min(len(returns), len(benchmark_returns))
        returns = returns[:min_len]
        benchmark_returns = benchmark_returns[:min_len]

        excess = returns - benchmark_returns

        # One-sample t-test against 0
        t_stat, p_val = stats.ttest_1samp(excess, 0)

        return {
            "t_statistic": float(t_stat),
            "p_value": float(p_val),
            "significant": p_val < 0.05,
        }
