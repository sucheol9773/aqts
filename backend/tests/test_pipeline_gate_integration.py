"""
Pipeline ↔ Gate/StateMachine/FallbackHandler 통합 테스트

InvestmentDecisionPipeline이 Gate 평가 결과(PASS/BLOCK)를 올바르게
수집하고, BLOCK 시 FallbackHandler를 통한 상태 전이가 정상 작동하는지
검증합니다.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.fallback_handler import FallbackHandler
from core.gate_registry import GateRegistry
from core.gates import DataGate
from core.gates.base import GateDecision, GateSeverity
from core.pipeline import (
    InvestmentDecisionPipeline,
    PipelineResult,
    _build_default_gate_registry,
)
from core.state_machine import PipelineState, PipelineStateMachine
from core.strategy_ensemble.engine import EnsembleSignal


# ────────────────────────────────────────────
# 헬퍼: Mock 객체 팩토리
# ────────────────────────────────────────────
def _make_mock_sentiment(score=0.6, confidence=0.7, summary="positive"):
    """SentimentResult 대체 Mock"""
    m = MagicMock()
    m.score = score
    m.confidence = confidence
    m.summary = summary
    m.to_dict.return_value = {"score": score, "summary": summary}
    m.to_signal_value.return_value = score
    return m


def _make_mock_opinion(action_value="BUY", conviction=0.8, signal_value=0.7):
    """InvestmentOpinion 대체 Mock"""
    m = MagicMock()
    m.action.value = action_value
    m.conviction = conviction
    m.to_signal_value.return_value = signal_value
    return m


def _make_mock_ensemble_signal(
    ticker="005930",
    final_signal=0.65,
    final_confidence=0.8,
    weights=None,
):
    """EnsembleSignal Mock"""
    return EnsembleSignal(
        ticker=ticker,
        final_signal=final_signal,
        final_confidence=final_confidence,
        weights_used=weights or {"TREND_FOLLOWING": 0.3, "MEAN_REVERSION": 0.3, "SENTIMENT": 0.4},
    )


class TestDefaultGateRegistry(unittest.TestCase):
    """_build_default_gate_registry 헬퍼 검증"""

    def test_registers_four_analysis_gates(self):
        registry = _build_default_gate_registry()
        assert len(registry) == 4
        assert registry.get("DataGate") is not None
        assert registry.get("FactorGate") is not None
        assert registry.get("SignalGate") is not None
        assert registry.get("EnsembleGate") is not None

    def test_execution_order(self):
        registry = _build_default_gate_registry()
        assert registry.gate_ids == ["DataGate", "FactorGate", "SignalGate", "EnsembleGate"]


class TestPipelineInit(unittest.TestCase):
    """Pipeline 생성자 Gate 계층 주입 검증"""

    @patch("core.pipeline.NewsCollectorService")
    @patch("core.pipeline.SentimentAnalyzer")
    @patch("core.pipeline.OpinionGenerator")
    @patch("core.pipeline.SignalGenerator")
    @patch("core.pipeline.StrategyEnsembleEngine")
    def test_default_gate_layer(self, *_mocks):
        """Gate 계층을 주입하지 않으면 기본 구성이 적용됩니다."""
        pipeline = InvestmentDecisionPipeline()
        assert isinstance(pipeline._sm, PipelineStateMachine)
        assert isinstance(pipeline._registry, GateRegistry)
        assert isinstance(pipeline._fallback, FallbackHandler)
        assert len(pipeline._registry) == 4

    @patch("core.pipeline.NewsCollectorService")
    @patch("core.pipeline.SentimentAnalyzer")
    @patch("core.pipeline.OpinionGenerator")
    @patch("core.pipeline.SignalGenerator")
    @patch("core.pipeline.StrategyEnsembleEngine")
    def test_custom_gate_layer_injection(self, *_mocks):
        """외부에서 Gate 계층을 주입할 수 있습니다."""
        sm = PipelineStateMachine()
        registry = GateRegistry()
        registry.register(DataGate())
        fallback = FallbackHandler(sm)

        pipeline = InvestmentDecisionPipeline(
            state_machine=sm,
            gate_registry=registry,
            fallback_handler=fallback,
        )
        assert pipeline._sm is sm
        assert pipeline._registry is registry
        assert pipeline._fallback is fallback
        assert len(pipeline._registry) == 1  # DataGate만


@pytest.mark.smoke
class TestPipelineFullAnalysisAllPass(unittest.IsolatedAsyncioTestCase):
    """모든 Gate가 PASS일 때 전체 흐름 검증"""

    async def asyncSetUp(self):
        # 외부 서비스 Mock
        self.news_mock = AsyncMock()
        self.news_mock.get_articles_for_ticker = AsyncMock(
            return_value=[
                {"title": "뉴스1"},
                {"title": "뉴스2"},
            ]
        )

        self.sentiment_mock = AsyncMock()
        self.sentiment_mock.analyze_ticker = AsyncMock(return_value=_make_mock_sentiment())

        self.opinion_mock = AsyncMock()
        self.opinion_mock.generate_stock_opinion = AsyncMock(return_value=_make_mock_opinion())

        self.ensemble_mock = AsyncMock()
        self.ensemble_result = _make_mock_ensemble_signal()
        self.ensemble_mock.generate_ensemble_signal = AsyncMock(return_value=self.ensemble_result)

    async def _run_pipeline(self, gate_registry=None):
        sm = PipelineStateMachine()
        registry = gate_registry or _build_default_gate_registry()
        fallback = FallbackHandler(sm)

        with (
            patch("core.pipeline.NewsCollectorService", return_value=self.news_mock),
            patch("core.pipeline.SentimentAnalyzer", return_value=self.sentiment_mock),
            patch("core.pipeline.OpinionGenerator", return_value=self.opinion_mock),
            patch("core.pipeline.SignalGenerator"),
            patch("core.pipeline.StrategyEnsembleEngine", return_value=self.ensemble_mock),
        ):
            pipeline = InvestmentDecisionPipeline(
                state_machine=sm,
                gate_registry=registry,
                fallback_handler=fallback,
            )
            return await pipeline.run_full_analysis("005930")

    async def test_returns_pipeline_result(self):
        result = await self._run_pipeline()
        assert isinstance(result, PipelineResult)

    async def test_ensemble_signal_present(self):
        result = await self._run_pipeline()
        assert result.ensemble_signal is not None
        assert result.ensemble_signal.ticker == "005930"
        assert result.ensemble_signal.final_signal == 0.65

    async def test_not_blocked(self):
        result = await self._run_pipeline()
        assert result.blocked is False
        assert result.blocked_by is None

    async def test_final_state_completed(self):
        result = await self._run_pipeline()
        assert result.final_state == PipelineState.COMPLETED

    async def test_gate_results_collected(self):
        """DataGate + SignalGate + EnsembleGate 결과가 수집됩니다."""
        result = await self._run_pipeline()
        gate_ids = [gr.gate_id for gr in result.gate_results]
        assert "DataGate" in gate_ids
        assert "SignalGate" in gate_ids
        assert "EnsembleGate" in gate_ids

    async def test_all_gates_passed(self):
        result = await self._run_pipeline()
        for gr in result.gate_results:
            assert gr.decision == GateDecision.PASS


@pytest.mark.smoke
class TestPipelineDataGateBlock(unittest.IsolatedAsyncioTestCase):
    """DataGate BLOCK 시 파이프라인 중단 검증"""

    async def test_empty_articles_triggers_block(self):
        """뉴스 데이터가 없으면 DataGate가 BLOCK → IDLE 폴백."""
        news_mock = AsyncMock()
        news_mock.get_articles_for_ticker = AsyncMock(return_value=[])

        sm = PipelineStateMachine()
        registry = _build_default_gate_registry()
        fallback = FallbackHandler(sm)

        with (
            patch("core.pipeline.NewsCollectorService", return_value=news_mock),
            patch("core.pipeline.SentimentAnalyzer"),
            patch("core.pipeline.OpinionGenerator"),
            patch("core.pipeline.SignalGenerator"),
            patch("core.pipeline.StrategyEnsembleEngine"),
        ):
            pipeline = InvestmentDecisionPipeline(
                state_machine=sm,
                gate_registry=registry,
                fallback_handler=fallback,
            )
            result = await pipeline.run_full_analysis("005930")

        assert result.blocked is True
        assert result.blocked_by == "DataGate"
        assert result.ensemble_signal is None
        assert result.final_state == PipelineState.IDLE  # FallbackHandler → IDLE

        # Gate 결과에 DataGate BLOCK 기록
        assert len(result.gate_results) == 1
        assert result.gate_results[0].gate_id == "DataGate"
        assert result.gate_results[0].decision == GateDecision.BLOCK
        assert result.gate_results[0].severity == GateSeverity.CRITICAL


class TestPipelineSignalGateBlock(unittest.IsolatedAsyncioTestCase):
    """SignalGate BLOCK 시 파이프라인 중단 검증"""

    async def test_no_signals_triggers_block(self):
        """시그널 입력이 빈 리스트면 SignalGate가 BLOCK."""
        news_mock = AsyncMock()
        news_mock.get_articles_for_ticker = AsyncMock(return_value=[{"title": "뉴스"}])

        sentiment_mock = AsyncMock()
        sentiment_mock.analyze_ticker = AsyncMock(return_value=_make_mock_sentiment())

        opinion_mock = AsyncMock()
        opinion_mock.generate_stock_opinion = AsyncMock(return_value=_make_mock_opinion())

        sm = PipelineStateMachine()
        registry = _build_default_gate_registry()
        fallback = FallbackHandler(sm)

        with (
            patch("core.pipeline.NewsCollectorService", return_value=news_mock),
            patch("core.pipeline.SentimentAnalyzer", return_value=sentiment_mock),
            patch("core.pipeline.OpinionGenerator", return_value=opinion_mock),
            patch("core.pipeline.SignalGenerator"),
            patch("core.pipeline.StrategyEnsembleEngine"),
            patch.object(
                InvestmentDecisionPipeline,
                "_build_ensemble_inputs",
                return_value=[],  # 빈 시그널
            ),
        ):
            pipeline = InvestmentDecisionPipeline(
                state_machine=sm,
                gate_registry=registry,
                fallback_handler=fallback,
            )
            result = await pipeline.run_full_analysis("005930")

        assert result.blocked is True
        assert result.blocked_by == "SignalGate"
        assert result.ensemble_signal is None
        # DataGate PASS + SignalGate BLOCK
        assert len(result.gate_results) == 2
        assert result.gate_results[0].decision == GateDecision.PASS
        assert result.gate_results[1].decision == GateDecision.BLOCK


class TestPipelineEnsembleGateBlock(unittest.IsolatedAsyncioTestCase):
    """EnsembleGate BLOCK 시 파이프라인 중단 검증"""

    async def test_invalid_weights_triggers_block(self):
        """가중치 합이 1.0이 아니면 EnsembleGate가 BLOCK."""
        news_mock = AsyncMock()
        news_mock.get_articles_for_ticker = AsyncMock(return_value=[{"title": "뉴스"}])

        sentiment_mock = AsyncMock()
        sentiment_mock.analyze_ticker = AsyncMock(return_value=_make_mock_sentiment())

        opinion_mock = AsyncMock()
        opinion_mock.generate_stock_opinion = AsyncMock(return_value=_make_mock_opinion())

        # 가중치 합이 0.8 (≠ 1.0)
        bad_ensemble = _make_mock_ensemble_signal(weights={"TREND": 0.5, "SENTIMENT": 0.3})  # sum = 0.8
        ensemble_mock = AsyncMock()
        ensemble_mock.generate_ensemble_signal = AsyncMock(return_value=bad_ensemble)

        sm = PipelineStateMachine()
        registry = _build_default_gate_registry()
        fallback = FallbackHandler(sm)

        with (
            patch("core.pipeline.NewsCollectorService", return_value=news_mock),
            patch("core.pipeline.SentimentAnalyzer", return_value=sentiment_mock),
            patch("core.pipeline.OpinionGenerator", return_value=opinion_mock),
            patch("core.pipeline.SignalGenerator"),
            patch("core.pipeline.StrategyEnsembleEngine", return_value=ensemble_mock),
        ):
            pipeline = InvestmentDecisionPipeline(
                state_machine=sm,
                gate_registry=registry,
                fallback_handler=fallback,
            )
            result = await pipeline.run_full_analysis("005930")

        assert result.blocked is True
        assert result.blocked_by == "EnsembleGate"
        assert result.ensemble_signal is None
        # DataGate PASS + SignalGate PASS + EnsembleGate BLOCK
        assert len(result.gate_results) == 3


class TestPipelineBatchAnalysis(unittest.IsolatedAsyncioTestCase):
    """run_batch_analysis Gate 통합 검증"""

    async def test_batch_returns_pipeline_results(self):
        """배치 분석 결과가 PipelineResult 딕셔너리입니다."""
        news_mock = AsyncMock()
        news_mock.get_articles_for_ticker = AsyncMock(return_value=[{"title": "뉴스"}])

        sentiment_mock = AsyncMock()
        sentiment_mock.analyze_ticker = AsyncMock(return_value=_make_mock_sentiment())

        opinion_mock = AsyncMock()
        opinion_mock.generate_stock_opinion = AsyncMock(return_value=_make_mock_opinion())

        ensemble_mock = AsyncMock()
        ensemble_mock.generate_ensemble_signal = AsyncMock(return_value=_make_mock_ensemble_signal())

        with (
            patch("core.pipeline.NewsCollectorService", return_value=news_mock),
            patch("core.pipeline.SentimentAnalyzer", return_value=sentiment_mock),
            patch("core.pipeline.OpinionGenerator", return_value=opinion_mock),
            patch("core.pipeline.SignalGenerator"),
            patch("core.pipeline.StrategyEnsembleEngine", return_value=ensemble_mock),
        ):
            pipeline = InvestmentDecisionPipeline()
            results = await pipeline.run_batch_analysis(["005930", "000660"])

        assert len(results) == 2
        for ticker, result in results.items():
            assert isinstance(result, PipelineResult)
            assert result.blocked is False

    async def test_batch_one_blocked_one_pass(self):
        """배치 중 일부 종목이 BLOCK되어도 나머지는 정상 진행."""
        call_count = 0

        async def alternating_articles(ticker, **kwargs):
            nonlocal call_count
            call_count += 1
            # 첫 번째 종목: 빈 데이터 → DataGate BLOCK
            if ticker == "BLOCK_ME":
                return []
            return [{"title": "뉴스"}]

        news_mock = AsyncMock()
        news_mock.get_articles_for_ticker = AsyncMock(side_effect=alternating_articles)

        sentiment_mock = AsyncMock()
        sentiment_mock.analyze_ticker = AsyncMock(return_value=_make_mock_sentiment())

        opinion_mock = AsyncMock()
        opinion_mock.generate_stock_opinion = AsyncMock(return_value=_make_mock_opinion())

        ensemble_mock = AsyncMock()
        ensemble_mock.generate_ensemble_signal = AsyncMock(return_value=_make_mock_ensemble_signal())

        with (
            patch("core.pipeline.NewsCollectorService", return_value=news_mock),
            patch("core.pipeline.SentimentAnalyzer", return_value=sentiment_mock),
            patch("core.pipeline.OpinionGenerator", return_value=opinion_mock),
            patch("core.pipeline.SignalGenerator"),
            patch("core.pipeline.StrategyEnsembleEngine", return_value=ensemble_mock),
        ):
            pipeline = InvestmentDecisionPipeline()
            results = await pipeline.run_batch_analysis(["BLOCK_ME", "005930"])

        assert results["BLOCK_ME"].blocked is True
        assert results["BLOCK_ME"].blocked_by == "DataGate"
        assert results["005930"].blocked is False
        assert results["005930"].ensemble_signal is not None


class TestPipelineStateTransitions(unittest.IsolatedAsyncioTestCase):
    """StateMachine 상태 전이 이력 검증"""

    async def test_successful_run_transitions(self):
        """성공 시 IDLE → COLLECTING → ANALYZING → … → COMPLETED."""
        news_mock = AsyncMock()
        news_mock.get_articles_for_ticker = AsyncMock(return_value=[{"title": "뉴스"}])
        sentiment_mock = AsyncMock()
        sentiment_mock.analyze_ticker = AsyncMock(return_value=_make_mock_sentiment())
        opinion_mock = AsyncMock()
        opinion_mock.generate_stock_opinion = AsyncMock(return_value=_make_mock_opinion())
        ensemble_mock = AsyncMock()
        ensemble_mock.generate_ensemble_signal = AsyncMock(return_value=_make_mock_ensemble_signal())

        sm = PipelineStateMachine()

        with (
            patch("core.pipeline.NewsCollectorService", return_value=news_mock),
            patch("core.pipeline.SentimentAnalyzer", return_value=sentiment_mock),
            patch("core.pipeline.OpinionGenerator", return_value=opinion_mock),
            patch("core.pipeline.SignalGenerator"),
            patch("core.pipeline.StrategyEnsembleEngine", return_value=ensemble_mock),
        ):
            pipeline = InvestmentDecisionPipeline(state_machine=sm)
            await pipeline.run_full_analysis("005930")

        states = [s for s, _ in sm.history]
        assert PipelineState.IDLE in states
        assert PipelineState.COLLECTING in states
        assert PipelineState.ANALYZING in states
        assert PipelineState.COMPLETED in states
        assert sm.state == PipelineState.COMPLETED

    async def test_block_resets_to_idle(self):
        """DataGate BLOCK 시 FallbackHandler가 IDLE로 리셋."""
        news_mock = AsyncMock()
        news_mock.get_articles_for_ticker = AsyncMock(return_value=[])

        sm = PipelineStateMachine()
        fallback = FallbackHandler(sm)

        with (
            patch("core.pipeline.NewsCollectorService", return_value=news_mock),
            patch("core.pipeline.SentimentAnalyzer"),
            patch("core.pipeline.OpinionGenerator"),
            patch("core.pipeline.SignalGenerator"),
            patch("core.pipeline.StrategyEnsembleEngine"),
        ):
            pipeline = InvestmentDecisionPipeline(
                state_machine=sm,
                fallback_handler=fallback,
            )
            result = await pipeline.run_full_analysis("005930")

        assert sm.state == PipelineState.IDLE
        assert result.final_state == PipelineState.IDLE


class TestPipelineFallbackCallback(unittest.IsolatedAsyncioTestCase):
    """FallbackHandler 콜백 호출 검증"""

    async def test_on_block_callback_invoked(self):
        """BLOCK 시 FallbackHandler의 on_block 콜백이 호출됩니다."""
        callback = MagicMock()

        news_mock = AsyncMock()
        news_mock.get_articles_for_ticker = AsyncMock(return_value=[])

        sm = PipelineStateMachine()
        fallback = FallbackHandler(sm, on_block_callback=callback)

        with (
            patch("core.pipeline.NewsCollectorService", return_value=news_mock),
            patch("core.pipeline.SentimentAnalyzer"),
            patch("core.pipeline.OpinionGenerator"),
            patch("core.pipeline.SignalGenerator"),
            patch("core.pipeline.StrategyEnsembleEngine"),
        ):
            pipeline = InvestmentDecisionPipeline(
                state_machine=sm,
                fallback_handler=fallback,
            )
            await pipeline.run_full_analysis("005930")

        callback.assert_called_once()
        gate_result, fallback_state = callback.call_args[0]
        assert gate_result.gate_id == "DataGate"
        assert gate_result.decision == GateDecision.BLOCK
        assert fallback_state == PipelineState.IDLE


class TestPipelineGateResultLogging(unittest.IsolatedAsyncioTestCase):
    """Gate 결과에 reason/severity가 올바르게 기록되는지 검증"""

    async def test_gate_results_contain_reason_and_severity(self):
        news_mock = AsyncMock()
        news_mock.get_articles_for_ticker = AsyncMock(return_value=[{"title": "뉴스"}])
        sentiment_mock = AsyncMock()
        sentiment_mock.analyze_ticker = AsyncMock(return_value=_make_mock_sentiment())
        opinion_mock = AsyncMock()
        opinion_mock.generate_stock_opinion = AsyncMock(return_value=_make_mock_opinion())
        ensemble_mock = AsyncMock()
        ensemble_mock.generate_ensemble_signal = AsyncMock(return_value=_make_mock_ensemble_signal())

        with (
            patch("core.pipeline.NewsCollectorService", return_value=news_mock),
            patch("core.pipeline.SentimentAnalyzer", return_value=sentiment_mock),
            patch("core.pipeline.OpinionGenerator", return_value=opinion_mock),
            patch("core.pipeline.SignalGenerator"),
            patch("core.pipeline.StrategyEnsembleEngine", return_value=ensemble_mock),
        ):
            pipeline = InvestmentDecisionPipeline()
            result = await pipeline.run_full_analysis("005930")

        for gr in result.gate_results:
            assert gr.reason != ""  # 모든 Gate가 reason을 기록
            assert gr.severity is not None
            assert gr.gate_id != ""


class TestPipelineNoGateRegistry(unittest.IsolatedAsyncioTestCase):
    """Gate 없이(빈 레지스트리) 실행 — 기존 호환성"""

    async def test_empty_registry_skips_gates(self):
        """빈 GateRegistry면 Gate 없이 정상 진행."""
        news_mock = AsyncMock()
        news_mock.get_articles_for_ticker = AsyncMock(return_value=[{"title": "뉴스"}])
        sentiment_mock = AsyncMock()
        sentiment_mock.analyze_ticker = AsyncMock(return_value=_make_mock_sentiment())
        opinion_mock = AsyncMock()
        opinion_mock.generate_stock_opinion = AsyncMock(return_value=_make_mock_opinion())
        ensemble_mock = AsyncMock()
        ensemble_mock.generate_ensemble_signal = AsyncMock(return_value=_make_mock_ensemble_signal())

        empty_registry = GateRegistry()  # Gate 없음

        with (
            patch("core.pipeline.NewsCollectorService", return_value=news_mock),
            patch("core.pipeline.SentimentAnalyzer", return_value=sentiment_mock),
            patch("core.pipeline.OpinionGenerator", return_value=opinion_mock),
            patch("core.pipeline.SignalGenerator"),
            patch("core.pipeline.StrategyEnsembleEngine", return_value=ensemble_mock),
        ):
            pipeline = InvestmentDecisionPipeline(gate_registry=empty_registry)
            result = await pipeline.run_full_analysis("005930")

        assert result.blocked is False
        assert result.ensemble_signal is not None
        assert len(result.gate_results) == 0  # Gate 없으므로 결과도 없음


if __name__ == "__main__":
    unittest.main()
