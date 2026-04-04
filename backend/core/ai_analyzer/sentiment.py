"""
AI 감성 분석 모듈 (Sentiment Analyzer - Mode A)

Phase 3 - F-03-01-A 구현:
- Claude Haiku 4.5를 활용한 뉴스/공시 감성 점수 산출
- 종목별 복수 뉴스를 배치 분석하여 통합 감성 점수 생성
- Redis 캐싱 (TTL: 1시간)으로 API 호출 비용 최적화
- 출력: -1.0 (극도 부정) ~ +1.0 (극도 긍정) 감성 점수 + 신뢰도

사용 라이브러리: anthropic 0.28.1, redis 5.0.7
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from anthropic import AsyncAnthropic

from config.constants import AI_CACHE_TTL, SentimentMode
from config.logging import logger
from config.settings import get_settings
from db.database import RedisManager


@dataclass
class SentimentResult:
    """감성 분석 결과 컨테이너"""

    ticker: str
    score: float  # -1.0 ~ +1.0
    confidence: float  # 0.0 ~ 1.0
    summary: str = ""  # 요약 (1~2문장)
    positive_factors: list[str] = field(default_factory=list)
    negative_factors: list[str] = field(default_factory=list)
    news_count: int = 0  # 분석에 사용된 뉴스 수
    model_used: str = ""
    analyzed_at: Optional[datetime] = None

    def to_signal_value(self) -> float:
        """전략 앙상블용 시그널 값 변환 (-1.0 ~ +1.0)"""
        return round(self.score, 4)

    def to_dict(self) -> dict:
        """DB 저장용 딕셔너리"""
        return {
            "ticker": self.ticker,
            "score": self.score,
            "confidence": self.confidence,
            "summary": self.summary,
            "positive_factors": self.positive_factors,
            "negative_factors": self.negative_factors,
            "news_count": self.news_count,
            "model_used": self.model_used,
            "time": self.analyzed_at or datetime.now(timezone.utc),
        }


# ══════════════════════════════════════
# 프롬프트 템플릿
# ══════════════════════════════════════
_SENTIMENT_SYSTEM_PROMPT = """당신은 한국 주식시장 전문 금융 분석가입니다.
주어진 뉴스/공시 텍스트를 분석하여 해당 종목에 대한 감성 점수를 산출합니다.

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 포함하지 마세요.

{
  "score": <-1.0 ~ +1.0 사이의 소수점 2자리 감성 점수>,
  "confidence": <0.0 ~ 1.0 사이의 신뢰도>,
  "summary": "<분석 요약 1~2문장>",
  "positive_factors": ["<긍정 요인 1>", "<긍정 요인 2>"],
  "negative_factors": ["<부정 요인 1>", "<부정 요인 2>"]
}

점수 기준:
- +0.7 ~ +1.0: 매우 긍정적 (실적 서프라이즈, 대규모 수주, 신사업 호재)
- +0.3 ~ +0.7: 긍정적 (실적 개선, 업종 호황, 우호적 정책)
- -0.3 ~ +0.3: 중립 (일상적 뉴스, 영향 미미)
- -0.7 ~ -0.3: 부정적 (실적 악화, 규제 강화, 소송)
- -1.0 ~ -0.7: 매우 부정적 (대규모 손실, 회계 부정, 상장폐지 위험)

신뢰도 기준:
- 뉴스 수가 많고 방향성이 일치하면 높음 (0.7~1.0)
- 뉴스 수가 적거나 방향이 혼재하면 낮음 (0.3~0.5)
- 뉴스가 없으면 최저 (0.1~0.2)"""


_SENTIMENT_USER_TEMPLATE = """종목코드: {ticker}

아래 {news_count}건의 최근 뉴스/공시를 분석하여 감성 점수를 산출하세요.

