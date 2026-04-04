"""
Market impact model for AQTS backtesting.

Implements Almgren-Chriss simplified model for estimating price impact
of orders based on market conditions and order size.

Stage 3-B: Backtest integrity advanced realism modules (market microstructure realism).
"""

import math


class MarketImpactModel:
    """
    Almgren-Chriss simplified market impact model.

    Estimates the permanent and temporary price impact of executing orders.
    """

    def __init__(self, gamma: float = 0.314, eta: float = 0.142):
        """
        Initialize the market impact model.

        Args:
            gamma: Permanent impact coefficient (default: 0.314)
            eta: Temporary impact coefficient (default: 0.142)
        """
        self.gamma = gamma
        self.eta = eta

    def permanent_impact(self, order_quantity: float, adv: float, daily_volatility: float) -> float:
        """
        Calculate permanent price impact.

        Permanent impact represents the market's updated view of fair value
        based on the order. Models linear market impact proportional to
        participation rate and volatility.

        Formula: permanent_impact_ratio = gamma * sigma * (Q/V)^0.5

        Args:
            order_quantity: Size of order (shares)
            adv: Average daily volume (shares)
            daily_volatility: Daily volatility (as decimal, e.g., 0.02 for 2%)

        Returns:
            Permanent impact as ratio of current price (e.g., 0.005 = 50 bps)

        Raises:
            ValueError: If inputs are invalid (negative or zero ADV)

        Example:
            >>> model = MarketImpactModel()
            >>> # 10,000 share order, 1M ADV, 2% volatility
            >>> impact = model.permanent_impact(10000, 1000000, 0.02)
            >>> isinstance(impact, float) and impact > 0
            True
        """
        if adv <= 0:
            raise ValueError("ADV must be positive")
        if daily_volatility < 0:
            raise ValueError("Volatility cannot be negative")

        # Avoid division by zero for zero volatility case
        if daily_volatility == 0:
            return 0.0

        participation_ratio = order_quantity / adv
        impact_ratio = self.gamma * daily_volatility * math.sqrt(participation_ratio)

        return impact_ratio

    def temporary_impact(self, order_quantity: float, adv: float, daily_volatility: float) -> float:
        """
        Calculate temporary price impact.

        Temporary impact represents the spread and liquidity cost of executing
        the order. Returns to equilibrium as order execution completes.

        Formula: temporary_impact_ratio = eta * sigma * (Q/V)^0.6

        Args:
            order_quantity: Size of order (shares)
            adv: Average daily volume (shares)
            daily_volatility: Daily volatility (as decimal, e.g., 0.02 for 2%)

        Returns:
            Temporary impact as ratio of current price (e.g., 0.005 = 50 bps)

        Raises:
            ValueError: If inputs are invalid (negative or zero ADV)

        Example:
            >>> model = MarketImpactModel()
            >>> # 10,000 share order, 1M ADV, 2% volatility
            >>> impact = model.temporary_impact(10000, 1000000, 0.02)
            >>> isinstance(impact, float) and impact > 0
            True
        """
        if adv <= 0:
            raise ValueError("ADV must be positive")
        if daily_volatility < 0:
            raise ValueError("Volatility cannot be negative")

        # Avoid division by zero for zero volatility case
        if daily_volatility == 0:
            return 0.0

        participation_ratio = order_quantity / adv
        impact_ratio = self.eta * daily_volatility * (participation_ratio**0.6)

        return impact_ratio

    def total_impact(self, order_quantity: float, adv: float, daily_volatility: float, price: float) -> float:
        """
        Calculate total price impact cost.

        Combines permanent and temporary impacts and returns the total cost
        in absolute price units (not percentage).

        Args:
            order_quantity: Size of order (shares)
            adv: Average daily volume (shares)
            daily_volatility: Daily volatility (as decimal, e.g., 0.02 for 2%)
            price: Current price per share

        Returns:
            Total cost in price units (e.g., 0.50 means $0.50 per share impact)

        Raises:
            ValueError: If inputs are invalid

        Example:
            >>> model = MarketImpactModel()
            >>> # 10,000 share order, 1M ADV, 2% volatility, $100 price
            >>> cost = model.total_impact(10000, 1000000, 0.02, 100.0)
            >>> isinstance(cost, float) and cost > 0
            True
        """
        if price <= 0:
            raise ValueError("Price must be positive")
        if adv <= 0:
            raise ValueError("ADV must be positive")

        perm_impact = self.permanent_impact(order_quantity, adv, daily_volatility)
        temp_impact = self.temporary_impact(order_quantity, adv, daily_volatility)

        total_impact_ratio = perm_impact + temp_impact
        total_cost = total_impact_ratio * price

        return total_cost
