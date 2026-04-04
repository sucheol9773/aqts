"""NewsData 계약 테스트 (Contract 3)."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from config.constants import NewsSource
from contracts.news_data import NewsData


def _valid_news(**overrides):
    defaults = dict(
        ticker="005930",
        title="삼성전자 실적 발표",
        content="삼성전자가 4분기 실적을 발표했습니다.",
        source=NewsSource.NAVER_FINANCE,
        published_at=datetime(2024, 6, 1, 9, 0),
    )
    defaults.update(overrides)
    return defaults


@pytest.mark.smoke
class TestNewsDataValid:
    def test_basic_creation(self):
        n = NewsData(**_valid_news())
        assert n.ticker == "005930"
        assert n.source == NewsSource.NAVER_FINANCE

    def test_with_sentiment(self):
        n = NewsData(**_valid_news(sentiment_score=0.8, sentiment_label="POSITIVE"))
        assert n.sentiment_score == 0.8
        assert n.sentiment_label == "POSITIVE"

    def test_sentiment_label_case_insensitive(self):
        n = NewsData(**_valid_news(sentiment_label="positive"))
        assert n.sentiment_label == "POSITIVE"

    def test_neutral_sentiment(self):
        n = NewsData(**_valid_news(sentiment_score=0.0, sentiment_label="NEUTRAL"))
        assert n.sentiment_score == 0.0

    def test_negative_sentiment(self):
        n = NewsData(**_valid_news(sentiment_score=-0.9, sentiment_label="NEGATIVE"))
        assert n.sentiment_score == -0.9

    def test_with_url(self):
        n = NewsData(**_valid_news(url="https://example.com/news/1"))
        assert n.url == "https://example.com/news/1"

    def test_reuters_source(self):
        n = NewsData(**_valid_news(source=NewsSource.REUTERS))
        assert n.source == NewsSource.REUTERS


@pytest.mark.smoke
class TestNewsDataInvalid:
    def test_sentiment_out_of_range_high(self):
        with pytest.raises(ValidationError, match="less than or equal to 1"):
            NewsData(**_valid_news(sentiment_score=1.5))

    def test_sentiment_out_of_range_low(self):
        with pytest.raises(ValidationError, match="greater than or equal to -1"):
            NewsData(**_valid_news(sentiment_score=-1.5))

    def test_invalid_sentiment_label(self):
        with pytest.raises(ValidationError, match="sentiment_label"):
            NewsData(**_valid_news(sentiment_label="BULLISH"))

    def test_empty_title(self):
        with pytest.raises(ValidationError):
            NewsData(**_valid_news(title=""))

    def test_empty_content(self):
        with pytest.raises(ValidationError):
            NewsData(**_valid_news(content=""))

    def test_extra_field(self):
        with pytest.raises(ValidationError, match="Extra inputs"):
            NewsData(**_valid_news(category="economy"))

    def test_content_too_long(self):
        with pytest.raises(ValidationError, match="100,000"):
            NewsData(**_valid_news(content="x" * 100_001))

    def test_empty_ticker(self):
        with pytest.raises(ValidationError):
            NewsData(**_valid_news(ticker=""))

    def test_immutable(self):
        n = NewsData(**_valid_news())
        with pytest.raises(ValidationError):
            n.title = "changed"

    def test_url_too_long(self):
        with pytest.raises(ValidationError):
            NewsData(**_valid_news(url="https://x.com/" + "a" * 2048))
