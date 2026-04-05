"""
동적 앙상블 서비스 (Dynamic Ensemble Service)

run_backtest.py의 _compute_dynamic_ensemble() 알고리즘을
라이브 파이프라인에서 사용할 수 있도록 모듈화한 서비스.

핵심 알고리즘 (OOS Gate PASS 달성 검증 완료):
  1. ADX + 모멘텀 + 변동성 백분위 → 레짐 판정
  2. 레짐별 전략 가중치 할당 (TF/MR/RP)
  3. 60일 롤링 성과 기반 softmax 보정 (온도 5.0)
  4. 레짐 70% + 성과 30% 블렌딩
  5. 변동성 타겟팅 (연 25% 목표)

사용법:
    service = DynamicEnsembleService()
    result = service.compute(ohlcv_df, mr_signals, tf_signals, rp_signals)
    today_signal = result.ensemble_signal  # 최신 날짜의 앙상블 시그널
    today_regime = result.regime           # 현재 레짐
    today_weights = result.weights         # 현재 가중치 {MR, TF, RP}
"""

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd


class DynamicRegime(str, Enum):
    """동적 앙상블 레짐"""

    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    SIDEWAYS = "SIDEWAYS"


@dataclass
class DynamicEnsembleResult:
    """동적 앙상블 계산 결과"""

    ensemble_signal: float  # 최신 날짜의 앙상블 시그널 값
    regime: DynamicRegime  # 현재 레짐
    weights: dict[str, float]  # 현재 가중치 {MR, TF, RP}
    adx: float  # 현재 ADX
    vol_percentile: float  # 현재 변동성 백분위
    vol_scalar: float  # 변동성 타겟 스칼라
    ensemble_series: pd.Series  # 전체 앙상블 시계열 (백테스트/분석용)


# ── 레짐별 기본 가중치 ──
# OOS 검증을 거친 값. 변경 시 OOS 재검증 필수.
REGIME_WEIGHTS = {
    DynamicRegime.TRENDING_UP: {"TF": 0.55, "MR": 0.15, "RP": 0.30},
    DynamicRegime.TRENDING_DOWN: {"TF": 0.40, "MR": 0.15, "RP": 0.45},
    DynamicRegime.HIGH_VOLATILITY: {"TF": 0.20, "MR": 0.20, "RP": 0.60},
    DynamicRegime.SIDEWAYS: {"TF": 0.25, "MR": 0.45, "RP": 0.30},
}

# ── 앙상블 하이퍼파라미터 ──
# OOS 검증 완료. RL 도입 시 action space로 전환 예정.
DEFAULT_PARAMS = {
    "adx_threshold": 25,  # ADX > 25 → 추세 존재
    "vol_pct_threshold": 0.75,  # 변동성 상위 25% → HIGH_VOL
    "perf_window": 60,  # 성과 측정 윈도우 (영업일)
    "softmax_temperature": 5.0,  # softmax 온도 (높을수록 균등)
    "perf_blend": 0.3,  # 성과 블렌딩 비율 (0.3 = 30%)
    "target_vol": 0.25,  # 연환산 목표 변동성
    "min_window": 60,  # 최소 데이터 요구량
}


