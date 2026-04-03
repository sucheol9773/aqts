"""
투자 의사결정 파이프라인 (Investment Decision Pipeline)

Phase 3 통합 서비스:
뉴스 수집 → AI 감성 분석 → AI 투자 의견 → 전략 앙상블

아키텍처 다이어그램 참조:
  User profile → Universe filtering → [Quant engine + AI mode A + AI mode B]
    → Strategy ensemble → Portfolio construction → Trade style decision

이 모듈은 Phase 3 범위의 "Market re-analysis" 단계를 담당합니다.
Quant Engine (Phase 2) 시그널과 AI Analyzer (Phase 3) 시그널을
Strategy Ensemble Engine에 합류시킵니다.
"""

from typing import Optional

from config.constants import RiskProfile, StrategyType
from config.logging import logger
from core.ai_analyzer.opinion import OpinionGenerator
from core.ai_analyzer.sentiment import SentimentAnalyzer
from core.data_collector.news_collector import NewsCollectorService
from core.quant_engine.signal_generator import Signal, SignalGenerator
from core.strategy_ensemble.engine import (
    EnsembleSignal,
    StrategyEnsembleEngine,
    StrategySignalInput,
)


class InvestmentDecisionPipeline:
    """
    투자 의사결정 파이프라인

    단일 호출로 뉴스 수집 → 감성 분석 → 투자 의견 생성 →
    앙상블 시그널 산출까지 전체 흐름을 실행합니다.
    """

    def __init__(self, risk_profile: RiskProfile = RiskProfile.BALANCED):
        self._profile = risk_profile
        self._news_service = NewsCollectorService()
        self._sentiment = SentimentAnalyzer()
        self._opinion = OpinionGenerator()
        self._signal_gen = SignalGenerator()
        self._ensemble = StrategyEnsembleEngine(risk_profile)

    async def run_full_analysis(
        self,
        ticker: str,
        quant_signals: Optional[list[Signal]] = None,
        composite_score: float = 50.0,
        force_refresh: bool = False,
    ) -> EnsembleSignal:
        """
        단일 종목 전체 분석 파이프라인

        Args:
            ticker: 종목코드
            quant_signals: Phase 2 Quant Engine 시그널 (None이면 건너뜀)
            composite_score: 팩터 복합 점수 (0~100)
            force_refresh: 캐시 무시 여부

        Returns:
            EnsembleSignal (최종 앙상블 시그널)
        """
        logger.info(f"Pipeline started: {ticker}, profile={self._profile.value}")

        # ── Step 1: 뉴스 수집 (이미 저장된 데이터 조회) ──
        articles = await self._news_service.get_articles_for_ticker(
            ticker, hours=24, limit=20,
        )
        logger.debug(f"[{ticker}] News articles found: {len(articles)}")

        # ── Step 2: AI 감성 분석 (Mode A) ──
        sentiment = await self._sentiment.analyze_ticker(
            ticker, articles, force_refresh=force_refresh,
        )

        # ── Step 3: AI 투자 의견 (Mode B) ──
        # 정량 시그널 요약 준비
        quant_summary = self._summarize_quant_signals(quant_signals, composite_score)

        opinion = await self._opinion.generate_stock_opinion(
            ticker=ticker,
            sentiment_result=sentiment.to_dict(),
            quant_signals=quant_summary,
            recent_news=articles[:5],
            force_refresh=force_refresh,
        )

        # ── Step 4: 시그널 통합 및 앙상블 ──
        ensemble_inputs = self._build_ensemble_inputs(
            quant_signals, sentiment, opinion,
        )

        result = await self._ensemble.generate_ensemble_signal(ticker, ensemble_inputs)

        logger.info(
            f"Pipeline complete: {ticker}, "
            f"sentiment={sentiment.score:.2f}, "
            f"opinion={opinion.action.value}, "
            f"ensemble={result.final_signal:.4f} ({result.action})"
        )
        return result

    async def run_batch_analysis(
        self,
        tickers: list[str],
        quant_data: Optional[dict[str, dict]] = None,
        force_refresh: bool = False,
    ) -> dict[str, EnsembleSignal]:
        """
        복수 종목 배치 분석 파이프라인

        Args:
            tickers: 종목코드 리스트
            quant_data: {ticker: {"signals": [Signal], "composite_score": float}}
            force_refresh: 캐시 무시

        Returns:
            {ticker: EnsembleSignal}
        """
        results: dict[str, EnsembleSignal] = {}

        for ticker in tickers:
            try:
                td = (quant_data or {}).get(ticker, {})
                q_signals = td.get("signals")
                c_score = td.get("composite_score", 50.0)

                result = await self.run_full_analysis(
                    ticker=ticker,
                    quant_signals=q_signals,
                    composite_score=c_score,
                    force_refresh=force_refresh,
                )
                results[ticker] = result
            except Exception as e:
                logger.error(f"Pipeline failed for {ticker}: {e}")
                continue

        logger.info(
            f"Batch pipeline complete: {len(results)}/{len(tickers)} tickers"
        )
        return results

    async def run_news_collection(self) -> dict:
        """
        뉴스/공시 수집 (배치 분석 전 호출)

        Returns:
            {"total_collected": int, "new_stored": int, "duplicates_skipped": int}
        """
        return await self._news_service.collect_and_store()

    async def run_sector_analysis(
        self,
        sector_name: str,
        tickers: list[str],
        force_refresh: bool = False,
    ):
        """
        섹터 분석 실행

        Args:
            sector_name: 섹터명 (예: "반도체")
            tickers: 해당 섹터 대표 종목 리스트
            force_refresh: 캐시 무시

        Returns:
            InvestmentOpinion (SECTOR)
        """
        # 섹터 종목별 감성 분석 수집
        ticker_sentiments: dict[str, dict] = {}
        for ticker in tickers:
            articles = await self._news_service.get_articles_for_ticker(
                ticker, hours=48, limit=10,
            )
            sentiment = await self._sentiment.analyze_ticker(
                ticker, articles, force_refresh=force_refresh,
            )
            ticker_sentiments[ticker] = {
                "score": sentiment.score,
                "summary": sentiment.summary,
            }

        # 섹터 관련 뉴스 (카테고리 "sector" 기반)
        sector_news = await self._news_service.get_recent_articles(
            hours=48, category="sector", limit=20,
        )

        # 거시경제 컨텍스트 (최근 매크로 분석 결과가 있으면 활용)
        macro_context = ""
        try:
            macro_articles = await self._news_service.get_macro_articles(hours=48, limit=5)
            if macro_articles:
                macro_context = " | ".join(
                    a.get("title", "") for a in macro_articles[:5]
                )
        except Exception:
            pass

        return await self._opinion.generate_sector_opinion(
            sector_name=sector_name,
            representative_tickers=tickers,
            ticker_sentiments=ticker_sentiments,
            sector_news=sector_news,
            macro_context=macro_context,
            force_refresh=force_refresh,
        )

    async def run_macro_analysis(self, force_refresh: bool = False):
        """
        거시경제 분석 실행

        Returns:
            InvestmentOpinion (MACRO)
        """
        macro_news = await self._news_service.get_macro_articles(hours=48, limit=30)
        return await self._opinion.generate_macro_opinion(
            macro_news, force_refresh=force_refresh,
        )

    async def recalibrate_ensemble_weights(
        self,
        strategy_performances: dict[str, float],
    ) -> dict[str, float]:
        """
        백테스트 성과 기반 앙상블 가중치 재계산

        Args:
            strategy_performances: {strategy_key: sharpe_ratio}

        Returns:
            새 가중치 딕셔너리
        """
        return await self._ensemble.recalibrate_weights(
            strategy_performances, method="sharpe",
        )

    # ══════════════════════════════════════
    # 내부 유틸리티
    # ══════════════════════════════════════
    @staticmethod
    def _summarize_quant_signals(
        signals: Optional[list[Signal]],
        composite_score: float,
    ) -> dict:
        """Quant Engine 시그널을 Opinion Generator 입력 형태로 변환"""
        summary = {
            "composite_score": composite_score,
            "trend_signal": 0.0,
            "mean_rev_signal": 0.0,
            "risk_parity_signal": 0.0,
        }
        if not signals:
            return summary

        for sig in signals:
            if sig.strategy == StrategyType.TREND_FOLLOWING:
                summary["trend_signal"] = sig.value
            elif sig.strategy == StrategyType.MEAN_REVERSION:
                summary["mean_rev_signal"] = sig.value
            elif sig.strategy == StrategyType.RISK_PARITY:
                summary["risk_parity_signal"] = sig.value

        return summary

    @staticmethod
    def _build_ensemble_inputs(
        quant_signals: Optional[list[Signal]],
        sentiment,
        opinion,
    ) -> list[StrategySignalInput]:
        """Quant + AI 시그널을 앙상블 입력 형태로 변환"""
        inputs: list[StrategySignalInput] = []

        # Quant Engine 시그널 (Phase 2)
        if quant_signals:
            for sig in quant_signals:
                inputs.append(StrategySignalInput(
                    strategy=sig.strategy.value if hasattr(sig.strategy, 'value') else str(sig.strategy),
                    value=sig.value,
                    confidence=sig.confidence,
                    reason=sig.reason,
                ))

        # AI 감성 시그널 (Phase 3 - Mode A + Mode B 통합)
        # 감성 점수와 투자 의견을 가중 평균하여 SENTIMENT 시그널 생성
        sentiment_value = sentiment.to_signal_value()
        opinion_value = opinion.to_signal_value()

        # 감성 60% + 의견 40% 가중 평균
        combined_sentiment = sentiment_value * 0.6 + opinion_value * 0.4
        combined_confidence = (
            sentiment.confidence * 0.6 + opinion.conviction * 0.4
        )

        inputs.append(StrategySignalInput(
            strategy="SENTIMENT",
            value=round(max(-1.0, min(1.0, combined_sentiment)), 4),
            confidence=round(max(0.0, min(1.0, combined_confidence)), 4),
            reason=f"Sentiment={sentiment_value:.2f}, Opinion={opinion.action.value}",
        ))

        return inputs