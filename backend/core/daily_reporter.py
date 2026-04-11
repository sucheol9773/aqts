"""
AQTS Phase 7 - 일일 리포트 자동 생성 및 발송

장 마감 후 일일 거래 성과 리포트를 자동 생성하고
Telegram으로 발송합니다.

리포트 내용:
  - 일일 수익률 및 손익 요약
  - 포지션 변동 내역
  - 서킷브레이커 발동 이력
  - 주요 거래 내역
  - 포트폴리오 현황
  - 다음 거래일 예고
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from loguru import logger

from config.settings import get_settings

# ══════════════════════════════════════
# 일일 리포트 데이터 구조
# ══════════════════════════════════════

KST = timezone(timedelta(hours=9))


@dataclass
class TradeRecord:
    """개별 거래 기록"""

    ticker: str
    name: str
    side: str  # BUY / SELL
    quantity: int
    price: float
    amount: float
    pnl: Optional[float] = None
    executed_at: Optional[datetime] = None


@dataclass
class PositionSnapshot:
    """포지션 스냅샷"""

    ticker: str
    name: str
    quantity: int
    avg_price: float
    current_price: float
    market_value: float
    pnl: float
    pnl_percent: float
    weight: float  # 포트폴리오 내 비중


@dataclass
class DailyReport:
    """일일 리포트"""

    report_date: date
    trading_mode: str = "DEMO"

    # 수익률 요약
    portfolio_value_start: float = 0.0
    portfolio_value_end: float = 0.0
    daily_pnl: float = 0.0
    daily_return_pct: float = 0.0
    cumulative_pnl: float = 0.0
    cumulative_return_pct: float = 0.0

    # 거래 요약
    total_trades: int = 0
    buy_trades: int = 0
    sell_trades: int = 0
    total_buy_amount: float = 0.0
    total_sell_amount: float = 0.0
    winning_trades: int = 0
    losing_trades: int = 0

    # 포지션 현황
    positions: list[PositionSnapshot] = field(default_factory=list)
    cash_balance: float = 0.0
    total_positions: int = 0

    # 거래 내역
    trades: list[TradeRecord] = field(default_factory=list)

    # 리스크 이벤트
    circuit_breaker_triggered: bool = False
    circuit_breaker_reason: str = ""
    max_drawdown_today: float = 0.0
    consecutive_losses: int = 0

    # Top/Bottom 3 종목 (F-09-01)
    top3_positions: list[PositionSnapshot] = field(default_factory=list)
    bottom3_positions: list[PositionSnapshot] = field(default_factory=list)

    # 메타
    generated_at: datetime = field(default_factory=lambda: datetime.now(KST))

    def to_dict(self) -> dict:
        return {
            "report_date": self.report_date.isoformat(),
            "trading_mode": self.trading_mode,
            "portfolio_value_start": self.portfolio_value_start,
            "portfolio_value_end": self.portfolio_value_end,
            "daily_pnl": self.daily_pnl,
            "daily_return_pct": self.daily_return_pct,
            "cumulative_pnl": self.cumulative_pnl,
            "cumulative_return_pct": self.cumulative_return_pct,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "total_positions": self.total_positions,
            "cash_balance": self.cash_balance,
            "circuit_breaker_triggered": self.circuit_breaker_triggered,
            "generated_at": self.generated_at.isoformat(),
        }


# ══════════════════════════════════════
# 일일 리포트 생성기
# ══════════════════════════════════════


class DailyReporter:
    """
    일일 거래 리포트 생성 및 발송

    Usage:
        reporter = DailyReporter()
        report = await reporter.generate_report(
            portfolio_value_start=50_000_000,
            portfolio_value_end=50_500_000,
            trades=[...],
            positions=[...],
        )
        await reporter.send_telegram_report(report)
    """

    def __init__(self):
        self._settings = get_settings()
        self._report_history: list[dict] = []

    # ── 리포트 생성 ──

    async def generate_report(
        self,
        report_date: Optional[date] = None,
        portfolio_value_start: float = 0.0,
        portfolio_value_end: float = 0.0,
        initial_capital: Optional[float] = None,
        trades: Optional[list[TradeRecord]] = None,
        positions: Optional[list[PositionSnapshot]] = None,
        cash_balance: float = 0.0,
        circuit_breaker_triggered: bool = False,
        circuit_breaker_reason: str = "",
        max_drawdown_today: float = 0.0,
        consecutive_losses: int = 0,
    ) -> DailyReport:
        """일일 리포트 생성"""

        if report_date is None:
            report_date = datetime.now(KST).date()

        if initial_capital is None:
            initial_capital = self._settings.risk.initial_capital_krw

        trades = trades or []
        positions = positions or []

        # 수익률 계산
        daily_pnl = portfolio_value_end - portfolio_value_start
        daily_return_pct = (daily_pnl / portfolio_value_start * 100) if portfolio_value_start > 0 else 0.0
        cumulative_pnl = portfolio_value_end - initial_capital
        cumulative_return_pct = (cumulative_pnl / initial_capital * 100) if initial_capital > 0 else 0.0

        # 거래 통계
        buy_trades = [t for t in trades if t.side == "BUY"]
        sell_trades = [t for t in trades if t.side == "SELL"]
        winning = [t for t in trades if t.pnl is not None and t.pnl > 0]
        losing = [t for t in trades if t.pnl is not None and t.pnl < 0]

        # Top/Bottom 3 종목 (F-09-01)
        # 보유 포지션이 6개 미만이면 동일 종목이 Top 과 Bottom 양쪽에 노출되는
        # 표시 버그가 있었다 (예: 1종목 보유 시 같은 티커가 🏆 와 💀 양쪽에 등장).
        # bottom3 는 top3 와 겹치지 않도록 ticker 기준으로 제외한다.
        # → 1~3종목: bottom3 비어있음 (top3 만 노출)
        # → 4종목: bottom3 = 1개, 5종목: bottom3 = 2개, 6종목+: 정식 3+3
        top3 = sorted(positions, key=lambda p: p.pnl_percent, reverse=True)[:3]
        top3_tickers = {p.ticker for p in top3}
        bottom3 = [p for p in sorted(positions, key=lambda p: p.pnl_percent) if p.ticker not in top3_tickers][:3]

        report = DailyReport(
            report_date=report_date,
            trading_mode=self._settings.kis.trading_mode.value,
            portfolio_value_start=portfolio_value_start,
            portfolio_value_end=portfolio_value_end,
            daily_pnl=daily_pnl,
            daily_return_pct=round(daily_return_pct, 2),
            cumulative_pnl=cumulative_pnl,
            cumulative_return_pct=round(cumulative_return_pct, 2),
            total_trades=len(trades),
            buy_trades=len(buy_trades),
            sell_trades=len(sell_trades),
            total_buy_amount=sum(t.amount for t in buy_trades),
            total_sell_amount=sum(t.amount for t in sell_trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            positions=positions,
            cash_balance=cash_balance,
            total_positions=len(positions),
            trades=trades,
            circuit_breaker_triggered=circuit_breaker_triggered,
            circuit_breaker_reason=circuit_breaker_reason,
            max_drawdown_today=max_drawdown_today,
            consecutive_losses=consecutive_losses,
            top3_positions=top3,
            bottom3_positions=bottom3,
        )

        self._report_history.append(report.to_dict())
        logger.info(f"일일 리포트 생성: {report_date} | " f"PnL: {daily_pnl:+,.0f}원 ({daily_return_pct:+.2f}%)")

        return report

    # ── Telegram 발송 ──

    async def send_telegram_report(self, report: DailyReport) -> bool:
        """Telegram으로 일일 리포트 발송"""
        try:
            from core.notification.telegram_transport import create_transport

            transport = create_transport()

            if not transport.is_configured():
                logger.warning(
                    "Telegram 미설정 (TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID 누락) " "— 리포트 발송을 건너뜁니다"
                )
                return False

            message = self._format_telegram_message(report)

            success = await transport.send_text(message)
            if success:
                logger.info(f"일일 리포트 Telegram 발송 성공: {report.report_date}")
            else:
                logger.warning(f"일일 리포트 Telegram 발송 실패: {report.report_date}")

            return success

        except Exception as e:
            logger.error(f"Telegram 발송 오류: {e}")
            return False

    def _format_telegram_message(self, report: DailyReport) -> str:
        """Telegram 메시지 포맷"""
        # 수익률 이모지
        pnl_emoji = "📈" if report.daily_pnl >= 0 else "📉"
        mode_label = "🔵 모의투자" if report.trading_mode == "DEMO" else "🔴 실투자"

        lines = [
            "━━━━━━━━━━━━━━━━",
            "📊 AQTS 일일 리포트",
            "━━━━━━━━━━━━━━━━",
            f"📅 {report.report_date} | {mode_label}",
            "",
            "💰 수익 요약",
            f"  시가 평가: {report.portfolio_value_start:>14,.0f}원",
            f"  종가 평가: {report.portfolio_value_end:>14,.0f}원",
            f"  {pnl_emoji} 일일 손익: {report.daily_pnl:>+14,.0f}원 ({report.daily_return_pct:+.2f}%)",
            f"  📊 누적 손익: {report.cumulative_pnl:>+14,.0f}원 ({report.cumulative_return_pct:+.2f}%)",
            "",
            "📋 거래 요약",
            f"  총 {report.total_trades}건 (매수 {report.buy_trades} / 매도 {report.sell_trades})",
        ]

        if report.total_trades > 0:
            win_rate = report.winning_trades / report.total_trades * 100 if report.total_trades > 0 else 0
            lines.append(f"  승률: {win_rate:.0f}% ({report.winning_trades}승 {report.losing_trades}패)")

        # 상위 거래 내역 (최대 5건)
        if report.trades:
            lines.append("")
            lines.append("📝 주요 거래")
            for trade in report.trades[:5]:
                side_mark = "🟢" if trade.side == "BUY" else "🔴"
                pnl_str = f" ({trade.pnl:+,.0f})" if trade.pnl else ""
                lines.append(f"  {side_mark} {trade.name} {trade.quantity}주 " f"@{trade.price:,.0f}{pnl_str}")
            if len(report.trades) > 5:
                lines.append(f"  ... 외 {len(report.trades) - 5}건")

        # Top/Bottom 3 종목 (F-09-01)
        if report.top3_positions:
            lines.append("")
            lines.append("🏆 Top 3 종목")
            for pos in report.top3_positions:
                lines.append(f"  🟢 {pos.name}: {pos.pnl_percent:+.1f}% " f"({pos.pnl:+,.0f}원)")

        if report.bottom3_positions:
            lines.append("")
            lines.append("💀 Bottom 3 종목")
            for pos in report.bottom3_positions:
                lines.append(f"  🔴 {pos.name}: {pos.pnl_percent:+.1f}% " f"({pos.pnl:+,.0f}원)")

        # 포지션 현황
        if report.positions:
            lines.append("")
            lines.append(f"📦 보유 종목 ({report.total_positions}종목)")
            sorted_positions = sorted(report.positions, key=lambda p: p.market_value, reverse=True)
            for pos in sorted_positions[:5]:
                pnl_mark = "+" if pos.pnl >= 0 else ""
                lines.append(
                    f"  {pos.name}: {pos.quantity}주 " f"({pnl_mark}{pos.pnl_percent:.1f}%, {pos.weight:.1f}%)"
                )
            if len(report.positions) > 5:
                lines.append(f"  ... 외 {len(report.positions) - 5}종목")

        lines.append("")
        lines.append(f"💵 현금 잔고: {report.cash_balance:,.0f}원")

        # 리스크 이벤트
        if report.circuit_breaker_triggered:
            lines.append("")
            lines.append(f"⚠️ 서킷브레이커 발동: {report.circuit_breaker_reason}")

        if report.max_drawdown_today > 0:
            lines.append(f"📉 금일 최대 낙폭: {report.max_drawdown_today:.2f}%")

        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━")

        return "\n".join(lines)

    # ── 리포트 조회 ──

    def get_report_history(self) -> list[dict]:
        """리포트 이력 조회"""
        return list(self._report_history)

    # ── 리포트 데이터 수집 헬퍼 ──

    async def collect_from_kis(self) -> dict:
        """KIS API에서 잔고 및 포지션 정보 수집"""
        try:
            from core.data_collector.kis_client import KISClient

            client = KISClient()
            balance_data = await client.get_kr_balance()

            positions = []
            cash = 0
            total_eval = 0

            if balance_data:
                output1 = balance_data.get("output1", [])
                output2 = balance_data.get("output2", [{}])

                for item in output1:
                    ticker = item.get("pdno", "")
                    name = item.get("prdt_name", ticker)
                    qty = int(item.get("hldg_qty", "0"))
                    avg_price = float(item.get("pchs_avg_pric", "0"))
                    curr_price = float(item.get("prpr", "0"))
                    eval_amt = float(item.get("evlu_amt", "0"))
                    pnl = float(item.get("evlu_pfls_amt", "0"))

                    if qty > 0:
                        cost = avg_price * qty
                        pnl_pct = (pnl / cost * 100) if cost > 0 else 0.0
                        positions.append(
                            PositionSnapshot(
                                ticker=ticker,
                                name=name,
                                quantity=qty,
                                avg_price=avg_price,
                                current_price=curr_price,
                                market_value=eval_amt,
                                pnl=pnl,
                                pnl_percent=round(pnl_pct, 2),
                                weight=0.0,  # 아래에서 계산
                            )
                        )

                if output2:
                    cash = int(output2[0].get("dnca_tot_amt", "0"))
                    total_eval = int(output2[0].get("tot_evlu_amt", "0"))

            # 비중 계산
            for pos in positions:
                pos.weight = round(pos.market_value / total_eval * 100 if total_eval > 0 else 0.0, 1)

            return {
                "positions": positions,
                "cash_balance": cash,
                "total_evaluation": total_eval,
            }

        except Exception as e:
            logger.error(f"KIS 잔고 수집 실패: {e}")
            return {"positions": [], "cash_balance": 0, "total_evaluation": 0}