{news_text}"""


# ══════════════════════════════════════
# 감성 분석기
# ══════════════════════════════════════
class SentimentAnalyzer:
    """
    Claude API 기반 감성 분석기 (Mode A)

    뉴스/공시 텍스트를 분석하여 종목별 감성 점수를 산출합니다.
    Redis 캐싱으로 동일 종목 반복 분석 비용을 절감합니다.
    """

    CACHE_PREFIX = "aqts:sentiment:"

    def __init__(self):
        settings = get_settings()
        self._client = AsyncAnthropic(api_key=settings.anthropic.api_key)
        self._model = settings.anthropic.default_model  # Haiku 4.5
        self._timeout = settings.anthropic.api_timeout
        self._cache_ttl = AI_CACHE_TTL[SentimentMode.SCORE]

    async def analyze_ticker(
        self,
        ticker: str,
        articles: list[dict],
        force_refresh: bool = False,
    ) -> SentimentResult:
        """
        종목별 감성 분석 수행

        Args:
            ticker: 종목코드
            articles: 뉴스 딕셔너리 리스트 (NewsCollectorService.get_articles_for_ticker 결과)
            force_refresh: True이면 캐시 무시

        Returns:
            SentimentResult
        """
        # 캐시 확인
        if not force_refresh:
            cached = await self._get_cached(ticker)
            if cached:
                logger.debug(f"Sentiment cache hit: {ticker}")
                return cached

        # 뉴스가 없으면 중립 반환
        if not articles:
            result = SentimentResult(
                ticker=ticker,
                score=0.0,
                confidence=0.1,
                summary="분석 가능한 최근 뉴스가 없습니다.",
                model_used=self._model,
                analyzed_at=datetime.now(timezone.utc),
            )
            await self._set_cache(ticker, result)
            return result

        # 뉴스 텍스트 조합 (토큰 제한 고려, 최대 15건)
        selected = articles[:15]
        news_text = self._format_news(selected)

        # Claude API 호출
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=500,
                system=_SENTIMENT_SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": _SENTIMENT_USER_TEMPLATE.format(
                            ticker=ticker,
                            news_count=len(selected),
                            news_text=news_text,
                        ),
                    }
                ],
            )

            result = self._parse_response(ticker, response, len(selected))
            await self._set_cache(ticker, result)
            await self._store_to_db(result)

            logger.info(
                f"Sentiment analyzed: {ticker}, score={result.score:.2f}, "
                f"confidence={result.confidence:.2f}, news={result.news_count}"
            )
            return result

        except Exception as e:
            logger.error(f"Sentiment analysis failed for {ticker}: {e}")
            return SentimentResult(
                ticker=ticker,
                score=0.0,
                confidence=0.0,
                summary=f"분석 실패: {str(e)[:100]}",
                model_used=self._model,
                analyzed_at=datetime.now(timezone.utc),
            )

    async def analyze_batch(
        self,
        ticker_articles: dict[str, list[dict]],
        force_refresh: bool = False,
    ) -> dict[str, SentimentResult]:
        """
        복수 종목 배치 감성 분석

        Args:
            ticker_articles: {ticker: [articles]} 딕셔너리
            force_refresh: 캐시 무시 여부

        Returns:
            {ticker: SentimentResult} 딕셔너리
        """
        results: dict[str, SentimentResult] = {}

        for ticker, articles in ticker_articles.items():
            result = await self.analyze_ticker(ticker, articles, force_refresh)
            results[ticker] = result

        logger.info(f"Batch sentiment analysis complete: {len(results)} tickers")
        return results

    # ══════════════════════════════════════
    # 내부 유틸리티
    # ══════════════════════════════════════
    @staticmethod
    def _format_news(articles: list[dict]) -> str:
        """뉴스 리스트를 프롬프트용 텍스트로 포맷팅"""
        parts = []
        for i, art in enumerate(articles, 1):
            title = art.get("title", "")
            content = art.get("content", "")
            source = art.get("source", "")
            pub = art.get("published_at", "")
            # 본문 500자 제한
            if len(content) > 500:
                content = content[:500] + "..."
            parts.append(f"[뉴스 {i}] ({source}, {pub})\n제목: {title}\n내용: {content}")
        return "\n\n".join(parts)

    def _parse_response(self, ticker: str, response, news_count: int) -> SentimentResult:
        """Claude API 응답 파싱"""
        try:
            raw_text = response.content[0].text.strip()
            # JSON 블록 추출 (```json ... ``` 가능성)
            if raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]
            data = json.loads(raw_text)

            return SentimentResult(
                ticker=ticker,
                score=max(-1.0, min(1.0, float(data.get("score", 0.0)))),
                confidence=max(0.0, min(1.0, float(data.get("confidence", 0.5)))),
                summary=str(data.get("summary", "")),
                positive_factors=list(data.get("positive_factors", [])),
                negative_factors=list(data.get("negative_factors", [])),
                news_count=news_count,
                model_used=self._model,
                analyzed_at=datetime.now(timezone.utc),
            )
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logger.warning(f"Sentiment response parse error for {ticker}: {e}")
            return SentimentResult(
                ticker=ticker,
                score=0.0,
                confidence=0.1,
                summary="응답 파싱 실패",
                news_count=news_count,
                model_used=self._model,
                analyzed_at=datetime.now(timezone.utc),
            )

    async def _get_cached(self, ticker: str) -> Optional[SentimentResult]:
        """Redis 캐시에서 감성 결과 조회"""
        try:
            redis = RedisManager.get_client()
            key = f"{self.CACHE_PREFIX}{ticker}"
            data = await redis.get(key)
            if data:
                d = json.loads(data)
                return SentimentResult(**d)
        except Exception:
            pass
        return None

    async def _set_cache(self, ticker: str, result: SentimentResult) -> None:
        """Redis 캐시에 감성 결과 저장"""
        try:
            redis = RedisManager.get_client()
            key = f"{self.CACHE_PREFIX}{ticker}"
            cache_data = {
                "ticker": result.ticker,
                "score": result.score,
                "confidence": result.confidence,
                "summary": result.summary,
                "positive_factors": result.positive_factors,
                "negative_factors": result.negative_factors,
                "news_count": result.news_count,
                "model_used": result.model_used,
            }
            await redis.setex(key, self._cache_ttl, json.dumps(cache_data, ensure_ascii=False))
        except Exception as e:
            logger.debug(f"Sentiment cache set failed for {ticker}: {e}")

    async def _store_to_db(self, result: SentimentResult) -> None:
        """PostgreSQL에 감성 분석 결과 저장"""
        try:
            from sqlalchemy import text

            from db.database import async_session_factory

            async with async_session_factory() as session:
                query = text("""
                    INSERT INTO sentiment_scores
                        (time, ticker, score, confidence, summary,
                         positive_factors, negative_factors, news_count, model_used)
                    VALUES
                        (:time, :ticker, :score, :confidence, :summary,
                         :positive_factors, :negative_factors, :news_count, :model_used)
                """)
                await session.execute(
                    query,
                    {
                        "time": result.analyzed_at or datetime.now(timezone.utc),
                        "ticker": result.ticker,
                        "score": result.score,
                        "confidence": result.confidence,
                        "summary": result.summary,
                        "positive_factors": json.dumps(result.positive_factors, ensure_ascii=False),
                        "negative_factors": json.dumps(result.negative_factors, ensure_ascii=False),
                        "news_count": result.news_count,
                        "model_used": result.model_used,
                    },
                )
                await session.commit()
        except Exception as e:
            logger.warning(f"Sentiment DB store failed for {result.ticker}: {e}")
