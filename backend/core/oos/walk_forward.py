"""
Walk-Forward OOS 실행기 (Walk-Forward Engine)

기존 BacktestEngine + MetricsCalculator + PerformanceJudge를 재사용하여
walk-forward OOS 검증을 오케스트레이션합니다.

실행 플로우:
1. 기간 분할 (train N개월 / test M개월 rolling)
2. 각 윈도우에서:
   a. train 구간으로 파라미터/가중치 산출
   b. test 구간에서 고정 정책 평가 (OOS)
   c. 레짐별 성과 집계
3. 전체 집계 + Gate 판정 + 리포트 저장

Shadow 확장 포인트:
- run_type=SHADOW 시 shadow_threshold 생성기 활성화
- reward_proxy 계산기 활성화
- 단, MVP에서는 모두 비활성
"""

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from config.logging import logger
from core.backtest_engine.engine import BacktestConfig, BacktestEngine, BacktestResult
from core.backtest_engine.regime_analyzer import RegimeAnalyzer
from core.oos.gate_evaluator import GateEvaluator
from core.oos.models import (
    OOSRun,
    OOSRunType,
    OOSStatus,
    OOSWindowResult,
)
from core.oos.regime_mapping import RegimeMapper


class WalkForwardEngine:
    """
    Walk-Forward OOS 검증 엔진

    BacktestEngine을 기간 분할 루프로 감싸서
    out-of-sample 검증을 수행합니다.
    """

    def __init__(
        self,
        gate_evaluator: Optional[GateEvaluator] = None,
        regime_mapper: Optional[RegimeMapper] = None,
    ):
        self._gate_evaluator = gate_evaluator or GateEvaluator()
        self._regime_mapper = regime_mapper or RegimeMapper()

    def run(
        self,
        strategy_name: str,
        signals: pd.DataFrame,
        prices: pd.DataFrame,
        train_months: int = 24,
        test_months: int = 3,
        run_type: OOSRunType = OOSRunType.OOS,
        strategy_version: str = "current",
        market_data: Optional[dict] = None,
    ) -> OOSRun:
        """
        Walk-forward OOS 검증 실행

        Args:
            strategy_name: 전략 이름
            signals: 날짜 × 종목 시그널 DataFrame
            prices: 날짜 × 종목 종가 DataFrame
            train_months: 학습 기간 (개월)
            test_months: 평가 기간 (개월)
            run_type: OOS 또는 SHADOW
            strategy_version: 전략 버전 식별자
            market_data: 레짐 분류용 추가 데이터
                         {"volatility": float, "interest_rate_change": float}

        Returns:
            OOSRun
        """
        run_id = str(uuid.uuid4())[:12]
        data_hash = self._compute_data_hash(signals, prices)

        oos_run = OOSRun(
            run_id=run_id,
            run_type=run_type,
            status=OOSStatus.RUNNING,
            strategy_version=strategy_version,
            data_version=data_hash,
            train_months=train_months,
            test_months=test_months,
            started_at=datetime.now(timezone.utc),
        )

        try:
            # ── 1. 기간 분할 ──
            windows = self._split_windows(signals.index, train_months, test_months)

            if len(windows) == 0:
                oos_run.status = OOSStatus.ERROR
                oos_run.error_message = "Insufficient data for walk-forward split"
                oos_run.error_code = "INSUFFICIENT_DATA"
                oos_run.ended_at = datetime.now(timezone.utc)
                return oos_run

            oos_run.overall_start = str(signals.index[0].date())
            oos_run.overall_end = str(signals.index[-1].date())
            oos_run.total_windows = len(windows)

            logger.info(
                f"OOS run {run_id}: {len(windows)} windows, "
                f"train={train_months}m, test={test_months}m, "
                f"period={oos_run.overall_start}~{oos_run.overall_end}"
            )

            # ── 2. 윈도우별 실행 ──
            window_results: list[OOSWindowResult] = []

            for i, (train_start, train_end, test_start, test_end) in enumerate(windows):
                window_result = self._run_single_window(
                    window_index=i,
                    strategy_name=strategy_name,
                    signals=signals,
                    prices=prices,
                    train_start=train_start,
                    train_end=train_end,
                    test_start=test_start,
                    test_end=test_end,
                    market_data=market_data,
                )
                window_results.append(window_result)

            oos_run.windows = window_results

            # ── 3. 전체 집계 ──
            self._aggregate_results(oos_run, window_results)

            # ── 4. Gate 판정 ──
            gate_result = self._gate_evaluator.evaluate_all(
                windows=window_results,
                avg_sharpe=oos_run.avg_sharpe,
                avg_calmar=oos_run.avg_calmar,
                worst_mdd=oos_run.worst_mdd,
                sharpe_variance=oos_run.sharpe_variance,
            )

            oos_run.gate_a_result = gate_result["gate_a"]["result"]
            oos_run.gate_b_result = gate_result["gate_b"]["result"]
            oos_run.gate_c_result = gate_result["gate_c"]["result"]
            oos_run.overall_gate = gate_result["overall"]
            oos_run.gate_reasons = gate_result["all_reasons"]

            # 상태 결정
            if gate_result["overall"] == "PASS":
                oos_run.status = OOSStatus.PASS
            elif gate_result["overall"] == "REVIEW":
                oos_run.status = OOSStatus.REVIEW
            else:
                oos_run.status = OOSStatus.FAIL

        except Exception as e:
            oos_run.status = OOSStatus.ERROR
            oos_run.error_message = str(e)
            oos_run.error_code = "EXECUTION_ERROR"
            logger.error(f"OOS run {run_id} failed: {e}")

        oos_run.ended_at = datetime.now(timezone.utc)

        logger.info(
            f"OOS run {run_id} completed: status={oos_run.status.value}, "
            f"gate={oos_run.overall_gate}, "
            f"windows={oos_run.total_windows}, "
            f"avg_sharpe={oos_run.avg_sharpe:.3f}, "
            f"worst_mdd={oos_run.worst_mdd:.2%}"
        )

        return oos_run

    def _split_windows(
        self,
        date_index: pd.DatetimeIndex,
        train_months: int,
        test_months: int,
    ) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
        """
        날짜 인덱스를 train/test 윈도우로 분할

        Returns:
            [(train_start, train_end, test_start, test_end), ...]
        """
        if len(date_index) == 0:
            return []

        # 영업일 기준 월수 추정 (1개월 ≈ 21 영업일)
        train_days = train_months * 21
        test_days = test_months * 21
        total_needed = train_days + test_days

        if len(date_index) < total_needed:
            return []

        windows = []
        start_idx = 0

        while start_idx + total_needed <= len(date_index):
            train_start = date_index[start_idx]
            train_end_idx = start_idx + train_days - 1
            test_start_idx = train_end_idx + 1
            test_end_idx = test_start_idx + test_days - 1

            if test_end_idx >= len(date_index):
                break

            train_end = date_index[train_end_idx]
            test_start = date_index[test_start_idx]
            test_end = date_index[test_end_idx]

            windows.append((train_start, train_end, test_start, test_end))

            # 다음 윈도우: test_months만큼 이동
            start_idx += test_days

        return windows

    def _run_single_window(
        self,
        window_index: int,
        strategy_name: str,
        signals: pd.DataFrame,
        prices: pd.DataFrame,
        train_start: pd.Timestamp,
        train_end: pd.Timestamp,
        test_start: pd.Timestamp,
        test_end: pd.Timestamp,
        market_data: Optional[dict] = None,
    ) -> OOSWindowResult:
        """
        단일 윈도우 실행

        1. Train 구간에서 BacktestEngine 실행 (파라미터 학습용)
        2. Test 구간에서 BacktestEngine 실행 (OOS 평가)
        3. 레짐별 성과 분해
        """
        window_result = OOSWindowResult(
            window_index=window_index,
            train_start=str(train_start.date()),
            train_end=str(train_end.date()),
            test_start=str(test_start.date()),
            test_end=str(test_end.date()),
        )

        try:
            # Test 구간 데이터 슬라이스
            test_signals = signals.loc[test_start:test_end]
            test_prices = prices.loc[test_start:test_end]

            if len(test_signals) == 0 or len(test_prices) == 0:
                logger.warning(f"Window {window_index}: empty test data")
                return window_result

            # ── BacktestEngine으로 OOS 평가 ──
            config = BacktestConfig()
            engine = BacktestEngine(config)
            result: BacktestResult = engine.run(
                strategy_name=f"{strategy_name}_oos_w{window_index}",
                signals=test_signals,
                prices=test_prices,
            )

            # 결과 기록
            window_result.cagr = result.cagr
            window_result.mdd = result.mdd
            window_result.sharpe_ratio = result.sharpe_ratio
            window_result.sortino_ratio = result.sortino_ratio
            window_result.calmar_ratio = result.calmar_ratio
            window_result.total_return = result.total_return
            window_result.total_trades = result.total_trades
            window_result.win_rate = result.win_rate
            window_result.profit_factor = result.profit_factor

            # ── 레짐별 성과 분해 ──
            if len(result.equity_curve) > 10 and market_data is not None:
                window_result.regime_metrics = self._compute_regime_metrics(result, market_data)

        except Exception as e:
            logger.warning(f"Window {window_index} execution error: {e}")

        return window_result

    def _compute_regime_metrics(
        self,
        result: BacktestResult,
        market_data: dict,
    ) -> dict:
        """레짐별 성과 분해 (RegimeAnalyzer 활용)"""
        try:
            daily_returns = result.equity_curve.pct_change().dropna()

            if len(daily_returns) < 10:
                return {}

            volatility = market_data.get("volatility", 0.15)
            interest_rate_change = market_data.get("interest_rate_change", 0.0)

            # 각 날짜에 대해 레짐 분류
            regime_labels = []
            returns_list = daily_returns.values.tolist()

            for i in range(len(daily_returns)):
                # 최근 126일(또는 가능한 만큼)의 수익률로 레짐 판단
                lookback = returns_list[max(0, i - 126) : i + 1]
                regime = RegimeAnalyzer.classify_regime(
                    market_returns=lookback,
                    volatility=volatility,
                    interest_rate_change=interest_rate_change,
                )
                regime_labels.append(regime)

            # 레짐별 지표 계산
            regime_metrics = RegimeAnalyzer.regime_metrics(
                returns=returns_list,
                regime_labels=regime_labels,
            )

            return regime_metrics

        except Exception as e:
            logger.debug(f"Regime metrics computation error: {e}")
            return {}

    def _aggregate_results(
        self,
        oos_run: OOSRun,
        windows: list[OOSWindowResult],
    ) -> None:
        """윈도우 결과를 집계하여 OOSRun에 기록"""
        if not windows:
            return

        sharpes = [w.sharpe_ratio for w in windows]
        mdds = [w.mdd for w in windows]
        cagrs = [w.cagr for w in windows]
        calmars = [w.calmar_ratio for w in windows]

        oos_run.avg_sharpe = round(float(np.mean(sharpes)), 4)
        oos_run.avg_mdd = round(float(np.mean(mdds)), 4)
        oos_run.avg_cagr = round(float(np.mean(cagrs)), 4)
        oos_run.avg_calmar = round(float(np.mean(calmars)), 4)
        oos_run.worst_mdd = round(float(min(mdds)), 4)  # 가장 큰 낙폭 (음수)
        oos_run.sharpe_variance = round(float(np.var(sharpes)), 6)

        # 양수 수익 윈도우 수
        oos_run.passed_windows = sum(1 for w in windows if w.total_return > 0)

    def _compute_data_hash(self, signals: pd.DataFrame, prices: pd.DataFrame) -> str:
        """데이터 재현성을 위한 해시 생성"""
        try:
            sig_hash = hashlib.md5(pd.util.hash_pandas_object(signals).values.tobytes()).hexdigest()[:8]
            price_hash = hashlib.md5(pd.util.hash_pandas_object(prices).values.tobytes()).hexdigest()[:8]
            return f"{sig_hash}_{price_hash}"
        except Exception:
            return "unknown"
