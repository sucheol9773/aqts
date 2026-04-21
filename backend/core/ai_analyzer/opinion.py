"""
AI 투자 의견 생성 모듈 (Investment Opinion Generator - Mode B)

Phase 3 - F-03-01-B 구현:
- Claude Sonnet 4를 활용한 거시경제 분석 및 투자 의견 생성
- 개별 종목, 섹터, 거시경제 3가지 유형 지원
- 감성 분석 결과 + 정량 지표를 종합하여 투자 판단
- Redis 캐싱 (TTL: 4시간)

사용 라이브러리: anthropic 0.28.1, redis 5.0.7
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from anthropic import AsyncAnthropic

from config.constants import AI_CACHE_TTL, OpinionAction, OpinionType, SentimentMode
from config.logging import logger
from config.settings import get_settings
from db.database import RedisManager


@dataclass
class InvestmentOpinion:
    """투자 의견 결과 컨테이너"""

    ticker: Optional[str]  # 종목코드 (MACRO는 None)
    opinion_type: OpinionType  # STOCK / SECTOR / MACRO
    action: OpinionAction  # STRONG_BUY ~ STRONG_SELL
    conviction: float  # 확신도 0.0 ~ 1.0
    target_weight: Optional[float] = None  # 권장 포트폴리오 비중
    reasoning: str = ""  # 투자 근거 (상세)
    market_context: str = ""  # 시장 환경 요약
    risk_factors: list[str] = field(default_factory=list)
    model_used: str = ""
    generated_at: Optional[datetime] = None

    def to_signal_value(self) -> float:
        """전략 앙상블용 시그널 값 변환 (-1.0 ~ +1.0)"""
        action_map = {
            OpinionAction.STRONG_BUY: 1.0,
            OpinionAction.BUY: 0.5,
            OpinionAction.HOLD: 0.0,
            OpinionAction.SELL: -0.5,
            OpinionAction.STRONG_SELL: -1.0,
        }
        raw = action_map.get(self.action, 0.0)
        return round(raw * self.conviction, 4)

    def to_dict(self) -> dict:
        """DB 저장용 딕셔너리"""
        return {
            "ticker": self.ticker,
            "opinion_type": self.opinion_type.value,
            "action": self.action.value,
            "conviction": self.conviction,
            "target_weight": self.target_weight,
            "reasoning": self.reasoning,
            "market_context": self.market_context,
            "risk_factors": self.risk_factors,
            "model_used": self.model_used,
            "time": self.generated_at or datetime.now(timezone.utc),
        }


# ══════════════════════════════════════
# 프롬프트 템플릿
# ══════════════════════════════════════
_OPINION_SYSTEM_PROMPT = """당신은 CFA 자격을 보유한 한국 시장 전문 투자 애널리스트입니다.
주어진 데이터를 종합 분석하여 투자 의견을 생성합니다.

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 포함하지 마세요.

{
  "action": "<STRONG_BUY | BUY | HOLD | SELL | STRONG_SELL>",
  "conviction": <0.0 ~ 1.0 확신도>,
  "target_weight": <0.0 ~ 0.20 권장 포트폴리오 비중, null이면 null>,
  "reasoning": "<투자 근거 상세 설명 (3~5문장)>",
  "market_context": "<현재 시장 환경 요약 (1~2문장)>",
  "risk_factors": ["<리스크 1>", "<리스크 2>", "<리스크 3>"]
}

투자 의견 기준:
- STRONG_BUY: 확신도 0.8+ | 정량+정성 모두 강한 매수 시그널 | 단기 촉매 존재
- BUY: 확신도 0.6+ | 긍정적 시그널 우세 | 중기 상승 전망
- HOLD: 확신도 관계없음 | 시그널 혼재 또는 중립 | 관망 권장
- SELL: 확신도 0.6+ | 부정적 시그널 우세 | 하방 리스크 확대
- STRONG_SELL: 확신도 0.8+ | 즉각적 리스크 노출 | 손절 권장"""


_STOCK_OPINION_TEMPLATE = """## 종목 투자 의견 생성

종목코드: {ticker}

### 정량 분석 데이터 (Quant Engine)
- 팩터 복합 점수: {composite_score}/100
- 추세추종 시그널: {trend_signal} (-1.0~+1.0)
- 평균회귀 시그널: {mean_rev_signal} (-1.0~+1.0)
- 리스크패리티 시그널: {risk_parity_signal} (-1.0~+1.0)

