"""
RL 실시간 추론 서비스 (Real-Time Inference Pipeline)

학습된 RL 모델을 사용하여 실시간 포지션 시그널을 생성합니다.

주요 기능:
- Champion 모델 자동 로드 + 캐싱
- 실시간 관찰(observation) 구성: OHLCV → 특성 → 모델 입력
- 포지션 시그널 생성: [-1, +1] 연속값 → 매매 결정
- 앙상블 시그널과 결합: RL 가중치 + 전통 앙상블 가중치 블렌딩
- 주문 변환: 포지션 → 매수/매도 수량 계산
- Shadow 모드: 실제 주문 없이 시그널만 기록

사용법:
    service = RLInferenceService(registry_dir="models/registry")
    service.load_model()

    # 단일 종목 시그널
    signal = service.predict(ticker="005930", ohlcv=ohlcv_df)

    # 전 종목 배치 추론
    signals = await service.predict_batch(ohlcv_dict, portfolio_value=50_000_000)

    # 주문 변환
    orders = service.signals_to_orders(signals, current_positions, portfolio_value)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from config.logging import logger
from core.rl.config import RLConfig
from core.rl.environment import TradingEnv
from core.rl.model_registry import ModelMetadata, ModelRegistry


@dataclass
class RLSignal:
    """RL 추론 시그널"""

    ticker: str
    position: float  # [-1, +1]
    confidence: float  # 행동 확률/분포 기반 신뢰도
    timestamp: str = ""

    # 관찰 공간 요약
    returns_5d: float = 0.0
    volatility_20d: float = 0.0
    current_drawdown: float = 0.0

    # 메타
    model_version: str = ""
    algorithm: str = ""

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "position": round(self.position, 4),
            "confidence": round(self.confidence, 4),
            "timestamp": self.timestamp,
            "returns_5d": round(self.returns_5d, 6),
            "volatility_20d": round(self.volatility_20d, 6),
            "current_drawdown": round(self.current_drawdown, 4),
            "model_version": self.model_version,
            "algorithm": self.algorithm,
        }


@dataclass
class OrderIntent:
    """주문 의도 (추론 결과 → 실행 전 단계)"""

    ticker: str
    side: str  # "BUY" | "SELL" | "HOLD"
    quantity: int = 0
    reason: str = ""
    rl_position: float = 0.0
    current_position_qty: int = 0
    target_position_qty: int = 0


@dataclass
class BatchInferenceResult:
    """배치 추론 결과"""

    signals: dict[str, RLSignal] = field(default_factory=dict)
    orders: list[OrderIntent] = field(default_factory=list)
    model_version: str = ""
    inference_time_ms: float = 0.0
    ticker_count: int = 0
    error_count: int = 0
    errors: dict[str, str] = field(default_factory=dict)
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "model_version": self.model_version,
            "inference_time_ms": round(self.inference_time_ms, 1),
            "ticker_count": self.ticker_count,
            "error_count": self.error_count,
            "timestamp": self.timestamp,
            "signals": {k: v.to_dict() for k, v in self.signals.items()},
            "orders": [
                {
                    "ticker": o.ticker,
                    "side": o.side,
                    "quantity": o.quantity,
                    "reason": o.reason,
                }
                for o in self.orders
            ],
        }


class RLInferenceService:
    """
    RL 실시간 추론 서비스

    학습된 모델로 포지션 시그널을 생성하고,
    앙상블 시그널과 블렌딩하여 최종 매매 결정을 내립니다.
    """

    def __init__(
        self,
        registry_dir: str = "models/registry",
        rl_weight: float = 0.4,
        ensemble_weight: float = 0.6,
        shadow_mode: bool = False,
        min_position_change: float = 0.05,
    ):
        """
        Args:
            registry_dir: 모델 레지스트리 디렉토리
            rl_weight: RL 시그널 가중치 (기본 0.4)
            ensemble_weight: 전통 앙상블 시그널 가중치 (기본 0.6)
            shadow_mode: True면 주문 없이 시그널만 기록
            min_position_change: 최소 포지션 변경 임계값
        """
        self.registry = ModelRegistry(registry_dir)
        self.rl_weight = rl_weight
        self.ensemble_weight = ensemble_weight
        self.shadow_mode = shadow_mode
        self.min_position_change = min_position_change

        # 캐시
        self._model = None
        self._model_meta: ModelMetadata | None = None
        self._config: RLConfig | None = None

    def load_model(self, version: str | None = None) -> bool:
        """
        모델 로드 (champion 또는 특정 버전)

        Args:
            version: 특정 버전 (None이면 champion)

        Returns:
            성공 여부
        """
        try:
            if version:
                result = self.registry.load_version(version)
            else:
                result = self.registry.load_champion()

            if result is None:
                logger.warning("[RLInference] No model available")
                return False

            self._model, self._model_meta = result

            # 설정 복원
            if self._model_meta.config_snapshot:
                self._config = RLConfig(**self._model_meta.config_snapshot)
            else:
                self._config = RLConfig()

            logger.info(
                f"[RLInference] Model loaded: {self._model_meta.version} "
                f"({self._model_meta.algorithm}, sharpe={self._model_meta.oos_sharpe:.4f})"
            )
            return True

        except Exception as e:
            logger.error(f"[RLInference] Model load failed: {e}")
            return False

    def predict(
        self,
        ticker: str,
        ohlcv: pd.DataFrame,
        portfolio_value: float = 0.0,
        current_drawdown: float = 0.0,
        cash_ratio: float = 1.0,
    ) -> RLSignal | None:
        """
        단일 종목 추론

        Args:
            ticker: 종목 코드
            ohlcv: OHLCV DataFrame (최소 lookback_window + 1일)
            portfolio_value: 현재 포트폴리오 가치
            current_drawdown: 현재 drawdown
            cash_ratio: 현금 비율

        Returns:
            RLSignal 또는 실패 시 None
        """
        if self._model is None:
            logger.warning("[RLInference] No model loaded")
            return None

        config = self._config or RLConfig()

        if len(ohlcv) < config.lookback_window + 10:
            logger.warning(
                f"[RLInference] {ticker}: insufficient data " f"({len(ohlcv)} < {config.lookback_window + 10})"
            )
            return None

        try:
            # 환경 생성 (추론 전용 — reset만 하고 마지막 step의 obs를 가져옴)
            env = TradingEnv(ohlcv, config)

            # 마지막 시점의 observation 구성
            obs = env.reset()[0]

            # 시계열 끝까지 진행하여 최신 obs 획득
            n_steps = len(env.close) - env.start_step - 1
            for _ in range(n_steps):
                # 무행동(0)으로 진행하여 최신 상태까지 이동
                obs, _, terminated, truncated, _ = env.step(np.array([0.0]))
                if terminated or truncated:
                    break

            # 포트폴리오 상태 오버라이드 (실제 계좌 상태 반영)
            if portfolio_value > 0:
                obs[8] = np.float32((portfolio_value - config.initial_capital) / config.initial_capital)
            obs[9] = np.float32(current_drawdown)
            obs[10] = np.float32(cash_ratio)

            # 추론
            action, _states = self._model.predict(obs, deterministic=True)
            position = float(np.clip(action[0], -1.0, 1.0))

            # 신뢰도 추정 (action 분포의 std로 추정 — PPO의 경우)
            confidence = self._estimate_confidence(obs)

            return RLSignal(
                ticker=ticker,
                position=position,
                confidence=confidence,
                timestamp=datetime.now(timezone.utc).isoformat(),
                returns_5d=float(obs[0]),
                volatility_20d=float(obs[1]),
                current_drawdown=float(obs[9]),
                model_version=self._model_meta.version if self._model_meta else "",
                algorithm=self._model_meta.algorithm if self._model_meta else "",
            )

        except Exception as e:
            logger.error(f"[RLInference] {ticker} prediction failed: {e}")
            return None

    def predict_batch(
        self,
        ohlcv_dict: dict[str, pd.DataFrame],
        portfolio_value: float = 50_000_000.0,
        current_positions: dict[str, int] | None = None,
        current_drawdown: float = 0.0,
        cash_ratio: float = 1.0,
    ) -> BatchInferenceResult:
        """
        배치 추론 (전 종목)

        Args:
            ohlcv_dict: {ticker: OHLCV DataFrame}
            portfolio_value: 포트폴리오 총 가치
            current_positions: {ticker: 보유 수량}
            current_drawdown: 현재 drawdown
            cash_ratio: 현금 비율

        Returns:
            BatchInferenceResult
        """
        import time

        start_time = time.time()
        result = BatchInferenceResult(
            model_version=(self._model_meta.version if self._model_meta else "none"),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        if self._model is None:
            if not self.load_model():
                result.errors["_global"] = "No model available"
                result.error_count = len(ohlcv_dict)
                return result

        for ticker, ohlcv in ohlcv_dict.items():
            signal = self.predict(
                ticker=ticker,
                ohlcv=ohlcv,
                portfolio_value=portfolio_value,
                current_drawdown=current_drawdown,
                cash_ratio=cash_ratio,
            )

            if signal is not None:
                result.signals[ticker] = signal
            else:
                result.error_count += 1
                result.errors[ticker] = "prediction_failed"

        result.ticker_count = len(ohlcv_dict)

        # 주문 변환
        if current_positions is not None and not self.shadow_mode:
            result.orders = self.signals_to_orders(
                result.signals,
                current_positions,
                portfolio_value,
                ohlcv_dict,
            )

        result.inference_time_ms = (time.time() - start_time) * 1000

        logger.info(
            f"[RLInference] Batch: {len(result.signals)}/{result.ticker_count} "
            f"tickers, {len(result.orders)} orders, "
            f"{result.inference_time_ms:.0f}ms"
        )

        return result

    def signals_to_orders(
        self,
        signals: dict[str, RLSignal],
        current_positions: dict[str, int],
        portfolio_value: float,
        ohlcv_dict: dict[str, pd.DataFrame] | None = None,
    ) -> list[OrderIntent]:
        """
        시그널 → 주문 변환

        Args:
            signals: {ticker: RLSignal}
            current_positions: {ticker: 현재 보유 수량}
            portfolio_value: 포트폴리오 총 가치
            ohlcv_dict: 현재가 조회용

        Returns:
            주문 리스트
        """
        orders = []

        for ticker, signal in signals.items():
            current_qty = current_positions.get(ticker, 0)

            # 현재가 추출
            current_price = 0.0
            if ohlcv_dict and ticker in ohlcv_dict:
                current_price = float(ohlcv_dict[ticker]["close"].iloc[-1])

            if current_price <= 0:
                continue

            # 목표 포지션 금액 = 포지션 비율 × 포트폴리오 가치
            # position > 0 → 매수, position < 0 → 매도
            target_value = signal.position * portfolio_value
            target_qty = int(target_value / current_price) if current_price > 0 else 0

            # 포지션 변동 계산
            qty_diff = target_qty - current_qty

            # 최소 변동 임계 체크
            if current_price > 0:
                value_change = abs(qty_diff * current_price)
                change_ratio = value_change / portfolio_value
                if change_ratio < self.min_position_change:
                    continue

            if qty_diff > 0:
                orders.append(
                    OrderIntent(
                        ticker=ticker,
                        side="BUY",
                        quantity=qty_diff,
                        reason=f"rl_position={signal.position:.3f}",
                        rl_position=signal.position,
                        current_position_qty=current_qty,
                        target_position_qty=target_qty,
                    )
                )
            elif qty_diff < 0:
                orders.append(
                    OrderIntent(
                        ticker=ticker,
                        side="SELL",
                        quantity=abs(qty_diff),
                        reason=f"rl_position={signal.position:.3f}",
                        rl_position=signal.position,
                        current_position_qty=current_qty,
                        target_position_qty=target_qty,
                    )
                )

        return orders

    def blend_with_ensemble(
        self,
        rl_signal: float,
        ensemble_signal: float,
    ) -> float:
        """
        RL 시그널과 앙상블 시그널 블렌딩

        Args:
            rl_signal: RL 포지션 [-1, 1]
            ensemble_signal: 앙상블 시그널 [-1, 1]

        Returns:
            블렌딩된 시그널 [-1, 1]
        """
        blended = self.rl_weight * rl_signal + self.ensemble_weight * ensemble_signal
        return float(np.clip(blended, -1.0, 1.0))

    def _estimate_confidence(self, obs: np.ndarray) -> float:
        """
        추론 신뢰도 추정

        PPO: 정책 분포의 entropy를 기반으로 신뢰도 추정
        SAC: action log_prob 기반

        Returns:
            0.0 ~ 1.0 신뢰도
        """
        try:
            import torch

            obs_tensor = torch.FloatTensor(obs).unsqueeze(0)
            with torch.no_grad():
                dist = self._model.policy.get_distribution(obs_tensor)
                entropy = dist.entropy().item()

            # entropy가 낮을수록 높은 신뢰도
            # 일반적으로 연속 Gaussian 정책의 entropy 범위: ~0.5 ~ ~3.0
            confidence = max(0.0, min(1.0, 1.0 - entropy / 3.0))
            return confidence
        except Exception:
            return 0.5  # 추정 불가 시 중립

    @property
    def is_loaded(self) -> bool:
        """모델 로드 여부"""
        return self._model is not None

    @property
    def model_version(self) -> str:
        """현재 로드된 모델 버전"""
        return self._model_meta.version if self._model_meta else ""
