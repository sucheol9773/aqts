"""
뉴스/공시 데이터 수집 모듈 (News & Disclosure Collector)

Phase 3 - F-03-02 구현:
- 네이버 금융 뉴스 (Google News RSS 경유)
- 한국경제 / 매일경제 RSS
- DART 전자공시 API
- 수집 데이터 → MongoDB 저장 (비정형 텍스트)
- URL 기반 중복 제거 (MD5 해싱)
- 종목 코드 자동 태깅 (정규식 + 유니버스 매칭)

사용 라이브러리: feedparser 6.0.11, beautifulsoup4 4.12.3, httpx 0.27.0
"""

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import feedparser
import httpx
from bs4 import BeautifulSoup

from config.constants import DART_API_BASE_URL, DART_DISCLOSURE_TYPES, NEWS_RSS_FEEDS, NewsSource
from config.logging import logger
from config.settings import get_settings
from db.database import MongoDBManager


# ══════════════════════════════════════
# 수집 데이터 구조
# ══════════════════════════════════════
class NewsArticle:
    """수집된 뉴스/공시 아티클 데이터 컨테이너"""

    __slots__ = (
        "url_hash",
        "title",
        "content",
        "url",
        "source",
        "published_at",
        "collected_at",
        "tickers",
        "category",
        "raw_html",
        "metadata",
    )

    def __init__(
        self,
        title: str,
        content: str,
        url: str,
        source: str,
        published_at: Optional[datetime] = None,
        tickers: Optional[list[str]] = None,
        category: str = "general",
        raw_html: str = "",
        metadata: Optional[dict] = None,
    ):
        self.url_hash = hashlib.md5(url.encode()).hexdigest()
        self.title = title.strip()
        self.content = content.strip()
        self.url = url
        self.source = source
        self.published_at = published_at or datetime.now(timezone.utc)
        self.collected_at = datetime.now(timezone.utc)
        self.tickers = tickers or []
        self.category = category
        self.raw_html = raw_html
        self.metadata = metadata or {}

    def to_dict(self) -> dict:
        """MongoDB 저장용 딕셔너리 변환"""
        return {
            "url_hash": self.url_hash,
            "title": self.title,
            "content": self.content,
            "url": self.url,
            "source": self.source,
            "published_at": self.published_at,
            "collected_at": self.collected_at,
            "tickers": self.tickers,
            "category": self.category,
            "metadata": self.metadata,
        }


# ══════════════════════════════════════
# 종목 코드 매칭 유틸리티
# ══════════════════════════════════════
# 한국 종목코드 패턴 (6자리 숫자)
_KR_TICKER_PATTERN = re.compile(r"\b(\d{6})\b")

# 주요 종목명 ↔ 코드 매핑 (빈번하게 등장하는 대형주)
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


def extract_tickers(text: str) -> list[str]:
    """
    텍스트에서 한국 종목코드 추출

    1단계: 6자리 숫자 패턴 매칭
    2단계: 주요 종목명 사전 매칭
    """
    tickers = set()

    # 6자리 코드 직접 매칭
    for match in _KR_TICKER_PATTERN.finditer(text):
        code = match.group(1)
        # 날짜 등 오탐 방지: 앞뒤 문맥 확인
        if code.startswith(("20", "19")):  # 연도 패턴 제외
            continue
        tickers.add(code)

    # 종목명 사전 매칭
    for name, code in _KR_NAME_TO_TICKER.items():
        if name in text:
            tickers.add(code)

    return sorted(tickers)


