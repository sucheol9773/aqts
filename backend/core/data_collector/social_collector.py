"""
Reddit SNS 데이터 수집 모듈 (Reddit Social Media Collector)

Phase 3 - F-01-03 구현:
- Reddit OAuth2 인증 (httpx 기반)
- 한국/글로벌 투자 서브레딧 수집
- 게시물 + 댓글 상위 5개 수집
- 금융 키워드 필터링
- 종목 코드 자동 태깅 (news_collector.py의 패턴 재사용)
- 스팸/광고 필터링
- MongoDB 저장

사용 라이브러리: httpx 0.27.0
"""

import asyncio
import base64
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from config.logging import logger
from config.settings import get_settings
from db.database import MongoDBManager

# ══════════════════════════════════════
# 금융 키워드 정의
# ══════════════════════════════════════
FINANCIAL_KEYWORDS_KR = {
    "주식",
    "매수",
    "매도",
    "상장",
    "공매도",
    "코스피",
    "코스닥",
    "배당",
    "실적",
    "상한가",
    "하한가",
    "급등",
    "급락",
    "수익",
    "손실",
    "수익률",
    "포트폴리오",
    "주가",
    "종목",
    "투자",
}

FINANCIAL_KEYWORDS_EN = {
    "stock",
    "buy",
    "sell",
    "bullish",
    "bearish",
    "earnings",
    "dividend",
    "ipo",
    "short",
    "long",
    "market",
    "trading",
    "price",
    "ticker",
    "portfolio",
    "profit",
    "loss",
    "investment",
}

# 전체 키워드 세트 (대소문자 무시)
FINANCIAL_KEYWORDS = FINANCIAL_KEYWORDS_KR | {kw.lower() for kw in FINANCIAL_KEYWORDS_EN}

# 광고 태그 패턴
ADVERTISEMENT_PATTERNS = {
    r"\[AD\]",
    r"\[PROMO\]",
    r"\[SPONSORED\]",
    r"^Promoted",
    r"^Advertising",
}

# 한국 종목코드 패턴 (6자리 숫자)
_KR_TICKER_PATTERN = re.compile(r"\b(\d{6})\b")

# 주요 종목명 ↔ 코드 매핑 (news_collector.py와 동일)
_KR_NAME_TO_TICKER = {
    "삼성전자": "005930",
    "SK하이닉스": "000660",
    "LG에너지솔루션": "373220",
    "삼성바이오로직스": "207940",
    "현대차": "005380",
    "현대자동차": "005380",
    "기아": "000270",
    "셀트리온": "068270",
    "KB금융": "105560",
    "POSCO홀딩스": "005490",
    "포스코홀딩스": "005490",
    "NAVER": "035420",
    "네이버": "035420",
    "카카오": "035720",
    "LG화학": "051910",
    "삼성SDI": "006400",
    "현대모비스": "012330",
    "신한지주": "055550",
    "SK이노베이션": "096770",
    "하나금융지주": "086790",
    "삼성물산": "028260",
    "LG전자": "066570",
    "SK텔레콤": "017670",
    "카카오뱅크": "323410",
    "두산에너빌리티": "034020",
    "에코프로비엠": "247540",
    "에코프로": "086520",
    "한화에어로스페이스": "012450",
}

# 미국 종목 코드 매핑 (상위 기업들)
_US_NAME_TO_TICKER = {
    "AAPL": "AAPL",
    "Apple": "AAPL",
    "MSFT": "MSFT",
    "Microsoft": "MSFT",
    "GOOGL": "GOOGL",
    "Google": "GOOGL",
    "AMZN": "AMZN",
    "Amazon": "AMZN",
    "TSLA": "TSLA",
    "Tesla": "TSLA",
    "META": "META",
    "Facebook": "META",
    "NVDA": "NVDA",
    "Nvidia": "NVDA",
    "JPM": "JPM",
    "JPMorgan": "JPM",
}


