"""
백테스트 엔진 (Backtest Engine)

F-07-01 명세 구현:
- vectorbt 대체 자체 구현 (Python 3.11 호환성 문제 해결)
- 거래 비용 (수수료 + 슬리피지) 및 세금 반영
- 성과 지표: CAGR, MDD, Sharpe, Sortino, Calmar, Win Rate, Profit Factor
- 벤치마크 대비 성과 비교

사용 라이브러리: pandas 2.2.2, numpy 1.26.4
"""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from config.constants import TRANSACTION_COSTS, Country
from config.logging import logger


@dataclass
class BacktestConfig:
    """백테스트 설정"""

    initial_capital: float = 50_000_000.0  # 원
    start_date: Optional[str] = None  # YYYY-MM-DD
    end_date: Optional[str] = None  # YYYY-MM-DD
    country: Country = Country.KR
    commission_rate: Optional[float] = None  # None이면 Country 기본값 사용
    tax_rate: Optional[float] = None
    slippage_rate: Optional[float] = None
    risk_free_rate: float = 0.035  # 연 3.5% (한국 기준금리 근사)
    benchmark_returns: Optional[pd.Series] = None  # 벤치마크 수익률
    # ── 리스크 관리 ──
    stop_loss_pct: Optional[float] = None  # 종목별 손절 (예: 0.15 = -15%에서 청산)
    stop_loss_atr_multiplier: Optional[float] = None  # ATR 기반 동적 손절 (예: 2.0 = 2×ATR)
    max_drawdown_limit: Optional[float] = None  # 포트폴리오 DD 한도 (예: 0.20 = -20%에서 전량 청산)
    drawdown_cooldown_days: int = 20  # DD 발동 후 거래 재개까지 대기 영업일

    def get_costs(self) -> dict:
        """거래 비용 반환 (명시적 설정값 또는 국가 기본값)"""
        defaults = TRANSACTION_COSTS[self.country]
        return {
            "commission": (self.commission_rate if self.commission_rate is not None else defaults["commission_rate"]),
            "tax": self.tax_rate if self.tax_rate is not None else defaults["tax_rate"],
            "slippage": (self.slippage_rate if self.slippage_rate is not None else defaults["slippage_rate"]),
        }


@dataclass
class TradeRecord:
    """개별 거래 기록"""

    date: str
    ticker: str
    side: str  # BUY / SELL
    quantity: int
    price: float
    cost: float  # 거래 비용 (수수료 + 세금 + 슬리피지)
    pnl: float = 0.0  # 실현 손익 (매도 시에만)


@dataclass
class BacktestResult:
    """백테스트 결과"""

    strategy_name: str
    config: BacktestConfig
    start_date: str
    end_date: str
    initial_capital: float
    final_capital: float
    total_return: float  # 총 수익률
    cagr: float  # 연평균 수익률
    mdd: float  # 최대 낙폭
    sharpe_ratio: float  # 샤프 비율
    sortino_ratio: float  # 소르티노 비율
    calmar_ratio: float  # 칼마 비율
    win_rate: float  # 승률
    profit_factor: float  # 수익 팩터
    total_trades: int  # 총 거래 횟수
    avg_trade_return: float  # 평균 거래 수익률
    max_consecutive_losses: int  # 최대 연속 손실
    # 벤치마크 대비 지표 (F-07-01 완성)
    alpha: float = 0.0  # Jensen's Alpha (연율)
    beta: float = 0.0  # 시장 Beta
    information_ratio: float = 0.0  # Information Ratio
    tracking_error: float = 0.0  # Tracking Error (연율)
    # 시계열 데이터
    equity_curve: pd.Series = field(default_factory=pd.Series)  # 자산 곡선
    drawdown_curve: pd.Series = field(default_factory=pd.Series)  # 드로다운 곡선
    trade_records: list = field(default_factory=list)
    monthly_returns: pd.Series = field(default_factory=pd.Series)


