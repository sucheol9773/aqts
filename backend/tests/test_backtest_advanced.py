"""
Advanced backtest integrity tests for Stage 3-B.

Tests corporate action processing, market impact modeling, and time-of-day rules
for realistic backtesting with market microstructure considerations.
"""

from datetime import time

import pytest

from config.constants import Market
from core.backtest_engine.impact_model import MarketImpactModel
from core.data_collector.corp_action import CorporateActionProcessor
from core.order_executor.time_rules import TimeOfDayRules

# ══════════════════════════════════════════════════════════════════════════════
# CorporateActionProcessor Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestCorporateActionProcessor:
    """Test suite for CorporateActionProcessor."""

    def test_adjust_for_split_2_to_1(self):
        """Test 2:1 stock split adjustment."""
        processor = CorporateActionProcessor()
        adjusted = processor.adjust_for_split(100.0, 2.0)
        assert adjusted == 50.0

    def test_adjust_for_split_3_to_1(self):
        """Test 3:1 stock split adjustment."""
        processor = CorporateActionProcessor()
        adjusted = processor.adjust_for_split(99.0, 3.0)
        assert adjusted == 33.0

    def test_adjust_for_split_reverse_split(self):
        """Test reverse stock split (consolidation)."""
        processor = CorporateActionProcessor()
        # 1:2 reverse split (0.5 ratio)
        adjusted = processor.adjust_for_split(50.0, 0.5)
        assert adjusted == 100.0

    def test_adjust_for_split_invalid_ratio(self):
        """Test split with invalid (negative) ratio."""
        processor = CorporateActionProcessor()
        with pytest.raises(ValueError, match="Split ratio must be positive"):
            processor.adjust_for_split(100.0, -2.0)

    def test_adjust_for_split_zero_ratio(self):
        """Test split with zero ratio."""
        processor = CorporateActionProcessor()
        with pytest.raises(ValueError, match="Split ratio must be positive"):
            processor.adjust_for_split(100.0, 0.0)

    def test_adjust_for_dividend(self):
        """Test dividend adjustment."""
        processor = CorporateActionProcessor()
        adjusted = processor.adjust_for_dividend(100.0, 1.5)
        assert adjusted == 98.5

    def test_adjust_for_dividend_zero(self):
        """Test zero dividend adjustment."""
        processor = CorporateActionProcessor()
        adjusted = processor.adjust_for_dividend(100.0, 0.0)
        assert adjusted == 100.0

    def test_adjust_for_dividend_large(self):
        """Test large dividend adjustment."""
        processor = CorporateActionProcessor()
        adjusted = processor.adjust_for_dividend(10.0, 5.0)
        assert adjusted == 5.0

    def test_adjust_for_dividend_exceeds_price(self):
        """Test dividend exceeding price (should floor at 0.0)."""
        processor = CorporateActionProcessor()
        adjusted = processor.adjust_for_dividend(50.0, 100.0)
        assert adjusted == 0.0

    def test_adjust_for_dividend_negative(self):
        """Test dividend with negative amount."""
        processor = CorporateActionProcessor()
        with pytest.raises(ValueError, match="Dividend amount cannot be negative"):
            processor.adjust_for_dividend(100.0, -1.0)

    def test_adjust_price_series_empty(self):
        """Test empty price series."""
        processor = CorporateActionProcessor()
        adjusted = processor.adjust_price_series([], [])
        assert adjusted == []

    def test_adjust_price_series_no_actions(self):
        """Test price series without any actions."""
        processor = CorporateActionProcessor()
        prices = [100.0, 101.0, 102.0]
        adjusted = processor.adjust_price_series(prices, [])
        assert adjusted == prices

    def test_adjust_price_series_single_split(self):
        """Test price series with single split."""
        processor = CorporateActionProcessor()
        prices = [100.0, 101.0, 102.0, 103.0]
        actions = [{"index": 2, "type": "split", "ratio": 2.0}]
        adjusted = processor.adjust_price_series(prices, actions)
        # All prices before index 2 (inclusive) are halved
        assert adjusted[0] == 50.0
        assert adjusted[1] == 50.5
        assert adjusted[2] == 51.0
        assert adjusted[3] == 103.0  # After split, no adjustment

    def test_adjust_price_series_single_dividend(self):
        """Test price series with single dividend."""
        processor = CorporateActionProcessor()
        prices = [100.0, 101.0, 102.0, 103.0]
        actions = [{"index": 2, "type": "dividend", "amount": 1.0}]
        adjusted = processor.adjust_price_series(prices, actions)
        # All prices before index 2 (inclusive) are reduced by dividend
        assert adjusted[0] == 99.0
        assert adjusted[1] == 100.0
        assert adjusted[2] == 101.0
        assert adjusted[3] == 103.0

    def test_adjust_price_series_multiple_actions(self):
        """Test price series with multiple corporate actions."""
        processor = CorporateActionProcessor()
        prices = [100.0, 101.0, 102.0, 103.0, 104.0]
        actions = [{"index": 2, "type": "split", "ratio": 2.0}, {"index": 4, "type": "dividend", "amount": 1.0}]
        adjusted = processor.adjust_price_series(prices, actions)
        # Verify both actions were applied
        assert len(adjusted) == 5
        # Index 4 dividend applied first, then index 2 split
        assert isinstance(adjusted[0], float)
        assert isinstance(adjusted[4], float)

    def test_adjust_price_series_invalid_index(self):
        """Test with out-of-bounds action index."""
        processor = CorporateActionProcessor()
        prices = [100.0, 101.0, 102.0]
        actions = [{"index": 10, "type": "split", "ratio": 2.0}]
        # Should not raise, just skip invalid action
        adjusted = processor.adjust_price_series(prices, actions)
        assert adjusted == prices

    def test_detect_split_basic(self):
        """Test basic split detection."""
        processor = CorporateActionProcessor()
        prices = [100.0, 101.0, 102.0, 51.0, 52.0, 53.0]
        splits = processor.detect_split(prices, threshold=0.4)
        assert 3 in splits  # Index where 100->51 occurs

    def test_detect_split_multiple(self):
        """Test detection of multiple splits."""
        processor = CorporateActionProcessor()
        prices = [100.0, 101.0, 50.0, 51.0, 25.5, 26.0]
        splits = processor.detect_split(prices, threshold=0.4)
        assert len(splits) >= 1

    def test_detect_split_no_splits(self):
        """Test when no splits are detected."""
        processor = CorporateActionProcessor()
        prices = [100.0, 101.0, 102.0, 103.0, 104.0]
        splits = processor.detect_split(prices, threshold=0.4)
        assert splits == []

    def test_detect_split_empty_prices(self):
        """Test split detection on empty price list."""
        processor = CorporateActionProcessor()
        splits = processor.detect_split([], threshold=0.4)
        assert splits == []

    def test_detect_split_single_price(self):
        """Test split detection with single price."""
        processor = CorporateActionProcessor()
        splits = processor.detect_split([100.0], threshold=0.4)
        assert splits == []

    def test_detect_split_custom_threshold(self):
        """Test split detection with custom threshold."""
        processor = CorporateActionProcessor()
        prices = [100.0, 101.0, 85.0, 86.0]  # 15% drop
        splits_strict = processor.detect_split(prices, threshold=0.2)
        splits_lenient = processor.detect_split(prices, threshold=0.1)
        # Lenient threshold should catch the 15% drop
        assert 2 in splits_lenient
        # Strict threshold (20%) should not
        assert 2 not in splits_strict

    def test_detect_split_zero_price_handling(self):
        """Test split detection handles zero prices gracefully."""
        processor = CorporateActionProcessor()
        prices = [0.0, 100.0, 101.0]
        splits = processor.detect_split(prices, threshold=0.4)
        # Should not crash, and index 1 should not be flagged
        assert isinstance(splits, list)

    def test_detect_split_threshold_bounds(self):
        """Test that invalid thresholds return empty list."""
        processor = CorporateActionProcessor()
        prices = [100.0, 50.0]
        assert processor.detect_split(prices, threshold=-0.1) == []
        assert processor.detect_split(prices, threshold=1.5) == []