def extract_tickers(text: str) -> list[str]:
    """
    텍스트에서 종목코드 추출

    1단계: 한국 6자리 코드 패턴 매칭
    2단계: 한국 종목명 사전 매칭
    3단계: 미국 종목명 사전 매칭
    """
    tickers = set()

    # 한국: 6자리 코드 직접 매칭
    for match in _KR_TICKER_PATTERN.finditer(text):
        code = match.group(1)
        # 날짜 등 오탐 방지: 앞뒤 문맥 확인
        if code.startswith(("20", "19")):
            continue
        tickers.add(code)

    # 한국: 종목명 사전 매칭
    for name, code in _KR_NAME_TO_TICKER.items():
        if name in text:
            tickers.add(code)

    # 미국: 종목명 사전 매칭
    for name, code in _US_NAME_TO_TICKER.items():
        if name in text or name.upper() in text.upper():
            tickers.add(code)

    return sorted(tickers)


def is_financial_content(text: str) -> bool:
    """금융 관련 키워드 포함 여부 확인"""
    text_lower = text.lower()
    return any(kw in text_lower for kw in FINANCIAL_KEYWORDS)


def is_spam(
    content: str,
    author: str,
    author_post_count: dict[str, int],
    current_time: datetime,
) -> bool:
    """
    스팸 필터링

    조건:
    - 길이 < 10자
    - 광고 태그 포함
    - 동일 저자의 1시간 내 5회 이상 게시
    - URL만으로 구성된 게시물
    """
    # 길이 필터
    if len(content.strip()) < 10:
        return True

    # 광고 태그 필터
    for pattern in ADVERTISEMENT_PATTERNS:
        if re.search(pattern, content):
            return True

    # 동일 저자의 1시간 내 5회 이상 게시 필터
    if author in author_post_count:
        posts_in_last_hour = [
            ts for ts in author_post_count.get(f"{author}_times", []) if (current_time - ts).total_seconds() < 3600
        ]
        if len(posts_in_last_hour) >= 5:
            return True

    # URL만으로 구성된 게시물 필터
    url_pattern = re.compile(r"https?://\S+")
    non_url_content = url_pattern.sub("", content).strip()
    if not non_url_content:
        return True

    return False


def extract_sentiment_keywords(text: str) -> list[str]:
    """감성 관련 키워드 추출"""
    sentiment_keywords_map = {
        "긍정": ["good", "excellent", "best", "awesome", "great", "amazing", "buy", "bullish"],
        "부정": ["bad", "worst", "terrible", "avoid", "sell", "bearish", "loss", "fail"],
        "중립": ["neutral", "hmm", "maybe", "unclear", "uncertain"],
    }

    found = []
    text_lower = text.lower()

    for sentiment, keywords in sentiment_keywords_map.items():
        for kw in keywords:
            if kw in text_lower:
                found.append(f"{sentiment}_{kw}")

    return found


# ══════════════════════════════════════
# 수집 데이터 구조
# ══════════════════════════════════════
@dataclass
class SocialPost:
    """Reddit 소셜 게시물 데이터 컨테이너"""

    post_id: str  # 고유 ID (platform_id)
    platform: str  # "REDDIT"
    subreddit: str  # 서브레딧명
    title: str
    content: str  # 본문 + 댓글
    author: str
    score: int  # upvotes - downvotes
    num_comments: int
    url: str
    published_at: datetime
    collected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tickers: list[str] = field(default_factory=list)
    sentiment_keywords: list[str] = field(default_factory=list)
    is_filtered: bool = False
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """MongoDB 저장용 딕셔너리 변환"""
        return {
            "post_id": self.post_id,
            "platform": self.platform,
            "subreddit": self.subreddit,
            "title": self.title,
            "content": self.content,
            "author": self.author,
            "score": self.score,
            "num_comments": self.num_comments,
            "url": self.url,
            "published_at": self.published_at,
            "collected_at": self.collected_at,
            "tickers": self.tickers,
            "sentiment_keywords": self.sentiment_keywords,
            "is_filtered": self.is_filtered,
            "metadata": self.metadata,
        }


