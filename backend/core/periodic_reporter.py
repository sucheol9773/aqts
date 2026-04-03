"""
주간/월간 리포트 생성기 (F-09)

DailyReporter를 확장하여 주간(금요일) 및 월간(말일) 리포트를 생성합니다.

리포트 유형:
- WEEKLY (금요일 장 마감 후): 주간 성과 요약
- MONTHLY (월 마지막 거래일 장 마감 후): 월간 성과 요약

주간 리포트 내용:
- 주간 수익률 및 손익
- 일별 수익률 추이
- Best/Worst 거래일
- 주간 거래 빈도/승률
- 포지션 변동 요약
- 다음 주 전략 시그널 요약

월간 리포트 내용:
- 월간 수익률 및 손익
- 주별 수익률 추이
- 최대 낙폭 (MDD)
- 전략별 기여도
- 포트폴리오 리밸런싱 이력
- 벤치마크 대비 성과
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from config.logging import logger
from core.market_calendar import KST, Market, MarketCalendar


# ══════════════════════════════════════
# 리포트 유형
# ══════════════════════════════════════
class ReportPeriod(str, Enum):
    """리포트 기간 유형"""
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


# ══════════════════════════════════════
# 주간/월간 리포트 데이터
# ══════════════════════════════════════
@dataclass
class DailySummary:
    """일별 요약 (주간/월간 리포트 내 항목)"""
    date: date
    portfolio_value: float
    daily_pnl: float
    daily_return_pct: float
    trades_count: int


@dataclass
class PeriodicReport:
    """주간/월간 리포트"""
    period: ReportPeriod
    start_date: date
    end_date: date
    trading_mode: str = "DEMO"

    # 기간 수익률
    portfolio_value_start: float = 0.0
    portfolio_value_end: float = 0.0
    period_pnl: float = 0.0
    period_return_pct: float = 0.0
    cumulative_return_pct: float = 0.0

    # 일별 요약
    daily_summaries: list[DailySummary] = field(default_factory=list)

    # Best/Worst
    best_day: Optional[DailySummary] = None
    worst_day: Optional[DailySummary] = None

    # 거래 통계
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_fees: float = 0.0

    # 리스크
    max_drawdown_pct: float = 0.0
    volatility_pct: float = 0.0
    sharpe_ratio: float = 0.0

    # 전략별 기여 (월간)
    strategy_contributions: dict[str, float] = field(default_factory=dict)

    # 벤치마크 대비 (월간)
    benchmark_return_pct: float = 0.0
    excess_return_pct: float = 0.0

    # 거래일 수
    trading_days: int = 0

    # 메타
    generated_at: datetime = field(
        default_factory=lambda: datetime.now(KST)
    )

    def to_dict(self) -> dict:
        return {
            "period": self.period.value,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "trading_mode": self.trading_mode,
            "portfolio_value_start": self.portfolio_value_start,
            "portfolio_value_end": self.portfolio_value_end,
            "period_pnl": self.period_pnl,
            "period_return_pct": self.period_return_pct,
            "cumulative_return_pct": self.cumulative_return_pct,
            "total_trades": self.total_trades,
            "win_rate": self.win_rate,
            "max_drawdown_pct": self.max_drawdown_pct,
            "sharpe_ratio": self.sharpe_ratio,
            "trading_days": self.trading_days,
            "generated_at": self.generated_at.isoformat(),
        }


# ══════════════════════════════════════
# 주간/월간 리포트 생성기
# ══════════════════════════════════════
class PeriodicReporter:
    """
    주간/월간 리포트 생성기

    DailyReport 이력을 집계하여 주간/월간 성과 리포트를 생성합니다.

    스케줄:
    - 주간: 매주 금요일 장 마감 후 (16:00 KST)
    - 월간: 매월 마지막 거래일 장 마감 후

    Usage:
        reporter = PeriodicReporter()
        weekly = reporter.generate_weekly(daily_data, week_start, week_end)
        monthly = reporter.generate_monthly(daily_data, year, month)
    """

    RISK_FREE_RATE = 0.035  # 연 3.5%

    def __init__(self):
        self._calendar = MarketCalendar()

    # ══════════════════════════════════════
    # 주간 리포트
    # ══════════════════════════════════════
    def generate_weekly(
        self,
        daily_data: list[DailySummary],
        week_start: date,
        week_end: date,
        initial_capital: float = 50_000_000.0,
        trading_mode: str = "DEMO",
    ) -> PeriodicReport:
        """
        주간 리포트 생성

        Args:
            daily_data: 일별 요약 데이터 리스트
            week_start: 주 시작일 (월요일)
            week_end: 주 종료일 (금요일)
            initial_capital: 초기 자본금
            trading_mode: 거래 모드

        Returns:
            PeriodicReport
        """
        # 해당 주간 데이터 필터
        week_data = [
            d for d in daily_data
            if week_start <= d.date <= week_end
        ]

        report = self._build_report(
            period=ReportPeriod.WEEKLY,
            start_date=week_start,
            end_date=week_end,
            daily_data=week_data,
            initial_capital=initial_capital,
            trading_mode=trading_mode,
        )

        logger.info(
            f"주간 리포트 생성: {week_start}~{week_end} | "
            f"PnL: {report.period_pnl:+,.0f}원 ({report.period_return_pct:+.2f}%)"
        )
        return report

    # ══════════════════════════════════════
    # 월간 리포트
    # ══════════════════════════════════════
    def generate_monthly(
        self,
        daily_data: list[DailySummary],
        year: int,
        month: int,
        initial_capital: float = 50_000_000.0,
        trading_mode: str = "DEMO",
        benchmark_return_pct: float = 0.0,
        strategy_contributions: Optional[dict[str, float]] = None,
    ) -> PeriodicReport:
        """
        월간 리포트 생성

        Args:
            daily_data: 일별 요약 데이터 리스트
            year: 연도
            month: 월
            initial_capital: 초기 자본금
            trading_mode: 거래 모드
            benchmark_return_pct: 벤치마크 월간 수익률 (%)
            strategy_contributions: 전략별 기여도

        Returns:
            PeriodicReport
        """
        # 월 시작/종료 계산
        month_start = date(year, month, 1)
        if month == 12:
            month_end = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            month_end = date(year, month + 1, 1) - timedelta(days=1)

        # 해당 월 데이터 필터
        month_data = [
            d for d in daily_data
            if month_start <= d.date <= month_end
        ]

        report = self._build_report(
            period=ReportPeriod.MONTHLY,
            start_date=month_start,
            end_date=month_end,
            daily_data=month_data,
            initial_capital=initial_capital,
            trading_mode=trading_mode,
        )

        # 월간 전용 필드
        report.benchmark_return_pct = benchmark_return_pct
        report.excess_return_pct = round(
            report.period_return_pct - benchmark_return_pct, 2
        )
        report.strategy_contributions = strategy_contributions or {}

        logger.info(
            f"월간 리포트 생성: {year}-{month:02d} | "
            f"PnL: {report.period_pnl:+,.0f}원 ({report.period_return_pct:+.2f}%) | "
            f"초과수익: {report.excess_return_pct:+.2f}%"
        )
        return report

    # ══════════════════════════════════════
    # 공통 빌드
    # ══════════════════════════════════════
    def _build_report(
        self,
        period: ReportPeriod,
        start_date: date,
        end_date: date,
        daily_data: list[DailySummary],
        initial_capital: float,
        trading_mode: str,
    ) -> PeriodicReport:
        """리포트 공통 빌드 로직"""

        if not daily_data:
            return PeriodicReport(
                period=period,
                start_date=start_date,
                end_date=end_date,
                trading_mode=trading_mode,
            )

        # 정렬
        daily_data = sorted(daily_data, key=lambda d: d.date)

        # 기간 수익률
        start_value = daily_data[0].portfolio_value - daily_data[0].daily_pnl
        end_value = daily_data[-1].portfolio_value
        period_pnl = end_value - start_value
        period_return = (
            (period_pnl / start_value * 100) if start_value > 0 else 0.0
        )
        cumulative_return = (
            ((end_value - initial_capital) / initial_capital * 100)
            if initial_capital > 0
            else 0.0
        )

        # Best/Worst day
        best = max(daily_data, key=lambda d: d.daily_return_pct)
        worst = min(daily_data, key=lambda d: d.daily_return_pct)

        # 거래 통계
        total_trades = sum(d.trades_count for d in daily_data)

        # MDD 계산
        mdd = self._calculate_mdd(daily_data)

        # 변동성 (일별 수익률 표준편차 × √252, 연환산)
        returns = [d.daily_return_pct / 100 for d in daily_data]
        volatility = self._calculate_volatility(returns)

        # Sharpe (기간 수익률 연환산 / 변동성)
        sharpe = self._calculate_sharpe(returns, len(daily_data))

        return PeriodicReport(
            period=period,
            start_date=start_date,
            end_date=end_date,
            trading_mode=trading_mode,
            portfolio_value_start=round(start_value, 0),
            portfolio_value_end=round(end_value, 0),
            period_pnl=round(period_pnl, 0),
            period_return_pct=round(period_return, 2),
            cumulative_return_pct=round(cumulative_return, 2),
            daily_summaries=daily_data,
            best_day=best,
            worst_day=worst,
            total_trades=total_trades,
            max_drawdown_pct=round(mdd, 2),
            volatility_pct=round(volatility * 100, 2),
            sharpe_ratio=round(sharpe, 2),
            trading_days=len(daily_data),
        )

    # ══════════════════════════════════════
    # 성과 지표 계산
    # ══════════════════════════════════════
    @staticmethod
    def _calculate_mdd(daily_data: list[DailySummary]) -> float:
        """최대 낙폭 (%) 계산"""
        if not daily_data:
            return 0.0

        peak = daily_data[0].portfolio_value
        max_dd = 0.0

        for d in daily_data:
            if d.portfolio_value > peak:
                peak = d.portfolio_value
            dd = (d.portfolio_value - peak) / peak * 100 if peak > 0 else 0.0
            max_dd = min(max_dd, dd)

        return max_dd

    @staticmethod
    def _calculate_volatility(daily_returns: list[float]) -> float:
        """변동성 (연환산) 계산"""
        if len(daily_returns) < 2:
            return 0.0

        import numpy as np
        return float(np.std(daily_returns, ddof=1) * np.sqrt(252))

    def _calculate_sharpe(
        self,
        daily_returns: list[float],
        n_days: int,
    ) -> float:
        """기간 Sharpe Ratio 계산"""
        if len(daily_returns) < 2:
            return 0.0

        import numpy as np
        rf_daily = self.RISK_FREE_RATE / 252
        excess = [r - rf_daily for r in daily_returns]

        std = float(np.std(excess, ddof=1))
        if std < 1e-10:
            return 0.0

        return float(np.mean(excess) / std * np.sqrt(252))

    # ══════════════════════════════════════
    # 발송 시점 판별
    # ══════════════════════════════════════
    def should_generate_weekly(self, d: date) -> bool:
        """
        주간 리포트 생성 시점 여부

        매주 금요일 또는 해당 주의 마지막 거래일
        """
        # 금요일(4)이면서 거래일
        if d.weekday() == 4 and self._calendar.is_trading_day(d, Market.KRX):
            return True

        # 금요일이 공휴일이면 그 전 거래일 (목요일 등)
        next_day = d + timedelta(days=1)
        # 다음 날이 토요일이면 이번 주 마지막 거래일
        if next_day.weekday() == 5 and self._calendar.is_trading_day(
            d, Market.KRX
        ):
            return True

        return False

    def should_generate_monthly(self, d: date) -> bool:
        """
        월간 리포트 생성 시점 여부

        해당 월의 마지막 거래일
        """
        if not self._calendar.is_trading_day(d, Market.KRX):
            return False

        # 다음 거래일이 다음 달이면 이번 달 마지막 거래일
        next_td = self._calendar.next_trading_day(d, Market.KRX)
        return next_td.month != d.month

    def get_week_range(self, d: date) -> tuple[date, date]:
        """주어진 날짜가 속한 주의 (월요일, 금요일) 반환"""
        # d.weekday(): 0=월 ... 4=금 ... 6=일
        monday = d - timedelta(days=d.weekday())
        friday = monday + timedelta(days=4)
        return monday, friday

    # ══════════════════════════════════════
    # Telegram 메시지 포맷
    # ══════════════════════════════════════
    def format_weekly_telegram(self, report: PeriodicReport) -> str:
        """주간 리포트 Telegram 메시지"""
        pnl_emoji = "📈" if report.period_pnl >= 0 else "📉"
        mode = "🔵 모의투자" if report.trading_mode == "DEMO" else "🔴 실투자"

        lines = [
            "━━━━━━━━━━━━━━━━",
            "📊 AQTS 주간 리포트",
            "━━━━━━━━━━━━━━━━",
            f"📅 {report.start_date} ~ {report.end_date} | {mode}",
            "",
            f"💰 주간 성과",
            f"  {pnl_emoji} 손익: {report.period_pnl:>+14,.0f}원",
            f"  📊 수익률: {report.period_return_pct:>+10.2f}%",
            f"  📊 누적 수익률: {report.cumulative_return_pct:>+8.2f}%",
            "",
            f"📋 거래 요약",
            f"  거래일: {report.trading_days}일",
            f"  총 거래: {report.total_trades}건",
            "",
            f"📉 리스크",
            f"  MDD: {report.max_drawdown_pct:.2f}%",
            f"  변동성: {report.volatility_pct:.2f}%",
            f"  Sharpe: {report.sharpe_ratio:.2f}",
        ]

        if report.best_day:
            lines.append("")
            lines.append(
                f"🏆 Best: {report.best_day.date} "
                f"({report.best_day.daily_return_pct:+.2f}%)"
            )
        if report.worst_day:
            lines.append(
                f"💀 Worst: {report.worst_day.date} "
                f"({report.worst_day.daily_return_pct:+.2f}%)"
            )

        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    def format_monthly_telegram(self, report: PeriodicReport) -> str:
        """월간 리포트 Telegram 메시지"""
        pnl_emoji = "📈" if report.period_pnl >= 0 else "📉"
        mode = "🔵 모의투자" if report.trading_mode == "DEMO" else "🔴 실투자"
        excess_emoji = "✅" if report.excess_return_pct >= 0 else "❌"

        lines = [
            "━━━━━━━━━━━━━━━━",
            "📊 AQTS 월간 리포트",
            "━━━━━━━━━━━━━━━━",
            f"📅 {report.start_date} ~ {report.end_date} | {mode}",
            "",
            f"💰 월간 성과",
            f"  {pnl_emoji} 손익: {report.period_pnl:>+14,.0f}원",
            f"  📊 수익률: {report.period_return_pct:>+10.2f}%",
            f"  📊 누적 수익률: {report.cumulative_return_pct:>+8.2f}%",
            "",
            f"📊 벤치마크 대비",
            f"  벤치마크 수익률: {report.benchmark_return_pct:>+8.2f}%",
            f"  {excess_emoji} 초과 수익률: {report.excess_return_pct:>+8.2f}%",
        ]

        if report.strategy_contributions:
            lines.append("")
            lines.append("🎯 전략별 기여도")
            for name, contrib in sorted(
                report.strategy_contributions.items(),
                key=lambda x: x[1],
                reverse=True,
            ):
                lines.append(f"  {name}: {contrib:+.2f}%")

        lines.extend([
            "",
            f"📋 거래 요약",
            f"  거래일: {report.trading_days}일",
            f"  총 거래: {report.total_trades}건",
            "",
            f"📉 리스크",
            f"  MDD: {report.max_drawdown_pct:.2f}%",
            f"  변동성: {report.volatility_pct:.2f}%",
            f"  Sharpe: {report.sharpe_ratio:.2f}",
        ])

        if report.best_day:
            lines.append("")
            lines.append(
                f"🏆 Best: {report.best_day.date} "
                f"({report.best_day.daily_return_pct:+.2f}%)"
            )
        if report.worst_day:
            lines.append(
                f"💀 Worst: {report.worst_day.date} "
                f"({report.worst_day.daily_return_pct:+.2f}%)"
            )

        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━")
        return "\n".join(lines)
