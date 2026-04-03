"""
AQTS Phase 7 - Daily Reporter Tests

Comprehensive pytest tests for daily_reporter.py module covering:
- TradeRecord, PositionSnapshot, DailyReport dataclasses
- DailyReporter.generate_report with PnL, returns, trade stats
- Telegram message formatting and sending
- Report history accumulation
- KIS data collection
- Edge cases
"""

from datetime import datetime, date, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Optional

import pytest

from config.settings import TradingMode
from core.daily_reporter import (
    TradeRecord,
    PositionSnapshot,
    DailyReport,
    DailyReporter,
    KST,
)


# ══════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════

@pytest.fixture
def mock_settings():
    """Mock settings with required configuration"""
    settings = MagicMock()
    settings.kis.trading_mode = TradingMode.DEMO
    settings.risk.initial_capital_krw = 50_000_000
    return settings


@pytest.fixture
def sample_trade_buy():
    """Sample BUY trade record"""
    return TradeRecord(
        ticker="005930",
        name="삼성전자",
        side="BUY",
        quantity=100,
        price=70000.0,
        amount=7_000_000.0,
        pnl=None,
        executed_at=datetime.now(KST),
    )


@pytest.fixture
def sample_trade_sell():
    """Sample SELL trade record"""
    return TradeRecord(
        ticker="005930",
        name="삼성전자",
        side="SELL",
        quantity=50,
        price=71400.0,
        amount=3_570_000.0,
        pnl=70_000.0,
        executed_at=datetime.now(KST),
    )


@pytest.fixture
def sample_trades(sample_trade_buy, sample_trade_sell):
    """List of sample trades"""
    return [sample_trade_buy, sample_trade_sell]


@pytest.fixture
def sample_position():
    """Sample position snapshot"""
    return PositionSnapshot(
        ticker="005930",
        name="삼성전자",
        quantity=100,
        avg_price=70000.0,
        current_price=71400.0,
        market_value=7_140_000.0,
        pnl=140_000.0,
        pnl_percent=2.0,
        weight=12.5,
    )


@pytest.fixture
def sample_positions(sample_position):
    """List of sample positions"""
    return [sample_position]


@pytest.fixture
def sample_daily_report():
    """Sample daily report"""
    return DailyReport(
        report_date=date(2026, 4, 3),
        trading_mode="DEMO",
        portfolio_value_start=50_000_000.0,
        portfolio_value_end=50_500_000.0,
        daily_pnl=500_000.0,
        daily_return_pct=1.0,
        cumulative_pnl=500_000.0,
        cumulative_return_pct=1.0,
        total_trades=2,
        buy_trades=1,
        sell_trades=1,
        total_buy_amount=7_000_000.0,
        total_sell_amount=3_570_000.0,
        winning_trades=1,
        losing_trades=0,
        cash_balance=42_860_000.0,
        total_positions=1,
    )


# ══════════════════════════════════════════════════════════════
# TradeRecord Tests
# ══════════════════════════════════════════════════════════════

class TestTradeRecord:
    """Test TradeRecord dataclass"""

    def test_trade_record_creation(self, sample_trade_buy):
        """TradeRecord can be created with all fields"""
        assert sample_trade_buy.ticker == "005930"
        assert sample_trade_buy.name == "삼성전자"
        assert sample_trade_buy.side == "BUY"
        assert sample_trade_buy.quantity == 100
        assert sample_trade_buy.price == 70000.0
        assert sample_trade_buy.amount == 7_000_000.0

    def test_trade_record_pnl_none_by_default(self):
        """TradeRecord.pnl is None by default"""
        trade = TradeRecord(
            ticker="005930",
            name="삼성전자",
            side="BUY",
            quantity=100,
            price=70000.0,
            amount=7_000_000.0,
        )
        assert trade.pnl is None

    def test_trade_record_executed_at_none_by_default(self):
        """TradeRecord.executed_at is None by default"""
        trade = TradeRecord(
            ticker="005930",
            name="삼성전자",
            side="BUY",
            quantity=100,
            price=70000.0,
            amount=7_000_000.0,
        )
        assert trade.executed_at is None

    def test_trade_record_with_pnl(self, sample_trade_sell):
        """TradeRecord can be created with PnL"""
        assert sample_trade_sell.pnl == 70_000.0
        assert sample_trade_sell.side == "SELL"


# ══════════════════════════════════════════════════════════════
# PositionSnapshot Tests
# ══════════════════════════════════════════════════════════════