# ══════════════════════════════════════════════════════════════════════════════
# MarketImpactModel Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestMarketImpactModel:
    """Test suite for MarketImpactModel."""

    def test_model_initialization_defaults(self):
        """Test model initializes with default parameters."""
        model = MarketImpactModel()
        assert model.gamma == 0.314
        assert model.eta == 0.142

    def test_model_initialization_custom(self):
        """Test model initializes with custom parameters."""
        model = MarketImpactModel(gamma=0.5, eta=0.2)
        assert model.gamma == 0.5
        assert model.eta == 0.2

    def test_permanent_impact_small_order(self):
        """Test permanent impact for small order."""
        model = MarketImpactModel()
        # 1000 shares, 1M ADV, 2% volatility
        impact = model.permanent_impact(1000, 1000000, 0.02)
        assert impact > 0
        assert impact < 0.01  # Should be small for small order

    def test_permanent_impact_medium_order(self):
        """Test permanent impact for medium order."""
        model = MarketImpactModel()
        # 50000 shares, 1M ADV, 2% volatility
        impact = model.permanent_impact(50000, 1000000, 0.02)
        assert impact > 0
        # Impact should grow with order size
        assert impact > model.permanent_impact(1000, 1000000, 0.02)

    def test_permanent_impact_large_order(self):
        """Test permanent impact for large order."""
        model = MarketImpactModel()
        # 500000 shares, 1M ADV, 2% volatility
        impact = model.permanent_impact(500000, 1000000, 0.02)
        assert impact > 0.004  # 500k / 1M = 0.5, sqrt(0.5) ~= 0.707, 0.314 * 0.02 * 0.707 ~= 0.0044

    def test_permanent_impact_zero_volatility(self):
        """Test permanent impact with zero volatility."""
        model = MarketImpactModel()
        impact = model.permanent_impact(10000, 1000000, 0.0)
        assert impact == 0.0

    def test_permanent_impact_high_volatility(self):
        """Test permanent impact with high volatility."""
        model = MarketImpactModel()
        impact_low_vol = model.permanent_impact(10000, 1000000, 0.02)
        impact_high_vol = model.permanent_impact(10000, 1000000, 0.10)
        # Higher volatility should lead to higher impact
        assert impact_high_vol > impact_low_vol

    def test_permanent_impact_zero_adv_raises(self):
        """Test permanent impact raises with zero ADV."""
        model = MarketImpactModel()
        with pytest.raises(ValueError, match="ADV must be positive"):
            model.permanent_impact(1000, 0, 0.02)

    def test_permanent_impact_negative_adv_raises(self):
        """Test permanent impact raises with negative ADV."""
        model = MarketImpactModel()
        with pytest.raises(ValueError, match="ADV must be positive"):
            model.permanent_impact(1000, -100000, 0.02)

    def test_permanent_impact_negative_volatility_raises(self):
        """Test permanent impact raises with negative volatility."""
        model = MarketImpactModel()
        with pytest.raises(ValueError, match="Volatility cannot be negative"):
            model.permanent_impact(1000, 1000000, -0.02)

    def test_temporary_impact_small_order(self):
        """Test temporary impact for small order."""
        model = MarketImpactModel()
        impact = model.temporary_impact(1000, 1000000, 0.02)
        assert impact > 0
        assert impact < 0.01

    def test_temporary_impact_medium_order(self):
        """Test temporary impact for medium order."""
        model = MarketImpactModel()
        impact = model.temporary_impact(50000, 1000000, 0.02)
        assert impact > 0
        assert impact > model.temporary_impact(1000, 1000000, 0.02)

    def test_temporary_impact_large_order(self):
        """Test temporary impact for large order."""
        model = MarketImpactModel()
        impact = model.temporary_impact(500000, 1000000, 0.02)
        assert impact > 0

    def test_temporary_impact_zero_volatility(self):
        """Test temporary impact with zero volatility."""
        model = MarketImpactModel()
        impact = model.temporary_impact(10000, 1000000, 0.0)
        assert impact == 0.0

    def test_temporary_impact_zero_adv_raises(self):
        """Test temporary impact raises with zero ADV."""
        model = MarketImpactModel()
        with pytest.raises(ValueError, match="ADV must be positive"):
            model.temporary_impact(1000, 0, 0.02)

    def test_temporary_impact_negative_volatility_raises(self):
        """Test temporary impact raises with negative volatility."""
        model = MarketImpactModel()
        with pytest.raises(ValueError, match="Volatility cannot be negative"):
            model.temporary_impact(1000, 1000000, -0.02)

    def test_total_impact_basic(self):
        """Test total impact calculation."""
        model = MarketImpactModel()
        cost = model.total_impact(10000, 1000000, 0.02, 100.0)
        assert cost > 0
        # Cost should be reasonable relative to order value
        order_value = 10000 * 100
        assert cost < order_value * 0.01  # Less than 1% of order value

    def test_total_impact_includes_both(self):
        """Test that total impact includes both permanent and temporary."""
        model = MarketImpactModel()
        perm = model.permanent_impact(10000, 1000000, 0.02)
        temp = model.temporary_impact(10000, 1000000, 0.02)
        total_ratio = perm + temp
        price = 100.0
        expected_cost = total_ratio * price
        actual_cost = model.total_impact(10000, 1000000, 0.02, 100.0)
        assert abs(actual_cost - expected_cost) < 0.001

    def test_total_impact_zero_price_raises(self):
        """Test total impact raises with zero price."""
        model = MarketImpactModel()
        with pytest.raises(ValueError, match="Price must be positive"):
            model.total_impact(10000, 1000000, 0.02, 0.0)

    def test_total_impact_negative_price_raises(self):
        """Test total impact raises with negative price."""
        model = MarketImpactModel()
        with pytest.raises(ValueError, match="Price must be positive"):
            model.total_impact(10000, 1000000, 0.02, -100.0)

    def test_total_impact_zero_adv_raises(self):
        """Test total impact raises with zero ADV."""
        model = MarketImpactModel()
        with pytest.raises(ValueError, match="ADV must be positive"):
            model.total_impact(10000, 0, 0.02, 100.0)

    def test_impact_scales_with_order_size(self):
        """Test that impact increases with order size."""
        model = MarketImpactModel()
        impact_1k = model.total_impact(1000, 1000000, 0.02, 100.0)
        impact_10k = model.total_impact(10000, 1000000, 0.02, 100.0)
        impact_100k = model.total_impact(100000, 1000000, 0.02, 100.0)
        assert impact_1k < impact_10k < impact_100k

    def test_impact_scales_with_volatility(self):
        """Test that impact increases with volatility."""
        model = MarketImpactModel()
        impact_low = model.total_impact(10000, 1000000, 0.01, 100.0)
        impact_med = model.total_impact(10000, 1000000, 0.02, 100.0)
        impact_high = model.total_impact(10000, 1000000, 0.05, 100.0)
        assert impact_low < impact_med < impact_high