# ══════════════════════════════════════
# Reddit OAuth2 토큰 관리
# ══════════════════════════════════════
class RedditOAuth2Manager:
    """Reddit OAuth2 토큰 발급 및 관리"""

    TOKEN_URL = "https://www.reddit.com/api/v1/access_token"

    def __init__(self, client_id: str, client_secret: str, user_agent: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._user_agent = user_agent
        self._access_token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None
        self._timeout = 15

    @property
    def is_available(self) -> bool:
        """Reddit API 키 설정 여부"""
        return bool(self._client_id and self._client_secret)

    async def get_valid_token(self) -> Optional[str]:
        """
        유효한 액세스 토큰 반환

        토큰이 만료되었으면 새로 발급합니다.
        """
        if not self.is_available:
            return None

        # 토큰 유효성 확인 (5분 전에 갱신)
        if (
            self._access_token
            and self._token_expiry
            and datetime.now(timezone.utc) < self._token_expiry - timedelta(minutes=5)
        ):
            return self._access_token

        # 새 토큰 발급
        try:
            auth_string = f"{self._client_id}:{self._client_secret}"
            auth_b64 = base64.b64encode(auth_string.encode()).decode()

            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self.TOKEN_URL,
                    headers={
                        "Authorization": f"Basic {auth_b64}",
                        "User-Agent": self._user_agent,
                    },
                    data={"grant_type": "client_credentials"},
                )
                response.raise_for_status()

            data = response.json()
            self._access_token = data.get("access_token")
            expires_in = data.get("expires_in", 3600)
            self._token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

            logger.debug(f"Reddit OAuth2 token refreshed (expires in {expires_in}s)")
            return self._access_token

        except Exception as e:
            logger.error(f"Failed to obtain Reddit OAuth2 token: {e}")
            return None