class TestPositionSnapshot:
    """Test PositionSnapshot dataclass"""

    def test_position_snapshot_creation(self, sample_position):
        """PositionSnapshot can be created with all fields"""
        assert sample_position.ticker == "005930"
        assert sample_position.name == "삼성전자"
        assert sample_position.quantity == 100
        assert sample_position.avg_price == 70000.0
        assert sample_position.current_price == 71400.0
        assert sample_position.market_value == 7_140_000.0
        assert sample_position.pnl == 140_000.0
        assert sample_position.pnl_percent == 2.0
        assert sample_position.weight == 12.5

    def test_position_snapshot_negative_pnl(self):
        """PositionSnapshot supports negative PnL"""
        pos = PositionSnapshot(
            ticker="005930",
            name="삼성전자",
            quantity=100,
            avg_price=70000.0,
            current_price=68000.0,
            market_value=6_800_000.0,
            pnl=-200_000.0,
            pnl_percent=-2.86,
            weight=9.6,
        )
        assert pos.pnl == -200_000.0
        assert pos.pnl_percent == -2.86

    def test_position_snapshot_zero_weight(self):
        """PositionSnapshot can have zero weight"""
        pos = PositionSnapshot(
            ticker="005930",
            name="삼성전자",
            quantity=100,
            avg_price=70000.0,
            current_price=71400.0,
            market_value=7_140_000.0,
            pnl=140_000.0,
            pnl_percent=2.0,
            weight=0.0,
        )
        assert pos.weight == 0.0


# ══════════════════════════════════════════════════════════════
# DailyReport Tests
# ══════════════════════════════════════════════════════════════

class TestDailyReport:
    """Test DailyReport dataclass"""

    def test_daily_report_creation(self, sample_daily_report):
        """DailyReport can be created with all fields"""
        assert sample_daily_report.report_date == date(2026, 4, 3)
        assert sample_daily_report.trading_mode == "DEMO"
        assert sample_daily_report.daily_pnl == 500_000.0
        assert sample_daily_report.daily_return_pct == 1.0

    def test_daily_report_default_values(self):
        """DailyReport has sensible defaults"""
        report = DailyReport(report_date=date(2026, 4, 3))
        assert report.trading_mode == "DEMO"
        assert report.portfolio_value_start == 0.0
        assert report.portfolio_value_end == 0.0
        assert report.daily_pnl == 0.0
        assert report.daily_return_pct == 0.0
        assert report.total_trades == 0
        assert report.buy_trades == 0
        assert report.sell_trades == 0
        assert report.circuit_breaker_triggered is False

    def test_daily_report_to_dict(self, sample_daily_report):
        """DailyReport.to_dict converts to dictionary"""
        report_dict = sample_daily_report.to_dict()
        assert isinstance(report_dict, dict)
        assert report_dict["report_date"] == "2026-04-03"
        assert report_dict["trading_mode"] == "DEMO"
        assert report_dict["daily_pnl"] == 500_000.0
        assert report_dict["daily_return_pct"] == 1.0
        assert report_dict["total_trades"] == 2

    def test_daily_report_to_dict_keys(self, sample_daily_report):
        """DailyReport.to_dict includes all expected keys"""
        report_dict = sample_daily_report.to_dict()
        expected_keys = [
            "report_date",
            "trading_mode",
            "portfolio_value_start",
            "portfolio_value_end",
            "daily_pnl",
            "daily_return_pct",
            "cumulative_pnl",
            "cumulative_return_pct",
            "total_trades",
            "winning_trades",
            "losing_trades",
            "total_positions",
            "cash_balance",
            "circuit_breaker_triggered",
            "generated_at",
        ]
        for key in expected_keys:
            assert key in report_dict

    def test_daily_report_generated_at_default(self):
        """DailyReport.generated_at has default KST timestamp"""
        report = DailyReport(report_date=date(2026, 4, 3))
        assert report.generated_at is not None
        assert report.generated_at.tzinfo == KST

    def test_daily_report_negative_pnl(self):
        """DailyReport supports negative PnL"""
        report = DailyReport(
            report_date=date(2026, 4, 3),
            portfolio_value_start=50_000_000.0,
            portfolio_value_end=49_500_000.0,
            daily_pnl=-500_000.0,
            daily_return_pct=-1.0,
        )
        assert report.daily_pnl == -500_000.0
        assert report.daily_return_pct == -1.0


# ══════════════════════════════════════════════════════════════
# DailyReporter Tests - Basic Report Generation
# ══════════════════════════════════════════════════════════════

