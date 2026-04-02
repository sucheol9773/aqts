"""
Phase 3 테스트: AI 감성 분석기 (SentimentAnalyzer)

모든 외부 API(Claude, Redis, MongoDB)는 Mock으로 대체합니다.
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.ai_analyzer.sentiment import SentimentAnalyzer, SentimentResult


class TestSentimentResult:
    """SentimentResult 데이터 구조 테스트"""

    def test_to_signal_value(self):
        result = SentimentResult(
            ticker="005930", score=0.75, confidence=0.9, model_used="test"
        )
        assert result.to_signal_value() == 0.75

    def test_to_signal_value_negative(self):
        result = SentimentResult(
            ticker="005930", score=-0.42, confidence=0.6, model_used="test"
        )
        assert result.to_signal_value() == -0.42

    def test_to_dict(self):
        result = SentimentResult(
            ticker="005930",
            score=0.65,
            confidence=0.8,
            summary="테스트 요약",
            positive_factors=["호재1"],
            negative_factors=["악재1"],
            news_count=5,
            model_used="test-model",
            analyzed_at=datetime(2026, 4, 3, tzinfo=timezone.utc),
        )
        d = result.to_dict()
        assert d["ticker"] == "005930"
        assert d["score"] == 0.65
        assert d["confidence"] == 0.8
        assert d["news_count"] == 5
        assert "호재1" in d["positive_factors"]


class TestSentimentAnalyzer:
    """SentimentAnalyzer 핵심 로직 테스트"""

    @pytest.fixture
    def mock_anthropic_response(self):
        """Claude API Mock 응답 생성"""
        response = MagicMock()
        response.content = [
            MagicMock(text=json.dumps({
                "score": 0.65,
                "confidence": 0.8,
                "summary": "삼성전자 반도체 수출 호조 전망",
                "positive_factors": ["반도체 수출 증가", "AI 메모리 수요 확대"],
                "negative_factors": ["중국 규제 리스크"],
            }, ensure_ascii=False))
        ]
        return response

    @pytest.fixture
    def sample_articles(self):
        """테스트용 뉴스 아티클"""
        return [
            {
                "title": "삼성전자, AI 반도체 수출 사상 최대",
                "content": "삼성전자가 AI용 HBM 메모리 반도체 수출이 전년 대비 45% 증가",
                "source": "NAVER_FINANCE",
                "published_at": datetime(2026, 4, 2, 9, 30, tzinfo=timezone.utc),
            },
            {
                "title": "반도체 업황 개선 기대감 확산",
                "content": "글로벌 반도체 시장이 AI 인프라 투자 확대에 힘입어 회복세",
                "source": "HANKYUNG",
                "published_at": datetime(2026, 4, 2, 10, 0, tzinfo=timezone.utc),
            },
        ]

    @pytest.mark.asyncio
    async def test_analyze_ticker_no_news(self):
        """뉴스 없을 때 중립 점수 반환"""
        with patch.object(SentimentAnalyzer, '_get_cached', return_value=None), \
             patch.object(SentimentAnalyzer, '_set_cache', new_callable=AsyncMock), \
             patch('core.ai_analyzer.sentiment.get_settings') as mock_settings:

            mock_settings.return_value = MagicMock(
                anthropic=MagicMock(
                    api_key="test-key",
                    default_model="claude-haiku-4-5-20251001",
                    api_timeout=30,
                )
            )

            analyzer = SentimentAnalyzer()
            result = await analyzer.analyze_ticker("005930", [])

            assert result.score == 0.0
            assert result.confidence == 0.1
            assert result.ticker == "005930"

    @pytest.mark.asyncio
    async def test_analyze_ticker_with_cache(self):
        """캐시 히트 시 캐시 결과 반환"""
        cached = SentimentResult(
            ticker="005930", score=0.55, confidence=0.7,
            summary="캐시 결과", model_used="cached",
        )

        with patch.object(SentimentAnalyzer, '_get_cached', return_value=cached), \
             patch('core.ai_analyzer.sentiment.get_settings') as mock_settings:

            mock_settings.return_value = MagicMock(
                anthropic=MagicMock(
                    api_key="test-key",
                    default_model="claude-haiku-4-5-20251001",
                    api_timeout=30,
                )
            )

            analyzer = SentimentAnalyzer()
            result = await analyzer.analyze_ticker("005930", [{"title": "test"}])

            assert result.score == 0.55
            assert result.summary == "캐시 결과"

    @pytest.mark.asyncio
    async def test_analyze_ticker_api_call(self, mock_anthropic_response, sample_articles):
        """Claude API 호출 및 결과 파싱 테스트"""
        with patch.object(SentimentAnalyzer, '_get_cached', return_value=None), \
             patch.object(SentimentAnalyzer, '_set_cache', new_callable=AsyncMock), \
             patch.object(SentimentAnalyzer, '_store_to_db', new_callable=AsyncMock), \
             patch('core.ai_analyzer.sentiment.get_settings') as mock_settings:

            mock_settings.return_value = MagicMock(
                anthropic=MagicMock(
                    api_key="test-key",
                    default_model="claude-haiku-4-5-20251001",
                    api_timeout=30,
                )
            )

            analyzer = SentimentAnalyzer()
            analyzer._client = AsyncMock()
            analyzer._client.messages.create.return_value = mock_anthropic_response

            result = await analyzer.analyze_ticker("005930", sample_articles)

            assert result.score == 0.65
            assert result.confidence == 0.8
            assert "반도체 수출 증가" in result.positive_factors
            assert result.news_count == 2

    def test_parse_response_valid(self, mock_anthropic_response):
        """정상 응답 파싱 검증"""
        with patch('core.ai_analyzer.sentiment.get_settings') as mock_settings:
            mock_settings.return_value = MagicMock(
                anthropic=MagicMock(
                    api_key="test-key",
                    default_model="test-model",
                    api_timeout=30,
                )
            )
            analyzer = SentimentAnalyzer()
            result = analyzer._parse_response("005930", mock_anthropic_response, 3)

            assert -1.0 <= result.score <= 1.0
            assert 0.0 <= result.confidence <= 1.0
            assert result.news_count == 3

    def test_parse_response_malformed(self):
        """잘못된 JSON 응답 처리"""
        with patch('core.ai_analyzer.sentiment.get_settings') as mock_settings:
            mock_settings.return_value = MagicMock(
                anthropic=MagicMock(
                    api_key="test-key",
                    default_model="test-model",
                    api_timeout=30,
                )
            )
            analyzer = SentimentAnalyzer()
            bad_response = MagicMock()
            bad_response.content = [MagicMock(text="not valid json")]

            result = analyzer._parse_response("005930", bad_response, 1)
            assert result.score == 0.0
            assert result.confidence == 0.1

    def test_format_news(self):
        """뉴스 포맷팅 테스트"""
        articles = [
            {"title": "뉴스1", "content": "내용1", "source": "RSS", "published_at": "2026-04-02"},
            {"title": "뉴스2", "content": "내용2", "source": "DART", "published_at": "2026-04-02"},
        ]
        result = SentimentAnalyzer._format_news(articles)
        assert "[뉴스 1]" in result
        assert "[뉴스 2]" in result
        assert "뉴스1" in result