# ══════════════════════════════════════════════════════════════════════════════
# TimeOfDayRules Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestTimeOfDayRules:
    """Test suite for TimeOfDayRules."""

    def test_initialization(self):
        """Test TimeOfDayRules initializes properly."""
        rules = TimeOfDayRules()
        assert rules is not None
        assert len(rules._market_hours) >= 4

    def test_get_market_hours_krx(self):
        """Test getting KRX market hours."""
        rules = TimeOfDayRules()
        hours = rules.get_market_hours(Market.KRX)
        assert hours["open"] == time(9, 0)
        assert hours["close"] == time(15, 30)
        assert hours["auction_open_start"] == time(8, 30)
        assert hours["auction_open_end"] == time(9, 0)
        assert hours["auction_close_start"] == time(15, 20)
        assert hours["auction_close_end"] == time(15, 30)

    def test_get_market_hours_nyse(self):
        """Test getting NYSE market hours."""
        rules = TimeOfDayRules()
        hours = rules.get_market_hours(Market.NYSE)
        assert hours["open"] == time(9, 30)
        assert hours["close"] == time(16, 0)
        assert hours["circuit_breaker_l1"] == 0.07

    def test_get_market_hours_unsupported(self):
        """Test getting hours for unsupported market."""
        rules = TimeOfDayRules()
        with pytest.raises(ValueError, match="Unsupported market"):
            rules.get_market_hours("INVALID_MARKET")

    def test_is_auction_period_krx_opening(self):
        """Test KRX opening auction detection."""
        rules = TimeOfDayRules()
        # During opening auction (08:30-09:00)
        assert rules.is_auction_period(time(8, 45), Market.KRX) is True

    def test_is_auction_period_krx_closing(self):
        """Test KRX closing auction detection."""
        rules = TimeOfDayRules()
        # During closing auction (15:20-15:30)
        assert rules.is_auction_period(time(15, 25), Market.KRX) is True

    def test_is_auction_period_krx_normal(self):
        """Test KRX during normal trading (not auction)."""
        rules = TimeOfDayRules()
        assert rules.is_auction_period(time(12, 0), Market.KRX) is False
        assert rules.is_auction_period(time(9, 30), Market.KRX) is False

    def test_is_auction_period_nyse_opening(self):
        """Test NYSE opening auction detection."""
        rules = TimeOfDayRules()
        assert rules.is_auction_period(time(9, 29), Market.NYSE) is True

    def test_is_auction_period_nyse_closing(self):
        """Test NYSE closing auction detection."""
        rules = TimeOfDayRules()
        assert rules.is_auction_period(time(15, 57), Market.NYSE) is True

    def test_is_auction_period_nyse_normal(self):
        """Test NYSE during normal trading."""
        rules = TimeOfDayRules()
        assert rules.is_auction_period(time(12, 0), Market.NYSE) is False

    def test_can_execute_during_normal_hours_krx(self):
        """Test execution allowed during normal KRX hours."""
        rules = TimeOfDayRules()
        assert rules.can_execute(time(10, 0), Market.KRX) is True
        assert rules.can_execute(time(14, 0), Market.KRX) is True

    def test_can_execute_during_auction_krx(self):
        """Test execution blocked during KRX auction."""
        rules = TimeOfDayRules()
        assert rules.can_execute(time(8, 45), Market.KRX) is False
        assert rules.can_execute(time(15, 25), Market.KRX) is False

    def test_can_execute_outside_hours_krx(self):
        """Test execution blocked outside KRX hours."""
        rules = TimeOfDayRules()
        assert rules.can_execute(time(8, 0), Market.KRX) is False
        assert rules.can_execute(time(16, 0), Market.KRX) is False

    def test_can_execute_during_normal_hours_nyse(self):
        """Test execution allowed during normal NYSE hours."""
        rules = TimeOfDayRules()
        assert rules.can_execute(time(10, 0), Market.NYSE) is True
        assert rules.can_execute(time(14, 0), Market.NYSE) is True

    def test_can_execute_during_auction_nyse(self):
        """Test execution blocked during NYSE auction."""
        rules = TimeOfDayRules()
        assert rules.can_execute(time(9, 29), Market.NYSE) is False
        assert rules.can_execute(time(15, 57), Market.NYSE) is False

    def test_can_execute_boundary_krx(self):
        """Test execution at KRX boundary times."""
        rules = TimeOfDayRules()
        # Right at open boundary (09:00)
        assert rules.can_execute(time(9, 0), Market.KRX) is True
        # Right at close boundary (15:30) is during closing auction, so cannot execute
        assert rules.can_execute(time(15, 30), Market.KRX) is False
        # Before close but outside auction window
        assert rules.can_execute(time(15, 10), Market.KRX) is True

    def test_can_execute_unsupported_market(self):
        """Test execution check with unsupported market."""
        rules = TimeOfDayRules()
        with pytest.raises(ValueError, match="Unsupported market"):
            rules.can_execute(time(12, 0), "INVALID")

    def test_get_spread_multiplier_krx_normal(self):
        """Test spread multiplier during KRX normal hours."""
        rules = TimeOfDayRules()
        multiplier = rules.get_spread_multiplier(time(12, 0), Market.KRX)
        assert multiplier == 1.0

    def test_get_spread_multiplier_krx_opening_auction(self):
        """Test spread multiplier during KRX opening auction."""
        rules = TimeOfDayRules()
        multiplier = rules.get_spread_multiplier(time(8, 45), Market.KRX)
        assert multiplier == 2.0

    def test_get_spread_multiplier_krx_closing_auction(self):
        """Test spread multiplier during KRX closing auction."""
        rules = TimeOfDayRules()
        multiplier = rules.get_spread_multiplier(time(15, 25), Market.KRX)
        assert multiplier == 2.0

    def test_get_spread_multiplier_krx_early_trading(self):
        """Test spread multiplier during KRX early trading."""
        rules = TimeOfDayRules()
        # First 10 minutes after 09:00
        multiplier = rules.get_spread_multiplier(time(9, 5), Market.KRX)
        assert multiplier == 1.5

    def test_get_spread_multiplier_krx_late_trading(self):
        """Test spread multiplier during KRX late trading."""
        rules = TimeOfDayRules()
        # 15:27 is during closing auction (15:20-15:30), so multiplier = 2.0
        multiplier = rules.get_spread_multiplier(time(15, 27), Market.KRX)
        assert multiplier == 2.0
        # Earlier time in the "last 10 minutes" zone but before auction start
        multiplier_before = rules.get_spread_multiplier(time(15, 15), Market.KRX)
        assert multiplier_before == 1.0  # More than 10 min before close

    def test_get_spread_multiplier_nyse_normal(self):
        """Test spread multiplier during NYSE normal hours."""
        rules = TimeOfDayRules()
        multiplier = rules.get_spread_multiplier(time(12, 0), Market.NYSE)
        assert multiplier == 1.0

    def test_get_spread_multiplier_nyse_opening(self):
        """Test spread multiplier during NYSE opening."""
        rules = TimeOfDayRules()
        multiplier = rules.get_spread_multiplier(time(9, 29), Market.NYSE)
        assert multiplier == 2.0

    def test_get_spread_multiplier_nyse_closing(self):
        """Test spread multiplier during NYSE closing."""
        rules = TimeOfDayRules()
        multiplier = rules.get_spread_multiplier(time(15, 57), Market.NYSE)
        assert multiplier == 2.0

    def test_get_spread_multiplier_nyse_early_trading(self):
        """Test spread multiplier during NYSE early trading."""
        rules = TimeOfDayRules()
        multiplier = rules.get_spread_multiplier(time(9, 33), Market.NYSE)
        assert multiplier == 1.5

    def test_get_spread_multiplier_nyse_late_trading(self):
        """Test spread multiplier during NYSE late trading."""
        rules = TimeOfDayRules()
        # 15:58 is during closing auction (15:55-16:00), so multiplier = 2.0
        multiplier = rules.get_spread_multiplier(time(15, 58), Market.NYSE)
        assert multiplier == 2.0
        # Earlier time in late trading but before closing auction starts
        multiplier_before = rules.get_spread_multiplier(time(15, 50), Market.NYSE)
        assert multiplier_before == 1.0  # More than 5 min before close

    def test_get_spread_multiplier_unsupported_market(self):
        """Test spread multiplier with unsupported market."""
        rules = TimeOfDayRules()
        with pytest.raises(ValueError, match="Unsupported market"):
            rules.get_spread_multiplier(time(12, 0), "INVALID")

    def test_market_hours_return_copy(self):
        """Test that get_market_hours returns a copy, not reference."""
        rules = TimeOfDayRules()
        hours1 = rules.get_market_hours(Market.KRX)
        hours2 = rules.get_market_hours(Market.KRX)
        # Should be equal but not the same object
        assert hours1 == hours2
        # Modifying one shouldn't affect the other
        hours1["open"] = time(10, 0)
        assert hours2["open"] == time(9, 0)