class TestDailyReporterBasic:
    """Test basic DailyReporter functionality"""

    @pytest.mark.asyncio
    async def test_reporter_initialization(self, mock_settings):
        """DailyReporter can be initialized"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            assert reporter._report_history == []

    @pytest.mark.asyncio
    async def test_generate_basic_report(self, mock_settings):
        """DailyReporter generates basic report with default date"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            report = await reporter.generate_report(
                portfolio_value_start=50_000_000.0,
                portfolio_value_end=50_500_000.0,
            )
            # DailyReporter uses KST (UTC+9) internally
            kst = timezone(timedelta(hours=9))
            assert report.report_date == datetime.now(kst).date()
            assert report.trading_mode == "DEMO"
            assert report.daily_pnl == 500_000.0

    @pytest.mark.asyncio
    async def test_generate_report_with_custom_date(self, mock_settings):
        """DailyReporter generates report with custom date"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            custom_date = date(2026, 4, 1)
            report = await reporter.generate_report(
                report_date=custom_date,
                portfolio_value_start=50_000_000.0,
                portfolio_value_end=50_500_000.0,
            )
            assert report.report_date == custom_date

    @pytest.mark.asyncio
    async def test_generate_report_with_custom_initial_capital(self, mock_settings):
        """DailyReporter uses custom initial_capital"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            custom_capital = 100_000_000.0
            report = await reporter.generate_report(
                portfolio_value_start=100_000_000.0,
                portfolio_value_end=100_500_000.0,
                initial_capital=custom_capital,
            )
            assert report.cumulative_return_pct == 0.5


# ══════════════════════════════════════════════════════════════
# DailyReporter Tests - With Trades and Positions
# ══════════════════════════════════════════════════════════════

class TestDailyReporterTradesAndPositions:
    """Test DailyReporter with trades and positions"""

    @pytest.mark.asyncio
    async def test_generate_report_with_trades(self, mock_settings, sample_trades):
        """DailyReporter generates report with trades"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            report = await reporter.generate_report(
                portfolio_value_start=50_000_000.0,
                portfolio_value_end=50_500_000.0,
                trades=sample_trades,
            )
            assert report.total_trades == 2
            assert report.buy_trades == 1
            assert report.sell_trades == 1

    @pytest.mark.asyncio
    async def test_generate_report_with_positions(self, mock_settings, sample_positions):
        """DailyReporter generates report with positions"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            report = await reporter.generate_report(
                portfolio_value_start=50_000_000.0,
                portfolio_value_end=50_500_000.0,
                positions=sample_positions,
            )
            assert report.total_positions == 1
            assert report.positions == sample_positions

    @pytest.mark.asyncio
    async def test_generate_report_with_cash_balance(self, mock_settings):
        """DailyReporter records cash balance"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            cash = 42_860_000.0
            report = await reporter.generate_report(
                portfolio_value_start=50_000_000.0,
                portfolio_value_end=50_500_000.0,
                cash_balance=cash,
            )
            assert report.cash_balance == cash


# ══════════════════════════════════════════════════════════════
# DailyReporter Tests - PnL Calculations
# ══════════════════════════════════════════════════════════════

class TestDailyReporterPnL:
    """Test DailyReporter PnL calculations"""

    @pytest.mark.asyncio
    async def test_generate_report_profit(self, mock_settings):
        """DailyReporter calculates profit correctly"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            report = await reporter.generate_report(
                portfolio_value_start=50_000_000.0,
                portfolio_value_end=51_000_000.0,
                initial_capital=50_000_000.0,
            )
            assert report.daily_pnl == 1_000_000.0
            assert report.daily_return_pct == 2.0

    @pytest.mark.asyncio
    async def test_generate_report_loss(self, mock_settings):
        """DailyReporter calculates loss correctly"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            report = await reporter.generate_report(
                portfolio_value_start=50_000_000.0,
                portfolio_value_end=49_000_000.0,
                initial_capital=50_000_000.0,
            )
            assert report.daily_pnl == -1_000_000.0
            assert report.daily_return_pct == -2.0

    @pytest.mark.asyncio
    async def test_generate_report_zero_pnl(self, mock_settings):
        """DailyReporter handles zero PnL"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            report = await reporter.generate_report(
                portfolio_value_start=50_000_000.0,
                portfolio_value_end=50_000_000.0,
                initial_capital=50_000_000.0,
            )
            assert report.daily_pnl == 0.0
            assert report.daily_return_pct == 0.0

    @pytest.mark.asyncio
    async def test_generate_report_cumulative_pnl(self, mock_settings):
        """DailyReporter calculates cumulative PnL"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            report = await reporter.generate_report(
                portfolio_value_start=50_000_000.0,
                portfolio_value_end=50_500_000.0,
                initial_capital=50_000_000.0,
            )
            assert report.cumulative_pnl == 500_000.0
            assert report.cumulative_return_pct == 1.0

    @pytest.mark.asyncio
    async def test_generate_report_cumulative_return_different_capital(self, mock_settings):
        """DailyReporter calculates cumulative return with different initial capital"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            report = await reporter.generate_report(
                portfolio_value_start=100_000_000.0,
                portfolio_value_end=100_500_000.0,
                initial_capital=100_000_000.0,
            )
            assert report.cumulative_pnl == 500_000.0
            assert report.cumulative_return_pct == 0.5


