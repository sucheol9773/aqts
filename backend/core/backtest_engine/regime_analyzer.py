"""
Stage 6 Performance Validation: Regime Analysis

Classify market regimes and calculate regime-specific metrics:
- BULL: Positive 6M return, low volatility
- BEAR: Negative 6M return, low volatility
- HIGH_VOL: High volatility environment (≥25%)
- RISING_RATE: Rising interest rate environment

Provides per-regime performance metrics for validation.
"""

from typing import Dict, List, Union

import numpy as np

from .metrics_calculator import MetricsCalculator


class RegimeAnalyzer:
    """Classify regimes and analyze per-regime performance."""

    # Regime classification constants
    BULL = "BULL"
    BEAR = "BEAR"
    HIGH_VOL = "HIGH_VOL"
    RISING_RATE = "RISING_RATE"

    VALID_REGIMES = {BULL, BEAR, HIGH_VOL, RISING_RATE}

    @staticmethod
    def classify_regime(
        market_returns: Union[List[float], np.ndarray],
        volatility: float,
        interest_rate_change: float,
    ) -> str:
        """
        Classify market regime.

        Rules (evaluated in order):
        1. HIGH_VOL: if volatility ≥ 25% → HIGH_VOL
        2. BULL: if recent return > 0 AND volatility < 20% → BULL
        3. BEAR: if recent return < 0 AND volatility < 25% → BEAR
        4. RISING_RATE: if interest_rate_change > 0 → RISING_RATE
        5. Default: BULL (fallback)

        Args:
            market_returns: List/array of daily market returns
            volatility: Current market volatility (e.g., 0.18 = 18%)
            interest_rate_change: Interest rate change (e.g., 0.001 = 10bps)

        Returns:
            String regime: BULL, BEAR, HIGH_VOL, or RISING_RATE
        """
        market_returns = np.asarray(market_returns)

        # Calculate 6-month (126 trading days) return
        if len(market_returns) >= 126:
            recent_return = np.prod(1 + market_returns[-126:]) - 1
        else:
            recent_return = np.prod(1 + market_returns) - 1

        # Rule 1: High volatility dominates
        if volatility >= 0.25:
            return RegimeAnalyzer.HIGH_VOL

        # Rule 2: Bull market
        if recent_return > 0 and volatility < 0.20:
            return RegimeAnalyzer.BULL

        # Rule 3: Bear market
        if recent_return < 0 and volatility < 0.25:
            return RegimeAnalyzer.BEAR

        # Rule 4: Rising rate environment
        if interest_rate_change > 0:
            return RegimeAnalyzer.RISING_RATE

        # Default fallback
        return RegimeAnalyzer.BULL

    @staticmethod
    def split_by_regime(
        returns: Union[List[float], np.ndarray],
        regime_labels: Union[List[str], np.ndarray],
    ) -> Dict[str, List[float]]:
        """
        Split returns by regime.

        Args:
            returns: List/array of daily fractional returns
            regime_labels: List/array of regime labels (same length as returns)

        Returns:
            Dict {regime: list of returns for that regime}
        """
        returns = np.asarray(returns)
        regime_labels = np.asarray(regime_labels)

        if len(returns) != len(regime_labels):
            raise ValueError("returns and regime_labels must have same length")

        regimes_split: Dict[str, List[float]] = {}

        for regime in RegimeAnalyzer.VALID_REGIMES:
            mask = regime_labels == regime
            regime_returns = returns[mask].tolist()
            if regime_returns:
                regimes_split[regime] = regime_returns

        return regimes_split

    @staticmethod
    def regime_metrics(
        returns: Union[List[float], np.ndarray],
        regime_labels: Union[List[str], np.ndarray],
    ) -> Dict[str, Dict[str, float]]:
        """
        Calculate MetricsCalculator for each regime.

        Args:
            returns: List/array of daily fractional returns
            regime_labels: List/array of regime labels (same length as returns)

        Returns:
            Dict {regime: metrics_dict} where metrics_dict has:
            cagr, max_drawdown, sharpe_ratio, sortino_ratio, calmar_ratio,
            hit_ratio, profit_factor, information_ratio, turnover
        """
        regimes_split = RegimeAnalyzer.split_by_regime(returns, regime_labels)

        regime_metrics: Dict[str, Dict[str, float]] = {}

        for regime, regime_returns in regimes_split.items():
            if not regime_returns:
                continue

            metrics = MetricsCalculator.calculate_all(regime_returns)
            regime_metrics[regime] = metrics

        return regime_metrics

    @staticmethod
    def regime_summary(
        returns: Union[List[float], np.ndarray],
        regime_labels: Union[List[str], np.ndarray],
    ) -> Dict[str, Dict[str, float]]:
        """
        Summary statistics per regime.

        Args:
            returns: List/array of daily fractional returns
            regime_labels: List/array of regime labels

        Returns:
            Dict {regime: {count, mean_return, std_return, ...}}
        """
        regimes_split = RegimeAnalyzer.split_by_regime(returns, regime_labels)

        summary: Dict[str, Dict[str, float]] = {}

        for regime, regime_returns in regimes_split.items():
            regime_array = np.asarray(regime_returns)
            summary[regime] = {
                "count": len(regime_returns),
                "mean_return": float(np.mean(regime_array)),
                "std_return": float(np.std(regime_array)),
                "min_return": float(np.min(regime_array)),
                "max_return": float(np.max(regime_array)),
            }

        return summary