class BacktestEngine:
    """
    백테스트 엔진

    시그널 기반 포트폴리오 시뮬레이션 수행
    거래 비용/세금 반영, 성과 지표 계산
    """

    def __init__(self, config: BacktestConfig):
        self._config = config
        self._costs = config.get_costs()

    def run(
        self,
        strategy_name: str,
        signals: pd.DataFrame,
        prices: pd.DataFrame,
    ) -> BacktestResult:
        """
        백테스트 실행

        Args:
            strategy_name: 전략 이름
            signals: 날짜 × 종목 시그널 DataFrame
                     index=날짜, columns=종목코드, values=시그널(-1~+1)
            prices: 날짜 × 종목 종가 DataFrame
                    index=날짜, columns=종목코드, values=종가

        Returns:
            BacktestResult
        """
        logger.info(
            f"Backtest starting: {strategy_name}, "
            f"period={prices.index[0]}~{prices.index[-1]}, "
            f"capital={self._config.initial_capital:,.0f}"
        )

        # 날짜 정렬 및 교집합
        common_dates = signals.index.intersection(prices.index).sort_values()
        common_tickers = signals.columns.intersection(prices.columns)

        if len(common_dates) == 0 or len(common_tickers) == 0:
            logger.error("No overlapping dates or tickers between signals and prices")
            return self._empty_result(strategy_name)

        signals = signals.loc[common_dates, common_tickers]
        prices = prices.loc[common_dates, common_tickers]

        # 시뮬레이션
        equity_curve, trade_records = self._simulate(signals, prices)

        # 성과 지표 계산
        result = self._calculate_metrics(
            strategy_name=strategy_name,
            equity_curve=equity_curve,
            trade_records=trade_records,
        )

        logger.info(
            f"Backtest complete: {strategy_name}, "
            f"Return={result.total_return:.2%}, CAGR={result.cagr:.2%}, "
            f"MDD={result.mdd:.2%}, Sharpe={result.sharpe_ratio:.2f}"
        )

        return result

    def _simulate(
        self,
        signals: pd.DataFrame,
        prices: pd.DataFrame,
    ) -> tuple[pd.Series, list[TradeRecord]]:
        """
        일별 시뮬레이션 수행

        단순화된 로직:
        - 시그널 > 0.3: 해당 종목 매수 (비중 = 시그널 강도)
        - 시그널 < -0.3: 해당 종목 매도
        - |시그널| <= 0.3: 보유 유지
        - 매일 시그널 기반으로 목표 포지션 재계산
        """
        capital = self._config.initial_capital
        cash = capital
        positions = {}  # {ticker: {"quantity": int, "avg_price": float}}
        equity_history = {}
        trade_records = []
        peak_value = capital  # 포트폴리오 최고점 추적
        cooldown_remaining = 0  # DD 발동 후 남은 쿨다운 일수

        dates = signals.index.tolist()

        for i, date in enumerate(dates):
            date_str = str(date)[:10] if not isinstance(date, str) else date

            # 현재 포트폴리오 평가
            portfolio_value = cash
            for ticker, pos in positions.items():
                if ticker in prices.columns:
                    current_price = prices.loc[date, ticker]
                    if not pd.isna(current_price) and current_price > 0:
                        portfolio_value += pos["quantity"] * current_price

            # ── 포트폴리오 Drawdown Limit 체크 ──
            peak_value = max(peak_value, portfolio_value)
            current_dd = (portfolio_value - peak_value) / peak_value if peak_value > 0 else 0.0

            if self._config.max_drawdown_limit is not None:
                # 쿨다운 중이면 카운트 감소, 만료 시 peak 리셋 후 거래 재개
                if cooldown_remaining > 0:
                    cooldown_remaining -= 1
                    if cooldown_remaining == 0:
                        # 쿨다운 종료 → peak를 현재 가치로 리셋
                        peak_value = portfolio_value
                    equity_history[date] = portfolio_value
                    continue

                if current_dd < -self._config.max_drawdown_limit:
                    # DD 한도 초과 → 전 포지션 강제 청산 + 쿨다운 시작
                    cooldown_remaining = self._config.drawdown_cooldown_days
                    for ticker in list(positions.keys()):
                        pos = positions[ticker]
                        sell_price = prices.loc[date, ticker] if ticker in prices.columns else 0
                        if pd.isna(sell_price) or sell_price <= 0:
                            continue
                        effective_price = sell_price * (1 - self._costs["slippage"])
                        quantity = pos["quantity"]
                        gross_proceeds = effective_price * quantity
                        commission = gross_proceeds * self._costs["commission"]
                        tax = gross_proceeds * self._costs["tax"]
                        total_cost = commission + tax
                        net_proceeds = gross_proceeds - total_cost
                        pnl = net_proceeds - (pos["avg_price"] * quantity)
                        cash += net_proceeds
                        trade_records.append(
                            TradeRecord(
                                date=date_str,
                                ticker=ticker,
                                side="SELL",
                                quantity=quantity,
                                price=effective_price,
                                cost=total_cost,
                                pnl=pnl,
                            )
                        )
                        del positions[ticker]
                    portfolio_value = cash
                    equity_history[date] = portfolio_value
                    continue

            # ── 종목별 Stop-loss 체크 (ATR 동적 or 고정 비율) ──
            if self._config.stop_loss_pct is not None or self._config.stop_loss_atr_multiplier is not None:
                for ticker in list(positions.keys()):
                    pos = positions[ticker]
                    current_price = prices.loc[date, ticker] if ticker in prices.columns else 0
                    if pd.isna(current_price) or current_price <= 0:
                        continue

                    # 손절 기준 결정
                    stop_threshold = self._config.stop_loss_pct or 0.15
                    if self._config.stop_loss_atr_multiplier is not None and ticker in prices.columns:
                        # ATR 기반 동적 손절: 최근 20일 True Range 평균
                        lookback = min(i, 20)
                        if lookback >= 5:
                            recent_prices = prices[ticker].iloc[max(0, i - lookback) : i + 1]
                            high_low = recent_prices.max() - recent_prices.min()
                            atr = high_low / lookback if lookback > 0 else 0
                            atr_pct = atr / pos["avg_price"] if pos["avg_price"] > 0 else 0
                            stop_threshold = max(
                                atr_pct * self._config.stop_loss_atr_multiplier,
                                0.05,  # 최소 5% 손절선
                            )

                    loss_pct = (current_price - pos["avg_price"]) / pos["avg_price"]
                    if loss_pct < -stop_threshold:
                        effective_price = current_price * (1 - self._costs["slippage"])
                        quantity = pos["quantity"]
                        gross_proceeds = effective_price * quantity
                        commission = gross_proceeds * self._costs["commission"]
                        tax = gross_proceeds * self._costs["tax"]
                        total_cost = commission + tax
                        net_proceeds = gross_proceeds - total_cost
                        pnl = net_proceeds - (pos["avg_price"] * quantity)
                        cash += net_proceeds
                        trade_records.append(
                            TradeRecord(
                                date=date_str,
                                ticker=ticker,
                                side="SELL",
                                quantity=quantity,
                                price=effective_price,
                                cost=total_cost,
                                pnl=pnl,
                            )
                        )
                        del positions[ticker]

            # 시그널 기반 목표 포지션 계산
            day_signals = signals.loc[date].dropna()
            buy_signals = day_signals[day_signals > 0.3].sort_values(ascending=False)
            sell_signals = day_signals[day_signals < -0.3]

            # 매도 처리
            for ticker in sell_signals.index:
                if ticker in positions and positions[ticker]["quantity"] > 0:
                    pos = positions[ticker]
                    sell_price = prices.loc[date, ticker]
                    if pd.isna(sell_price) or sell_price <= 0:
                        continue

                    # 슬리피지 적용
                    effective_price = sell_price * (1 - self._costs["slippage"])
                    quantity = pos["quantity"]
                    gross_proceeds = effective_price * quantity

                    # 비용 계산 (수수료 + 매도세)
                    commission = gross_proceeds * self._costs["commission"]
                    tax = gross_proceeds * self._costs["tax"]
                    total_cost = commission + tax

                    net_proceeds = gross_proceeds - total_cost
                    pnl = net_proceeds - (pos["avg_price"] * quantity)

                    cash += net_proceeds

                    trade_records.append(
                        TradeRecord(
                            date=date_str,
                            ticker=ticker,
                            side="SELL",
                            quantity=quantity,
                            price=effective_price,
                            cost=total_cost,
                            pnl=pnl,
                        )
                    )

                    del positions[ticker]

            # 매수 처리 (시그널 강도에 비례한 비중)
            if len(buy_signals) > 0:
                # 총 시그널 강도 합으로 정규화
                total_signal = buy_signals.sum()
                if total_signal > 0:
                    available_cash = cash * 0.95  # 5% 현금 유보

                    for ticker, sig in buy_signals.items():
                        if ticker in positions:
                            continue  # 이미 보유 중이면 스킵

                        target_weight = sig / total_signal
                        target_amount = available_cash * target_weight

                        buy_price = prices.loc[date, ticker]
                        if pd.isna(buy_price) or buy_price <= 0:
                            continue

                        # 슬리피지 적용
                        effective_price = buy_price * (1 + self._costs["slippage"])

                        # 수량 계산 (정수)
                        quantity = int(target_amount / effective_price)
                        if quantity <= 0:
                            continue

                        gross_cost = effective_price * quantity
                        commission = gross_cost * self._costs["commission"]
                        total_cost = gross_cost + commission

                        if total_cost > cash:
                            continue

                        cash -= total_cost
                        positions[ticker] = {
                            "quantity": quantity,
                            "avg_price": effective_price,
                        }

                        trade_records.append(
                            TradeRecord(
                                date=date_str,
                                ticker=ticker,
                                side="BUY",
                                quantity=quantity,
                                price=effective_price,
                                cost=commission,
                            )
                        )

            # 일말 포트폴리오 평가
            eod_value = cash
            for ticker, pos in positions.items():
                if ticker in prices.columns:
                    current_price = prices.loc[date, ticker]
                    if not pd.isna(current_price) and current_price > 0:
                        eod_value += pos["quantity"] * current_price

            equity_history[date] = eod_value

        equity_curve = pd.Series(equity_history)
        equity_curve.index = pd.to_datetime(equity_curve.index)

        return equity_curve, trade_records

    def _calculate_metrics(
        self,
        strategy_name: str,
        equity_curve: pd.Series,
        trade_records: list[TradeRecord],
    ) -> BacktestResult:
        """성과 지표 계산"""

        if len(equity_curve) < 2:
            return self._empty_result(strategy_name)

        initial = self._config.initial_capital
        final = equity_curve.iloc[-1]

        # 일별 수익률
        daily_returns = equity_curve.pct_change().dropna()

        # 총 수익률
        total_return = (final - initial) / initial

        # CAGR
        n_days = (equity_curve.index[-1] - equity_curve.index[0]).days
        n_years = max(n_days / 365.25, 0.01)
        cagr = (final / initial) ** (1.0 / n_years) - 1.0

        # MDD
        cummax = equity_curve.cummax()
        drawdown = (equity_curve - cummax) / cummax
        mdd = drawdown.min()

        # Sharpe Ratio (연환산)
        rf_daily = self._config.risk_free_rate / 252
        excess_returns = daily_returns - rf_daily
        sharpe = 0.0
        if len(excess_returns) > 0 and excess_returns.std() > 1e-10:
            sharpe = (excess_returns.mean() / excess_returns.std()) * np.sqrt(252)

        # Sortino Ratio
        downside_returns = excess_returns[excess_returns < 0]
        sortino = 0.0
        if len(downside_returns) > 0 and downside_returns.std() > 1e-10:
            sortino = (excess_returns.mean() / downside_returns.std()) * np.sqrt(252)

        # Calmar Ratio
        calmar = 0.0
        if abs(mdd) > 1e-10:
            calmar = cagr / abs(mdd)

        # 거래 통계
        sell_trades = [t for t in trade_records if t.side == "SELL"]
        total_trades = len(sell_trades)
        winning_trades = [t for t in sell_trades if t.pnl > 0]
        losing_trades = [t for t in sell_trades if t.pnl <= 0]

        win_rate = len(winning_trades) / max(total_trades, 1)

        # Profit Factor
        total_profit = sum(t.pnl for t in winning_trades) if winning_trades else 0.0
        total_loss = abs(sum(t.pnl for t in losing_trades)) if losing_trades else 0.0
        profit_factor = total_profit / max(total_loss, 1.0)

        # 평균 거래 수익률
        avg_trade_return = 0.0
        if total_trades > 0:
            avg_trade_return = sum(t.pnl for t in sell_trades) / total_trades / initial

        # 최대 연속 손실
        max_consec_losses = _max_consecutive([1 if t.pnl <= 0 else 0 for t in sell_trades])

        # 월별 수익률
        monthly_returns = daily_returns.resample("ME").apply(lambda x: (1 + x).prod() - 1)

        # ── 벤치마크 대비 지표 (F-07-01 완성) ──
        alpha, beta, info_ratio, tracking_err = self._calculate_benchmark_metrics(
            daily_returns,
        )

        return BacktestResult(
            strategy_name=strategy_name,
            config=self._config,
            start_date=str(equity_curve.index[0].date()),
            end_date=str(equity_curve.index[-1].date()),
            initial_capital=initial,
            final_capital=final,
            total_return=total_return,
            cagr=cagr,
            mdd=mdd,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            win_rate=win_rate,
            profit_factor=profit_factor,
            total_trades=total_trades,
            avg_trade_return=avg_trade_return,
            max_consecutive_losses=max_consec_losses,
            alpha=alpha,
            beta=beta,
            information_ratio=info_ratio,
            tracking_error=tracking_err,
            equity_curve=equity_curve,
            drawdown_curve=drawdown,
            trade_records=trade_records,
            monthly_returns=monthly_returns,
        )

    def _calculate_benchmark_metrics(
        self,
        daily_returns: pd.Series,
    ) -> tuple[float, float, float, float]:
        """
        벤치마크 대비 성과 지표 계산 (F-07-01)

        벤치마크 수익률이 제공되지 않은 경우 모두 0.0을 반환합니다.

        계산 지표:
            - Alpha (Jensen's Alpha): R_p - [R_f + β(R_m - R_f)], 연율화
            - Beta: Cov(R_p, R_m) / Var(R_m)
            - Information Ratio: (R_p - R_m).mean() / (R_p - R_m).std() × √252
            - Tracking Error: (R_p - R_m).std() × √252

        Args:
            daily_returns: 전략의 일별 수익률 시리즈

        Returns:
            (alpha, beta, information_ratio, tracking_error) 튜플
        """
        bm = self._config.benchmark_returns

        if bm is None or len(bm) == 0 or len(daily_returns) == 0:
            return 0.0, 0.0, 0.0, 0.0

        # 인덱스 교집합으로 맞춤
        common_idx = daily_returns.index.intersection(bm.index)
        if len(common_idx) < 5:
            return 0.0, 0.0, 0.0, 0.0

        rp = daily_returns.loc[common_idx].values
        rm = bm.loc[common_idx].values

        # Beta = Cov(R_p, R_m) / Var(R_m)
        cov_matrix = np.cov(rp, rm)
        var_m = cov_matrix[1, 1]
        beta = cov_matrix[0, 1] / var_m if var_m > 1e-12 else 0.0

        # Alpha (Jensen's Alpha) — 연율화
        rf_daily = self._config.risk_free_rate / 252
        alpha_daily = np.mean(rp) - (rf_daily + beta * (np.mean(rm) - rf_daily))
        alpha = alpha_daily * 252

        # Tracking Error — 연율화
        active_returns = rp - rm
        tracking_error = float(np.std(active_returns, ddof=1) * np.sqrt(252))

        # Information Ratio
        info_ratio = 0.0
        if tracking_error > 1e-10:
            info_ratio = float(np.mean(active_returns) * 252 / tracking_error)

        return alpha, beta, info_ratio, tracking_error

    def _empty_result(self, strategy_name: str) -> BacktestResult:
        """빈 결과 반환"""
        return BacktestResult(
            strategy_name=strategy_name,
            config=self._config,
            start_date="",
            end_date="",
            initial_capital=self._config.initial_capital,
            final_capital=self._config.initial_capital,
            total_return=0.0,
            cagr=0.0,
            mdd=0.0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            calmar_ratio=0.0,
            win_rate=0.0,
            profit_factor=0.0,
            total_trades=0,
            avg_trade_return=0.0,
            max_consecutive_losses=0,
        )


