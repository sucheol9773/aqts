"""
환율 데이터 관리 모듈 (F-05-05)

USD/KRW 환율 조회, 캐싱, 통화 변환을 담당합니다.
한국투자증권 API와 FRED를 이용한 이중 소스를 지원하며,
Redis 캐싱으로 성능을 최적화합니다.

주요 기능:
- async get_current_rate: 현재 환율 조회 (캐시 우선)
- async fetch_from_kis: KIS API로부터 환율 조회
- async fetch_from_fred: FRED API로부터 환율 조회 (Fallback)
- async convert_to_krw: USD → KRW 변환
- async get_portfolio_krw_value: 포트폴리오 전체 KRW 가치 계산
- _is_market_hours: 시장 시간 확인

캐싱 전략:
- 장중 (09:00-15:30 KST): 5분 TTL
- 장외: 24시간 TTL
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone, time, timedelta
from typing import Optional, Any

import httpx
from redis.asyncio import Redis

from config.logging import logger
from config.settings import get_settings
from db.database import RedisManager
from core.data_collector.kis_client import KISClient


# ══════════════════════════════════════════════════════════════════════════════
# 환율 데이터 구조
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class ExchangeRate:
    """
    환율 데이터

    현재 환율 정보를 포함합니다.
    조회 시원, 조회 시각, 환율 값을 제공합니다.
    """

    pair: str
    """통화 쌍 (예: "USD/KRW")"""

    rate: float
    """환율 (1 USD = rate KRW)"""

    source: str
    """조회 출처 (KIS, FRED, CACHE 등)"""

    fetched_at: datetime
    """조회 시각 (UTC)"""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환"""
        return {
            "pair": self.pair,
            "rate": self.rate,
            "source": self.source,
            "fetched_at": self.fetched_at.isoformat(),
        }