class DynamicEnsembleService:
    """
    동적 앙상블 서비스

    백테스트와 동일한 알고리즘으로 라이브 시그널을 생성.
    OHLCV + 3개 전략 시그널을 입력받아 레짐 기반 동적 가중 앙상블을 반환.
    """

    def __init__(self, params: dict | None = None):
        self._params = {**DEFAULT_PARAMS, **(params or {})}

    def compute(
        self,
        ohlcv: pd.DataFrame,
        mr_signal: pd.Series,
        tf_signal: pd.Series,
        rp_signal: pd.Series,
    ) -> DynamicEnsembleResult:
        """
        동적 앙상블 시그널 계산

        Args:
            ohlcv: OHLCV DataFrame (columns: open, high, low, close, volume)
            mr_signal: MEAN_REVERSION 시그널 시리즈
            tf_signal: TREND_FOLLOWING 시그널 시리즈
            rp_signal: RISK_PARITY 시그널 시리즈

        Returns:
            DynamicEnsembleResult: 최신 시그널, 레짐, 가중치 등
        """
        p = self._params
        close = ohlcv["close"].astype(float)
        high = ohlcv["high"].astype(float)
        low = ohlcv["low"].astype(float)
        dates = ohlcv.index

        # ── 1) 롤링 ADX 계산 (벡터화) ──
        adx = self._compute_adx(high, low, close)

        # ── 2) 롤링 변동성 백분위 ──
        returns = close.pct_change()
        rolling_vol = returns.rolling(20).std() * np.sqrt(252)
        vol_percentile = rolling_vol.expanding(min_periods=p["min_window"]).rank(pct=True).fillna(0.5)

        # ── 3) 모멘텀 (20일 수익률) ──
        momentum = close.pct_change(20).fillna(0.0)

        # ── 4) 레짐별 가중치 매핑 ──
        w_tf, w_mr, w_rp, regime_series = self._assign_regime_weights(
            adx, momentum, vol_percentile, dates, p["adx_threshold"], p["vol_pct_threshold"]
        )

        # ── 5) 롤링 성과 기반 softmax 보정 ──
        w_tf, w_mr, w_rp = self._apply_performance_adjustment(
            w_tf,
            w_mr,
            w_rp,
            mr_signal,
            tf_signal,
            rp_signal,
            returns,
            p["perf_window"],
            p["softmax_temperature"],
            p["perf_blend"],
        )

        # ── 6) 재정규화 + 동적 가중 합산 ──
        w_total = w_mr + w_tf + w_rp
        w_mr = w_mr / w_total
        w_tf = w_tf / w_total
        w_rp = w_rp / w_total

        ensemble = w_tf * tf_signal + w_mr * mr_signal + w_rp * rp_signal

        # ── 7) 변동성 타겟팅 ──
        current_vol = returns.rolling(20).std() * np.sqrt(252)
        current_vol = current_vol.fillna(p["target_vol"])
        vol_scalar = (p["target_vol"] / current_vol.replace(0, p["target_vol"])).clip(upper=1.0)
        ensemble = ensemble * vol_scalar

        # ── 최신 날짜 결과 추출 ──
        current_regime = regime_series.iloc[-1]
        current_weights = {
            "MR": float(w_mr.iloc[-1]),
            "TF": float(w_tf.iloc[-1]),
            "RP": float(w_rp.iloc[-1]),
        }

        return DynamicEnsembleResult(
            ensemble_signal=float(ensemble.iloc[-1]),
            regime=current_regime,
            weights=current_weights,
            adx=float(adx.iloc[-1]),
            vol_percentile=float(vol_percentile.iloc[-1]),
            vol_scalar=float(vol_scalar.iloc[-1]),
            ensemble_series=ensemble,
        )

    @staticmethod
    def _compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """ADX 계산 (EMA 기반 벡터화)"""
        prev_high = high.shift(1)
        prev_low = low.shift(1)
        prev_close = close.shift(1)

        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        plus_dm = high - prev_high
        minus_dm = prev_low - low
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

        alpha = 1.0 / period
        atr = tr.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
        safe_atr = atr.replace(0, np.nan)
        plus_di = 100.0 * plus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean() / safe_atr
        minus_di = 100.0 * minus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean() / safe_atr

        di_sum = (plus_di + minus_di).replace(0, np.nan)
        dx = 100.0 * (plus_di - minus_di).abs() / di_sum
        adx = dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean().fillna(0.0)
        return adx

    @staticmethod
    def _assign_regime_weights(
        adx: pd.Series,
        momentum: pd.Series,
        vol_percentile: pd.Series,
        dates: pd.DatetimeIndex,
        adx_threshold: float,
        vol_pct_threshold: float,
    ) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
        """레짐 판정 및 가중치 할당"""
        # 기본 가중치
        w_tf = pd.Series(0.40, index=dates)
        w_mr = pd.Series(0.30, index=dates)
        w_rp = pd.Series(0.30, index=dates)
        regime_series = pd.Series(DynamicRegime.SIDEWAYS, index=dates)

        # TRENDING_UP
        trend_up = (adx > adx_threshold) & (momentum > 0)
        w_tf = w_tf.where(~trend_up, 0.55)
        w_mr = w_mr.where(~trend_up, 0.15)
        w_rp = w_rp.where(~trend_up, 0.30)
        regime_series = regime_series.where(~trend_up, DynamicRegime.TRENDING_UP)

        # TRENDING_DOWN
        trend_down = (adx > adx_threshold) & (momentum < 0) & (~trend_up)
        w_tf = w_tf.where(~trend_down, 0.40)
        w_mr = w_mr.where(~trend_down, 0.15)
        w_rp = w_rp.where(~trend_down, 0.45)
        regime_series = regime_series.where(~trend_down, DynamicRegime.TRENDING_DOWN)

        # HIGH_VOLATILITY
        high_vol = (vol_percentile > vol_pct_threshold) & (adx <= adx_threshold) & (~trend_up) & (~trend_down)
        w_tf = w_tf.where(~high_vol, 0.20)
        w_mr = w_mr.where(~high_vol, 0.20)
        w_rp = w_rp.where(~high_vol, 0.60)
        regime_series = regime_series.where(~high_vol, DynamicRegime.HIGH_VOLATILITY)

        # SIDEWAYS (나머지)
        sideways = (~trend_up) & (~trend_down) & (~high_vol)
        w_tf = w_tf.where(~sideways, 0.25)
        w_mr = w_mr.where(~sideways, 0.45)
        w_rp = w_rp.where(~sideways, 0.30)

        return w_tf, w_mr, w_rp, regime_series

    @staticmethod
    def _apply_performance_adjustment(
        w_tf: pd.Series,
        w_mr: pd.Series,
        w_rp: pd.Series,
        mr_signal: pd.Series,
        tf_signal: pd.Series,
        rp_signal: pd.Series,
        returns: pd.Series,
        perf_window: int,
        temperature: float,
        blend: float,
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        """롤링 성과 기반 softmax 가중치 보정"""
        mr_perf = (mr_signal * returns).rolling(perf_window).sum().fillna(0.0)
        tf_perf = (tf_signal * returns).rolling(perf_window).sum().fillna(0.0)
        rp_perf = (rp_signal * returns).rolling(perf_window).sum().fillna(0.0)

        exp_mr = np.exp(mr_perf / temperature)
        exp_tf = np.exp(tf_perf / temperature)
        exp_rp = np.exp(rp_perf / temperature)
        exp_sum = exp_mr + exp_tf + exp_rp

        perf_adj_mr = exp_mr / exp_sum
        perf_adj_tf = exp_tf / exp_sum
        perf_adj_rp = exp_rp / exp_sum

        w_mr = w_mr * (1 - blend) + perf_adj_mr * blend
        w_tf = w_tf * (1 - blend) + perf_adj_tf * blend
        w_rp = w_rp * (1 - blend) + perf_adj_rp * blend

        return w_tf, w_mr, w_rp
