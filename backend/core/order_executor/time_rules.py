"""
Time-of-day execution rules for AQTS.

Handles market hours, auction periods, and time-of-day dependent execution
constraints for different markets (KRX, NYSE, NASDAQ, AMEX).

Stage 3-B: Backtest integrity advanced realism modules (market microstructure realism).
"""

from datetime import time
from typing import Dict, Optional
from config.constants import Market


class TimeOfDayRules:
    """
    Market hours and time-of-day execution rules.

    Manages opening/closing auctions, trading hours, and spread multipliers
    based on time of day and market.
    """

    def __init__(self):
        """Initialize time-of-day rules with market-specific configurations."""
        self._market_hours = self._setup_market_hours()

    @staticmethod
    def _setup_market_hours() -> Dict[str, Dict]:
        """
        Set up market hours and auction periods for each market.

        Returns:
            Dict mapping market names to their trading hours configuration
        """
        return {
            Market.KRX: {
                "open": time(9, 0),          # 09:00
                "close": time(15, 30),       # 15:30
                "auction_open_start": time(8, 30),    # 08:30 opening auction
                "auction_open_end": time(9, 0),       # 09:00
                "auction_close_start": time(15, 20),  # 15:20 closing auction
                "auction_close_end": time(15, 30),    # 15:30
                "daily_limit_pct": 0.30,    # ±30% circuit breaker
            },
            Market.NYSE: {
                "open": time(9, 30),         # 09:30
                "close": time(16, 0),        # 16:00
                "auction_open_start": time(9, 28),    # Pre-market, ~2 min before open
                "auction_open_end": time(9, 30),      # First 5 min = opening auction-like
                "auction_close_start": time(15, 55),  # Last 5 minutes (15:55-16:00)
                "auction_close_end": time(16, 0),
                "circuit_breaker_l1": 0.07,  # 7% market-wide limit
                "circuit_breaker_l2": 0.13,  # 13% market-wide limit
                "circuit_breaker_l3": 0.20,  # 20% market-wide limit
            },
            Market.NASDAQ: {
                "open": time(9, 30),         # 09:30
                "close": time(16, 0),        # 16:00
                "auction_open_start": time(9, 28),
                "auction_open_end": time(9, 30),
                "auction_close_start": time(15, 55),
                "auction_close_end": time(16, 0),
                "circuit_breaker_l1": 0.07,
                "circuit_breaker_l2": 0.13,
                "circuit_breaker_l3": 0.20,
            },
            Market.AMEX: {
                "open": time(9, 30),
                "close": time(16, 0),
                "auction_open_start": time(9, 28),
                "auction_open_end": time(9, 30),
                "auction_close_start": time(15, 55),
                "auction_close_end": time(16, 0),
                "circuit_breaker_l1": 0.07,
                "circuit_breaker_l2": 0.13,
                "circuit_breaker_l3": 0.20,
            },
        }

    def get_market_hours(self, market: str) -> Dict:
        """
        Get market hours configuration for a specific market.

        Args:
            market: Market name (from config.constants.Market)

        Returns:
            Dict with 'open', 'close', 'auction_open_start', 'auction_open_end',
            'auction_close_start', 'auction_close_end', and market-specific limits

        Raises:
            ValueError: If market is not supported

        Example:
            >>> rules = TimeOfDayRules()
            >>> hours = rules.get_market_hours(Market.KRX)
            >>> hours['open'] == time(9, 0)
            True
        """
        if market not in self._market_hours:
            raise ValueError(f"Unsupported market: {market}")

        return self._market_hours[market].copy()

    def is_auction_period(self, time_of_day: time, market: str) -> bool:
        """
        Check if current time is within auction period.

        KRX: 08:30-09:00 (opening), 15:20-15:30 (closing)
        NYSE/NASDAQ/AMEX: ~09:28-09:30 (opening), 15:55-16:00 (closing)

        Args:
            time_of_day: Current time as time object
            market: Market name (from config.constants.Market)

        Returns:
            True if in auction period, False otherwise

        Raises:
            ValueError: If market is not supported

        Example:
            >>> rules = TimeOfDayRules()
            >>> rules.is_auction_period(time(8, 45), Market.KRX)
            True
            >>> rules.is_auction_period(time(12, 0), Market.KRX)
            False
        """
        hours = self.get_market_hours(market)

        # Check opening auction
        if hours["auction_open_start"] <= time_of_day < hours["auction_open_end"]:
            return True

        # Check closing auction
        if hours["auction_close_start"] <= time_of_day <= hours["auction_close_end"]:
            return True

        return False

    def can_execute(self, time_of_day: time, market: str) -> bool:
        """
        Determine if execution is allowed at current time.

        Returns False during auction periods when execution is restricted.
        Returns False outside regular market hours.

        Args:
            time_of_day: Current time as time object
            market: Market name (from config.constants.Market)

        Returns:
            True if execution is allowed, False otherwise

        Raises:
            ValueError: If market is not supported

        Example:
            >>> rules = TimeOfDayRules()
            >>> rules.can_execute(time(10, 0), Market.KRX)
            True
            >>> rules.can_execute(time(8, 45), Market.KRX)  # Opening auction
            False
        """
        hours = self.get_market_hours(market)

        # Check if within market hours
        if not (hours["open"] <= time_of_day <= hours["close"]):
            return False

        # Check if in auction period (cannot execute during auction)
        if self.is_auction_period(time_of_day, market):
            return False

        return True

    def get_spread_multiplier(self, time_of_day: time, market: str) -> float:
        """
        Get spread multiplier based on time of day.

        Spreads widen at market open and close due to increased uncertainty
        and volatility. Multiplier of 1.0 represents normal hours spread.
        Auction times (open/close) have multiplier of 2.0.

        Specific times:
        - KRX: First 10 min after 09:00, last 10 min before 15:30
        - NYSE: First 5 min after 09:30, last 5 min before 16:00

        Args:
            time_of_day: Current time as time object
            market: Market name (from config.constants.Market)

        Returns:
            Spread multiplier (1.0 = normal, 2.0 = auction-like, 1.5 = midday)

        Raises:
            ValueError: If market is not supported

        Example:
            >>> rules = TimeOfDayRules()
            >>> rules.get_spread_multiplier(time(9, 2), Market.KRX)
            2.0
            >>> rules.get_spread_multiplier(time(12, 0), Market.KRX)
            1.0
        """
        hours = self.get_market_hours(market)

        # Highest spreads during opening auction
        if hours["auction_open_start"] <= time_of_day < hours["auction_open_end"]:
            return 2.0

        # Highest spreads during closing auction
        if hours["auction_close_start"] <= time_of_day <= hours["auction_close_end"]:
            return 2.0

        # Elevated spreads in early trading (first 5-10 min after official open)
        market_str = str(market).upper() if isinstance(market, Market) else market.upper()

        if market_str == "KRX":
            # First 10 minutes after 09:00
            if hours["open"] <= time_of_day < time(9, 10):
                return 1.5
            # Last 10 minutes before 15:30
            if time(15, 20) <= time_of_day <= hours["close"]:
                return 1.5
        else:  # NYSE, NASDAQ, AMEX
            # First 5 minutes after 09:30
            if hours["open"] <= time_of_day < time(9, 35):
                return 1.5
            # Last 5 minutes before 16:00
            if time(15, 55) <= time_of_day <= hours["close"]:
                return 1.5

        # Normal spreads during regular hours
        return 1.0