# ══════════════════════════════════════════════════════════════════════════════
# 환율 관리자
# ══════════════════════════════════════════════════════════════════════════════
class ExchangeRateManager:
    """
    환율 데이터 관리자

    USD/KRW 환율을 조회하고 캐싱합니다.
    KIS API와 FRED API를 이용한 이중 소스를 지원하며,
    Redis 캐싱으로 성능을 최적화합니다.

    캐싱 전략:
    - 장중 (09:00-15:30 KST): 5분 TTL
    - 장외: 24시간 TTL

    조회 우선순위:
    1. Redis 캐시 확인 (유효하면 반환)
    2. KIS API 조회 (실시간 환율)
    3. FRED API 조회 (Fallback)
    """

    CACHE_KEY = "exchange_rate:USD_KRW"
    MARKET_HOURS_START = time(9, 0)
    MARKET_HOURS_END = time(15, 30)
    MARKET_HOURS_TTL = 300  # 5분
    OFF_HOURS_TTL = 86400  # 24시간

    def __init__(self):
        """환율 관리자 초기화"""
        self._settings = get_settings()
        self._kis_client = KISClient()
        logger.info("ExchangeRateManager 초기화 완료")

    async def get_current_rate(self, pair: str = "USD/KRW") -> ExchangeRate:
        """
        현재 환율 조회

        캐시를 먼저 확인하고, 만료되었으면 신규 조회합니다.
        조회 순서: Redis Cache → KIS API → FRED API

        Args:
            pair: 통화 쌍 (기본값: "USD/KRW")

        Returns:
            ExchangeRate: 환율 데이터

        캐싱 전략:
        - 장중 (09:00-15:30 KST): 5분 유지
        - 장외 (15:30-09:00 KST): 24시간 유지
        """
        logger.debug(f"환율 조회: {pair}")

        try:
            # 1. Redis 캐시 확인
            rate_data = await self._get_cached_rate(pair)
            if rate_data:
                logger.debug(f"캐시된 환율: {rate_data.rate} ({rate_data.source})")
                return rate_data

            # 2. KIS API로부터 조회
            try:
                rate = await self.fetch_from_kis()
                rate_data = ExchangeRate(
                    pair=pair,
                    rate=rate,
                    source="KIS",
                    fetched_at=datetime.now(timezone.utc),
                )
                await self._cache_rate(rate_data)
                logger.info(f"KIS 환율 조회 완료: {rate}")
                return rate_data
            except Exception as e:
                logger.warning(f"KIS API 조회 실패: {e}, FRED로 Fallback")

            # 3. FRED API로부터 조회 (Fallback)
            rate = await self.fetch_from_fred()
            rate_data = ExchangeRate(
                pair=pair,
                rate=rate,
                source="FRED",
                fetched_at=datetime.now(timezone.utc),
            )
            await self._cache_rate(rate_data)
            logger.info(f"FRED 환율 조회 완료: {rate}")
            return rate_data

        except Exception as e:
            logger.error(f"환율 조회 실패: {e}")
            raise

    async def fetch_from_kis(self) -> float:
        """
        KIS API로부터 환율 조회

        한국투자증권 OpenAPI를 이용하여 현재 USD/KRW 환율을 조회합니다.

        Returns:
            float: USD/KRW 환율

        Raises:
            Exception: API 호출 실패 시
        """
        logger.debug("KIS API 환율 조회 시작")

        try:
            result = await self._kis_client.get_exchange_rate()

            # API 응답에서 환율 추출
            # TODO: 실제 응답 형식에 맞게 파싱
            rate = float(result.get("exchange_rate", 1300.0))  # 기본값: 1300

            if rate <= 0:
                raise ValueError(f"유효하지 않은 환율: {rate}")

            logger.info(f"KIS 환율: 1 USD = {rate} KRW")
            return rate

        except Exception as e:
            logger.error(f"KIS API 환율 조회 실패: {e}")
            raise

    async def fetch_from_fred(self) -> float:
        """
        FRED API로부터 환율 조회

        Federal Reserve Economic Data (FRED) API를 이용하여
        USD/KRW 환율을 조회합니다.
        KIS API 실패 시 Fallback으로 사용됩니다.

        FRED Series ID: DEXKOUS (한국 원/미국 달러 일일 환율)

        Returns:
            float: USD/KRW 환율

        Raises:
            Exception: API 호출 실패 시
        """
        logger.debug("FRED API 환율 조회 시작")

        try:
            fred_key = self._settings.external.fred_api_key
            if not fred_key:
                raise ValueError("FRED API 키가 설정되지 않았습니다")

            # FRED API 호출
            url = "https://api.stlouisfed.org/fred/series/data"
            params = {
                "series_id": "DEXKOUS",  # KRW/USD (역수 계산 필요)
                "api_key": fred_key,
                "file_type": "json",
                "limit": 1,
                "sort_order": "desc",
            }

            timeout = self._settings.kis.api_timeout
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()

            # 응답에서 환율 추출 (역수 계산)
            observations = data.get("observations", [])
            if not observations:
                raise ValueError("FRED API 응답에 데이터가 없습니다")

            krw_per_usd = float(observations[0]["value"])
            if krw_per_usd <= 0:
                raise ValueError(f"유효하지 않은 환율: {krw_per_usd}")

            logger.info(f"FRED 환율: 1 USD = {krw_per_usd} KRW")
            return krw_per_usd

        except Exception as e:
            logger.error(f"FRED API 환율 조회 실패: {e}")
            raise

    async def convert_to_krw(self, usd_amount: float) -> float:
        """
        USD를 KRW로 변환

        주어진 USD 금액을 현재 환율로 KRW로 변환합니다.

        Args:
            usd_amount: USD 금액

        Returns:
            float: KRW 금액

        Example:
            >>> manager = ExchangeRateManager()
            >>> krw = await manager.convert_to_krw(1000)
            >>> print(krw)  # 1000 USD를 KRW로 변환
        """
        logger.debug(f"USD → KRW 변환: {usd_amount}")

        try:
            rate_data = await self.get_current_rate("USD/KRW")
            krw_amount = usd_amount * rate_data.rate

            logger.debug(f"{usd_amount} USD = {krw_amount} KRW")
            return krw_amount

        except Exception as e:
            logger.error(f"USD → KRW 변환 실패: {e}")
            raise

    async def get_portfolio_krw_value(self, portfolio: dict[str, Any]) -> float:
        """
        포트폴리오 전체 KRW 가치 계산

        포트폴리오에 포함된 모든 자산을 KRW로 변환하여 합산합니다.
        USD 자산은 현재 환율로 변환하며, KRW 자산은 그대로 합산합니다.

        Args:
            portfolio: 포트폴리오 데이터
                {
                    "krw_holdings": [{"ticker": "005930", "value": 1000000}, ...],
                    "usd_holdings": [{"ticker": "AAPL", "value": 5000}, ...],
                    "cash_krw": 500000,
                    "cash_usd": 1000,
                }

        Returns:
            float: 총 포트폴리오 KRW 가치

        Example:
            >>> portfolio = {
            ...     "krw_holdings": [{"value": 1000000}],
            ...     "usd_holdings": [{"value": 5000}],
            ...     "cash_krw": 500000,
            ...     "cash_usd": 1000,
            ... }
            >>> total = await manager.get_portfolio_krw_value(portfolio)
        """
        logger.info("포트폴리오 KRW 가치 계산 시작")

        try:
            total_krw = 0.0

            # KRW 자산
            krw_holdings = portfolio.get("krw_holdings", [])
            krw_value = sum(h.get("value", 0) for h in krw_holdings)
            total_krw += krw_value
            logger.debug(f"KRW 자산: {krw_value}")

            # KRW 현금
            cash_krw = portfolio.get("cash_krw", 0)
            total_krw += cash_krw
            logger.debug(f"KRW 현금: {cash_krw}")

            # USD 자산
            usd_holdings = portfolio.get("usd_holdings", [])
            usd_value = sum(h.get("value", 0) for h in usd_holdings)
            if usd_value > 0:
                usd_holdings_krw = await self.convert_to_krw(usd_value)
                total_krw += usd_holdings_krw
                logger.debug(f"USD 자산: {usd_value} USD = {usd_holdings_krw} KRW")

            # USD 현금
            cash_usd = portfolio.get("cash_usd", 0)
            if cash_usd > 0:
                cash_usd_krw = await self.convert_to_krw(cash_usd)
                total_krw += cash_usd_krw
                logger.debug(f"USD 현금: {cash_usd} USD = {cash_usd_krw} KRW")

            logger.info(f"포트폴리오 KRW 가치: {total_krw}")
            return total_krw

        except Exception as e:
            logger.error(f"포트폴리오 KRW 가치 계산 실패: {e}")
            raise

    def _is_market_hours(self) -> bool:
        """
        한국 시장 시간 확인

        현재 시각이 한국 거래소 거래 시간(09:00-15:30 KST)인지 확인합니다.
        장중에는 5분 캐시, 장외에는 24시간 캐시를 사용합니다.

        Returns:
            bool: 장중(True), 장외(False)

        거래 시간:
        - 개장: 09:00 KST
        - 폐장: 15:30 KST
        - 주중: 월요일 ~ 금요일
        """
        now = datetime.now(timezone.utc)
        # UTC to KST (UTC+9)
        kst_time = now + timedelta(hours=9)
        current_time = kst_time.time()

        # 주말 체크 (0=월요일, 6=일요일)
        is_weekday = kst_time.weekday() < 5

        is_market_hours = (
            is_weekday
            and self.MARKET_HOURS_START <= current_time <= self.MARKET_HOURS_END
        )

        logger.debug(f"시장 시간 확인: {is_market_hours} (시각: {current_time})")
        return is_market_hours

    async def _get_cached_rate(self, pair: str) -> Optional[ExchangeRate]:
        """
        Redis 캐시에서 환율 조회

        Args:
            pair: 통화 쌍

        Returns:
            Optional[ExchangeRate]: 캐시된 환율 (없으면 None)
        """
        try:
            redis_client = RedisManager.get_client()
            cached_data = await redis_client.get(self.CACHE_KEY)

            if cached_data:
                import json

                data = json.loads(cached_data)
                return ExchangeRate(
                    pair=data["pair"],
                    rate=data["rate"],
                    source="CACHE",
                    fetched_at=datetime.fromisoformat(data["fetched_at"]),
                )
            return None

        except Exception as e:
            logger.warning(f"캐시 조회 실패: {e}")
            return None

    async def _cache_rate(self, rate: ExchangeRate) -> None:
        """
        환율을 Redis 캐시에 저장

        Args:
            rate: 환율 데이터
        """
        try:
            import json

            redis_client = RedisManager.get_client()
            ttl = self.MARKET_HOURS_TTL if self._is_market_hours() else self.OFF_HOURS_TTL

            await redis_client.setex(
                self.CACHE_KEY,
                ttl,
                json.dumps(rate.to_dict()),
            )

            logger.debug(f"환율 캐시 저장: TTL={ttl}초")

        except Exception as e:
            logger.warning(f"캐시 저장 실패: {e}")
