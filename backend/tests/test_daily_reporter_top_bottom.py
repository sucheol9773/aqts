"""
일간 Top/Bottom 3 종목 테스트 (F-09-01)

DailyReporter의 Top/Bottom 3 종목 기능 테스트
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from core.daily_reporter import DailyReport, DailyReporter, PositionSnapshot


def _make_positions(n: int = 6) -> list[PositionSnapshot]:
    """테스트용 포지션 생성"""
    data = [
        ("005930", "삼성전자", 5.2),
        ("000660", "SK하이닉스", -3.1),
        ("035720", "카카오", 8.5),
        ("AAPL", "Apple", -1.5),
        ("GOOGL", "Alphabet", 12.0),
        ("TSLA", "Tesla", -7.8),
    ]
    positions = []
    for i, (ticker, name, pnl_pct) in enumerate(data[:n]):
        avg = 50000
        qty = 10
        curr = avg * (1 + pnl_pct / 100)
        positions.append(PositionSnapshot(
            ticker=ticker,
            name=name,
            quantity=qty,
            avg_price=avg,
            current_price=curr,
            market_value=curr * qty,
            pnl=avg * qty * pnl_pct / 100,
            pnl_percent=pnl_pct,
            weight=100 / n,
        ))
    return positions


class TestTopBottom3:
    """Top/Bottom 3 종목 테스트"""

    @pytest.mark.asyncio
    @patch("core.daily_reporter.get_settings")
    async def test_top3_sorted_descending(self, mock_settings):
        """Top 3가 수익률 내림차순"""
        mock_settings.return_value = MagicMock(
            kis=MagicMock(trading_mode=MagicMock(value="DEMO")),
            risk=MagicMock(initial_capital_krw=50_000_000),
        )

        reporter = DailyReporter()
        positions = _make_positions()
        report = await reporter.generate_report(
            report_date=date(2026, 1, 5),
            portfolio_value_start=50_000_000,
            portfolio_value_end=50_100_000,
            positions=positions,
        )

        assert len(report.top3_positions) == 3
        # Top 3: GOOGL(12.0), 카카오(8.5), 삼성전자(5.2)
        pcts = [p.pnl_percent for p in report.top3_positions]
        assert pcts == sorted(pcts, reverse=True)
        assert report.top3_positions[0].ticker == "GOOGL"

    @pytest.mark.asyncio
    @patch("core.daily_reporter.get_settings")
    async def test_bottom3_sorted_ascending(self, mock_settings):
        """Bottom 3가 수익률 오름차순"""
        mock_settings.return_value = MagicMock(
            kis=MagicMock(trading_mode=MagicMock(value="DEMO")),
            risk=MagicMock(initial_capital_krw=50_000_000),
        )

        reporter = DailyReporter()
        positions = _make_positions()
        report = await reporter.generate_report(
            report_date=date(2026, 1, 5),
            portfolio_value_start=50_000_000,
            portfolio_value_end=50_100_000,
            positions=positions,
        )

        assert len(report.bottom3_positions) == 3
        # Bottom 3: Tesla(-7.8), SK하이닉스(-3.1), Apple(-1.5)
        pcts = [p.pnl_percent for p in report.bottom3_positions]
        assert pcts == sorted(pcts)
        assert report.bottom3_positions[0].ticker == "TSLA"

    @pytest.mark.asyncio
    @patch("core.daily_reporter.get_settings")
    async def test_fewer_than_3_positions(self, mock_settings):
        """포지션이 3개 미만이면 전체 반환"""
        mock_settings.return_value = MagicMock(
            kis=MagicMock(trading_mode=MagicMock(value="DEMO")),
            risk=MagicMock(initial_capital_krw=50_000_000),
        )

        reporter = DailyReporter()
        positions = _make_positions(2)
        report = await reporter.generate_report(
            report_date=date(2026, 1, 5),
            portfolio_value_start=50_000_000,
            portfolio_value_end=50_100_000,
            positions=positions,
        )

        assert len(report.top3_positions) == 2
        assert len(report.bottom3_positions) == 2

    @pytest.mark.asyncio
    @patch("core.daily_reporter.get_settings")
    async def test_empty_positions(self, mock_settings):
        """포지션 없으면 빈 리스트"""
        mock_settings.return_value = MagicMock(
            kis=MagicMock(trading_mode=MagicMock(value="DEMO")),
            risk=MagicMock(initial_capital_krw=50_000_000),
        )

        reporter = DailyReporter()
        report = await reporter.generate_report(
            report_date=date(2026, 1, 5),
            portfolio_value_start=50_000_000,
            portfolio_value_end=50_000_000,
        )

        assert report.top3_positions == []
        assert report.bottom3_positions == []

    def test_telegram_contains_top_bottom(self):
        """Telegram 메시지에 Top/Bottom 포함"""
        positions = _make_positions()
        top3 = sorted(positions, key=lambda p: p.pnl_percent, reverse=True)[:3]
        bottom3 = sorted(positions, key=lambda p: p.pnl_percent)[:3]

        report = DailyReport(
            report_date=date(2026, 1, 5),
            positions=positions,
            top3_positions=top3,
            bottom3_positions=bottom3,
        )

        # DailyReporter 없이 직접 포맷 테스트
        with patch("core.daily_reporter.get_settings") as mock:
            mock.return_value = MagicMock(
                kis=MagicMock(trading_mode=MagicMock(value="DEMO")),
            )
            reporter = DailyReporter()
            msg = reporter._format_telegram_message(report)

        assert "Top 3" in msg
        assert "Bottom 3" in msg
        assert "Alphabet" in msg  # GOOGL = top
        assert "Tesla" in msg     # TSLA = bottom
