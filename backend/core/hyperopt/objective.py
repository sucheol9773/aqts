"""
하이퍼파라미터 최적화 목적 함수 (Objective Function)

파라미터 조합을 받아 walk-forward OOS Sharpe를 반환합니다.

흐름:
  1. 샘플된 앙상블 파라미터 → DynamicEnsembleService 생성
  2. 샘플된 레짐 가중치 → REGIME_WEIGHTS 오버라이드
  3. VectorizedSignalGenerator → 시그널 시계열
  4. 앙상블 → 날짜별 신호 시리즈
  5. Walk-forward 윈도우 분할 → BacktestEngine OOS 평가
  6. 윈도우 Sharpe 평균 반환
"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np
import pandas as pd

from config.logging import logger
from core.backtest_engine.engine import BacktestConfig, BacktestEngine
from core.hyperopt.search_space import SearchSpace
from core.quant_engine.vectorized_signals import VectorizedSignalGenerator
from core.strategy_ensemble.dynamic_ensemble import (
    DEFAULT_PARAMS,
    REGIME_WEIGHTS,
    DynamicEnsembleService,
    DynamicRegime,
)


class ObjectiveFunction:
    """
    Optuna 목적 함수

    사전 로드된 OHLCV 데이터에 대해
    walk-forward OOS Sharpe를 계산합니다.
    """

    def __init__(
        self,
        ohlcv_data: dict[str, pd.DataFrame],
        train_months: int = 24,
        test_months: int = 3,
        initial_capital: float = 50_000_000.0,
        min_window_days: int = 60,
        groups: Optional[list[str]] = None,
    ):
        """
        Args:
            ohlcv_data: {ticker: OHLCV DataFrame} 전종목 데이터
            train_months: 학습 기간 (개월)
            test_months: OOS 평가 기간 (개월)
            initial_capital: 초기 자본
            min_window_days: 최소 윈도우 거래일
            groups: 최적화할 파라미터 그룹 (None=전체)
        """
        self._ohlcv_data = ohlcv_data
        self._train_months = train_months
        self._test_months = test_months
        self._initial_capital = initial_capital
        self._min_window_days = min_window_days
        self._groups = groups

        # 시그널 사전 생성 (OHLCV → MR/TF/RP 시그널은 앙상블 파라미터에 무관)
        self._precomputed_signals: dict[str, dict[str, pd.Series]] = {}
        self._precompute_signals()

    def _precompute_signals(self) -> None:
        """벡터화 시그널 사전 계산 (trial 간 불변)"""
        gen = VectorizedSignalGenerator(min_window=60)
        for ticker, ohlcv in self._ohlcv_data.items():
            if len(ohlcv) >= 200:
                self._precomputed_signals[ticker] = gen.generate(ohlcv)

        logger.info(
            f"[Hyperopt] 시그널 사전계산 완료: " f"{len(self._precomputed_signals)}/{len(self._ohlcv_data)} 종목"
        )

    def __call__(self, trial) -> float:
        """
        Optuna 목적 함수 (최대화: OOS Sharpe)

        Args:
            trial: optuna.trial.Trial

        Returns:
            평균 OOS Sharpe ratio
        """
        start_time = time.time()

        # 1. 파라미터 샘플링
        params = SearchSpace.suggest_params(trial, groups=self._groups)
        config = SearchSpace.params_to_ensemble_config(params)

        # 2. 앙상블 서비스 생성 (커스텀 파라미터)
        ensemble_params = {**DEFAULT_PARAMS, **config["ensemble_params"]}
        regime_weights = self._build_regime_weights(config["regime_weights"])

        ensemble_svc = DynamicEnsembleService(params=ensemble_params)
        # 레짐 가중치 오버라이드 (내부 상수 대체)
        ensemble_svc._regime_weights_override = regime_weights

        risk_params = config["risk_params"]

        # 3. 종목별 앙상블 시그널 생성
        all_signals = {}
        all_prices = {}

        for ticker, ohlcv in self._ohlcv_data.items():
            if ticker not in self._precomputed_signals:
                continue

            signals = self._precomputed_signals[ticker]
            mr = signals["MEAN_REVERSION"]
            tf = signals["TREND_FOLLOWING"]
            rp = signals["RISK_PARITY"]

            # DynamicEnsembleService.compute는 내부에서 레짐 판정 + 가중 합산
            result = self._compute_ensemble_with_weights(ensemble_svc, ohlcv, mr, tf, rp, regime_weights)

            if result is not None:
                all_signals[ticker] = result
                all_prices[ticker] = ohlcv["close"]

        if not all_signals:
            return float("-inf")

        # 4. DataFrame 변환 (날짜 × 종목)
        signals_df = pd.DataFrame(all_signals)
        prices_df = pd.DataFrame(all_prices)

        # 공통 인덱스
        common_idx = signals_df.dropna(how="all").index.intersection(prices_df.dropna(how="all").index)
        if len(common_idx) < self._min_window_days * 2:
            return float("-inf")

        signals_df = signals_df.loc[common_idx]
        prices_df = prices_df.loc[common_idx]

        # 5. Walk-forward OOS 평가
        oos_sharpes = self._walk_forward_eval(signals_df, prices_df, risk_params)

        if not oos_sharpes:
            return float("-inf")

        # Pruning: 중간 결과 보고
        import optuna

        for step, sharpe in enumerate(oos_sharpes):
            trial.report(sharpe, step)
            if trial.should_prune():
                raise optuna.TrialPruned()

        avg_sharpe = float(np.mean(oos_sharpes))
        duration = time.time() - start_time

        logger.debug(
            f"[Hyperopt] Trial {trial.number}: "
            f"Sharpe={avg_sharpe:.3f} "
            f"({len(oos_sharpes)} windows, {duration:.1f}s)"
        )

        # 사용자 속성 저장 (결과 분석용)
        trial.set_user_attr("oos_sharpes", oos_sharpes)
        trial.set_user_attr("n_windows", len(oos_sharpes))
        trial.set_user_attr("sharpe_std", float(np.std(oos_sharpes)))
        trial.set_user_attr("duration_s", round(duration, 1))

        return avg_sharpe

    def _build_regime_weights(self, custom_weights: dict[str, dict]) -> dict:
        """기본 레짐 가중치에 커스텀 값을 오버라이드"""
        weights = {}
        for regime in DynamicRegime:
            if regime.value in custom_weights:
                weights[regime] = custom_weights[regime.value]
            else:
                weights[regime] = REGIME_WEIGHTS[regime]
        return weights

    def _compute_ensemble_with_weights(
        self,
        svc: DynamicEnsembleService,
        ohlcv: pd.DataFrame,
        mr_signal: pd.Series,
        tf_signal: pd.Series,
        rp_signal: pd.Series,
        custom_regime_weights: dict,
    ) -> Optional[pd.Series]:
        """커스텀 레짐 가중치로 앙상블 시계열 계산"""
        try:
            # DynamicEnsembleService.compute() 호출
            # 내부의 REGIME_WEIGHTS를 custom으로 대체하기 위해
            # 직접 알고리즘 수행
            p = svc._params
            close = ohlcv["close"]
            high = ohlcv["high"]
            low = ohlcv["low"]

            # ADX, 모멘텀, 변동성 계산
            adx = DynamicEnsembleService._compute_adx(high, low, close)
            momentum = close.pct_change(20).fillna(0.0)
            rolling_vol = close.pct_change().rolling(20).std() * np.sqrt(252)
            vol_percentile = rolling_vol.expanding(min_periods=p["min_window"]).rank(pct=True).fillna(0.5)

            dates = ohlcv.index

            # 레짐 판정 + 가중치 할당
            w_tf, w_mr, w_rp, regime_series = DynamicEnsembleService._assign_regime_weights(
                adx,
                momentum,
                vol_percentile,
                dates,
                p["adx_threshold"],
                p["vol_pct_threshold"],
            )

            # 커스텀 레짐 가중치 적용
            for regime_enum, wts in custom_regime_weights.items():
                mask = regime_series == regime_enum
                w_tf[mask] = wts["TF"]
                w_mr[mask] = wts["MR"]
                w_rp[mask] = wts["RP"]

            # 성과 기반 보정
            window = p["perf_window"]
            temp = p["softmax_temperature"]
            blend = p["perf_blend"]

            perf_mr = mr_signal.rolling(window).mean().fillna(0)
            perf_tf = tf_signal.rolling(window).mean().fillna(0)
            perf_rp = rp_signal.rolling(window).mean().fillna(0)

            perf_stack = pd.DataFrame({"TF": perf_tf, "MR": perf_mr, "RP": perf_rp})

            exp_perf = np.exp(perf_stack / temp)
            softmax_w = exp_perf.div(exp_perf.sum(axis=1), axis=0).fillna(1 / 3)

            # 블렌딩
            final_tf = (1 - blend) * w_tf + blend * softmax_w["TF"]
            final_mr = (1 - blend) * w_mr + blend * softmax_w["MR"]
            final_rp = (1 - blend) * w_rp + blend * softmax_w["RP"]

            # 앙상블 신호
            ensemble = final_tf * tf_signal + final_mr * mr_signal + final_rp * rp_signal

            # 변동성 타겟팅
            current_vol = rolling_vol.fillna(p["target_vol"])
            vol_scalar = (p["target_vol"] / current_vol.replace(0, p["target_vol"])).clip(upper=1.0)
            ensemble = ensemble * vol_scalar

            return ensemble

        except Exception as e:
            logger.warning(f"[Hyperopt] 앙상블 계산 실패: {e}")
            return None

    def _walk_forward_eval(
        self,
        signals_df: pd.DataFrame,
        prices_df: pd.DataFrame,
        risk_params: dict,
    ) -> list[float]:
        """Walk-forward OOS 평가: 윈도우별 Sharpe 리스트 반환"""
        dates = signals_df.index
        total_months = (dates[-1] - dates[0]).days / 30.44
        step_months = self._test_months
        train_months = self._train_months

        oos_sharpes = []
        offset_months = 0

        while offset_months + train_months + step_months <= total_months:
            # Train/Test 분할
            train_start = dates[0] + pd.DateOffset(months=offset_months)
            train_end = train_start + pd.DateOffset(months=train_months)
            test_start = train_end
            test_end = test_start + pd.DateOffset(months=step_months)

            # Test 구간 슬라이싱
            test_mask = (dates >= test_start) & (dates < test_end)
            test_signals = signals_df.loc[test_mask]
            test_prices = prices_df.loc[test_mask]

            if len(test_signals) < self._min_window_days // 2:
                offset_months += step_months
                continue

            # BacktestConfig with risk params
            config = BacktestConfig(
                initial_capital=self._initial_capital,
                start_date=str(test_signals.index[0].date()),
                end_date=str(test_signals.index[-1].date()),
                stop_loss_atr_multiplier=risk_params.get("stop_loss_atr_multiplier"),
                trailing_stop_atr_multiplier=risk_params.get("trailing_stop_atr_multiplier"),
                max_drawdown_limit=risk_params.get("max_drawdown_limit"),
                drawdown_cooldown_days=int(risk_params.get("drawdown_cooldown_days", 20)),
                dd_cushion_start=risk_params.get("dd_cushion_start"),
                dd_cushion_floor=risk_params.get("dd_cushion_floor", 0.25),
            )

            try:
                engine = BacktestEngine(config)
                result = engine.run(
                    strategy_name="ENSEMBLE_HYPEROPT",
                    signals=test_signals,
                    prices=test_prices,
                )
                oos_sharpes.append(float(result.sharpe_ratio))
            except Exception:
                oos_sharpes.append(0.0)

            offset_months += step_months

        return oos_sharpes

    def get_baseline_score(self) -> tuple[float, dict]:
        """
        현재 기본값으로 OOS Sharpe 계산 (기준선)

        Returns:
            (avg_oos_sharpe, default_params)
        """
        defaults = SearchSpace.get_defaults()
        config = SearchSpace.params_to_ensemble_config(defaults)

        ensemble_params = {**DEFAULT_PARAMS, **config["ensemble_params"]}
        regime_weights = self._build_regime_weights(config["regime_weights"])
        risk_params = config["risk_params"]

        ensemble_svc = DynamicEnsembleService(params=ensemble_params)

        all_signals = {}
        all_prices = {}

        for ticker, ohlcv in self._ohlcv_data.items():
            if ticker not in self._precomputed_signals:
                continue

            signals = self._precomputed_signals[ticker]
            result = self._compute_ensemble_with_weights(
                ensemble_svc,
                ohlcv,
                signals["MEAN_REVERSION"],
                signals["TREND_FOLLOWING"],
                signals["RISK_PARITY"],
                regime_weights,
            )
            if result is not None:
                all_signals[ticker] = result
                all_prices[ticker] = ohlcv["close"]

        if not all_signals:
            return 0.0, defaults

        signals_df = pd.DataFrame(all_signals)
        prices_df = pd.DataFrame(all_prices)

        common_idx = signals_df.dropna(how="all").index.intersection(prices_df.dropna(how="all").index)
        signals_df = signals_df.loc[common_idx]
        prices_df = prices_df.loc[common_idx]

        oos_sharpes = self._walk_forward_eval(signals_df, prices_df, risk_params)

        avg_sharpe = float(np.mean(oos_sharpes)) if oos_sharpes else 0.0
        return avg_sharpe, defaults
