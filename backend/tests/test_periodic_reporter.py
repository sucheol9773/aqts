"""
주간/월간 리포트 테스트 (F-09)

PeriodicReporter의 종합 단위 테스트

테스트 범위:
- 주간 리포트 생성 및 필드 검증
- 월간 리포트 생성 및 벤치마크 대비
- Best/Worst 일 추출
- MDD 계산
- Sharpe/변동성 계산
- 발송 시점 판별 (주간/월간)
- Telegram 포맷
- 빈 데이터 처리
"""

from datetime import date, timedelta

import pytest

from core.periodic_reporter import (
    DailySummary,
    PeriodicReport,
    PeriodicReporter,
    ReportPeriod,
)


# ══════════════════════════════════════
# 테스트 픽스처
# ══════════════════════════════════════
def _make_daily_data(
    start: date,
    n_days: int = 5,
    base_value: float = 50_000_000.0,
    daily_pnl_pattern: list[float] | None = None,
) -> list[DailySummary]:
    """테스트용 일별 데이터 생성"""
    if daily_pnl_pattern is None:
        daily_pnl_pattern = [100_000, -50_000, 200_000, -30_000, 150_000]

    data = []
    current_value = base_value
    for i in range(min(n_days, len(daily_pnl_pattern))):
        d = start + timedelta(days=i)
        # 주말 건너뛰기
        while d.weekday() >= 5:
            d = d + timedelta(days=1)

        pnl = daily_pnl_pattern[i]
        current_value += pnl
        pct = (pnl / (current_value - pnl) * 100) if (current_value - pnl) > 0 else 0

        data.append(DailySummary(
            date=d,
            portfolio_value=current_value,
            daily_pnl=pnl,
            daily_return_pct=round(pct, 4),
            trades_count=3,
        ))
    return data


@pytest.fixture
def reporter():
    return PeriodicReporter()


@pytest.fixture
def sample_week_data():
    """2026-01-05 ~ 2026-01-09 (월~금) 샘플 데이터"""
    return _make_daily_data(date(2026, 1, 5))


# ══════════════════════════════════════
# 주간 리포트 테스트
# ══════════════════════════════════════
class TestWeeklyReport:
    """주간 리포트 생성 테스트"""

    def test_basic_weekly(self, reporter, sample_week_data):
        """기본 주간 리포트 생성"""
        report = reporter.generate_weekly(
            sample_week_data,
            week_start=date(2026, 1, 5),
            week_end=date(2026, 1, 9),
        )

        assert report.period == ReportPeriod.WEEKLY
        assert report.start_date == date(2026, 1, 5)
        assert report.end_date == date(2026, 1, 9)
        assert report.trading_days == 5

    def test_period_pnl(self, reporter, sample_week_data):
        """주간 손익 합산"""
        report = reporter.generate_weekly(
            sample_week_data,
            week_start=date(2026, 1, 5),
            week_end=date(2026, 1, 9),
        )

        # 100k - 50k + 200k - 30k + 150k = 370k
        assert report.period_pnl == 370_000

    def test_period_return(self, reporter, sample_week_data):
        """주간 수익률"""
        report = reporter.generate_weekly(
            sample_week_data,
            week_start=date(2026, 1, 5),
            week_end=date(2026, 1, 9),
        )
        # 50_370_000 / 50_000_000 - 1 = 0.74%
        assert report.period_return_pct > 0

    def test_best_worst_day(self, reporter, sample_week_data):
        """Best/Worst 거래일"""
        report = reporter.generate_weekly(
            sample_week_data,
            week_start=date(2026, 1, 5),
            week_end=date(2026, 1, 9),
        )

        assert report.best_day is not None
        assert report.worst_day is not None
        assert report.best_day.daily_return_pct >= report.worst_day.daily_return_pct

    def test_total_trades(self, reporter, sample_week_data):
        """총 거래 수 합산"""
        report = reporter.generate_weekly(
            sample_week_data,
            week_start=date(2026, 1, 5),
            week_end=date(2026, 1, 9),
        )
        assert report.total_trades == 15  # 5일 × 3건

    def test_empty_week(self, reporter):
        """빈 주간 데이터"""
        report = reporter.generate_weekly(
            [],
            week_start=date(2026, 1, 5),
            week_end=date(2026, 1, 9),
        )
        assert report.period_pnl == 0.0
        assert report.trading_days == 0


