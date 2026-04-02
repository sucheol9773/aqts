"""
Phase 3 테스트: 뉴스/공시 수집기 (NewsCollector)
"""

import pytest

from core.data_collector.news_collector import (
    NewsArticle,
    RSSNewsCollector,
    extract_tickers,
)


class TestExtractTickers:
    """종목코드 추출 테스트"""

    def test_extract_from_name(self):
        text = "삼성전자가 AI 반도체 수출 호조를 기록했다"
        result = extract_tickers(text)
        assert "005930" in result

    def test_extract_multiple_names(self):
        text = "삼성전자와 SK하이닉스의 반도체 경쟁이 심화되고 있다"
        result = extract_tickers(text)
        assert "005930" in result
        assert "000660" in result

    def test_extract_from_code(self):
        text = "종목코드 005930 오늘 상한가"
        result = extract_tickers(text)
        assert "005930" in result

    def test_exclude_date_pattern(self):
        text = "2026년 04월 03일 시장 동향"
        result = extract_tickers(text)
        assert "202604" not in result

    def test_no_tickers(self):
        text = "오늘 날씨가 좋습니다"
        result = extract_tickers(text)
        assert len(result) == 0

    def test_naver_kakao(self):
        text = "네이버와 카카오가 AI 경쟁에 돌입"
        result = extract_tickers(text)
        assert "035420" in result
        assert "035720" in result


class TestNewsArticle:
    """NewsArticle 데이터 구조 테스트"""

    def test_url_hash_consistency(self):
        a1 = NewsArticle(title="T", content="C", url="https://example.com/1", source="RSS")
        a2 = NewsArticle(title="T2", content="C2", url="https://example.com/1", source="DART")
        assert a1.url_hash == a2.url_hash  # 동일 URL → 동일 해시

    def test_url_hash_uniqueness(self):
        a1 = NewsArticle(title="T", content="C", url="https://example.com/1", source="RSS")
        a2 = NewsArticle(title="T", content="C", url="https://example.com/2", source="RSS")
        assert a1.url_hash != a2.url_hash

    def test_to_dict(self):
        article = NewsArticle(
            title="테스트 뉴스",
            content="내용입니다",
            url="https://example.com/news/1",
            source="NAVER_FINANCE",
            tickers=["005930"],
            category="finance",
        )
        d = article.to_dict()
        assert d["title"] == "테스트 뉴스"
        assert d["source"] == "NAVER_FINANCE"
        assert "005930" in d["tickers"]
        assert "url_hash" in d


class TestRSSNewsCollector:
    """RSS 수집기 카테고리 분류 테스트"""

    def test_classify_macro(self):
        result = RSSNewsCollector._classify_category("Fed 금리 동결 시사, FOMC 회의 결과")
        assert result == "macro"

    def test_classify_earnings(self):
        result = RSSNewsCollector._classify_category("삼성전자 분기 영업이익 15조원 실적 발표")
        assert result == "earnings"

    def test_classify_sector(self):
        result = RSSNewsCollector._classify_category("반도체 업황 개선, AI 수요 확대")
        assert result == "sector"

    def test_classify_general(self):
        result = RSSNewsCollector._classify_category("기업 행사 안내")
        assert result == "general"