# ══════════════════════════════════════
# RSS 뉴스 수집기
# ══════════════════════════════════════
class RSSNewsCollector:
    """RSS 피드 기반 뉴스 수집기"""

    def __init__(self):
        self._feeds = NEWS_RSS_FEEDS
        self._timeout = 15

    async def collect_all(self) -> list[NewsArticle]:
        """
        등록된 모든 RSS 소스에서 뉴스 수집

        Returns:
            수집된 뉴스 아티클 리스트
        """
        all_articles: list[NewsArticle] = []

        for source, urls in self._feeds.items():
            for feed_url in urls:
                try:
                    articles = await self._parse_feed(feed_url, source)
                    all_articles.extend(articles)
                    logger.debug(f"RSS [{source.value}] {feed_url}: {len(articles)} articles")
                except Exception as e:
                    logger.warning(f"RSS feed error [{source.value}] {feed_url}: {e}")
                    continue

        logger.info(f"RSS collection complete: {len(all_articles)} total articles")
        return all_articles

    async def collect_source(self, source: NewsSource) -> list[NewsArticle]:
        """특정 소스에서만 수집"""
        articles: list[NewsArticle] = []
        urls = self._feeds.get(source, [])

        for feed_url in urls:
            try:
                parsed = await self._parse_feed(feed_url, source)
                articles.extend(parsed)
            except Exception as e:
                logger.warning(f"RSS feed error [{source.value}] {feed_url}: {e}")
                continue

        return articles

    async def _parse_feed(self, feed_url: str, source: NewsSource) -> list[NewsArticle]:
        """단일 RSS 피드 파싱"""
        articles: list[NewsArticle] = []

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(feed_url, follow_redirects=True)
            response.raise_for_status()

        feed = feedparser.parse(response.text)

        for entry in feed.entries:
            try:
                title = entry.get("title", "")
                link = entry.get("link", "")
                if not title or not link:
                    continue

                # 본문 추출 (summary 또는 content)
                content = ""
                if hasattr(entry, "content") and entry.content:
                    raw = entry.content[0].get("value", "")
                    content = BeautifulSoup(raw, "lxml").get_text(strip=True)
                elif hasattr(entry, "summary"):
                    content = BeautifulSoup(entry.summary, "lxml").get_text(strip=True)

                # 발행 시간 파싱
                published_at = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    published_at = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                    published_at = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)

                # 24시간 이내 뉴스만 수집
                if published_at:
                    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
                    if published_at < cutoff:
                        continue

                # 종목 태깅
                combined_text = f"{title} {content}"
                tickers = extract_tickers(combined_text)

                # 카테고리 분류
                category = self._classify_category(combined_text)

                articles.append(
                    NewsArticle(
                        title=title,
                        content=content[:5000],  # 본문 최대 5000자
                        url=link,
                        source=source.value,
                        published_at=published_at,
                        tickers=tickers,
                        category=category,
                    )
                )
            except Exception as e:
                logger.debug(f"RSS entry parse error: {e}")
                continue

        return articles

    @staticmethod
    def _classify_category(text: str) -> str:
        """텍스트 기반 간이 카테고리 분류"""
        macro_keywords = ["금리", "기준금리", "Fed", "FOMC", "환율", "GDP", "인플레이션", "CPI", "고용"]
        sector_keywords = ["반도체", "2차전지", "바이오", "AI", "자동차", "은행", "보험", "건설"]
        earnings_keywords = ["실적", "영업이익", "매출", "순이익", "분기", "어닝"]

        for kw in earnings_keywords:
            if kw in text:
                return "earnings"
        for kw in macro_keywords:
            if kw in text:
                return "macro"
        for kw in sector_keywords:
            if kw in text:
                return "sector"
        return "general"


# ══════════════════════════════════════
# DART 전자공시 수집기
# ══════════════════════════════════════
class DARTCollector:
    """
    DART 전자공시 수집기

    주요 공시 유형 (정기보고서, 주요사항보고, 지분공시 등)을
    수집하여 MongoDB에 저장합니다.
    """

    def __init__(self):
        settings = get_settings()
        self._api_key = settings.external.dart_api_key
        self._base_url = DART_API_BASE_URL
        self._timeout = 15

    @property
    def is_available(self) -> bool:
        """DART API 키 설정 여부"""
        return bool(self._api_key)

    async def collect_recent(
        self,
        begin_date: Optional[str] = None,
        end_date: Optional[str] = None,
        corp_code: Optional[str] = None,
    ) -> list[NewsArticle]:
        """
        최근 공시 수집

        Args:
            begin_date: 시작일 (YYYYMMDD). 기본값: 어제
            end_date: 종료일 (YYYYMMDD). 기본값: 오늘
            corp_code: 특정 회사 코드 (None이면 전체)

        Returns:
            공시 아티클 리스트
        """
        if not self.is_available:
            logger.warning("DART API key not configured. Skipping disclosure collection.")
            return []

        today = datetime.now()
        if not end_date:
            end_date = today.strftime("%Y%m%d")
        if not begin_date:
            begin_date = (today - timedelta(days=1)).strftime("%Y%m%d")

        articles: list[NewsArticle] = []

        for pblntf_ty in DART_DISCLOSURE_TYPES:
            try:
                fetched = await self._fetch_disclosures(begin_date, end_date, pblntf_ty, corp_code)
                articles.extend(fetched)
            except Exception as e:
                logger.warning(f"DART disclosure fetch error (type={pblntf_ty}): {e}")
                continue

        logger.info(f"DART collection complete: {len(articles)} disclosures ({begin_date}~{end_date})")
        return articles

    async def _fetch_disclosures(
        self,
        begin_date: str,
        end_date: str,
        pblntf_ty: str,
        corp_code: Optional[str] = None,
    ) -> list[NewsArticle]:
        """단일 공시 유형 조회"""
        params = {
            "crtfc_key": self._api_key,
            "bgn_de": begin_date,
            "end_de": end_date,
            "pblntf_ty": pblntf_ty,
            "page_count": 100,
            "sort": "date",
            "sort_mth": "desc",
        }
        if corp_code:
            params["corp_code"] = corp_code

        articles: list[NewsArticle] = []

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(f"{self._base_url}/list.json", params=params)
            response.raise_for_status()

        data = response.json()
        if data.get("status") != "000":
            return articles

        for item in data.get("list", []):
            try:
                title = item.get("report_nm", "")
                corp_name = item.get("corp_name", "")
                stock_code = item.get("stock_code", "").strip()
                rcept_no = item.get("rcept_no", "")
                rcept_dt = item.get("rcept_dt", "")

                url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

                published_at = None
                if rcept_dt and len(rcept_dt) == 8:
                    published_at = datetime.strptime(rcept_dt, "%Y%m%d").replace(tzinfo=timezone.utc)

                tickers = [stock_code] if stock_code else []

                articles.append(
                    NewsArticle(
                        title=f"[{corp_name}] {title}",
                        content=f"{corp_name} 전자공시: {title}",
                        url=url,
                        source=NewsSource.DART.value,
                        published_at=published_at,
                        tickers=tickers,
                        category="disclosure",
                        metadata={
                            "corp_code": item.get("corp_code", ""),
                            "corp_name": corp_name,
                            "stock_code": stock_code,
                            "pblntf_ty": pblntf_ty,
                            "rcept_no": rcept_no,
                            "flr_nm": item.get("flr_nm", ""),
                        },
                    )
                )
            except Exception as e:
                logger.debug(f"DART item parse error: {e}")
                continue

        return articles