# ══════════════════════════════════════
# 월간 리포트 테스트
# ══════════════════════════════════════
class TestMonthlyReport:
    """월간 리포트 생성 테스트"""

    def test_basic_monthly(self, reporter):
        """기본 월간 리포트"""
        data = _make_daily_data(
            date(2026, 1, 2),
            n_days=20,
            daily_pnl_pattern=[50_000] * 20,
        )

        report = reporter.generate_monthly(
            data, year=2026, month=1,
            benchmark_return_pct=0.5,
        )

        assert report.period == ReportPeriod.MONTHLY
        assert report.start_date == date(2026, 1, 1)
        assert report.end_date == date(2026, 1, 31)

    def test_benchmark_excess(self, reporter):
        """벤치마크 대비 초과수익"""
        data = _make_daily_data(
            date(2026, 1, 2),
            n_days=10,
            daily_pnl_pattern=[100_000] * 10,
        )

        report = reporter.generate_monthly(
            data, year=2026, month=1,
            benchmark_return_pct=0.5,
        )

        assert report.benchmark_return_pct == 0.5
        assert report.excess_return_pct == round(
            report.period_return_pct - 0.5, 2
        )

    def test_strategy_contributions(self, reporter):
        """전략별 기여도"""
        data = _make_daily_data(date(2026, 1, 2), n_days=5)
        contributions = {"FACTOR": 0.5, "TREND": 0.3, "RISK_PARITY": 0.2}

        report = reporter.generate_monthly(
            data, year=2026, month=1,
            strategy_contributions=contributions,
        )

        assert report.strategy_contributions == contributions

    def test_monthly_december(self, reporter):
        """12월 월간 리포트 (연말)"""
        data = _make_daily_data(date(2025, 12, 1), n_days=5)
        report = reporter.generate_monthly(data, year=2025, month=12)

        assert report.end_date == date(2025, 12, 31)

    def test_empty_month(self, reporter):
        """빈 월간 데이터"""
        report = reporter.generate_monthly([], year=2026, month=1)
        assert report.period_pnl == 0.0


# ══════════════════════════════════════
# MDD 계산 테스트
# ══════════════════════════════════════
class TestMDDCalculation:
    """MDD 계산 테스트"""

    def test_mdd_with_drawdown(self, reporter):
        """낙폭이 있는 경우 MDD"""
        data = _make_daily_data(
            date(2026, 1, 5),
            n_days=5,
            daily_pnl_pattern=[500_000, -1_000_000, -500_000, 200_000, 300_000],
        )

        report = reporter.generate_weekly(
            data,
            week_start=date(2026, 1, 5),
            week_end=date(2026, 1, 9),
        )

        assert report.max_drawdown_pct < 0

    def test_mdd_no_drawdown(self, reporter):
        """상승만 있으면 MDD = 0"""
        data = _make_daily_data(
            date(2026, 1, 5),
            n_days=5,
            daily_pnl_pattern=[100_000] * 5,
        )

        report = reporter.generate_weekly(
            data,
            week_start=date(2026, 1, 5),
            week_end=date(2026, 1, 9),
        )

        assert report.max_drawdown_pct == 0.0

    def test_mdd_empty(self):
        """빈 데이터"""
        assert PeriodicReporter._calculate_mdd([]) == 0.0


# ══════════════════════════════════════
# Sharpe/변동성 테스트
# ══════════════════════════════════════
class TestRiskMetrics:
    """리스크 지표 테스트"""

    def test_volatility_positive(self, reporter, sample_week_data):
        """변동성이 양수"""
        report = reporter.generate_weekly(
            sample_week_data,
            week_start=date(2026, 1, 5),
            week_end=date(2026, 1, 9),
        )
        assert report.volatility_pct > 0

    def test_volatility_zero_with_constant(self):
        """일정 수익률이면 변동성 ≈ 0"""
        returns = [0.001] * 10
        vol = PeriodicReporter._calculate_volatility(returns)
        assert vol < 0.01

    def test_sharpe_positive_for_gains(self, reporter):
        """양의 수익이면 Sharpe > 0"""
        data = _make_daily_data(
            date(2026, 1, 5),
            n_days=5,
            daily_pnl_pattern=[200_000, 150_000, 300_000, 100_000, 250_000],
        )
        report = reporter.generate_weekly(
            data,
            week_start=date(2026, 1, 5),
            week_end=date(2026, 1, 9),
        )
        assert report.sharpe_ratio > 0