# ══════════════════════════════════════════════════════════════
# DailyReporter Tests - Trade Statistics
# ══════════════════════════════════════════════════════════════

class TestDailyReporterTradeStats:
    """Test DailyReporter trade statistics"""

    @pytest.mark.asyncio
    async def test_generate_report_winning_losing_trades(self, mock_settings):
        """DailyReporter counts winning and losing trades"""
        trades = [
            TradeRecord("005930", "삼성전자", "BUY", 100, 70000, 7_000_000, pnl=100_000),
            TradeRecord("000660", "SK하이닉스", "SELL", 50, 120000, 6_000_000, pnl=-50_000),
            TradeRecord("005380", "현대차", "BUY", 10, 200000, 2_000_000, pnl=200_000),
        ]
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            report = await reporter.generate_report(
                portfolio_value_start=50_000_000.0,
                portfolio_value_end=50_500_000.0,
                trades=trades,
            )
            assert report.winning_trades == 2
            assert report.losing_trades == 1

    @pytest.mark.asyncio
    async def test_generate_report_buy_sell_amounts(self, mock_settings):
        """DailyReporter sums buy and sell amounts"""
        trades = [
            TradeRecord("005930", "삼성전자", "BUY", 100, 70000, 7_000_000),
            TradeRecord("000660", "SK하이닉스", "BUY", 50, 100000, 5_000_000),
            TradeRecord("005380", "현대차", "SELL", 10, 200000, 2_000_000),
        ]
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            report = await reporter.generate_report(
                portfolio_value_start=50_000_000.0,
                portfolio_value_end=50_500_000.0,
                trades=trades,
            )
            assert report.total_buy_amount == 12_000_000.0
            assert report.total_sell_amount == 2_000_000.0

    @pytest.mark.asyncio
    async def test_generate_report_no_winning_trades(self, mock_settings):
        """DailyReporter handles reports with no winning trades"""
        trades = [
            TradeRecord("005930", "삼성전자", "BUY", 100, 70000, 7_000_000, pnl=-100_000),
            TradeRecord("000660", "SK하이닉스", "BUY", 50, 100000, 5_000_000, pnl=-50_000),
        ]
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            report = await reporter.generate_report(
                portfolio_value_start=50_000_000.0,
                portfolio_value_end=50_500_000.0,
                trades=trades,
            )
            assert report.winning_trades == 0
            assert report.losing_trades == 2

    @pytest.mark.asyncio
    async def test_generate_report_ignores_trades_without_pnl(self, mock_settings):
        """DailyReporter ignores trades without PnL in win/loss count"""
        trades = [
            TradeRecord("005930", "삼성전자", "BUY", 100, 70000, 7_000_000, pnl=None),
            TradeRecord("000660", "SK하이닉스", "BUY", 50, 100000, 5_000_000, pnl=50_000),
        ]
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            report = await reporter.generate_report(
                portfolio_value_start=50_000_000.0,
                portfolio_value_end=50_500_000.0,
                trades=trades,
            )
            assert report.winning_trades == 1
            assert report.losing_trades == 0


# ══════════════════════════════════════════════════════════════
# DailyReporter Tests - Telegram Message Formatting
# ══════════════════════════════════════════════════════════════

