"""
Phase 3 테스트: 전략 앙상블 엔진 (StrategyEnsembleEngine)

모든 외부 의존성 (DB, Redis)은 Mock으로 대체합니다.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from config.constants import RiskProfile, StrategyType
from core.strategy_ensemble.engine import (
    EnsembleSignal,
    StrategyEnsembleEngine,
    StrategySignalInput,
)


class TestStrategySignalInput:
    """StrategySignalInput 데이터 구조 테스트"""

    def test_creation(self):
        sig = StrategySignalInput(
            strategy="FACTOR", value=0.6, confidence=0.8, reason="test"
        )
        assert sig.strategy == "FACTOR"
        assert sig.value == 0.6


class TestEnsembleSignal:
    """EnsembleSignal 데이터 구조 테스트"""

    def test_action_buy(self):
        sig = EnsembleSignal(ticker="005930", final_signal=0.5, final_confidence=0.8)
        assert sig.action == "BUY"

    def test_action_sell(self):
        sig = EnsembleSignal(ticker="005930", final_signal=-0.5, final_confidence=0.8)
        assert sig.action == "SELL"

    def test_action_hold(self):
        sig = EnsembleSignal(ticker="005930", final_signal=0.1, final_confidence=0.5)
        assert sig.action == "HOLD"

    def test_to_dict(self):
        sig = EnsembleSignal(
            ticker="005930",
            final_signal=0.42,
            final_confidence=0.75,
            component_signals={"FACTOR": 0.6, "SENTIMENT": 0.3},
            weights_used={"FACTOR": 0.25, "SENTIMENT": 0.10},
            risk_profile="BALANCED",
        )
        d = sig.to_dict()
        assert d["ticker"] == "005930"
        assert d["final_signal"] == 0.42


class TestStrategyEnsembleEngine:
    """StrategyEnsembleEngine 핵심 로직 테스트"""

    @pytest.fixture
    def sample_signals(self):
        """테스트용 전략 시그널"""
        return [
            StrategySignalInput(
                strategy=StrategyType.FACTOR.value,
                value=0.7, confidence=0.85, reason="Factor composite=78",
            ),
            StrategySignalInput(
                strategy=StrategyType.MEAN_REVERSION.value,
                value=-0.2, confidence=0.6, reason="RSI=55",
            ),
            StrategySignalInput(
                strategy=StrategyType.TREND_FOLLOWING.value,
                value=0.5, confidence=0.75, reason="MA bullish",
            ),
            StrategySignalInput(
                strategy=StrategyType.RISK_PARITY.value,
                value=0.3, confidence=0.7, reason="Low vol",
            ),
            StrategySignalInput(
                strategy="SENTIMENT",
                value=0.45, confidence=0.8, reason="Positive news",
            ),
        ]

    @pytest.fixture
    def balanced_weights(self):
        return {
            "FACTOR": 0.25,
            "MEAN_REVERSION": 0.10,
            "TREND_FOLLOWING": 0.20,
            "RISK_PARITY": 0.20,
            "ML_SIGNAL": 0.00,
            "SENTIMENT": 0.25,
        }

    @pytest.mark.asyncio
    async def test_generate_ensemble_signal(self, sample_signals, balanced_weights):
        """앙상블 시그널 생성 테스트"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)
        engine._weights = balanced_weights

        with patch.object(engine, '_store_signal', new_callable=AsyncMock):
            result = await engine.generate_ensemble_signal("005930", sample_signals)

        assert isinstance(result, EnsembleSignal)
        assert result.ticker == "005930"
        assert -1.0 <= result.final_signal <= 1.0
        assert 0.0 <= result.final_confidence <= 1.0
        assert result.risk_profile == "BALANCED"
        assert "FACTOR" in result.component_signals
        assert "SENTIMENT" in result.component_signals

    @pytest.mark.asyncio
    async def test_ensemble_empty_signals(self, balanced_weights):
        """빈 시그널 입력 시 중립 반환"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)
        engine._weights = balanced_weights

        result = await engine.generate_ensemble_signal("005930", [])

        assert result.final_signal == 0.0
        assert result.final_confidence == 0.0

    @pytest.mark.asyncio
    async def test_ensemble_single_signal(self, balanced_weights):
        """단일 시그널만 있을 때"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)
        engine._weights = balanced_weights

        signals = [
            StrategySignalInput(
                strategy="SENTIMENT", value=0.8, confidence=0.9, reason="Strong positive"
            )
        ]

        with patch.object(engine, '_store_signal', new_callable=AsyncMock):
            result = await engine.generate_ensemble_signal("005930", signals)

        # SENTIMENT만 있으므로 해당 시그널 * confidence가 결과
        assert result.final_signal > 0
        assert result.action == "BUY" or result.action == "HOLD"

    @pytest.mark.asyncio
    async def test_ensemble_conflicting_signals(self, balanced_weights):
        """상충하는 시그널 처리 (정량: 매수 vs 감성: 매도)"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)
        engine._weights = balanced_weights

        signals = [
            StrategySignalInput(strategy="FACTOR", value=0.9, confidence=0.9, reason="Strong factor"),
            StrategySignalInput(strategy="TREND_FOLLOWING", value=0.7, confidence=0.8, reason="Uptrend"),
            StrategySignalInput(strategy="SENTIMENT", value=-0.8, confidence=0.9, reason="Bad news"),
        ]

        with patch.object(engine, '_store_signal', new_callable=AsyncMock):
            result = await engine.generate_ensemble_signal("005930", signals)

        # 상충 시그널이므로 절대값이 작아야 함
        assert abs(result.final_signal) < 0.8

    @pytest.mark.asyncio
    async def test_recalibrate_weights_sharpe(self, balanced_weights):
        """Sharpe 기반 가중치 재계산"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)
        engine._weights = balanced_weights

        performances = {
            "FACTOR": 1.5,
            "MEAN_REVERSION": 0.3,
            "TREND_FOLLOWING": 1.2,
            "RISK_PARITY": 0.8,
            "SENTIMENT": 1.0,
        }

        with patch.object(engine, '_save_weights_to_db', new_callable=AsyncMock), \
             patch.object(engine, '_cache_weights', new_callable=AsyncMock), \
             patch.object(engine, '_log_weight_update', new_callable=AsyncMock):

            new_weights = await engine.recalibrate_weights(performances, method="sharpe")

        # 가중치 합계 ≈ 1.0
        total = sum(v for v in new_weights.values() if v > 0)
        assert abs(total - 1.0) < 0.01

        # Sharpe가 높은 FACTOR가 가장 높은 가중치
        active = {k: v for k, v in new_weights.items() if v > 0}
        max_key = max(active, key=active.get)
        assert max_key == "FACTOR"

    @pytest.mark.asyncio
    async def test_recalibrate_weights_equal(self, balanced_weights):
        """동일 가중치 재계산"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)
        engine._weights = balanced_weights

        with patch.object(engine, '_save_weights_to_db', new_callable=AsyncMock), \
             patch.object(engine, '_cache_weights', new_callable=AsyncMock), \
             patch.object(engine, '_log_weight_update', new_callable=AsyncMock):

            new_weights = await engine.recalibrate_weights({}, method="equal")

        # 활성 전략끼리 동일 가중치
        active_weights = [v for v in new_weights.values() if v > 0]
        if active_weights:
            assert max(active_weights) - min(active_weights) < 0.01

    @pytest.mark.asyncio
    async def test_get_weights_default(self):
        """기본 가중치 로드 테스트"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)

        with patch.object(engine, '_get_cached_weights', return_value=None), \
             patch.object(engine, '_load_weights_from_db', return_value=None):

            weights = await engine.get_weights()

        assert "FACTOR" in weights
        assert "SENTIMENT" in weights
        assert sum(weights.values()) > 0.99

    @pytest.mark.asyncio
    async def test_batch_signals(self, sample_signals, balanced_weights):
        """배치 앙상블 시그널 생성"""
        engine = StrategyEnsembleEngine(RiskProfile.BALANCED)
        engine._weights = balanced_weights

        ticker_signals = {
            "005930": sample_signals,
            "000660": sample_signals[:3],
        }

        with patch.object(engine, '_store_signal', new_callable=AsyncMock):
            results = await engine.generate_batch_signals(ticker_signals)

        assert len(results) == 2
        assert "005930" in results
        assert "000660" in results