"""
전략 앙상블 엔진 (Strategy Ensemble Engine)

Phase 3 - F-03-03 구현:
- Quant Engine (4개 정량 전략) + AI Analyzer (감성 시그널) 통합
- 프로필별 가중 평균 앙상블 시그널 생성
- 하이브리드 가중치 관리: 정적 기본 + 월 1회 자동 재계산
- Backtest Engine 연동 → 성과 기반 가중치 피드백 루프
- 시장 레짐 감지 기반 동적 임계값 + 신뢰도 캘리브레이션 + 가중치 라우팅

아키텍처 흐름 (Investment Decision Pipeline 참조):
  Quant Engine  ──┐                    ┌── RegimeDetector
  AI Mode A     ──┼──→ Ensemble ←──────┤── DynamicThreshold
  AI Mode B     ──┘        ↑           ├── ConfidenceCalibrator
                    Backtest Engine     └── RegimeWeightRouter

사용 라이브러리: pandas 2.2.2, numpy 1.26.4
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from config.constants import (
    ENSEMBLE_DEFAULT_WEIGHTS,
    RiskProfile,
    StrategyType,
)
from config.logging import logger
from core.strategy_ensemble.regime import (
    ConfidenceCalibrator,
    DynamicThreshold,
    MarketRegime,
    MarketRegimeDetector,
    RegimeInfo,
    RegimeWeightRouter,
)
from db.database import RedisManager


# ══════════════════════════════════════
# 앙상블 입력/출력 데이터 구조
# ══════════════════════════════════════
@dataclass
class StrategySignalInput:
    """단일 전략의 시그널 입력"""

    strategy: str  # StrategyType.value 또는 "SENTIMENT"
    value: float  # -1.0 ~ +1.0
    confidence: float  # 0.0 ~ 1.0
    reason: str = ""


@dataclass
class EnsembleSignal:
    """앙상블 최종 시그널 출력"""

    ticker: str
    final_signal: float  # -1.0 ~ +1.0
    final_confidence: float  # 0.0 ~ 1.0
    component_signals: dict[str, float] = field(default_factory=dict)
    weights_used: dict[str, float] = field(default_factory=dict)
    risk_profile: str = "BALANCED"
    generated_at: Optional[datetime] = None
    # 레짐 기반 동적 판단
    regime: str = "SIDEWAYS"  # MarketRegime.value
    buy_threshold: float = 0.3
    sell_threshold: float = 0.3
    raw_confidence: float = 0.0  # 캘리브레이션 전 원본

    @property
    def action(self) -> str:
        """레짐 기반 동적 임계값으로 행동 분류"""
        if self.final_signal > self.buy_threshold:
            return "BUY"
        elif self.final_signal < -self.sell_threshold:
            return "SELL"
        return "HOLD"

    def to_dict(self) -> dict:
        """DB 저장용 딕셔너리"""
        return {
            "time": self.generated_at or datetime.now(timezone.utc),
            "ticker": self.ticker,
            "final_signal": self.final_signal,
            "final_confidence": self.final_confidence,
            "component_signals": json.dumps(self.component_signals),
            "weights_used": json.dumps(self.weights_used),
            "risk_profile": self.risk_profile,
        }

    def to_detailed_dict(self) -> dict:
        """상세 정보 (API 응답/모니터링용)"""
        d = self.to_dict()
        d.update(
            {
                "regime": self.regime,
                "buy_threshold": self.buy_threshold,
                "sell_threshold": self.sell_threshold,
                "raw_confidence": self.raw_confidence,
                "action": self.action,
            }
        )
        return d


# ══════════════════════════════════════
# 앙상블 엔진
# ══════════════════════════════════════
class StrategyEnsembleEngine:
    """
    전략 앙상블 엔진

    복수 전략 시그널을 가중 평균으로 통합하여
    최종 투자 시그널을 생성합니다.

    가중치 관리 방식 (하이브리드):
    - 기본: constants.py의 ENSEMBLE_DEFAULT_WEIGHTS 사용
    - DB: strategy_weights 테이블에서 로드 (수동 조정 가능)
    - 자동: 월 1회 백테스트 성과 기반 재계산 (recalibrate_weights)
    """

    WEIGHT_CACHE_PREFIX = "aqts:ensemble_weights:"

    def __init__(self, risk_profile: RiskProfile = RiskProfile.BALANCED):
        self._risk_profile = risk_profile
        self._weights: Optional[dict[str, float]] = None
        self._regime_detector = MarketRegimeDetector()
        self._dynamic_threshold = DynamicThreshold()
        self._confidence_calibrator = ConfidenceCalibrator()
        self._regime_router = RegimeWeightRouter()

    @property
    def risk_profile(self) -> RiskProfile:
        return self._risk_profile

    async def get_weights(self) -> dict[str, float]:
        """
        현재 앙상블 가중치 로드

        우선순위: Redis 캐시 → DB → constants 기본값
        """
        if self._weights:
            return self._weights.copy()

        # 1. Redis 캐시
        cached = await self._get_cached_weights()
        if cached:
            self._weights = cached
            return cached.copy()

        # 2. DB 조회
        db_weights = await self._load_weights_from_db()
        if db_weights:
            self._weights = db_weights
            await self._cache_weights(db_weights)
            return db_weights.copy()

        # 3. 기본값
        defaults = ENSEMBLE_DEFAULT_WEIGHTS.get(self._risk_profile, {})
        self._weights = {(k.value if isinstance(k, StrategyType) else k): v for k, v in defaults.items()}
        return self._weights.copy()

    async def generate_ensemble_signal(
        self,
        ticker: str,
        signals: list[StrategySignalInput],
        ohlcv: Optional[pd.DataFrame] = None,
    ) -> EnsembleSignal:
        """
        단일 종목 앙상블 시그널 생성

        레짐 감지 → 가중치 라우팅 → 가중 평균 → 동적 임계값 → 신뢰도 캘리브레이션

        Args:
            ticker: 종목코드
            signals: 전략별 시그널 리스트
            ohlcv: 시세 데이터 (있으면 레짐 감지에 사용, 없으면 기본 레짐)

        Returns:
            EnsembleSignal
        """
        weights = await self.get_weights()
        now = datetime.now(timezone.utc)

        if not signals:
            return EnsembleSignal(
                ticker=ticker,
                final_signal=0.0,
                final_confidence=0.0,
                risk_profile=self._risk_profile.value,
                generated_at=now,
            )

        # ── 1. 레짐 감지 ──
        if ohlcv is not None and len(ohlcv) >= 60:
            regime_info = self._regime_detector.detect(ohlcv)
        else:
            regime_info = RegimeInfo(
                regime=MarketRegime.SIDEWAYS,
                confidence=0.3,
                volatility_percentile=0.5,
                trend_strength=0.0,
                details={"reason": "no_ohlcv"},
            )

        # ── 2. 레짐 기반 가중치 라우팅 ──
        routed_weights = self._regime_router.adjust_weights(weights, regime_info)

        # 시그널 매핑
        signal_map: dict[str, StrategySignalInput] = {}
        for sig in signals:
            signal_map[sig.strategy] = sig

        # ── 3. 가중 평균 계산 ──
        weighted_sum = 0.0
        confidence_sum = 0.0
        total_weight = 0.0
        component_signals: dict[str, float] = {}

        for strategy_key, weight in routed_weights.items():
            if weight <= 0:
                continue

            sig = signal_map.get(strategy_key)
            if sig is None:
                continue

            # 신뢰도 가중: 낮은 신뢰도의 시그널은 중립(0) 쪽으로 감쇠
            adjusted_value = sig.value * sig.confidence
            weighted_sum += adjusted_value * weight
            confidence_sum += sig.confidence * weight
            total_weight += weight
            component_signals[strategy_key] = round(sig.value, 4)

        # 정규화
        if total_weight > 0:
            final_signal = weighted_sum / total_weight
            raw_confidence = confidence_sum / total_weight
        else:
            final_signal = 0.0
            raw_confidence = 0.0

        # 클리핑
        final_signal = max(-1.0, min(1.0, final_signal))
        raw_confidence = max(0.0, min(1.0, raw_confidence))

        # ── 4. 신뢰도 캘리브레이션 ──
        calibrated_confidence = self._confidence_calibrator.calibrate(
            raw_confidence=raw_confidence,
            component_signals=component_signals,
            regime_info=regime_info,
        )

        # ── 5. 동적 임계값 ──
        buy_threshold, sell_threshold = self._dynamic_threshold.compute(regime_info)

        result = EnsembleSignal(
            ticker=ticker,
            final_signal=round(final_signal, 4),
            final_confidence=round(calibrated_confidence, 4),
            component_signals=component_signals,
            weights_used={k: round(v, 4) for k, v in routed_weights.items() if v > 0},
            risk_profile=self._risk_profile.value,
            generated_at=now,
            regime=regime_info.regime.value,
            buy_threshold=buy_threshold,
            sell_threshold=sell_threshold,
            raw_confidence=round(raw_confidence, 4),
        )

        # DB 저장
        await self._store_signal(result)

        logger.debug(
            f"Ensemble signal: {ticker}, final={result.final_signal:.4f}, "
            f"action={result.action}, confidence={result.final_confidence:.4f} "
            f"(raw={raw_confidence:.4f}), regime={regime_info.regime.value}, "
            f"thresholds=±{buy_threshold:.3f}"
        )
        return result

    async def generate_batch_signals(
        self,
        ticker_signals: dict[str, list[StrategySignalInput]],
        ticker_ohlcv: Optional[dict[str, pd.DataFrame]] = None,
    ) -> dict[str, EnsembleSignal]:
        """
        복수 종목 배치 앙상블 시그널 생성

        Args:
            ticker_signals: {ticker: [StrategySignalInput]} 딕셔너리
            ticker_ohlcv: {ticker: OHLCV DataFrame} (레짐 감지용, optional)

        Returns:
            {ticker: EnsembleSignal} 딕셔너리
        """
        results: dict[str, EnsembleSignal] = {}
        ohlcv_map = ticker_ohlcv or {}

        for ticker, signals in ticker_signals.items():
            ohlcv = ohlcv_map.get(ticker)
            result = await self.generate_ensemble_signal(ticker, signals, ohlcv=ohlcv)
            results[ticker] = result

        logger.info(f"Batch ensemble complete: {len(results)} tickers, " f"profile={self._risk_profile.value}")
        return results

    # ══════════════════════════════════════
    # 가중치 재계산 (Backtest Feedback Loop)
    # ══════════════════════════════════════
    async def recalibrate_weights(
        self,
        strategy_performances: dict[str, float],
        method: str = "sharpe",
    ) -> dict[str, float]:
        """
        백테스트 성과 기반 가중치 자동 재계산

        Args:
            strategy_performances: {strategy_key: sharpe_ratio} 딕셔너리
            method: "sharpe" (샤프 비율 비례) 또는 "equal" (동일 가중)

        Returns:
            새 가중치 딕셔너리
        """
        old_weights = await self.get_weights()

        if method == "equal":
            active = {k: v for k, v in old_weights.items() if v > 0}
            n = max(len(active), 1)
            new_weights = {k: (1.0 / n if v > 0 else 0.0) for k, v in old_weights.items()}
        else:
            # Sharpe 비율 비례 가중치
            # 음수 Sharpe는 0으로 처리, 최소 가중치 0.05 보장
            positive_sharpes = {}
            for key, weight in old_weights.items():
                if weight <= 0:
                    continue
                perf = strategy_performances.get(key, 0.0)
                positive_sharpes[key] = max(perf, 0.0)

            total = sum(positive_sharpes.values())
            if total < 1e-10:
                # 모든 Sharpe가 0 이하 → 동일 가중
                n = max(len(positive_sharpes), 1)
                new_weights = {k: 1.0 / n for k in positive_sharpes}
            else:
                new_weights = {}
                for k, s in positive_sharpes.items():
                    raw_weight = s / total
                    # 최소 5%, 최대 40% 제한
                    new_weights[k] = max(0.05, min(0.40, raw_weight))

                # 정규화 (합계 = 1.0)
                total_new = sum(new_weights.values())
                new_weights = {k: v / total_new for k, v in new_weights.items()}

            # 비활성 전략 가중치 유지 (0)
            for key in old_weights:
                if key not in new_weights:
                    new_weights[key] = 0.0

        # 반올림
        new_weights = {k: round(v, 4) for k, v in new_weights.items()}

        # DB 저장 및 캐시 갱신
        await self._save_weights_to_db(new_weights)
        await self._cache_weights(new_weights)
        await self._log_weight_update(old_weights, new_weights, method, strategy_performances)

        self._weights = new_weights

        logger.info(f"Weights recalibrated ({method}): " f"{json.dumps(new_weights, indent=2)}")
        return new_weights

    # ══════════════════════════════════════
    # 내부: 가중치 캐시/DB
    # ══════════════════════════════════════
    async def _get_cached_weights(self) -> Optional[dict[str, float]]:
        """Redis에서 가중치 로드"""
        try:
            redis = RedisManager.get_client()
            key = f"{self.WEIGHT_CACHE_PREFIX}{self._risk_profile.value}"
            data = await redis.get(key)
            if data:
                return json.loads(data)
        except Exception:
            pass
        return None

    async def _cache_weights(self, weights: dict[str, float]) -> None:
        """Redis에 가중치 캐시 (24시간)"""
        try:
            redis = RedisManager.get_client()
            key = f"{self.WEIGHT_CACHE_PREFIX}{self._risk_profile.value}"
            await redis.setex(key, 86400, json.dumps(weights))
        except Exception as e:
            logger.debug(f"Weight cache set failed: {e}")

    async def _load_weights_from_db(self) -> Optional[dict[str, float]]:
        """PostgreSQL strategy_weights 테이블에서 가중치 로드"""
        try:
            from sqlalchemy import text

            from db.database import async_session_factory

            async with async_session_factory() as session:
                query = text(
                    """
                    SELECT strategy_type, weight
                    FROM strategy_weights
                    WHERE risk_profile = :profile
                """
                )
                rows = await session.execute(query, {"profile": self._risk_profile.value})
                data = rows.fetchall()

                if data:
                    return {row[0]: float(row[1]) for row in data}
        except Exception as e:
            logger.warning(f"Weight DB load failed: {e}")
        return None

    async def _save_weights_to_db(self, weights: dict[str, float]) -> None:
        """PostgreSQL strategy_weights 테이블에 가중치 저장 (UPSERT)"""
        try:
            from sqlalchemy import text

            from db.database import async_session_factory

            async with async_session_factory() as session:
                for strategy_type, weight in weights.items():
                    query = text(
                        """
                        INSERT INTO strategy_weights (strategy_type, weight, risk_profile, updated_at)
                        VALUES (:strategy_type, :weight, :profile, NOW())
                        ON CONFLICT (strategy_type, risk_profile) DO UPDATE SET
                            weight = :weight,
                            updated_at = NOW()
                    """
                    )
                    await session.execute(
                        query,
                        {
                            "strategy_type": strategy_type,
                            "weight": weight,
                            "profile": self._risk_profile.value,
                        },
                    )
                await session.commit()
        except Exception as e:
            logger.warning(f"Weight DB save failed: {e}")

    async def _store_signal(self, signal: EnsembleSignal) -> None:
        """앙상블 시그널 결과를 DB에 저장"""
        try:
            from sqlalchemy import text

            from db.database import async_session_factory

            async with async_session_factory() as session:
                data = signal.to_dict()
                query = text(
                    """
                    INSERT INTO ensemble_signals
                        (time, ticker, final_signal, final_confidence,
                         component_signals, weights_used, risk_profile)
                    VALUES
                        (:time, :ticker, :final_signal, :final_confidence,
                         :component_signals, :weights_used, :risk_profile)
                """
                )
                await session.execute(query, data)
                await session.commit()
        except Exception as e:
            logger.debug(f"Ensemble signal store failed for {signal.ticker}: {e}")

    async def _log_weight_update(
        self,
        old_weights: dict,
        new_weights: dict,
        method: str,
        performances: dict,
    ) -> None:
        """가중치 변경 이력 기록"""
        try:
            from sqlalchemy import text

            from db.database import async_session_factory

            async with async_session_factory() as session:
                query = text(
                    """
                    INSERT INTO weight_update_history
                        (risk_profile, old_weights, new_weights, method,
                         performance_metrics, reason)
                    VALUES
                        (:profile, :old_w, :new_w, :method, :perf, :reason)
                """
                )
                await session.execute(
                    query,
                    {
                        "profile": self._risk_profile.value,
                        "old_w": json.dumps(old_weights),
                        "new_w": json.dumps(new_weights),
                        "method": method,
                        "perf": json.dumps(performances),
                        "reason": f"Auto recalibration via {method}",
                    },
                )
                await session.commit()
        except Exception as e:
            logger.debug(f"Weight update log failed: {e}")
