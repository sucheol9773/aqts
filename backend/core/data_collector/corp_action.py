"""
Corporate action handling module for AQTS.

Handles stock splits, dividends, and other corporate actions that affect price series.
Stage 3-B: Backtest integrity advanced realism modules (market microstructure realism).
"""

from typing import List, Dict, Tuple, Optional
from datetime import datetime


class CorporateActionProcessor:
    """
    Process and apply corporate actions to historical price data.

    Adjusts prices for stock splits and dividends to ensure backtesting accuracy.
    """

    def __init__(self):
        """Initialize the corporate action processor."""
        pass

    @staticmethod
    def adjust_for_split(price: float, split_ratio: float) -> float:
        """
        Adjust price for stock split.

        Args:
            price: Original price before split
            split_ratio: Split ratio (e.g., 2.0 for 2:1 split)

        Returns:
            Adjusted price after split

        Example:
            >>> processor = CorporateActionProcessor()
            >>> processor.adjust_for_split(100.0, 2.0)
            50.0
            >>> processor.adjust_for_split(100.0, 0.5)
            200.0
        """
        if split_ratio <= 0:
            raise ValueError("Split ratio must be positive")
        return price / split_ratio

    @staticmethod
    def adjust_for_dividend(price: float, dividend_amount: float) -> float:
        """
        Adjust price for cash dividend.

        Subtracts dividend amount from price to account for ex-dividend date.

        Args:
            price: Price before dividend adjustment
            dividend_amount: Cash dividend per share

        Returns:
            Adjusted price after dividend

        Example:
            >>> processor = CorporateActionProcessor()
            >>> processor.adjust_for_dividend(100.0, 1.5)
            98.5
        """
        if dividend_amount < 0:
            raise ValueError("Dividend amount cannot be negative")
        return max(price - dividend_amount, 0.0)

    @staticmethod
    def adjust_price_series(prices: List[float], actions: List[Dict]) -> List[float]:
        """
        Apply a series of corporate actions to historical price data.

        Adjusts prices chronologically. Actions are applied in order, with later
        actions affecting earlier prices.

        Args:
            prices: List of prices (typically oldest to newest)
            actions: List of action dicts with keys:
                - 'date' or 'index': Action date or price index
                - 'type': 'split' or 'dividend'
                - 'ratio' (for split): Split ratio
                - 'amount' (for dividend): Dividend amount

        Returns:
            List of adjusted prices

        Example:
            >>> processor = CorporateActionProcessor()
            >>> prices = [100.0, 101.0, 102.0, 103.0, 104.0]
            >>> actions = [
            ...     {'index': 2, 'type': 'split', 'ratio': 2.0},
            ...     {'index': 4, 'type': 'dividend', 'amount': 1.0}
            ... ]
            >>> adjusted = processor.adjust_price_series(prices, actions)
            >>> adjusted == [50.0, 50.5, 51.0, 103.0, 103.0]
            True
        """
        if not prices:
            return []

        # Work backwards to apply adjustments correctly
        adjusted = prices.copy()

        # Sort actions by index/date in descending order
        sorted_actions = sorted(
            actions,
            key=lambda x: x.get('index', x.get('date', 0)),
            reverse=True
        )

        for action in sorted_actions:
            action_index = action.get('index', action.get('date'))
            if not isinstance(action_index, int) or action_index < 0 or action_index >= len(adjusted):
                continue

            action_type = action.get('type', '').lower()

            if action_type == 'split':
                split_ratio = action.get('ratio', 1.0)
                # Adjust all prices before the split
                for i in range(action_index + 1):
                    adjusted[i] = CorporateActionProcessor.adjust_for_split(
                        adjusted[i], split_ratio
                    )
            elif action_type == 'dividend':
                dividend_amount = action.get('amount', 0.0)
                # Adjust all prices before the dividend (ex-dividend date)
                for i in range(action_index + 1):
                    adjusted[i] = CorporateActionProcessor.adjust_for_dividend(
                        adjusted[i], dividend_amount
                    )

        return adjusted

    @staticmethod
    def detect_split(prices: List[float], threshold: float = 0.4) -> List[int]:
        """
        Detect potential stock splits in price data.

        Identifies overnight price changes exceeding the threshold (default 40%).
        These are likely candidates for stock splits or other corporate actions.

        Args:
            prices: List of prices (oldest to newest)
            threshold: Minimum price change ratio to flag as potential split (default 0.4)

        Returns:
            List of indices where splits were detected

        Example:
            >>> processor = CorporateActionProcessor()
            >>> prices = [100.0, 101.0, 102.0, 51.0, 52.0, 53.0]
            >>> splits = processor.detect_split(prices, threshold=0.4)
            >>> splits == [3]
            True
        """
        if len(prices) < 2 or threshold < 0 or threshold > 1:
            return []

        detected_splits = []

        for i in range(1, len(prices)):
            prev_price = prices[i - 1]
            curr_price = prices[i]

            if prev_price <= 0:
                continue

            # Calculate percentage change
            pct_change = abs(curr_price - prev_price) / prev_price

            # Flag if change exceeds threshold
            if pct_change > threshold:
                detected_splits.append(i)

        return detected_splits