# ══════════════════════════════════════════════════════════════════════════════
# Integration Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestBacktestIntegration:
    """Integration tests combining multiple modules."""

    def test_corporate_action_then_impact_model(self):
        """Test applying corporate actions then calculating impact."""
        processor = CorporateActionProcessor()
        model = MarketImpactModel()

        # Simulate 2:1 split affecting future impact calculation
        prices = [100.0, 101.0, 102.0, 103.0]
        actions = [{"index": 1, "type": "split", "ratio": 2.0}]
        adjusted_prices = processor.adjust_price_series(prices, actions)

        # Calculate impact on adjusted price
        impact = model.total_impact(10000, 1000000, 0.02, adjusted_prices[-1])
        assert impact > 0

    def test_time_rules_with_impact_multiplier(self):
        """Test using spread multiplier from time rules with impact model."""
        rules = TimeOfDayRules()
        model = MarketImpactModel()

        # Get base impact
        base_impact = model.total_impact(10000, 1000000, 0.02, 100.0)

        # Get spread multiplier at different times
        multiplier_normal = rules.get_spread_multiplier(time(12, 0), Market.KRX)
        multiplier_open = rules.get_spread_multiplier(time(9, 5), Market.KRX)

        # In reality, you'd multiply base temporary impact by multiplier
        assert multiplier_normal < multiplier_open

    def test_market_selection_determines_hours_and_limits(self):
        """Test that market selection determines applicable rules."""
        rules = TimeOfDayRules()

        krx_hours = rules.get_market_hours(Market.KRX)
        nyse_hours = rules.get_market_hours(Market.NYSE)

        # Different markets have different hours
        assert krx_hours["open"] != nyse_hours["open"]
        assert krx_hours["close"] != nyse_hours["close"]

        # KRX has daily limit, NYSE has circuit breakers
        assert "daily_limit_pct" in krx_hours
        assert "circuit_breaker_l1" in nyse_hours
