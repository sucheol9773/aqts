"""
Comprehensive unit tests for SocialCollectorService and Reddit data collector

Tests cover:
- Reddit OAuth2 token management
- Reddit post collection (hot/new sorting)
- Comment fetching
- Financial content filtering
- Spam detection
- Ticker extraction from text
- Sentiment keyword extraction
- MongoDB storage operations
"""

import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, call
import httpx

from core.data_collector.social_collector import (
    SocialPost,
    RedditOAuth2Manager,
    RedditCollector,
    SocialCollectorService,
    extract_tickers,
    is_financial_content,
    is_spam,
    extract_sentiment_keywords,
    FINANCIAL_KEYWORDS,
    FINANCIAL_KEYWORDS_KR,
    FINANCIAL_KEYWORDS_EN,
    ADVERTISEMENT_PATTERNS,
)


@pytest.mark.smoke
class TestSocialPostDataclass:
    """Tests for SocialPost dataclass"""

    def test_social_post_initialization(self):
        """Test SocialPost initialization with required fields"""
        post = SocialPost(
            post_id="reddit_abc123",
            platform="REDDIT",
            subreddit="ko_stocks",
            title="삼성전자 실적 분석",
            content="삼성전자는 좋은 실적을 기록했습니다",
            author="user123",
            score=45,
            num_comments=12,
            url="https://reddit.com/r/ko_stocks/abc123",
            published_at=datetime.now(timezone.utc),
        )

        assert post.post_id == "reddit_abc123"
        assert post.platform == "REDDIT"
        assert post.subreddit == "ko_stocks"
        assert post.title == "삼성전자 실적 분석"
        assert post.author == "user123"
        assert post.is_filtered is False

    def test_social_post_to_dict(self):
        """Test SocialPost to_dict conversion"""
        now = datetime.now(timezone.utc)
        post = SocialPost(
            post_id="reddit_abc123",
            platform="REDDIT",
            subreddit="ko_stocks",
            title="삼성전자 실적",
            content="좋은 실적",
            author="user123",
            score=45,
            num_comments=12,
            url="https://reddit.com/r/ko_stocks",
            published_at=now,
            tickers=["005930"],
            sentiment_keywords=["긍정_good"],
        )

        result = post.to_dict()

        assert result["post_id"] == "reddit_abc123"
        assert result["platform"] == "REDDIT"
        assert result["tickers"] == ["005930"]
        assert result["sentiment_keywords"] == ["긍정_good"]
        assert "published_at" in result
        assert "collected_at" in result

    def test_social_post_with_metadata(self):
        """Test SocialPost with metadata"""
        post = SocialPost(
            post_id="reddit_abc123",
            platform="REDDIT",
            subreddit="ko_stocks",
            title="Test",
            content="Content",
            author="user123",
            score=45,
            num_comments=12,
            url="https://reddit.com/r/ko_stocks",
            published_at=datetime.now(timezone.utc),
            metadata={"upvotes": 50, "downvotes": 5, "is_self": True},
        )

        assert post.metadata["upvotes"] == 50
        assert post.metadata["downvotes"] == 5
        assert post.metadata["is_self"] is True