class StrategyComparator:
    """
    전략 비교기 (F-07-02)

    복수 백테스트 결과를 비교하여 최적 전략/앙상블 구성 추천
    """

    @staticmethod
    def compare(results: list[BacktestResult]) -> pd.DataFrame:
        """
        백테스트 결과 비교 테이블 생성

        Returns:
            DataFrame with strategy names as index and metrics as columns
        """
        if not results:
            return pd.DataFrame()

        rows = []
        for r in results:
            rows.append(
                {
                    "strategy": r.strategy_name,
                    "total_return": r.total_return,
                    "cagr": r.cagr,
                    "mdd": r.mdd,
                    "sharpe": r.sharpe_ratio,
                    "sortino": r.sortino_ratio,
                    "calmar": r.calmar_ratio,
                    "alpha": r.alpha,
                    "beta": r.beta,
                    "info_ratio": r.information_ratio,
                    "tracking_error": r.tracking_error,
                    "win_rate": r.win_rate,
                    "profit_factor": r.profit_factor,
                    "total_trades": r.total_trades,
                    "max_consec_loss": r.max_consecutive_losses,
                }
            )

        df = pd.DataFrame(rows).set_index("strategy")
        return df.sort_values("sharpe", ascending=False)

    @staticmethod
    def recommend_weights(
        results: list[BacktestResult],
        method: str = "sharpe",
    ) -> dict[str, float]:
        """
        전략별 추천 가중치 산출

        Args:
            results: 백테스트 결과 리스트
            method: "sharpe" (샤프 비율 기반) 또는 "equal" (동일 가중)

        Returns:
            {strategy_name: weight} 딕셔너리
        """
        if not results:
            return {}

        if method == "equal":
            n = len(results)
            return {r.strategy_name: 1.0 / n for r in results}

        # Sharpe 기반 가중치 (음수 Sharpe는 0으로 처리)
        sharpes = {r.strategy_name: max(r.sharpe_ratio, 0.0) for r in results}
        total = sum(sharpes.values())

        if total < 1e-10:
            # 모든 Sharpe가 0 이하면 동일 가중
            n = len(results)
            return {r.strategy_name: 1.0 / n for r in results}

        return {name: s / total for name, s in sharpes.items()}


# ══════════════════════════════════════
# 유틸리티 함수
# ══════════════════════════════════════
def _max_consecutive(binary_list: list[int]) -> int:
    """리스트에서 연속 1의 최대 길이 반환"""
    max_count = 0
    current = 0
    for val in binary_list:
        if val == 1:
            current += 1
            max_count = max(max_count, current)
        else:
            current = 0
    return max_count