class TestDailyReporterTelegramFormat:
    """Test Telegram message formatting"""

    def test_format_telegram_message_basic(self, mock_settings, sample_daily_report):
        """_format_telegram_message generates valid message"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            message = reporter._format_telegram_message(sample_daily_report)
            assert isinstance(message, str)
            assert "AQTS 일일 리포트" in message
            assert "2026-04-03" in message

    def test_format_telegram_message_demo_mode(self, mock_settings, sample_daily_report):
        """_format_telegram_message shows demo mode indicator"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            message = reporter._format_telegram_message(sample_daily_report)
            assert "🔵 모의투자" in message

    def test_format_telegram_message_live_mode(self, mock_settings, sample_daily_report):
        """_format_telegram_message shows live mode indicator"""
        sample_daily_report.trading_mode = "LIVE"
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            message = reporter._format_telegram_message(sample_daily_report)
            assert "🔴 실투자" in message

    def test_format_telegram_message_profit_emoji(self, mock_settings, sample_daily_report):
        """_format_telegram_message shows profit emoji for positive PnL"""
        sample_daily_report.daily_pnl = 500_000.0
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            message = reporter._format_telegram_message(sample_daily_report)
            assert "📈" in message

    def test_format_telegram_message_loss_emoji(self, mock_settings, sample_daily_report):
        """_format_telegram_message shows loss emoji for negative PnL"""
        sample_daily_report.daily_pnl = -500_000.0
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            message = reporter._format_telegram_message(sample_daily_report)
            assert "📉" in message

    def test_format_telegram_message_with_trades(self, mock_settings, sample_daily_report):
        """_format_telegram_message includes trade summary"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            message = reporter._format_telegram_message(sample_daily_report)
            assert "거래 요약" in message
            assert "총 2건" in message
            assert "매수 1" in message
            assert "매도 1" in message

    def test_format_telegram_message_win_rate(self, mock_settings, sample_daily_report):
        """_format_telegram_message calculates win rate"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            message = reporter._format_telegram_message(sample_daily_report)
            assert "승률:" in message
            assert "50%" in message

    def test_format_telegram_message_no_trades(self, mock_settings):
        """_format_telegram_message handles no trades"""
        report = DailyReport(report_date=date(2026, 4, 3))
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            message = reporter._format_telegram_message(report)
            assert "총 0건" in message

    def test_format_telegram_message_with_positions(self, mock_settings, sample_daily_report, sample_positions):
        """_format_telegram_message includes position summary"""
        sample_daily_report.positions = sample_positions
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            message = reporter._format_telegram_message(sample_daily_report)
            assert "보유 종목" in message
            assert "삼성전자" in message

    def test_format_telegram_message_position_top_5_limit(self, mock_settings, sample_daily_report):
        """_format_telegram_message limits positions to top 5"""
        # Create 7 positions
        positions = [
            PositionSnapshot(
                ticker=f"{i:06d}",
                name=f"회사{i}",
                quantity=100,
                avg_price=100000.0,
                current_price=100000.0 + (i * 1000),
                market_value=10_000_000.0 + (i * 1_000_000),
                pnl=0.0 + (i * 100_000),
                pnl_percent=1.0 + (i * 0.1),
                weight=1.0 + (i * 0.5),
            )
            for i in range(7)
        ]
        sample_daily_report.positions = positions
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            message = reporter._format_telegram_message(sample_daily_report)
            assert "... 외 2종목" in message

    def test_format_telegram_message_with_circuit_breaker(self, mock_settings, sample_daily_report):
        """_format_telegram_message includes circuit breaker warning"""
        sample_daily_report.circuit_breaker_triggered = True
        sample_daily_report.circuit_breaker_reason = "일일 손실 한도 초과"
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            message = reporter._format_telegram_message(sample_daily_report)
            assert "⚠️ 서킷브레이커 발동" in message
            assert "일일 손실 한도 초과" in message

    def test_format_telegram_message_with_max_drawdown(self, mock_settings, sample_daily_report):
        """_format_telegram_message includes max drawdown"""
        sample_daily_report.max_drawdown_today = 2.5
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            message = reporter._format_telegram_message(sample_daily_report)
            assert "금일 최대 낙폭" in message
            assert "2.50%" in message

    def test_format_telegram_message_trade_detail_buy(self, mock_settings):
        """_format_telegram_message shows BUY trades with green marker"""
        report = DailyReport(
            report_date=date(2026, 4, 3),
            trades=[
                TradeRecord("005930", "삼성전자", "BUY", 100, 70000, 7_000_000),
            ],
        )
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            message = reporter._format_telegram_message(report)
            assert "🟢" in message

    def test_format_telegram_message_trade_detail_sell(self, mock_settings):
        """_format_telegram_message shows SELL trades with red marker"""
        report = DailyReport(
            report_date=date(2026, 4, 3),
            trades=[
                TradeRecord("005930", "삼성전자", "SELL", 100, 70000, 7_000_000),
            ],
        )
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            message = reporter._format_telegram_message(report)
            assert "🔴" in message

    def test_format_telegram_message_trade_top_5_limit(self, mock_settings, sample_daily_report):
        """_format_telegram_message limits trades to top 5"""
        trades = [
            TradeRecord(f"{i:06d}", f"회사{i}", "BUY", 100, 70000 + i, 7_000_000)
            for i in range(7)
        ]
        sample_daily_report.trades = trades
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            message = reporter._format_telegram_message(sample_daily_report)
            assert "... 외 2건" in message

    def test_format_telegram_message_cash_balance(self, mock_settings, sample_daily_report):
        """_format_telegram_message includes cash balance"""
        sample_daily_report.cash_balance = 42_860_000.0
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            message = reporter._format_telegram_message(sample_daily_report)
            assert "현금 잔고" in message
            assert "42,860,000" in message