# ══════════════════════════════════════
# Reddit 게시물 수집기
# ══════════════════════════════════════
class RedditCollector:
    """Reddit API 기반 게시물 수집기"""

    API_BASE_URL = "https://oauth.reddit.com"

    # 수집 대상 서브레딧
    SUBREDDITS = {
        "korean": [
            "KoreanStockMarket",
            "ko_stocks",
        ],
        "global": [
            "wallstreetbets",
            "stocks",
            "investing",
            "ValueInvesting",
        ],
    }

    def __init__(self, oauth_manager: RedditOAuth2Manager):
        self._oauth = oauth_manager
        self._timeout = 15
        self._max_retries = 3
        self._rate_limit_delay = 1  # 요청 간 최소 지연 (초)

    async def collect_all(self, limit: int = 25, comments_limit: int = 5) -> list[SocialPost]:
        """
        모든 서브레딧에서 게시물 수집

        Args:
            limit: 서브레딧당 수집 게시물 수
            comments_limit: 게시물당 수집 댓글 수

        Returns:
            수집된 SocialPost 리스트
        """
        all_posts: list[SocialPost] = []
        token = await self._oauth.get_valid_token()

        if not token:
            logger.error("Failed to obtain Reddit authentication token")
            return []

        # 한국 투자 서브레딧
        for subreddit in self.SUBREDDITS["korean"]:
            try:
                posts = await self._fetch_subreddit_posts(subreddit, token, limit, comments_limit)
                all_posts.extend(posts)
                await self._rate_limit_delay_async()
            except Exception as e:
                logger.warning(f"Failed to collect from r/{subreddit}: {e}")
                continue

        # 글로벌 투자 서브레딧
        for subreddit in self.SUBREDDITS["global"]:
            try:
                posts = await self._fetch_subreddit_posts(subreddit, token, limit, comments_limit)
                all_posts.extend(posts)
                await self._rate_limit_delay_async()
            except Exception as e:
                logger.warning(f"Failed to collect from r/{subreddit}: {e}")
                continue

        logger.info(f"Reddit collection complete: {len(all_posts)} total posts")
        return all_posts

    async def _fetch_subreddit_posts(
        self,
        subreddit: str,
        token: str,
        limit: int = 25,
        comments_limit: int = 5,
    ) -> list[SocialPost]:
        """특정 서브레딧에서 hot/new 게시물 수집"""
        posts: list[SocialPost] = []

        for sort_by in ["hot", "new"]:
            try:
                fetched = await self._fetch_posts_by_sort(subreddit, sort_by, token, limit, comments_limit)
                posts.extend(fetched)
                await self._rate_limit_delay_async()
            except Exception as e:
                logger.debug(f"Failed to fetch {sort_by} posts from r/{subreddit}: {e}")
                continue

        return posts

    async def _fetch_posts_by_sort(
        self,
        subreddit: str,
        sort_by: str,
        token: str,
        limit: int = 25,
        comments_limit: int = 5,
    ) -> list[SocialPost]:
        """
        특정 정렬 방식으로 게시물 수집

        Args:
            subreddit: 서브레딧명
            sort_by: "hot" 또는 "new"
            token: OAuth2 액세스 토큰
            limit: 수집 게시물 수
            comments_limit: 게시물당 댓글 수
        """
        url = f"{self.API_BASE_URL}/r/{subreddit}/{sort_by}"
        params = {"limit": min(limit, 25), "t": "day"}

        posts: list[SocialPost] = []

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(
                url,
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": (await self._get_user_agent()),
                },
            )
            response.raise_for_status()

        data = response.json()

        for item in data.get("data", {}).get("children", []):
            try:
                post_data = item.get("data", {})
                post_id = f"reddit_{post_data.get('id')}"
                title = post_data.get("title", "")
                selftext = post_data.get("selftext", "")
                author = post_data.get("author", "[deleted]")

                # 자체 게시물만 본문으로 사용 (링크 게시물은 제목만)
                content = selftext if not post_data.get("is_self") else selftext

                # 댓글 수집
                comment_text = ""
                try:
                    permalink = post_data.get("permalink", "")
                    comments = await self._fetch_comments(permalink, token, limit=comments_limit)
                    if comments:
                        comment_text = "\n".join(comments[:comments_limit])
                        await self._rate_limit_delay_async()
                except Exception as e:
                    logger.debug(f"Failed to fetch comments for {post_id}: {e}")
                    pass

                # 본문 + 댓글 통합
                combined_content = f"{content}\n\n[TOP COMMENTS]\n{comment_text}"

                # 시간 파싱
                created_utc = post_data.get("created_utc", 0)
                published_at = datetime.fromtimestamp(created_utc, tz=timezone.utc)

                # URL 구성
                post_url = f"https://reddit.com{post_data.get('permalink', '')}"

                # SocialPost 생성
                post = SocialPost(
                    post_id=post_id,
                    platform="REDDIT",
                    subreddit=subreddit,
                    title=title,
                    content=combined_content[:10000],  # 최대 10000자
                    author=author,
                    score=post_data.get("ups", 0) - post_data.get("downs", 0),
                    num_comments=post_data.get("num_comments", 0),
                    url=post_url,
                    published_at=published_at,
                    metadata={
                        "upvotes": post_data.get("ups", 0),
                        "downvotes": post_data.get("downs", 0),
                        "is_self": post_data.get("is_self", False),
                        "sort_by": sort_by,
                    },
                )

                posts.append(post)

            except Exception as e:
                logger.debug(f"Failed to parse Reddit post: {e}")
                continue

        return posts

    async def _fetch_comments(self, permalink: str, token: str, limit: int = 5) -> list[str]:
        """게시물의 상위 댓글 수집"""
        url = f"{self.API_BASE_URL}{permalink}"
        params = {"limit": limit, "sort": "best"}

        comments = []

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    url,
                    params=params,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "User-Agent": (await self._get_user_agent()),
                    },
                )
                response.raise_for_status()

            data = response.json()

            # data[1]이 댓글 리스트
            if isinstance(data, list) and len(data) > 1:
                comment_data = data[1].get("data", {}).get("children", [])

                for item in comment_data[:limit]:
                    try:
                        comment = item.get("data", {}).get("body", "")
                        if comment and not comment.startswith("[deleted"):
                            comments.append(comment)
                    except Exception:
                        continue

        except Exception as e:
            logger.debug(f"Failed to fetch comments: {e}")
            pass

        return comments

    async def _get_user_agent(self) -> str:
        """User-Agent 반환"""
        settings = get_settings()
        return settings.external.reddit_user_agent

    async def _rate_limit_delay_async(self) -> None:
        """API 레이트 리밋 대응 지연"""
        await asyncio.sleep(self._rate_limit_delay)