### AI 감성 분석 결과 (Mode A)
- 감성 점수: {sentiment_score} (-1.0~+1.0)
- 감성 신뢰도: {sentiment_confidence}
- 감성 요약: {sentiment_summary}
- 긍정 요인: {positive_factors}
- 부정 요인: {negative_factors}

### 최근 뉴스 (최대 5건)
{recent_news}

위 데이터를 종합 분석하여 투자 의견을 생성하세요."""


_SECTOR_OPINION_TEMPLATE = """## 섹터 분석 및 투자 의견

### 섹터 정보
- 섹터명: {sector_name}
- 대표 종목: {representative_tickers}

### 섹터 내 종목별 감성 점수 (AI Mode A)
{ticker_sentiments}

### 섹터 관련 뉴스 (최대 10건)
{sector_news}

### 거시경제 컨텍스트
{macro_context}

위 데이터를 종합 분석하여 해당 섹터에 대한 투자 의견을 생성하세요.
action은 섹터 내 비중 확대(BUY)/유지(HOLD)/축소(SELL)를 의미합니다.
target_weight는 전체 포트폴리오 대비 해당 섹터 권장 비중(0.0~0.40)입니다."""


_MACRO_OPINION_TEMPLATE = """## 거시경제 분석 및 시장 전망

### 최근 거시경제 뉴스
{macro_news}

### 현재 시장 상황
- 분석 대상: 한국/미국 주식시장 종합 전망