# ══════════════════════════════════════
# 발송 시점 판별 테스트
# ══════════════════════════════════════
class TestScheduleTiming:
    """리포트 발송 시점 판별 테스트"""

    def test_friday_is_weekly(self, reporter):
        """금요일 거래일 → 주간 리포트 생성"""
        # 2026-01-09 (금) — 거래일
        assert reporter.should_generate_weekly(date(2026, 1, 9)) is True

    def test_monday_not_weekly(self, reporter):
        """월요일 → 주간 리포트 아님"""
        assert reporter.should_generate_weekly(date(2026, 1, 5)) is False

    def test_last_trading_day_of_month(self, reporter):
        """월 마지막 거래일 → 월간 리포트 생성"""
        # 2026-01-30 (금) — 1월 마지막 거래일
        assert reporter.should_generate_monthly(date(2026, 1, 30)) is True

    def test_mid_month_not_monthly(self, reporter):
        """월 중순 → 월간 리포트 아님"""
        assert reporter.should_generate_monthly(date(2026, 1, 15)) is False

    def test_weekend_not_monthly(self, reporter):
        """주말 → 월간 리포트 아님"""
        assert reporter.should_generate_monthly(date(2026, 1, 31)) is False  # 토요일

    def test_get_week_range(self, reporter):
        """주간 범위 (월~금)"""
        monday, friday = reporter.get_week_range(date(2026, 1, 7))  # 수요일
        assert monday == date(2026, 1, 5)
        assert friday == date(2026, 1, 9)


# ══════════════════════════════════════
# Telegram 포맷 테스트
# ══════════════════════════════════════
class TestTelegramFormat:
    """Telegram 메시지 포맷 테스트"""

    def test_weekly_format_contains_period(self, reporter, sample_week_data):
        """주간 포맷에 기간 포함"""
        report = reporter.generate_weekly(
            sample_week_data,
            week_start=date(2026, 1, 5),
            week_end=date(2026, 1, 9),
        )
        msg = reporter.format_weekly_telegram(report)
        assert "주간 리포트" in msg
        assert "2026-01-05" in msg

    def test_monthly_format_contains_benchmark(self, reporter):
        """월간 포맷에 벤치마크 대비 포함"""
        data = _make_daily_data(date(2026, 1, 2), n_days=5)
        report = reporter.generate_monthly(
            data, year=2026, month=1,
            benchmark_return_pct=0.5,
        )
        msg = reporter.format_monthly_telegram(report)
        assert "월간 리포트" in msg
        assert "벤치마크" in msg

    def test_monthly_format_strategy_contributions(self, reporter):
        """월간 포맷에 전략 기여도 포함"""
        data = _make_daily_data(date(2026, 1, 2), n_days=5)
        report = reporter.generate_monthly(
            data, year=2026, month=1,
            strategy_contributions={"FACTOR": 0.5, "TREND": 0.3},
        )
        msg = reporter.format_monthly_telegram(report)
        assert "전략별 기여도" in msg
        assert "FACTOR" in msg


# ══════════════════════════════════════
# to_dict 테스트
# ══════════════════════════════════════
class TestSerialization:
    """직렬화 테스트"""

    def test_to_dict(self, reporter, sample_week_data):
        """to_dict 필드 확인"""
        report = reporter.generate_weekly(
            sample_week_data,
            week_start=date(2026, 1, 5),
            week_end=date(2026, 1, 9),
        )
        d = report.to_dict()
        assert "period" in d
        assert "period_pnl" in d
        assert "sharpe_ratio" in d
        assert d["period"] == "WEEKLY"