# ══════════════════════════════════════════════════════════════
# DailyReporter Tests - Telegram Sending
# ══════════════════════════════════════════════════════════════

class TestDailyReporterTelegramSend:
    """Test Telegram message sending"""

    @pytest.mark.asyncio
    async def test_send_telegram_report_success(self, mock_settings, sample_daily_report):
        """send_telegram_report returns True on success"""
        mock_notifier = AsyncMock()
        mock_notifier.send_message.return_value = True

        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            with patch(
                "core.notification.telegram_notifier.TelegramNotifier",
                return_value=mock_notifier
            ):
                reporter = DailyReporter()
                result = await reporter.send_telegram_report(sample_daily_report)
                assert result is True
                mock_notifier.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_telegram_report_failure(self, mock_settings, sample_daily_report):
        """send_telegram_report returns False on failure"""
        mock_notifier = AsyncMock()
        mock_notifier.send_message.return_value = False

        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            with patch(
                "core.notification.telegram_notifier.TelegramNotifier",
                return_value=mock_notifier
            ):
                reporter = DailyReporter()
                result = await reporter.send_telegram_report(sample_daily_report)
                assert result is False

    @pytest.mark.asyncio
    async def test_send_telegram_report_exception(self, mock_settings, sample_daily_report):
        """send_telegram_report handles exceptions"""
        mock_notifier = AsyncMock()
        mock_notifier.send_message.side_effect = Exception("Network error")

        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            with patch(
                "core.notification.telegram_notifier.TelegramNotifier",
                return_value=mock_notifier
            ):
                reporter = DailyReporter()
                result = await reporter.send_telegram_report(sample_daily_report)
                assert result is False

    @pytest.mark.asyncio
    async def test_send_telegram_report_calls_formatter(self, mock_settings, sample_daily_report):
        """send_telegram_report formats message before sending"""
        mock_notifier = AsyncMock()
        mock_notifier.send_message.return_value = True

        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            with patch(
                "core.notification.telegram_notifier.TelegramNotifier",
                return_value=mock_notifier
            ):
                reporter = DailyReporter()
                await reporter.send_telegram_report(sample_daily_report)
                # Verify send_message was called with a formatted string
                call_args = mock_notifier.send_message.call_args
                message = call_args[0][0] if call_args[0] else call_args.kwargs.get("message", "")
                assert "AQTS 일일 리포트" in message


# ══════════════════════════════════════════════════════════════
# DailyReporter Tests - Report History
# ══════════════════════════════════════════════════════════════

class TestDailyReporterHistory:
    """Test report history accumulation"""

    @pytest.mark.asyncio
    async def test_get_report_history_empty(self, mock_settings):
        """get_report_history returns empty list initially"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            history = reporter.get_report_history()
            assert history == []

    @pytest.mark.asyncio
    async def test_get_report_history_accumulates(self, mock_settings):
        """get_report_history accumulates reports"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()

            # Generate multiple reports
            for i in range(3):
                await reporter.generate_report(
                    report_date=date(2026, 4, 1 + i),
                    portfolio_value_start=50_000_000.0,
                    portfolio_value_end=50_500_000.0 + (i * 100_000),
                )

            history = reporter.get_report_history()
            assert len(history) == 3

    @pytest.mark.asyncio
    async def test_get_report_history_returns_copy(self, mock_settings):
        """get_report_history returns a copy, not reference"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()

            await reporter.generate_report(
                portfolio_value_start=50_000_000.0,
                portfolio_value_end=50_500_000.0,
            )

            history1 = reporter.get_report_history()
            history2 = reporter.get_report_history()

            assert history1 == history2
            assert history1 is not history2  # Different objects

    @pytest.mark.asyncio
    async def test_get_report_history_contains_dict(self, mock_settings):
        """get_report_history returns list of dicts"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()

            await reporter.generate_report(
                portfolio_value_start=50_000_000.0,
                portfolio_value_end=50_500_000.0,
            )

            history = reporter.get_report_history()
            assert len(history) == 1
            assert isinstance(history[0], dict)
            assert "report_date" in history[0]
            assert "daily_pnl" in history[0]


# ══════════════════════════════════════════════════════════════
# DailyReporter Tests - KIS Data Collection
# ══════════════════════════════════════════════════════════════