# ══════════════════════════════════════
# 통합 뉴스 수집 서비스
# ══════════════════════════════════════
class NewsCollectorService:
    """
    통합 뉴스/공시 수집 서비스

    모든 소스에서 데이터를 수집하고 MongoDB에 저장합니다.
    중복 제거는 url_hash 기반으로 수행됩니다.
    """

    COLLECTION_NAME = "news_articles"

    def __init__(self):
        self._rss = RSSNewsCollector()
        self._dart = DARTCollector()

    async def collect_and_store(self) -> dict:
        """
        전체 소스 수집 후 MongoDB 저장

        Returns:
            {"total_collected": int, "new_stored": int, "duplicates_skipped": int}
        """
        # 전체 수집
        all_articles: list[NewsArticle] = []

        # RSS 뉴스
        rss_articles = await self._rss.collect_all()
        all_articles.extend(rss_articles)

        # DART 공시
        dart_articles = await self._dart.collect_recent()
        all_articles.extend(dart_articles)

        # MongoDB 저장 (중복 제거)
        new_count = 0
        dup_count = 0
        collection = MongoDBManager.get_collection(self.COLLECTION_NAME)

        # url_hash 유니크 인덱스 확보
        await collection.create_index("url_hash", unique=True, background=True)
        await collection.create_index([("published_at", -1)])
        await collection.create_index("tickers")
        await collection.create_index("source")

        for article in all_articles:
            try:
                await collection.insert_one(article.to_dict())
                new_count += 1
            except Exception:
                # DuplicateKeyError (url_hash 중복)
                dup_count += 1

        result = {
            "total_collected": len(all_articles),
            "new_stored": new_count,
            "duplicates_skipped": dup_count,
        }

        logger.info(f"News collection stored: {new_count} new, " f"{dup_count} duplicates, {len(all_articles)} total")
        return result

    async def get_articles_for_ticker(
        self,
        ticker: str,
        hours: int = 24,
        limit: int = 20,
    ) -> list[dict]:
        """
        특정 종목 관련 최근 뉴스 조회

        Args:
            ticker: 종목코드
            hours: 조회 기간 (시간)
            limit: 최대 건수

        Returns:
            뉴스 딕셔너리 리스트
        """
        collection = MongoDBManager.get_collection(self.COLLECTION_NAME)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        cursor = (
            collection.find(
                {
                    "tickers": ticker,
                    "published_at": {"$gte": cutoff},
                },
                {"_id": 0, "raw_html": 0},
            )
            .sort("published_at", -1)
            .limit(limit)
        )

        return await cursor.to_list(length=limit)

    async def get_recent_articles(
        self,
        hours: int = 24,
        category: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """
        최근 뉴스 전체 조회

        Args:
            hours: 조회 기간
            category: 카테고리 필터 (None이면 전체)
            limit: 최대 건수
        """
        collection = MongoDBManager.get_collection(self.COLLECTION_NAME)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        query: dict = {"published_at": {"$gte": cutoff}}
        if category:
            query["category"] = category

        cursor = (
            collection.find(
                query,
                {"_id": 0, "raw_html": 0},
            )
            .sort("published_at", -1)
            .limit(limit)
        )

        return await cursor.to_list(length=limit)

    async def get_macro_articles(self, hours: int = 48, limit: int = 30) -> list[dict]:
        """거시경제 관련 뉴스 조회"""
        return await self.get_recent_articles(hours=hours, category="macro", limit=limit)