@pytest.mark.smoke
class TestRedditOAuth2Manager:
    """Tests for RedditOAuth2Manager"""

    def test_oauth_manager_initialization(self):
        """Test RedditOAuth2Manager initialization"""
        manager = RedditOAuth2Manager(
            client_id="test_client_id",
            client_secret="test_secret",
            user_agent="TestBot/1.0"
        )

        assert manager._client_id == "test_client_id"
        assert manager._client_secret == "test_secret"
        assert manager.is_available is True

    def test_oauth_manager_is_available_returns_false_when_missing_credentials(self):
        """Test is_available returns False when credentials are missing"""
        manager = RedditOAuth2Manager(
            client_id="",
            client_secret="test_secret",
            user_agent="TestBot/1.0"
        )

        assert manager.is_available is False

    @pytest.mark.asyncio
    async def test_get_valid_token_success(self):
        """Test successful OAuth2 token acquisition"""
        manager = RedditOAuth2Manager(
            client_id="test_client_id",
            client_secret="test_secret",
            user_agent="TestBot/1.0"
        )

        with patch("core.data_collector.social_collector.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "access_token": "test_token_123",
                "expires_in": 3600,
            }
            mock_client.post.return_value = mock_response

            token = await manager.get_valid_token()

            assert token == "test_token_123"
            assert manager._access_token == "test_token_123"
            assert manager._token_expiry is not None

    @pytest.mark.asyncio
    async def test_get_valid_token_returns_cached_token_if_valid(self):
        """Test get_valid_token returns cached token if still valid"""
        manager = RedditOAuth2Manager(
            client_id="test_client_id",
            client_secret="test_secret",
            user_agent="TestBot/1.0"
        )

        # Set a token that expires far in the future
        manager._access_token = "cached_token"
        manager._token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)

        token = await manager.get_valid_token()

        assert token == "cached_token"

    @pytest.mark.asyncio
    async def test_get_valid_token_returns_none_when_not_available(self):
        """Test get_valid_token returns None when credentials not available"""
        manager = RedditOAuth2Manager(
            client_id="",
            client_secret="",
            user_agent="TestBot/1.0"
        )

        token = await manager.get_valid_token()

        assert token is None

    @pytest.mark.asyncio
    async def test_get_valid_token_handles_api_error(self):
        """Test get_valid_token handles API errors"""
        manager = RedditOAuth2Manager(
            client_id="test_client_id",
            client_secret="test_secret",
            user_agent="TestBot/1.0"
        )

        with patch("core.data_collector.social_collector.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client.post.side_effect = Exception("API Error")

            token = await manager.get_valid_token()

            assert token is None


@pytest.mark.smoke
class TestExtractTickers:
    """Tests for extract_tickers function"""

    def test_extract_tickers_korean_code_pattern(self):
        """Test extracting Korean ticker codes (6-digit pattern)"""
        text = "삼성전자(005930)가 좋은 실적을 기록했습니다"
        tickers = extract_tickers(text)

        assert "005930" in tickers

    def test_extract_tickers_korean_company_name(self):
        """Test extracting tickers from Korean company names"""
        text = "삼성전자와 SK하이닉스는 좋은 주식입니다"
        tickers = extract_tickers(text)

        assert "005930" in tickers
        assert "000660" in tickers

    def test_extract_tickers_us_company_name(self):
        """Test extracting US ticker codes from company names"""
        text = "Apple and Microsoft are great stocks to own"
        tickers = extract_tickers(text)

        assert "AAPL" in tickers
        assert "MSFT" in tickers

    def test_extract_tickers_multiple_formats(self):
        """Test extracting tickers from mixed Korean and US names"""
        text = "삼성전자(005930)와 AAPL, 현대차(005380)를 추천합니다"
        tickers = extract_tickers(text)

        assert "005930" in tickers
        assert "AAPL" in tickers
        assert "005380" in tickers

    def test_extract_tickers_filters_date_patterns(self):
        """Test that date patterns (2023, 2024) are not extracted as tickers"""
        text = "2023년 2024년에는 매우 좋았습니다"
        tickers = extract_tickers(text)

        # Should be empty or only contain valid tickers, not dates
        assert "2023" not in tickers
        assert "2024" not in tickers

    def test_extract_tickers_returns_sorted_list(self):
        """Test that extracted tickers are returned as sorted list"""
        text = "삼성전자(005930)와 AAPL, LG화학(051910)"
        tickers = extract_tickers(text)

        assert isinstance(tickers, list)
        assert tickers == sorted(tickers)

    def test_extract_tickers_empty_text(self):
        """Test extracting tickers from empty text"""
        tickers = extract_tickers("")

        assert len(tickers) == 0

    def test_extract_tickers_no_tickers_found(self):
        """Test text with no ticker references"""
        text = "이것은 티커가 없는 일반적인 텍스트입니다"
        tickers = extract_tickers(text)

        assert len(tickers) == 0


@pytest.mark.smoke
class TestIsFinancialContent:
    """Tests for is_financial_content function"""

    def test_is_financial_content_with_korean_keyword(self):
        """Test financial content detection with Korean keywords"""
        text = "삼성전자에 매수 신호가 있습니다"
        assert is_financial_content(text) is True

    def test_is_financial_content_with_english_keyword(self):
        """Test financial content detection with English keywords"""
        text = "Apple stock price is bullish"
        assert is_financial_content(text) is True

    def test_is_financial_content_with_multiple_keywords(self):
        """Test financial content with multiple keywords"""
        text = "배당 수익률이 좋은 주식 매수 추천"
        assert is_financial_content(text) is True

    def test_is_financial_content_case_insensitive(self):
        """Test financial content detection is case-insensitive"""
        text = "This STOCK is BULLISH"
        assert is_financial_content(text) is True

    def test_is_financial_content_non_financial_text(self):
        """Test non-financial content is not detected"""
        text = "오늘 날씨가 좋네요"
        assert is_financial_content(text) is False

    def test_is_financial_content_empty_text(self):
        """Test empty text returns False"""
        assert is_financial_content("") is False


@pytest.mark.smoke
class TestIsSpam:
    """Tests for is_spam function"""

    def test_is_spam_short_content(self):
        """Test spam detection for short content"""
        current_time = datetime.now(timezone.utc)
        assert is_spam("abc", "user1", {}, current_time) is True

    def test_is_spam_advertisement_pattern(self):
        """Test spam detection for advertisement patterns"""
        current_time = datetime.now(timezone.utc)
        content = "[AD] Check this out!"
        assert is_spam(content, "user1", {}, current_time) is True

    def test_is_spam_promoted_pattern(self):
        """Test spam detection for promoted content"""
        current_time = datetime.now(timezone.utc)
        content = "Promoted: Click here now"
        assert is_spam(content, "user1", {}, current_time) is True

    def test_is_spam_author_spam_detection(self):
        """Test spam detection for author posting too frequently"""
        current_time = datetime.now(timezone.utc)
        author_post_count = {
            "spam_user": "spam_user",  # author key exists
            "spam_user_times": [
                current_time - timedelta(minutes=5),
                current_time - timedelta(minutes=10),
                current_time - timedelta(minutes=15),
                current_time - timedelta(minutes=20),
                current_time - timedelta(minutes=25),
            ]
        }
        content = "This is legitimate content that is at least 10 characters long"
        assert is_spam(content, "spam_user", author_post_count, current_time) is True

    def test_is_spam_url_only_content(self):
        """Test spam detection for URL-only content"""
        current_time = datetime.now(timezone.utc)
        content = "https://example.com https://another.com"
        assert is_spam(content, "user1", {}, current_time) is True

    def test_is_spam_legitimate_content(self):
        """Test legitimate content is not flagged as spam"""
        current_time = datetime.now(timezone.utc)
        content = "This is a legitimate analysis of the market conditions"
        assert is_spam(content, "user1", {}, current_time) is False

    def test_is_spam_content_with_url_and_text(self):
        """Test content with URL and text is not spam"""
        current_time = datetime.now(timezone.utc)
        content = "Check this analysis: https://example.com with detailed information"
        assert is_spam(content, "user1", {}, current_time) is False


@pytest.mark.smoke
class TestExtractSentimentKeywords:
    """Tests for extract_sentiment_keywords function"""

    def test_extract_sentiment_keywords_positive(self):
        """Test extraction of positive sentiment keywords"""
        text = "This stock is good and excellent"
        keywords = extract_sentiment_keywords(text)

        assert any("긍정_good" in kw for kw in keywords)
        assert any("긍정_excellent" in kw for kw in keywords)

    def test_extract_sentiment_keywords_negative(self):
        """Test extraction of negative sentiment keywords"""
        text = "This is bad and worst company"
        keywords = extract_sentiment_keywords(text)

        assert any("부정_bad" in kw for kw in keywords)
        assert any("부정_worst" in kw for kw in keywords)

    def test_extract_sentiment_keywords_neutral(self):
        """Test extraction of neutral sentiment keywords"""
        text = "This is neutral and maybe unclear"
        keywords = extract_sentiment_keywords(text)

        assert any("중립_neutral" in kw for kw in keywords)
        assert any("중립_maybe" in kw for kw in keywords)

    def test_extract_sentiment_keywords_bullish_bearish(self):
        """Test extraction of financial sentiment keywords"""
        text = "Market is bullish, avoid bearish trends"
        keywords = extract_sentiment_keywords(text)

        assert any("긍정_bullish" in kw for kw in keywords)
        assert any("부정_bearish" in kw for kw in keywords)

    def test_extract_sentiment_keywords_empty_text(self):
        """Test extraction from empty text"""
        keywords = extract_sentiment_keywords("")

        assert len(keywords) == 0

    def test_extract_sentiment_keywords_no_matches(self):
        """Test text with no sentiment keywords"""
        text = "This is a random text with no sentiment"
        keywords = extract_sentiment_keywords(text)

        assert len(keywords) == 0

    def test_extract_sentiment_keywords_case_insensitive(self):
        """Test sentiment extraction is case-insensitive"""
        text = "GREAT and BEST analysis"
        keywords = extract_sentiment_keywords(text)

        assert any("긍정" in kw for kw in keywords)


@pytest.mark.smoke
class TestRedditCollector:
    """Tests for RedditCollector"""

    @pytest.mark.asyncio
    async def test_collector_initialization(self):
        """Test RedditCollector initialization"""
        mock_oauth = MagicMock()
        collector = RedditCollector(mock_oauth)

        assert collector._oauth == mock_oauth
        assert collector._timeout == 15
        assert collector._max_retries == 3

    @pytest.mark.asyncio
    async def test_collect_all_no_token(self):
        """Test collect_all returns empty list when token unavailable"""
        mock_oauth = AsyncMock()
        mock_oauth.get_valid_token.return_value = None

        collector = RedditCollector(mock_oauth)
        result = await collector.collect_all(limit=25)

        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_posts_by_sort_success(self):
        """Test successful fetching of posts by sort type"""
        mock_oauth = MagicMock()
        collector = RedditCollector(mock_oauth)

        with patch("core.data_collector.social_collector.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "data": {
                    "children": [
                        {
                            "data": {
                                "id": "abc123",
                                "title": "삼성전자 분석",
                                "selftext": "Good company",
                                "author": "user1",
                                "ups": 50,
                                "downs": 5,
                                "num_comments": 10,
                                "is_self": True,
                                "permalink": "/r/ko_stocks/abc123",
                                "created_utc": 1640000000,
                            }
                        }
                    ]
                }
            }
            mock_client.get.return_value = mock_response

            with patch.object(collector, "_get_user_agent", new_callable=AsyncMock) as mock_ua:
                mock_ua.return_value = "TestBot/1.0"

                with patch.object(collector, "_fetch_comments", new_callable=AsyncMock) as mock_comments:
                    mock_comments.return_value = []

                    result = await collector._fetch_posts_by_sort(
                        subreddit="ko_stocks",
                        sort_by="hot",
                        token="test_token",
                        limit=25
                    )

                    assert len(result) == 1
                    assert result[0].platform == "REDDIT"
                    assert result[0].subreddit == "ko_stocks"
                    assert result[0].title == "삼성전자 분석"

    @pytest.mark.asyncio
    async def test_fetch_comments_success(self):
        """Test successful fetching of comments"""
        mock_oauth = MagicMock()
        collector = RedditCollector(mock_oauth)

        with patch("core.data_collector.social_collector.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.json.return_value = [
                {},
                {
                    "data": {
                        "children": [
                            {"data": {"body": "Great analysis"}},
                            {"data": {"body": "I agree with this"}},
                        ]
                    }
                }
            ]
            mock_client.get.return_value = mock_response

            with patch.object(collector, "_get_user_agent", new_callable=AsyncMock) as mock_ua:
                mock_ua.return_value = "TestBot/1.0"

                comments = await collector._fetch_comments(
                    permalink="/r/ko_stocks/abc123",
                    token="test_token",
                    limit=5
                )

                assert len(comments) == 2
                assert "Great analysis" in comments
                assert "I agree with this" in comments

    @pytest.mark.asyncio
    async def test_fetch_comments_filters_deleted(self):
        """Test fetch_comments filters out deleted comments"""
        mock_oauth = MagicMock()
        collector = RedditCollector(mock_oauth)

        with patch("core.data_collector.social_collector.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.json.return_value = [
                {},
                {
                    "data": {
                        "children": [
                            {"data": {"body": "Good comment"}},
                            {"data": {"body": "[deleted by user]"}},
                        ]
                    }
                }
            ]
            mock_client.get.return_value = mock_response

            with patch.object(collector, "_get_user_agent", new_callable=AsyncMock) as mock_ua:
                mock_ua.return_value = "TestBot/1.0"

                comments = await collector._fetch_comments(
                    permalink="/r/ko_stocks/abc123",
                    token="test_token",
                    limit=5
                )

                assert len(comments) == 1
                assert comments[0] == "Good comment"