class TestDailyReporterKISCollection:
    """Test KIS data collection"""

    @pytest.mark.asyncio
    async def test_collect_from_kis_success(self, mock_settings):
        """collect_from_kis returns structured data"""
        mock_client = AsyncMock()
        mock_client.get_kr_balance.return_value = {
            "output1": [
                {
                    "pdno": "005930",
                    "prdt_name": "삼성전자",
                    "hldg_qty": "100",
                    "pchs_avg_pric": "70000.00",
                    "prpr": "71400",
                    "evlu_amt": "7140000",
                    "evlu_pfls_amt": "140000",
                }
            ],
            "output2": [
                {
                    "dnca_tot_amt": "42860000",
                    "tot_evlu_amt": "50000000",
                }
            ],
        }

        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            with patch(
                "core.data_collector.kis_client.KISClient",
                return_value=mock_client
            ):
                reporter = DailyReporter()
                result = await reporter.collect_from_kis()

                assert "positions" in result
                assert "cash_balance" in result
                assert "total_evaluation" in result

    @pytest.mark.asyncio
    async def test_collect_from_kis_position_snapshot(self, mock_settings):
        """collect_from_kis creates PositionSnapshot objects"""
        mock_client = AsyncMock()
        mock_client.get_kr_balance.return_value = {
            "output1": [
                {
                    "pdno": "005930",
                    "prdt_name": "삼성전자",
                    "hldg_qty": "100",
                    "pchs_avg_pric": "70000.00",
                    "prpr": "71400",
                    "evlu_amt": "7140000",
                    "evlu_pfls_amt": "140000",
                }
            ],
            "output2": [
                {
                    "dnca_tot_amt": "42860000",
                    "tot_evlu_amt": "50000000",
                }
            ],
        }

        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            with patch(
                "core.data_collector.kis_client.KISClient",
                return_value=mock_client
            ):
                reporter = DailyReporter()
                result = await reporter.collect_from_kis()

                positions = result["positions"]
                assert len(positions) == 1
                assert isinstance(positions[0], PositionSnapshot)
                assert positions[0].ticker == "005930"
                assert positions[0].quantity == 100

    @pytest.mark.asyncio
    async def test_collect_from_kis_calculates_weight(self, mock_settings):
        """collect_from_kis calculates position weight"""
        mock_client = AsyncMock()
        mock_client.get_kr_balance.return_value = {
            "output1": [
                {
                    "pdno": "005930",
                    "prdt_name": "삼성전자",
                    "hldg_qty": "100",
                    "pchs_avg_pric": "70000.00",
                    "prpr": "71400",
                    "evlu_amt": "7140000",
                    "evlu_pfls_amt": "140000",
                }
            ],
            "output2": [
                {
                    "dnca_tot_amt": "42860000",
                    "tot_evlu_amt": "50000000",
                }
            ],
        }

        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            with patch(
                "core.data_collector.kis_client.KISClient",
                return_value=mock_client
            ):
                reporter = DailyReporter()
                result = await reporter.collect_from_kis()

                positions = result["positions"]
                assert positions[0].weight == 14.3  # 7140000 / 50000000 * 100

    @pytest.mark.asyncio
    async def test_collect_from_kis_empty_response(self, mock_settings):
        """collect_from_kis handles empty KIS response"""
        mock_client = AsyncMock()
        mock_client.get_kr_balance.return_value = None

        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            with patch(
                "core.data_collector.kis_client.KISClient",
                return_value=mock_client
            ):
                reporter = DailyReporter()
                result = await reporter.collect_from_kis()

                assert result["positions"] == []
                assert result["cash_balance"] == 0
                assert result["total_evaluation"] == 0

    @pytest.mark.asyncio
    async def test_collect_from_kis_exception(self, mock_settings):
        """collect_from_kis handles exceptions gracefully"""
        mock_client = AsyncMock()
        mock_client.get_kr_balance.side_effect = Exception("API error")

        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            with patch(
                "core.data_collector.kis_client.KISClient",
                return_value=mock_client
            ):
                reporter = DailyReporter()
                result = await reporter.collect_from_kis()

                assert result["positions"] == []
                assert result["cash_balance"] == 0
                assert result["total_evaluation"] == 0


# ══════════════════════════════════════════════════════════════
# Edge Cases and Error Handling
# ══════════════════════════════════════════════════════════════