# ══════════════════════════════════════
# 통합 소셜 데이터 수집 서비스
# ══════════════════════════════════════
class SocialCollectorService:
    """
    통합 소셜 미디어 수집 서비스

    현재: Reddit만 지원
    추후: Twitter, Naver Finance Comments 등 확장 가능
    """

    COLLECTION_NAME = "social_posts"

    def __init__(self):
        settings = get_settings()
        self._oauth_manager = RedditOAuth2Manager(
            client_id=settings.external.reddit_client_id or "",
            client_secret=settings.external.reddit_client_secret or "",
            user_agent=settings.external.reddit_user_agent,
        )
        self._reddit_collector = RedditCollector(self._oauth_manager)
        self._author_post_count: dict[str, list[datetime]] = {}

    @property
    def is_available(self) -> bool:
        """Reddit API 설정 여부"""
        return self._oauth_manager.is_available

    async def collect_reddit_posts(
        self,
        limit: int = 25,
        comments_limit: int = 5,
    ) -> list[SocialPost]:
        """
        Reddit 게시물 수집

        Args:
            limit: 서브레딧당 수집 게시물 수
            comments_limit: 게시물당 수집 댓글 수

        Returns:
            수집된 SocialPost 리스트
        """
        if not self.is_available:
            logger.warning("Reddit API credentials not configured. Skipping collection.")
            return []

        posts = await self._reddit_collector.collect_all(limit, comments_limit)
        logger.info(f"Collected {len(posts)} Reddit posts")
        return posts

    def filter_financial_posts(self, posts: list[SocialPost]) -> list[SocialPost]:
        """
        금융 관련 게시물만 필터링

        조건:
        - 금융 키워드 포함
        - 스팸 아님
        - 길이 >= 10자
        """
        filtered = []
        current_time = datetime.now(timezone.utc)

        for post in posts:
            # 금융 관련 키워드 확인
            combined_text = f"{post.title} {post.content}"
            if not is_financial_content(combined_text):
                post.is_filtered = True
                continue

            # 스팸 필터링
            if is_spam(
                post.content,
                post.author,
                self._author_post_count,
                current_time,
            ):
                post.is_filtered = True
                continue

            # 저자별 게시물 시간 추적
            if post.author not in self._author_post_count:
                self._author_post_count[post.author] = []
            self._author_post_count[post.author].append(post.published_at)

            # 종목 코드 추출
            post.tickers = extract_tickers(combined_text)

            # 감성 키워드 추출
            post.sentiment_keywords = extract_sentiment_keywords(combined_text)

            filtered.append(post)

        logger.info(
            f"Filtered {len(filtered)} / {len(posts)} posts " f"({len(posts) - len(filtered)} spam/non-financial)"
        )
        return filtered

    async def save_to_db(self, posts: list[SocialPost]) -> dict:
        """
        MongoDB에 게시물 저장

        Returns:
            {"total": int, "new_stored": int, "duplicates_skipped": int}
        """
        collection = MongoDBManager.get_collection(self.COLLECTION_NAME)

        # 인덱스 생성
        await collection.create_index("post_id", unique=True, background=True)
        await collection.create_index([("published_at", -1)])
        await collection.create_index("tickers")
        await collection.create_index("platform")
        await collection.create_index("subreddit")

        new_count = 0
        dup_count = 0

        for post in posts:
            try:
                await collection.insert_one(post.to_dict())
                new_count += 1
            except Exception:
                # DuplicateKeyError (post_id 중복)
                dup_count += 1

        result = {
            "total": len(posts),
            "new_stored": new_count,
            "duplicates_skipped": dup_count,
        }

        logger.info(f"Social posts stored: {new_count} new, " f"{dup_count} duplicates, {len(posts)} total")
        return result

    async def get_recent_posts(
        self,
        tickers: Optional[list[str]] = None,
        hours: int = 24,
        limit: int = 50,
    ) -> list[dict]:
        """
        최근 소셜 게시물 조회

        Args:
            tickers: 특정 종목만 조회 (None이면 전체)
            hours: 조회 기간 (시간)
            limit: 최대 건수

        Returns:
            게시물 딕셔너리 리스트
        """
        collection = MongoDBManager.get_collection(self.COLLECTION_NAME)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        query: dict = {
            "published_at": {"$gte": cutoff},
            "is_filtered": False,
            "platform": "REDDIT",
        }

        if tickers:
            query["tickers"] = {"$in": tickers}

        cursor = collection.find(query, {"_id": 0}).sort("published_at", -1).limit(limit)
        return await cursor.to_list(length=limit)

    async def get_posts_by_subreddit(
        self,
        subreddit: str,
        hours: int = 24,
        limit: int = 20,
    ) -> list[dict]:
        """특정 서브레딧 게시물 조회"""
        collection = MongoDBManager.get_collection(self.COLLECTION_NAME)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        cursor = (
            collection.find(
                {
                    "subreddit": subreddit,
                    "published_at": {"$gte": cutoff},
                    "is_filtered": False,
                },
                {"_id": 0},
            )
            .sort("published_at", -1)
            .limit(limit)
        )

        return await cursor.to_list(length=limit)

    async def get_posts_by_ticker(
        self,
        ticker: str,
        hours: int = 24,
        limit: int = 20,
    ) -> list[dict]:
        """특정 종목 관련 게시물 조회"""
        collection = MongoDBManager.get_collection(self.COLLECTION_NAME)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        cursor = (
            collection.find(
                {
                    "tickers": ticker,
                    "published_at": {"$gte": cutoff},
                    "is_filtered": False,
                    "platform": "REDDIT",
                },
                {"_id": 0},
            )
            .sort("score", -1)
            .limit(limit)
        )

        return await cursor.to_list(length=limit)

    async def collect_and_store(
        self,
        limit: int = 25,
        comments_limit: int = 5,
    ) -> dict:
        """
        전체 수집 및 저장 파이프라인

        Returns:
            {"collected": int, "filtered": int, "stored": int, "duplicates": int}
        """
        # 1. Reddit 게시물 수집
        raw_posts = await self.collect_reddit_posts(limit, comments_limit)

        if not raw_posts:
            logger.warning("No Reddit posts collected")
            return {
                "collected": 0,
                "filtered": 0,
                "stored": 0,
                "duplicates": 0,
            }

        # 2. 금융 관련 필터링
        filtered_posts = self.filter_financial_posts(raw_posts)

        # 3. MongoDB 저장
        db_result = await self.save_to_db(filtered_posts)

        return {
            "collected": len(raw_posts),
            "filtered": len(filtered_posts),
            "stored": db_result["new_stored"],
            "duplicates": db_result["duplicates_skipped"],
        }