@pytest.mark.smoke
class TestSocialCollectorService:
    """Tests for SocialCollectorService"""

    def test_service_initialization(self):
        """Test SocialCollectorService initialization"""
        with patch("core.data_collector.social_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.reddit_client_id = "test_client_id"
            mock_settings.return_value.external.reddit_client_secret = "test_secret"
            mock_settings.return_value.external.reddit_user_agent = "TestBot/1.0"

            service = SocialCollectorService()

            assert service._oauth_manager is not None
            assert service._reddit_collector is not None

    def test_service_is_available_true(self):
        """Test is_available returns True when Reddit credentials configured"""
        with patch("core.data_collector.social_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.reddit_client_id = "test_client_id"
            mock_settings.return_value.external.reddit_client_secret = "test_secret"
            mock_settings.return_value.external.reddit_user_agent = "TestBot/1.0"

            service = SocialCollectorService()

            assert service.is_available is True

    def test_service_is_available_false(self):
        """Test is_available returns False when Reddit credentials missing"""
        with patch("core.data_collector.social_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.reddit_client_id = ""
            mock_settings.return_value.external.reddit_client_secret = ""
            mock_settings.return_value.external.reddit_user_agent = "TestBot/1.0"

            service = SocialCollectorService()

            assert service.is_available is False

    @pytest.mark.asyncio
    async def test_collect_reddit_posts_not_available(self):
        """Test collect_reddit_posts returns empty list when service unavailable"""
        with patch("core.data_collector.social_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.reddit_client_id = ""
            mock_settings.return_value.external.reddit_client_secret = ""
            mock_settings.return_value.external.reddit_user_agent = "TestBot/1.0"

            service = SocialCollectorService()
            result = await service.collect_reddit_posts(limit=25)

            assert result == []

    def test_filter_financial_posts_success(self):
        """Test filtering of financial posts"""
        with patch("core.data_collector.social_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.reddit_client_id = "test"
            mock_settings.return_value.external.reddit_client_secret = "test"
            mock_settings.return_value.external.reddit_user_agent = "TestBot/1.0"

            service = SocialCollectorService()

            posts = [
                SocialPost(
                    post_id="1",
                    platform="REDDIT",
                    subreddit="ko_stocks",
                    title="삼성전자 매수 추천",
                    content="삼성전자는 좋은 주식입니다",
                    author="user1",
                    score=10,
                    num_comments=5,
                    url="https://reddit.com",
                    published_at=datetime.now(timezone.utc),
                ),
                SocialPost(
                    post_id="2",
                    platform="REDDIT",
                    subreddit="ko_stocks",
                    title="오늘 날씨",
                    content="날씨가 좋네요",
                    author="user2",
                    score=5,
                    num_comments=2,
                    url="https://reddit.com",
                    published_at=datetime.now(timezone.utc),
                ),
            ]

            result = service.filter_financial_posts(posts)

            # Only the first post is financial
            assert len(result) == 1
            assert result[0].post_id == "1"
            assert result[0].is_filtered is False

    def test_filter_financial_posts_extracts_tickers(self):
        """Test that filtering extracts ticker information"""
        with patch("core.data_collector.social_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.reddit_client_id = "test"
            mock_settings.return_value.external.reddit_client_secret = "test"
            mock_settings.return_value.external.reddit_user_agent = "TestBot/1.0"

            service = SocialCollectorService()

            posts = [
                SocialPost(
                    post_id="1",
                    platform="REDDIT",
                    subreddit="ko_stocks",
                    title="삼성전자(005930) 매수",
                    content="삼성전자는 좋은 투자입니다",
                    author="user1",
                    score=10,
                    num_comments=5,
                    url="https://reddit.com",
                    published_at=datetime.now(timezone.utc),
                ),
            ]

            result = service.filter_financial_posts(posts)

            assert len(result) == 1
            assert "005930" in result[0].tickers

    def test_filter_financial_posts_extracts_sentiment(self):
        """Test that filtering extracts sentiment keywords"""
        with patch("core.data_collector.social_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.reddit_client_id = "test"
            mock_settings.return_value.external.reddit_client_secret = "test"
            mock_settings.return_value.external.reddit_user_agent = "TestBot/1.0"

            service = SocialCollectorService()

            posts = [
                SocialPost(
                    post_id="1",
                    platform="REDDIT",
                    subreddit="ko_stocks",
                    title="삼성전자 great stock to buy",
                    content="This is good and bullish",
                    author="user1",
                    score=10,
                    num_comments=5,
                    url="https://reddit.com",
                    published_at=datetime.now(timezone.utc),
                ),
            ]

            result = service.filter_financial_posts(posts)

            assert len(result) == 1
            assert len(result[0].sentiment_keywords) > 0

    def test_filter_financial_posts_filters_spam(self):
        """Test that spam posts are filtered out"""
        with patch("core.data_collector.social_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.reddit_client_id = "test"
            mock_settings.return_value.external.reddit_client_secret = "test"
            mock_settings.return_value.external.reddit_user_agent = "TestBot/1.0"

            service = SocialCollectorService()

            posts = [
                SocialPost(
                    post_id="1",
                    platform="REDDIT",
                    subreddit="ko_stocks",
                    title="삼성전자 주식",
                    content="[AD] 주식을 사세요",
                    author="user1",
                    score=10,
                    num_comments=5,
                    url="https://reddit.com",
                    published_at=datetime.now(timezone.utc),
                ),
            ]

            result = service.filter_financial_posts(posts)

            # Spam post should be filtered
            assert len(result) == 0

    @pytest.mark.asyncio
    async def test_save_to_db_success(self):
        """Test successful saving of posts to MongoDB"""
        with patch("core.data_collector.social_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.reddit_client_id = "test"
            mock_settings.return_value.external.reddit_client_secret = "test"
            mock_settings.return_value.external.reddit_user_agent = "TestBot/1.0"

            service = SocialCollectorService()

            posts = [
                SocialPost(
                    post_id="1",
                    platform="REDDIT",
                    subreddit="ko_stocks",
                    title="Test",
                    content="Content",
                    author="user1",
                    score=10,
                    num_comments=5,
                    url="https://reddit.com",
                    published_at=datetime.now(timezone.utc),
                ),
            ]

            with patch("core.data_collector.social_collector.MongoDBManager.get_collection") as mock_get_coll:
                mock_collection = AsyncMock()
                mock_get_coll.return_value = mock_collection

                # Mock insert_one to succeed
                mock_collection.insert_one.return_value = MagicMock()

                result = await service.save_to_db(posts)

                assert result["total"] == 1
                assert result["new_stored"] == 1
                assert result["duplicates_skipped"] == 0

    @pytest.mark.asyncio
    async def test_save_to_db_handles_duplicates(self):
        """Test save_to_db handles duplicate key errors"""
        with patch("core.data_collector.social_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.reddit_client_id = "test"
            mock_settings.return_value.external.reddit_client_secret = "test"
            mock_settings.return_value.external.reddit_user_agent = "TestBot/1.0"

            service = SocialCollectorService()

            posts = [
                SocialPost(
                    post_id="1",
                    platform="REDDIT",
                    subreddit="ko_stocks",
                    title="Test",
                    content="Content",
                    author="user1",
                    score=10,
                    num_comments=5,
                    url="https://reddit.com",
                    published_at=datetime.now(timezone.utc),
                ),
                SocialPost(
                    post_id="2",
                    platform="REDDIT",
                    subreddit="ko_stocks",
                    title="Test 2",
                    content="Content 2",
                    author="user2",
                    score=5,
                    num_comments=2,
                    url="https://reddit.com",
                    published_at=datetime.now(timezone.utc),
                ),
            ]

            with patch("core.data_collector.social_collector.MongoDBManager.get_collection") as mock_get_coll:
                mock_collection = AsyncMock()
                mock_get_coll.return_value = mock_collection

                # First post succeeds, second fails (duplicate)
                mock_collection.insert_one.side_effect = [
                    MagicMock(),
                    Exception("Duplicate key error"),
                ]

                result = await service.save_to_db(posts)

                assert result["total"] == 2
                assert result["new_stored"] == 1
                assert result["duplicates_skipped"] == 1

    @pytest.mark.asyncio
    async def test_get_recent_posts(self):
        """Test retrieving recent posts from MongoDB"""
        with patch("core.data_collector.social_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.reddit_client_id = "test"
            mock_settings.return_value.external.reddit_client_secret = "test"
            mock_settings.return_value.external.reddit_user_agent = "TestBot/1.0"

            service = SocialCollectorService()

            with patch("core.data_collector.social_collector.MongoDBManager.get_collection") as mock_get_coll:
                mock_collection = AsyncMock()
                mock_get_coll.return_value = mock_collection

                # Create a mock cursor that supports chaining: find().sort().limit().to_list()
                mock_cursor = AsyncMock()
                mock_cursor.to_list = AsyncMock(return_value=[
                    {"post_id": "1", "title": "Test"},
                    {"post_id": "2", "title": "Test 2"},
                ])
                # Mock the sort() method to return the cursor (for chaining)
                mock_cursor.sort = MagicMock(return_value=mock_cursor)
                # Mock the limit() method to return the cursor (for chaining)
                mock_cursor.limit = MagicMock(return_value=mock_cursor)
                # Mock find() to return the cursor
                mock_collection.find = MagicMock(return_value=mock_cursor)

                result = await service.get_recent_posts(
                    tickers=["005930"],
                    hours=24,
                    limit=50
                )

                assert len(result) == 2
                assert result[0]["post_id"] == "1"

    @pytest.mark.asyncio
    async def test_collect_and_store_full_pipeline(self):
        """Test full collection and storage pipeline"""
        with patch("core.data_collector.social_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.reddit_client_id = "test"
            mock_settings.return_value.external.reddit_client_secret = "test"
            mock_settings.return_value.external.reddit_user_agent = "TestBot/1.0"

            service = SocialCollectorService()

            with patch.object(service, "collect_reddit_posts", new_callable=AsyncMock) as mock_collect:
                with patch.object(service, "filter_financial_posts") as mock_filter:
                    with patch.object(service, "save_to_db", new_callable=AsyncMock) as mock_save:
                        # Setup mock returns
                        raw_post = SocialPost(
                            post_id="1",
                            platform="REDDIT",
                            subreddit="ko_stocks",
                            title="삼성전자",
                            content="좋은 주식입니다",
                            author="user1",
                            score=10,
                            num_comments=5,
                            url="https://reddit.com",
                            published_at=datetime.now(timezone.utc),
                        )
                        mock_collect.return_value = [raw_post]

                        filtered_post = raw_post
                        filtered_post.tickers = ["005930"]
                        mock_filter.return_value = [filtered_post]

                        mock_save.return_value = {
                            "total": 1,
                            "new_stored": 1,
                            "duplicates_skipped": 0,
                        }

                        result = await service.collect_and_store()

                        assert result["collected"] == 1
                        assert result["filtered"] == 1
                        assert result["stored"] == 1


@pytest.mark.smoke
class TestFinancialKeywords:
    """Tests for financial keywords constants"""

    def test_financial_keywords_kr_contains_expected_keywords(self):
        """Test Korean financial keywords are defined"""
        assert "주식" in FINANCIAL_KEYWORDS_KR
        assert "매수" in FINANCIAL_KEYWORDS_KR
        assert "배당" in FINANCIAL_KEYWORDS_KR

    def test_financial_keywords_en_contains_expected_keywords(self):
        """Test English financial keywords are defined"""
        assert "stock" in FINANCIAL_KEYWORDS_EN
        assert "buy" in FINANCIAL_KEYWORDS_EN
        assert "dividend" in FINANCIAL_KEYWORDS_EN

    def test_financial_keywords_combined(self):
        """Test combined financial keywords"""
        assert "주식" in FINANCIAL_KEYWORDS
        assert "stock" in FINANCIAL_KEYWORDS


@pytest.mark.smoke
class TestAdvertisementPatterns:
    """Tests for advertisement pattern definitions"""

    def test_advertisement_patterns_defined(self):
        """Test advertisement patterns are defined"""
        assert len(ADVERTISEMENT_PATTERNS) > 0

    def test_advertisement_patterns_match_examples(self):
        """Test advertisement patterns match expected examples"""
        patterns_list = list(ADVERTISEMENT_PATTERNS)
        assert any("[AD]" in p for p in patterns_list) or any(r"\[AD\]" in p for p in patterns_list)