위 정보를 종합하여 현재 시장 환경에 대한 투자 의견을 생성하세요.
action은 전체 시장 포지션(주식 비중 확대/유지/축소)을 의미합니다."""


# ══════════════════════════════════════
# 투자 의견 생성기
# ══════════════════════════════════════
class OpinionGenerator:
    """
    Claude API 기반 투자 의견 생성기 (Mode B)

    감성 분석 결과 + 정량 시그널 + 뉴스를 종합하여
    투자 의견(BUY/SELL/HOLD)과 근거를 생성합니다.
    """

    CACHE_PREFIX = "aqts:opinion:"

    def __init__(self):
        settings = get_settings()
        self._client = AsyncAnthropic(api_key=settings.anthropic.api_key)
        self._model = settings.anthropic.advanced_model  # Sonnet 4
        self._timeout = settings.anthropic.api_timeout
        self._cache_ttl = AI_CACHE_TTL[SentimentMode.OPINION]

    async def generate_stock_opinion(
        self,
        ticker: str,
        sentiment_result: dict,
        quant_signals: dict,
        recent_news: list[dict],
        force_refresh: bool = False,
    ) -> InvestmentOpinion:
        """
        개별 종목 투자 의견 생성

        Args:
            ticker: 종목코드
            sentiment_result: SentimentResult.to_dict() 결과
            quant_signals: 정량 시그널 딕셔너리
                {"composite_score", "trend_signal", "mean_rev_signal", "risk_parity_signal"}
            recent_news: 최근 뉴스 리스트
            force_refresh: 캐시 무시

        Returns:
            InvestmentOpinion
        """
        cache_key = f"stock:{ticker}"

        if not force_refresh:
            cached = await self._get_cached(cache_key)
            if cached:
                logger.debug(f"Opinion cache hit: {ticker}")
                return cached

        # 뉴스 텍스트 포맷
        news_text = self._format_news_brief(recent_news[:5])

        prompt = _STOCK_OPINION_TEMPLATE.format(
            ticker=ticker,
            composite_score=quant_signals.get("composite_score", 50.0),
            trend_signal=quant_signals.get("trend_signal", 0.0),
            mean_rev_signal=quant_signals.get("mean_rev_signal", 0.0),
            risk_parity_signal=quant_signals.get("risk_parity_signal", 0.0),
            sentiment_score=sentiment_result.get("score", 0.0),
            sentiment_confidence=sentiment_result.get("confidence", 0.0),
            sentiment_summary=sentiment_result.get("summary", "N/A"),
            positive_factors=", ".join(sentiment_result.get("positive_factors", [])),
            negative_factors=", ".join(sentiment_result.get("negative_factors", [])),
            recent_news=news_text,
        )

        opinion = await self._call_api(prompt, ticker, OpinionType.STOCK)
        await self._set_cache(cache_key, opinion)
        await self._store_to_db(opinion)

        logger.info(
            f"Opinion generated: {ticker}, action={opinion.action.value}, " f"conviction={opinion.conviction:.2f}"
        )
        return opinion

    async def generate_sector_opinion(
        self,
        sector_name: str,
        representative_tickers: list[str],
        ticker_sentiments: dict[str, dict],
        sector_news: list[dict],
        macro_context: str = "",
        force_refresh: bool = False,
    ) -> InvestmentOpinion:
        """
        섹터 분석 투자 의견 생성

        Args:
            sector_name: 섹터명 (예: "반도체", "2차전지")
            representative_tickers: 대표 종목 코드 리스트
            ticker_sentiments: {ticker: {"score": float, "summary": str}}
            sector_news: 섹터 관련 뉴스 리스트
            macro_context: 거시경제 컨텍스트 요약 (없으면 빈 문자열)
            force_refresh: 캐시 무시

        Returns:
            InvestmentOpinion (opinion_type=SECTOR)
        """
        cache_key = f"sector:{sector_name}"

        if not force_refresh:
            cached = await self._get_cached(cache_key)
            if cached:
                logger.debug(f"Sector opinion cache hit: {sector_name}")
                return cached

        # 종목별 감성 점수 포맷
        sentiment_lines = []
        for ticker, data in ticker_sentiments.items():
            score = data.get("score", 0.0)
            summary = data.get("summary", "N/A")
            sentiment_lines.append(f"- {ticker}: 감성={score:+.2f}, {summary}")
        sentiments_text = "\n".join(sentiment_lines) if sentiment_lines else "데이터 없음"

        # 뉴스 포맷
        news_text = self._format_news_brief(sector_news[:10])

        prompt = _SECTOR_OPINION_TEMPLATE.format(
            sector_name=sector_name,
            representative_tickers=", ".join(representative_tickers),
            ticker_sentiments=sentiments_text,
            sector_news=news_text,
            macro_context=macro_context or "컨텍스트 없음",
        )

        opinion = await self._call_api(prompt, None, OpinionType.SECTOR)
        # 섹터 의견은 ticker 대신 sector_name을 metadata로 보관
        opinion.market_context = f"[섹터: {sector_name}] {opinion.market_context}"
        opinion.target_weight = min(opinion.target_weight or 0.0, 0.40)

        await self._set_cache(cache_key, opinion)
        await self._store_to_db(opinion)

        logger.info(
            f"Sector opinion generated: {sector_name}, action={opinion.action.value}, "
            f"conviction={opinion.conviction:.2f}"
        )
        return opinion

    async def generate_macro_opinion(
        self,
        macro_news: list[dict],
        force_refresh: bool = False,
    ) -> InvestmentOpinion:
        """
        거시경제 분석 및 시장 전망 의견 생성

        Args:
            macro_news: 거시경제 뉴스 리스트
            force_refresh: 캐시 무시

        Returns:
            InvestmentOpinion (ticker=None, opinion_type=MACRO)
        """
        cache_key = "macro:market"

        if not force_refresh:
            cached = await self._get_cached(cache_key)
            if cached:
                logger.debug("Macro opinion cache hit")
                return cached

        news_text = self._format_news_brief(macro_news[:10])

        prompt = _MACRO_OPINION_TEMPLATE.format(macro_news=news_text)

        opinion = await self._call_api(prompt, None, OpinionType.MACRO)
        await self._set_cache(cache_key, opinion)
        await self._store_to_db(opinion)

        logger.info(f"Macro opinion generated: action={opinion.action.value}")
        return opinion

    # ══════════════════════════════════════
    # API 호출 및 파싱
    # ══════════════════════════════════════
    async def _call_api(
        self,
        prompt: str,
        ticker: Optional[str],
        opinion_type: OpinionType,
    ) -> InvestmentOpinion:
        """Claude API 호출 및 응답 파싱"""
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=1000,
                system=_OPINION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )

            return self._parse_response(ticker, opinion_type, response)

        except Exception as e:
            logger.error(f"Opinion API call failed (ticker={ticker}): {e}")
            return InvestmentOpinion(
                ticker=ticker,
                opinion_type=opinion_type,
                action=OpinionAction.HOLD,
                conviction=0.0,
                reasoning=f"의견 생성 실패: {str(e)[:100]}",
                model_used=self._model,
                generated_at=datetime.now(timezone.utc),
            )

    def _parse_response(
        self,
        ticker: Optional[str],
        opinion_type: OpinionType,
        response,
    ) -> InvestmentOpinion:
        """Claude 응답 JSON 파싱"""
        try:
            raw_text = response.content[0].text.strip()
            if raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]
            data = json.loads(raw_text)

            # action 파싱
            action_str = data.get("action", "HOLD").upper()
            try:
                action = OpinionAction(action_str)
            except ValueError:
                action = OpinionAction.HOLD

            # target_weight 유효성 검증
            target_weight = data.get("target_weight")
            if target_weight is not None:
                target_weight = max(0.0, min(0.20, float(target_weight)))

            return InvestmentOpinion(
                ticker=ticker,
                opinion_type=opinion_type,
                action=action,
                conviction=max(0.0, min(1.0, float(data.get("conviction", 0.5)))),
                target_weight=target_weight,
                reasoning=str(data.get("reasoning", "")),
                market_context=str(data.get("market_context", "")),
                risk_factors=list(data.get("risk_factors", [])),
                model_used=self._model,
                generated_at=datetime.now(timezone.utc),
            )
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logger.warning(f"Opinion response parse error: {e}")
            return InvestmentOpinion(
                ticker=ticker,
                opinion_type=opinion_type,
                action=OpinionAction.HOLD,
                conviction=0.1,
                reasoning="응답 파싱 실패",
                model_used=self._model,
                generated_at=datetime.now(timezone.utc),
            )

    @staticmethod
    def _format_news_brief(articles: list[dict]) -> str:
        """뉴스를 간결한 형태로 포맷팅"""
        if not articles:
            return "최근 관련 뉴스 없음"

        parts = []
        for i, art in enumerate(articles, 1):
            title = art.get("title", "")
            content = art.get("content", "")[:200]
            parts.append(f"{i}. {title}\n   {content}")
        return "\n".join(parts)

    # ══════════════════════════════════════
    # 캐시 및 저장
    # ══════════════════════════════════════
    async def _get_cached(self, cache_key: str) -> Optional[InvestmentOpinion]:
        """Redis 캐시 조회"""
        try:
            redis = RedisManager.get_client()
            key = f"{self.CACHE_PREFIX}{cache_key}"
            data = await redis.get(key)
            if data:
                d = json.loads(data)
                d["opinion_type"] = OpinionType(d["opinion_type"])
                d["action"] = OpinionAction(d["action"])
                return InvestmentOpinion(**d)
        except Exception:
            pass
        return None

    async def _set_cache(self, cache_key: str, opinion: InvestmentOpinion) -> None:
        """Redis 캐시 저장"""
        try:
            redis = RedisManager.get_client()
            key = f"{self.CACHE_PREFIX}{cache_key}"
            cache_data = {
                "ticker": opinion.ticker,
                "opinion_type": opinion.opinion_type.value,
                "action": opinion.action.value,
                "conviction": opinion.conviction,
                "target_weight": opinion.target_weight,
                "reasoning": opinion.reasoning,
                "market_context": opinion.market_context,
                "risk_factors": opinion.risk_factors,
                "model_used": opinion.model_used,
            }
            await redis.setex(key, self._cache_ttl, json.dumps(cache_data, ensure_ascii=False))
        except Exception as e:
            logger.debug(f"Opinion cache set failed: {e}")

    async def _store_to_db(self, opinion: InvestmentOpinion) -> None:
        """PostgreSQL에 투자 의견 저장"""
        try:
            from sqlalchemy import text

            from db.database import async_session_factory

            async with async_session_factory() as session:
                query = text(
                    """
                    INSERT INTO investment_opinions
                        (time, ticker, opinion_type, action, conviction,
                         target_weight, reasoning, market_context, risk_factors, model_used)
                    VALUES
                        (:time, :ticker, :opinion_type, :action, :conviction,
                         :target_weight, :reasoning, :market_context, :risk_factors, :model_used)
                """
                )
                await session.execute(
                    query,
                    {
                        "time": opinion.generated_at or datetime.now(timezone.utc),
                        "ticker": opinion.ticker,
                        "opinion_type": opinion.opinion_type.value,
                        "action": opinion.action.value,
                        "conviction": opinion.conviction,
                        "target_weight": opinion.target_weight,
                        "reasoning": opinion.reasoning,
                        "market_context": opinion.market_context,
                        "risk_factors": json.dumps(opinion.risk_factors, ensure_ascii=False),
                        "model_used": opinion.model_used,
                    },
                )
                await session.commit()
        except Exception as e:
            logger.warning(f"Opinion DB store failed: {e}")
