"""
시장 레짐 감지 및 동적 임계값/캘리브레이션 모듈

실전 운용 관점의 3가지 핵심 개선:

1. MarketRegimeDetector:
   - 변동성/추세/모멘텀을 복합 측정하여 4가지 레짐으로 분류
   - TRENDING_UP, TRENDING_DOWN, SIDEWAYS, HIGH_VOLATILITY

2. DynamicThreshold:
   - 고정 ±0.3 대신 레짐별 동적 임계값 산출
   - 고변동장에서는 임계값 확대 (노이즈 필터링)
   - 추세장에서는 임계값 축소 (빠른 진입)

3. ConfidenceCalibrator:
   - 과신(overconfidence) 방지를 위한 시그널 신뢰도 보정
   - 시그널 일치도 + 변동성 상태 기반 감쇠
   - 극단 시그널일수록 보수적 조정

사용법:
    detector = MarketRegimeDetector()
    regime = detector.detect(ohlcv_df)

    threshold = DynamicThreshold()
    buy_t, sell_t = threshold.compute(regime)

    calibrator = ConfidenceCalibrator()
    adjusted = calibrator.calibrate(raw_confidence, signals, regime)
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from config.logging import logger


# ══════════════════════════════════════
# 시장 레짐 정의
# ══════════════════════════════════════
class MarketRegime(str, Enum):
    """시장 레짐 분류"""

    TRENDING_UP = "TRENDING_UP"  # 상승 추세
    TRENDING_DOWN = "TRENDING_DOWN"  # 하락 추세
    SIDEWAYS = "SIDEWAYS"  # 횡보/박스권
    HIGH_VOLATILITY = "HIGH_VOLATILITY"  # 고변동 (방향 불명)
    CRISIS = "CRISIS"  # 위기 (급락 + 고변동 + 상관관계 급등)


@dataclass
class RegimeInfo:
    """레짐 감지 결과"""

    regime: MarketRegime
    confidence: float  # 레짐 판단 확신도 (0~1)
    volatility_percentile: float  # 변동성 백분위 (0~1)
    trend_strength: float  # 추세 강도 (-1~+1, 양수=상승)
    details: dict  # 세부 지표


# ══════════════════════════════════════
# 1. 시장 레짐 감지기
# ══════════════════════════════════════
class MarketRegimeDetector:
    """
    복합 지표 기반 시장 레짐 감지

    판단 기준:
    - 변동성: 60일 변동성의 2년 백분위
    - 추세: ADX + 20/60 MA 관계
    - 모멘텀: 20일 수익률 방향성

    레짐 판정 로직:
    1. 변동성 75백분위 초과 + 추세 약함 → HIGH_VOLATILITY
    2. ADX > 25 + 20MA > 60MA → TRENDING_UP
    3. ADX > 25 + 20MA < 60MA → TRENDING_DOWN
    4. 나머지 → SIDEWAYS
    """

    # 레짐 판정 임계값
    VOL_HIGH_PERCENTILE = 0.75
    ADX_TREND_THRESHOLD = 25.0
    MIN_DATA_POINTS = 120  # 최소 6개월
    # CRISIS 레짐 임계값
    CRISIS_VOL_PERCENTILE = 0.90  # 변동성 90백분위 초과
    CRISIS_MOMENTUM_THRESHOLD = -0.10  # 20일 수익률 -10% 이하
    CRISIS_DRAWDOWN_THRESHOLD = -0.15  # 60일 고점 대비 -15% 이하

    def detect(self, ohlcv: pd.DataFrame) -> RegimeInfo:
        """
        OHLCV 데이터에서 현재 시장 레짐을 감지

        Args:
            ohlcv: DataFrame (open, high, low, close, volume 컬럼 필수)

        Returns:
            RegimeInfo
        """
        if len(ohlcv) < 60:
            return RegimeInfo(
                regime=MarketRegime.SIDEWAYS,
                confidence=0.3,
                volatility_percentile=0.5,
                trend_strength=0.0,
                details={"reason": "insufficient_data"},
            )

        close = ohlcv["close"].astype(float)
        high = ohlcv["high"].astype(float)
        low = ohlcv["low"].astype(float)

        # ── 변동성 측정 ──
        returns = close.pct_change().dropna()
        vol_20d = returns.tail(20).std() * np.sqrt(252)
        vol_60d = returns.tail(60).std() * np.sqrt(252)

        # 변동성 백분위 (전체 기간 대비)
        rolling_vol = returns.rolling(20).std() * np.sqrt(252)
        rolling_vol = rolling_vol.dropna()
        if len(rolling_vol) > 0:
            vol_percentile = float((rolling_vol < vol_20d).sum() / len(rolling_vol))
        else:
            vol_percentile = 0.5

        # ── 추세 측정 (ADX 근사) ──
        adx = self._compute_adx(high, low, close, period=14)

        # ── MA 관계 ──
        ma20 = close.rolling(20).mean().iloc[-1]
        ma60 = close.rolling(60).mean().iloc[-1]
        ma_spread = (ma20 - ma60) / ma60 if ma60 > 0 else 0.0

        # ── 모멘텀 ──
        momentum_20d = float((close.iloc[-1] / close.iloc[-20] - 1.0) if len(close) >= 20 else 0.0)

        # ── 추세 강도 ──
        trend_strength = np.clip(ma_spread * 10.0, -1.0, 1.0)

        # ── 60일 고점 대비 하락폭 (CRISIS 감지용) ──
        high_60d = close.tail(60).max()
        dd_from_60d_high = (close.iloc[-1] / high_60d - 1.0) if high_60d > 0 else 0.0

        # ── 레짐 판정 ──
        regime, regime_confidence = self._classify_regime(
            vol_percentile=vol_percentile,
            adx=adx,
            ma_spread=ma_spread,
            momentum=momentum_20d,
            trend_strength=trend_strength,
            drawdown_from_60d_high=dd_from_60d_high,
        )

        details = {
            "vol_20d": round(vol_20d, 4),
            "vol_60d": round(vol_60d, 4),
            "vol_percentile": round(vol_percentile, 4),
            "adx": round(adx, 2),
            "ma20": round(ma20, 2),
            "ma60": round(ma60, 2),
            "ma_spread": round(ma_spread, 4),
            "momentum_20d": round(momentum_20d, 4),
            "dd_from_60d_high": round(dd_from_60d_high, 4),
        }

        logger.debug(
            f"MarketRegime: {regime.value} (conf={regime_confidence:.2f}), "
            f"vol_pct={vol_percentile:.2f}, adx={adx:.1f}, "
            f"trend={trend_strength:.2f}"
        )

        return RegimeInfo(
            regime=regime,
            confidence=round(regime_confidence, 4),
            volatility_percentile=round(vol_percentile, 4),
            trend_strength=round(trend_strength, 4),
            details=details,
        )

    def _compute_adx(self, high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
        """ADX (Average Directional Index) 계산"""
        if len(close) < period * 2:
            return 0.0

        prev_high = high.shift(1)
        prev_low = low.shift(1)
        prev_close = close.shift(1)

        # True Range
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # +DM / -DM
        plus_dm = high - prev_high
        minus_dm = prev_low - low
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

        # Smoothed (Wilder's)
        atr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        plus_di = 100.0 * (
            plus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean() / atr.replace(0, np.nan)
        )
        minus_di = 100.0 * (
            minus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean() / atr.replace(0, np.nan)
        )

        # DX → ADX
        dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        adx = dx.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

        last_adx = adx.iloc[-1]
        return float(last_adx) if not pd.isna(last_adx) else 0.0

    def _classify_regime(
        self,
        vol_percentile: float,
        adx: float,
        ma_spread: float,
        momentum: float,
        trend_strength: float,
        drawdown_from_60d_high: float = 0.0,
    ) -> tuple[MarketRegime, float]:
        """레짐 분류 + 확신도"""
        # Rule 0: CRISIS — 극단적 변동성 + 급락 + 깊은 낙폭
        # 위기 시 모든 자산 상관관계가 1에 수렴하므로 최대한 보수적 대응
        crisis_signals = 0
        if vol_percentile > self.CRISIS_VOL_PERCENTILE:
            crisis_signals += 1
        if momentum < self.CRISIS_MOMENTUM_THRESHOLD:
            crisis_signals += 1
        if drawdown_from_60d_high < self.CRISIS_DRAWDOWN_THRESHOLD:
            crisis_signals += 1
        if crisis_signals >= 2:
            conf = min(0.7 + crisis_signals * 0.1, 0.95)
            return MarketRegime.CRISIS, conf

        # Rule 1: 고변동 + 추세 약함
        if vol_percentile > self.VOL_HIGH_PERCENTILE and adx < self.ADX_TREND_THRESHOLD:
            conf = min(vol_percentile, 0.95)
            return MarketRegime.HIGH_VOLATILITY, conf

        # Rule 2: 강한 추세
        if adx >= self.ADX_TREND_THRESHOLD:
            if ma_spread > 0 and momentum > 0:
                conf = min(0.5 + adx / 100.0, 0.95)
                return MarketRegime.TRENDING_UP, conf
            elif ma_spread < 0 and momentum < 0:
                conf = min(0.5 + adx / 100.0, 0.95)
                return MarketRegime.TRENDING_DOWN, conf

        # Rule 3: 약한 추세 + 방향 있음
        if abs(trend_strength) > 0.3 and abs(momentum) > 0.03:
            if trend_strength > 0:
                return MarketRegime.TRENDING_UP, 0.5
            else:
                return MarketRegime.TRENDING_DOWN, 0.5

        # 기본: 횡보
        return MarketRegime.SIDEWAYS, 0.6


# ══════════════════════════════════════
# 2. 동적 임계값
# ══════════════════════════════════════
class DynamicThreshold:
    """
    레짐/변동성 기반 동적 매수/매도 임계값

    기존: 고정 BUY > 0.3, SELL < -0.3
    개선: 레짐에 따라 임계값을 조절

    - 추세장(상승/하락): 임계값 축소 → 빠른 진입/청산
    - 횡보장: 임계값 유지 → 기본 기준
    - 고변동장: 임계값 확대 → 노이즈 필터링

    추가로 변동성 백분위에 따라 연속적 보정.
    """

    # 레짐별 기본 임계값
    REGIME_THRESHOLDS: dict[MarketRegime, float] = {
        MarketRegime.TRENDING_UP: 0.20,
        MarketRegime.TRENDING_DOWN: 0.20,
        MarketRegime.SIDEWAYS: 0.30,
        MarketRegime.HIGH_VOLATILITY: 0.40,
        MarketRegime.CRISIS: 0.50,  # 위기 시 매우 보수적 (높은 임계값)
    }

    # 변동성 보정 계수 (vol_percentile 0.5 기준)
    VOL_ADJUSTMENT_FACTOR = 0.15

    def compute(self, regime_info: RegimeInfo) -> tuple[float, float]:
        """
        동적 임계값 산출

        Args:
            regime_info: 레짐 감지 결과

        Returns:
            (buy_threshold, sell_threshold) — 둘 다 양수, 시그널 비교 시 부호 고려
            예: buy if signal > buy_threshold, sell if signal < -sell_threshold
        """
        base = self.REGIME_THRESHOLDS.get(regime_info.regime, 0.30)

        # 변동성 연속 보정: 높은 변동성 → 임계값 상향
        vol_adj = (regime_info.volatility_percentile - 0.5) * self.VOL_ADJUSTMENT_FACTOR
        adjusted = base + vol_adj

        # 레짐 확신도 블렌딩: 확신이 낮으면 기본값(0.3)에 가깝게
        blended = adjusted * regime_info.confidence + 0.30 * (1.0 - regime_info.confidence)

        # 범위 제한: 0.10 ~ 0.50
        final = max(0.10, min(0.50, blended))

        logger.debug(
            f"DynamicThreshold: regime={regime_info.regime.value}, "
            f"base={base:.2f}, vol_adj={vol_adj:.3f}, "
            f"blended={blended:.3f}, final={final:.3f}"
        )

        # 매수/매도 동일 임계값 (비대칭 설정 가능하도록 tuple 반환)
        return round(final, 4), round(final, 4)

    def classify_action(self, signal: float, regime_info: RegimeInfo) -> str:
        """
        시그널 + 레짐 기반 액션 분류

        Args:
            signal: 앙상블 최종 시그널 (-1 ~ +1)
            regime_info: 레짐 감지 결과

        Returns:
            "BUY", "SELL", "HOLD"
        """
        buy_threshold, sell_threshold = self.compute(regime_info)

        if signal > buy_threshold:
            return "BUY"
        elif signal < -sell_threshold:
            return "SELL"
        return "HOLD"


# ══════════════════════════════════════
# 3. 신뢰도 캘리브레이션
# ══════════════════════════════════════
class ConfidenceCalibrator:
    """
    앙상블 신뢰도 과신(overconfidence) 보정

    문제:
    - 개별 전략이 모두 높은 confidence를 주면 앙상블도 과도하게 높아짐
    - 실제로는 전략 간 상관관계, 시장 불확실성을 감안해야 함

    보정 방법:
    1. 시그널 불일치 페널티: 전략 간 방향이 다르면 confidence 감소
    2. 변동성 페널티: 고변동장에서 confidence 보수적 조정
    3. 극단값 감쇠: confidence > 0.9를 자연로그 감쇠로 조정
    4. 전략 수 보정: 시그널이 적으면 confidence 하향
    """

    # 불일치 페널티 강도
    DISAGREEMENT_PENALTY = 0.3

    # 변동성 페널티 강도
    VOLATILITY_PENALTY = 0.2

    # 극단값 감쇠 시작점
    EXTREME_THRESHOLD = 0.85

    # 최소 전략 수 (이하면 페널티)
    MIN_STRATEGIES = 3

    def calibrate(
        self,
        raw_confidence: float,
        component_signals: dict[str, float],
        regime_info: Optional[RegimeInfo] = None,
    ) -> float:
        """
        Raw confidence를 보정하여 calibrated confidence 반환

        Args:
            raw_confidence: 앙상블 산출 원본 신뢰도 (0~1)
            component_signals: {전략: 시그널값} 딕셔너리
            regime_info: 레짐 정보 (없으면 보정 축소)

        Returns:
            보정된 confidence (0~1)
        """
        if not component_signals:
            return max(0.0, min(raw_confidence * 0.5, 1.0))

        calibrated = raw_confidence

        # ── 1. 시그널 불일치 페널티 ──
        signal_values = list(component_signals.values())
        disagreement = self._compute_disagreement(signal_values)
        calibrated *= 1.0 - disagreement * self.DISAGREEMENT_PENALTY

        # ── 2. 변동성 페널티 ──
        if regime_info is not None:
            vol_penalty = max(0, regime_info.volatility_percentile - 0.5) * self.VOLATILITY_PENALTY
            calibrated *= 1.0 - vol_penalty

            # 고변동 레짐 추가 감쇠
            if regime_info.regime == MarketRegime.HIGH_VOLATILITY:
                calibrated *= 0.85

        # ── 3. 극단값 감쇠 (로그 압축) ──
        if calibrated > self.EXTREME_THRESHOLD:
            excess = calibrated - self.EXTREME_THRESHOLD
            # 로그 감쇠: 0.85 이상 구간을 완만하게 압축
            compressed = self.EXTREME_THRESHOLD + excess * np.log1p(excess) / np.log1p(1.0 - self.EXTREME_THRESHOLD)
            calibrated = min(compressed, 0.95)  # 상한 0.95

        # ── 4. 전략 수 보정 ──
        n_strategies = len(signal_values)
        if n_strategies < self.MIN_STRATEGIES:
            calibrated *= n_strategies / self.MIN_STRATEGIES

        return round(max(0.0, min(1.0, calibrated)), 4)

    def _compute_disagreement(self, signals: list[float]) -> float:
        """
        전략 간 불일치도 계산 (0=완전 일치, 1=완전 불일치)

        - 모든 시그널이 같은 부호 → 불일치 0
        - 절반이 반대 → 불일치 ~0.5
        - 방향 균등 분포 → 불일치 ~1.0
        """
        if len(signals) <= 1:
            return 0.0

        positive = sum(1 for s in signals if s > 0.05)
        negative = sum(1 for s in signals if s < -0.05)
        total = len(signals)

        if total == 0:
            return 0.0

        # 소수 방향의 비율 (0 = 완전 일치, 0.5 = 완전 불일치)
        minority_ratio = min(positive, negative) / total
        return minority_ratio * 2.0  # 0~1로 스케일


# ══════════════════════════════════════
# 4. 레짐 기반 전략 가중치 라우팅
# ══════════════════════════════════════
class RegimeWeightRouter:
    """
    레짐별 전략 가중치 자동 조절

    추세장: 추세추종 + 모멘텀 가중 확대
    횡보장: 평균회귀 + 팩터 가중 확대
    고변동장: 리스크패리티 가중 확대, 전체 축소

    기본 가중치에 레짐 승수(multiplier)를 적용한 뒤 재정규화합니다.
    """

    # 레짐별 전략 승수 (1.0 = 변경 없음)
    REGIME_MULTIPLIERS: dict[MarketRegime, dict[str, float]] = {
        MarketRegime.TRENDING_UP: {
            "FACTOR": 0.8,
            "MEAN_REVERSION": 0.5,  # 추세장에서 역추세 전략 약화
            "TREND_FOLLOWING": 1.5,  # 추세추종 강화
            "RISK_PARITY": 0.9,
            "ML_SIGNAL": 1.0,
            "SENTIMENT": 1.2,  # 감성 약간 강화
        },
        MarketRegime.TRENDING_DOWN: {
            "FACTOR": 0.7,
            "MEAN_REVERSION": 0.6,
            "TREND_FOLLOWING": 1.4,  # 하락 추세에서도 추세추종 유효
            "RISK_PARITY": 1.3,  # 리스크 관리 강화
            "ML_SIGNAL": 1.0,
            "SENTIMENT": 1.1,
        },
        MarketRegime.SIDEWAYS: {
            "FACTOR": 1.2,  # 팩터 유효
            "MEAN_REVERSION": 1.5,  # 평균회귀 강화
            "TREND_FOLLOWING": 0.6,  # 추세추종 약화
            "RISK_PARITY": 1.0,
            "ML_SIGNAL": 1.0,
            "SENTIMENT": 1.0,
        },
        MarketRegime.HIGH_VOLATILITY: {
            "FACTOR": 0.7,
            "MEAN_REVERSION": 0.8,
            "TREND_FOLLOWING": 0.7,
            "RISK_PARITY": 1.8,  # 리스크패리티 크게 강화
            "ML_SIGNAL": 1.0,
            "SENTIMENT": 0.6,  # 감성 약화 (뉴스 과반응 가능)
        },
        MarketRegime.CRISIS: {
            "FACTOR": 0.3,  # 위기 시 팩터 대부분 무력화
            "MEAN_REVERSION": 0.2,  # 역추세 위험 (낙폭 더 확대 가능)
            "TREND_FOLLOWING": 1.5,  # 하락 추세 추종만 유효
            "RISK_PARITY": 2.0,  # 리스크 관리 최우선
            "ML_SIGNAL": 0.5,
            "SENTIMENT": 0.3,  # 뉴스 기반 판단 최소화
        },
    }

    def adjust_weights(
        self,
        base_weights: dict[str, float],
        regime_info: RegimeInfo,
    ) -> dict[str, float]:
        """
        레짐에 따라 기본 가중치를 조절

        Args:
            base_weights: 원본 가중치 {strategy: weight}
            regime_info: 레짐 감지 결과

        Returns:
            레짐 조절된 가중치 (합계 1.0, 원본 비활성 전략은 유지)
        """
        multipliers = self.REGIME_MULTIPLIERS.get(regime_info.regime, {})

        # 승수 적용
        adjusted = {}
        for strategy, weight in base_weights.items():
            if weight <= 0:
                adjusted[strategy] = 0.0
                continue

            mult = multipliers.get(strategy, 1.0)

            # 레짐 확신도에 따라 조절 강도 블렌딩
            # 확신이 낮으면 승수를 1.0에 가깝게
            blended_mult = mult * regime_info.confidence + 1.0 * (1.0 - regime_info.confidence)
            adjusted[strategy] = weight * blended_mult

        # 재정규화 (합계 1.0)
        total = sum(adjusted.values())
        if total > 0:
            adjusted = {k: v / total for k, v in adjusted.items()}

        # 최소 가중치 보장 (활성 전략은 최소 3%)
        for k, v in adjusted.items():
            if base_weights.get(k, 0) > 0 and v < 0.03:
                adjusted[k] = 0.03

        # 재정규화
        total = sum(adjusted.values())
        if total > 0:
            adjusted = {k: round(v / total, 4) for k, v in adjusted.items()}

        logger.debug(f"RegimeWeightRouter: {regime_info.regime.value} → " f"adjusted_weights={adjusted}")

        return adjusted