class TestDailyReporterEdgeCases:
    """Test edge cases and error conditions"""

    @pytest.mark.asyncio
    async def test_generate_report_zero_portfolio_value(self, mock_settings):
        """DailyReporter handles zero portfolio value"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            report = await reporter.generate_report(
                portfolio_value_start=0.0,
                portfolio_value_end=0.0,
                initial_capital=50_000_000.0,
            )
            # When portfolio_value_start is 0, daily_return_pct should be 0
            assert report.daily_return_pct == 0.0
            # Cumulative return should be -100% (went from 50M to 0)
            assert report.cumulative_return_pct == -100.0

    @pytest.mark.asyncio
    async def test_generate_report_zero_initial_capital(self, mock_settings):
        """DailyReporter handles zero initial capital"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            report = await reporter.generate_report(
                portfolio_value_start=50_000_000.0,
                portfolio_value_end=51_000_000.0,
                initial_capital=0.0,
            )
            assert report.cumulative_return_pct == 0.0

    @pytest.mark.asyncio
    async def test_generate_report_none_trades_list(self, mock_settings):
        """DailyReporter handles None trades list"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            report = await reporter.generate_report(
                portfolio_value_start=50_000_000.0,
                portfolio_value_end=50_500_000.0,
                trades=None,
            )
            assert report.total_trades == 0
            assert report.trades == []

    @pytest.mark.asyncio
    async def test_generate_report_none_positions_list(self, mock_settings):
        """DailyReporter handles None positions list"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            report = await reporter.generate_report(
                portfolio_value_start=50_000_000.0,
                portfolio_value_end=50_500_000.0,
                positions=None,
            )
            assert report.total_positions == 0
            assert report.positions == []

    @pytest.mark.asyncio
    async def test_generate_report_empty_trades_list(self, mock_settings):
        """DailyReporter handles empty trades list"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            report = await reporter.generate_report(
                portfolio_value_start=50_000_000.0,
                portfolio_value_end=50_500_000.0,
                trades=[],
            )
            assert report.total_trades == 0
            assert report.winning_trades == 0
            assert report.losing_trades == 0

    def test_format_telegram_message_no_positions(self, mock_settings):
        """_format_telegram_message handles no positions"""
        report = DailyReport(report_date=date(2026, 4, 3), positions=[])
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            message = reporter._format_telegram_message(report)
            assert "보유 종목" not in message

    def test_format_telegram_message_no_trades_no_win_rate(self, mock_settings):
        """_format_telegram_message skips win rate when no trades"""
        report = DailyReport(report_date=date(2026, 4, 3), total_trades=0)
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            message = reporter._format_telegram_message(report)
            assert "승률:" not in message

    @pytest.mark.asyncio
    async def test_generate_report_uses_settings_initial_capital(self, mock_settings):
        """DailyReporter uses settings initial_capital when not provided"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            report = await reporter.generate_report(
                portfolio_value_start=50_000_000.0,
                portfolio_value_end=50_500_000.0,
                # initial_capital not provided
            )
            # Should use mock_settings.risk.initial_capital_krw
            assert report.cumulative_return_pct == 1.0

    @pytest.mark.asyncio
    async def test_generate_report_with_circuit_breaker(self, mock_settings):
        """DailyReporter records circuit breaker event"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            report = await reporter.generate_report(
                portfolio_value_start=50_000_000.0,
                portfolio_value_end=49_000_000.0,
                circuit_breaker_triggered=True,
                circuit_breaker_reason="Max drawdown exceeded",
            )
            assert report.circuit_breaker_triggered is True
            assert report.circuit_breaker_reason == "Max drawdown exceeded"

    @pytest.mark.asyncio
    async def test_generate_report_with_max_drawdown(self, mock_settings):
        """DailyReporter records max drawdown"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            report = await reporter.generate_report(
                portfolio_value_start=50_000_000.0,
                portfolio_value_end=49_000_000.0,
                max_drawdown_today=2.5,
            )
            assert report.max_drawdown_today == 2.5

    @pytest.mark.asyncio
    async def test_generate_report_with_consecutive_losses(self, mock_settings):
        """DailyReporter records consecutive losses"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            report = await reporter.generate_report(
                portfolio_value_start=50_000_000.0,
                portfolio_value_end=49_000_000.0,
                consecutive_losses=3,
            )
            assert report.consecutive_losses == 3

    @pytest.mark.asyncio
    async def test_generate_report_pnl_rounding(self, mock_settings):
        """DailyReporter rounds PnL percentages to 2 decimals"""
        with patch("core.daily_reporter.get_settings", return_value=mock_settings):
            reporter = DailyReporter()
            report = await reporter.generate_report(
                portfolio_value_start=50_000_000.0,
                portfolio_value_end=50_333_333.33,
                initial_capital=50_000_000.0,
            )
            # 333333.33 / 50000000 * 100 = 0.666666...
            assert report.daily_return_pct == 0.67
            assert report.cumulative_return_pct == 0.67
