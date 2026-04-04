"""
파라미터 민감도 분석 엔진

BacktestEngine을 재사용하여 파라미터 조합별 백테스트를 실행하고
결과를 수집합니다.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from config.logging import logger
from core.backtest_engine.engine import BacktestConfig, BacktestEngine

from .analyzer import SensitivityAnalyzer
from .models import (
    ParamRange,
    ParamTrialResult,
    SensitivityRun,
    SensitivityStatus,
    SweepMethod,
)
from .sweep_generator import DEFAULT_PARAM_RANGES, SweepGenerator


class ParamSensitivityEngine:
    """
    파라미터 민감도 분석 엔진

    OAT(One-at-a-Time) 또는 Grid/Random 스윕으로
    파라미터 조합별 BacktestEngine을 실행하고 탄성치를 계산합니다.
    """

    def __init__(
        self,
        param_ranges: Optional[list[ParamRange]] = None,
        sweep_method: SweepMethod = SweepMethod.GRID,
        max_trials: int = 500,
    ):
        self._param_ranges = param_ranges or DEFAULT_PARAM_RANGES
        self._sweep_method = sweep_method
        self._max_trials = max_trials

    def run(
        self,
        strategy_version: str,
        signals: pd.DataFrame,
        prices: pd.DataFrame,
        use_oat: bool = True,
    ) -> SensitivityRun:
        """
        민감도 분석 실행

        Args:
            strategy_version: 전략 버전 식별자
            signals: 시그널 DataFrame (dates × tickers)
            prices: 가격 DataFrame (dates × tickers)
            use_oat: True이면 OAT 방식, False이면 Grid/Random

        Returns:
            SensitivityRun
        """
        run_id = f"sens_{uuid.uuid4().hex[:12]}"
        sweep = SweepGenerator(
            param_ranges=self._param_ranges,
            method=self._sweep_method,
            max_trials=self._max_trials,
        )

        run = SensitivityRun(
            run_id=run_id,
            strategy_version=strategy_version,
            sweep_method=self._sweep_method,
            param_ranges=self._param_ranges,
            status=SensitivityStatus.RUNNING,
        )

        try:
            # 스윕 조합 생성
            if use_oat:
                trials = sweep.generate_one_at_a_time()
            else:
                trials = sweep.generate()

            run.total_trials = len(trials)
            logger.info(
                f"[{run_id}] Starting {len(trials)} trials (method={'OAT' if use_oat else self._sweep_method.value})"
            )

            # 기본값 백테스트 (baseline)
            base_result = self._run_single_trial(sweep.base_values, signals, prices)
            run.base_sharpe = base_result.sharpe_ratio

            # 모든 trial 실행
            for i, param_values in enumerate(trials):
                trial_result = self._run_single_trial(param_values, signals, prices)
                run.trial_results.append(trial_result)
                run.completed_trials = i + 1

                if (i + 1) % 50 == 0:
                    logger.info(f"[{run_id}] Progress: {i + 1}/{len(trials)}")

            # 최적 파라미터 찾기
            if run.trial_results:
                best = max(run.trial_results, key=lambda r: r.sharpe_ratio)
                run.best_params = best.param_values.copy()
                run.best_sharpe = best.sharpe_ratio

            # 탄성치 분석
            analyzer = SensitivityAnalyzer(
                param_ranges=self._param_ranges,
                base_values=sweep.base_values,
            )
            run.elasticities = analyzer.compute_elasticities(run.trial_results)

            run.status = SensitivityStatus.COMPLETED
            run.completed_at = datetime.now(timezone.utc)
            logger.info(
                f"[{run_id}] Completed: base_sharpe={run.base_sharpe:.4f}, "
                f"best_sharpe={run.best_sharpe:.4f}, "
                f"improvement={run.best_sharpe - run.base_sharpe:+.4f}"
            )

        except Exception as e:
            run.status = SensitivityStatus.ERROR
            run.error_message = str(e)
            logger.error(f"[{run_id}] Error: {e}")

        return run

    def _run_single_trial(
        self,
        param_values: dict[str, float],
        signals: pd.DataFrame,
        prices: pd.DataFrame,
    ) -> ParamTrialResult:
        """단일 파라미터 조합으로 백테스트 실행"""
        # 파라미터에서 BacktestConfig 관련 값 추출
        config = BacktestConfig(
            commission_rate=param_values.get("commission_rate"),
            slippage_rate=param_values.get("slippage_rate"),
        )

        engine = BacktestEngine(config)

        # 시그널 조정: 팩터 가중치/기술 파라미터에 따른 시그널 스케일링
        adjusted_signals = self._adjust_signals(signals, param_values)

        result = engine.run("sensitivity_trial", adjusted_signals, prices)

        return ParamTrialResult(
            param_values=param_values.copy(),
            sharpe_ratio=result.sharpe_ratio,
            cagr=result.cagr,
            mdd=result.mdd,
            sortino_ratio=result.sortino_ratio,
            calmar_ratio=result.calmar_ratio,
            profit_factor=result.profit_factor,
            win_rate=result.win_rate,
            total_trades=result.total_trades,
            total_return=result.total_return,
        )

    def _adjust_signals(
        self,
        signals: pd.DataFrame,
        param_values: dict[str, float],
    ) -> pd.DataFrame:
        """
        파라미터에 따라 시그널을 조정

        팩터 가중치 변경은 시그널 강도를 스케일링합니다.
        기술적 파라미터 변경은 시그널 임계값을 조정합니다.
        """
        adjusted = signals.copy()

        # 팩터 가중치 합계에 따른 시그널 스케일링
        factor_weights = {k: v for k, v in param_values.items() if k.startswith("factor_") and k.endswith("_weight")}
        if factor_weights:
            total_weight = sum(factor_weights.values())
            if total_weight > 0:
                # 기본 총 가중치(0.65) 대비 비율로 스케일링
                scale = total_weight / 0.65
                adjusted = adjusted * np.clip(scale, 0.5, 2.0)

        # RSI 임계값 조정: 임계값이 좁아지면 시그널이 강해짐
        overbought = param_values.get("rsi_overbought", 70)
        oversold = param_values.get("rsi_oversold", 30)
        threshold_width = (overbought - oversold) / 40.0  # 기본 40 기준 정규화
        if threshold_width > 0:
            adjusted = adjusted / np.clip(threshold_width, 0.5, 2.0)

        # 값 범위 클리핑
        adjusted = adjusted.clip(-1.0, 1.0)

        return adjusted
