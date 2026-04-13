"""
경제지표 데이터 수집 모듈 (Economic Indicators Collector)

Phase 3 - F-01-04 구현:
- FRED (Federal Reserve Economic Data) API: 미국 거시경제 지표
- ECOS (한국은행 경제통계) API: 한국 거시경제 지표
- 수집 데이터 → TimescaleDB 저장 (시계열 최적화)
- 재시도 로직 (Exponential Backoff, 최대 3회)
- Redis 캐싱 (TTL 24시간)

사용 라이브러리: httpx 0.27.0, redis 5.0.7
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from config.constants import EconomicIndicatorType
from config.logging import logger
from config.settings import get_settings
from db.database import RedisManager

# ══════════════════════════════════════
# FRED ↔ 지표명 매핑
# ══════════════════════════════════════
FRED_SERIES_MAP = {
    EconomicIndicatorType.GDP: "A191RL1Q225SBEA",  # Real Gross Domestic Product
    EconomicIndicatorType.CPI: "CPIAUCSL",  # Consumer Price Index for All Urban Consumers
    EconomicIndicatorType.FED_FUNDS_RATE: "FEDFUNDS",  # Effective Federal Funds Rate
    EconomicIndicatorType.TREASURY_2Y: "DGS2",  # 2-Year Treasury Constant Maturity Rate
    EconomicIndicatorType.TREASURY_10Y: "DGS10",  # 10-Year Treasury Constant Maturity Rate
    EconomicIndicatorType.UNEMPLOYMENT: "UNRATE",  # Unemployment Rate
    EconomicIndicatorType.PMI: "MMNRNJ",  # ISM Manufacturing: PMI
    EconomicIndicatorType.VIX: "VIXCLS",  # VIX Closing Price
    EconomicIndicatorType.USD_KRW: "DEXKOUS",  # Korean Won to U.S. Dollar Spot Exchange Rate
}

# ══════════════════════════════════════
# ECOS ↔ 지표명 매핑
# ══════════════════════════════════════
ECOS_SERIES_MAP = {
    EconomicIndicatorType.BOK_BASE_RATE: {
        "stat_code": "722Y001",  # 한국은행 기준금리
        "item_code": "0101000",  # 기준금리
    },
    EconomicIndicatorType.KR_CPI: {
        "stat_code": "901Y009",  # 소비자물가지수
        "item_code": "0",  # 지수
    },
    EconomicIndicatorType.KR_UNEMPLOYMENT: {
        "stat_code": "902Y014",  # 경제활동별 인구 (실업률)
        "item_code": "0",  # 실업률
    },
    EconomicIndicatorType.KR_GDP: {
        "stat_code": "111Y002",  # 국민소득(GDP)
        "item_code": "10101",  # 국내총생산 (실질, 계절조정)
    },
    EconomicIndicatorType.KR_CURRENT_ACCOUNT: {
        "stat_code": "721Y017",  # 국제수지 (경상수지)
        "item_code": "0",  # 경상수지
    },
}


# ══════════════════════════════════════
# 데이터 컨테이너
# ══════════════════════════════════════
@dataclass
class EconomicIndicator:
    """경제지표 데이터 컨테이너

    DB 스키마(economic_indicators 테이블)와 1:1 매핑:
      time, indicator_code, indicator_name, value, country, source
    """

    indicator_name: str  # 지표명 (예: "GDP", "CPI")
    indicator_code: str  # 시리즈 코드 (FRED: series_id, ECOS: stat_code)
    value: float  # 지표값
    time: datetime  # 데이터 기준 날짜 (DB 컬럼명: time)
    source: str  # 데이터 출처 (FRED, ECOS)
    country: str  # 국가 코드 (US, KR)

    def to_dict(self) -> dict[str, Any]:
        """TimescaleDB 저장용 딕셔너리 변환"""
        return {
            "indicator_name": self.indicator_name,
            "indicator_code": self.indicator_code,
            "value": self.value,
            "time": self.time,
            "source": self.source,
            "country": self.country,
        }


# ══════════════════════════════════════
# FRED 수집기
# ══════════════════════════════════════
class FREDCollector:
    """
    FRED (Federal Reserve Economic Data) API 수집기

    https://api.stlouisfed.org/fred/series/observations
    """

    BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

    def __init__(self):
        settings = get_settings()
        self._api_key = settings.external.fred_api_key
        self._timeout = 15
        self._retry_count = 3
        self._base_backoff = 2  # Exponential backoff 기본값 (초)

    @property
    def is_available(self) -> bool:
        """FRED API 키 설정 여부"""
        return bool(self._api_key)

    async def collect_all(self) -> list[EconomicIndicator]:
        """
        모든 FRED 지표 수집

        Returns:
            수집된 경제지표 리스트
        """
        if not self.is_available:
            logger.warning("FRED API key not configured. Skipping FRED collection.")
            return []

        indicators: list[EconomicIndicator] = []

        for indicator_type, series_id in FRED_SERIES_MAP.items():
            try:
                data = await self._fetch_series(series_id, indicator_type)
                if data:
                    indicators.append(data)
                    logger.debug(f"FRED [{indicator_type.value}] collected: {data.value}")
            except Exception as e:
                logger.warning(f"FRED collection error [{indicator_type.value}]: {e}")
                continue

        logger.info(f"FRED collection complete: {len(indicators)} indicators")
        return indicators

    async def collect_indicator(self, indicator_type: EconomicIndicatorType) -> Optional[EconomicIndicator]:
        """
        특정 FRED 지표 수집

        Args:
            indicator_type: 지표 타입

        Returns:
            수집된 경제지표 또는 None
        """
        if not self.is_available:
            logger.warning("FRED API key not configured.")
            return None

        if indicator_type not in FRED_SERIES_MAP:
            logger.warning(f"Unsupported FRED indicator: {indicator_type.value}")
            return None

        series_id = FRED_SERIES_MAP[indicator_type]
        return await self._fetch_series(series_id, indicator_type)

    async def _fetch_series(
        self,
        series_id: str,
        indicator_type: EconomicIndicatorType,
    ) -> Optional[EconomicIndicator]:
        """
        단일 FRED 시리즈 조회

        Args:
            series_id: FRED 시리즈 ID
            indicator_type: 지표 타입

        Returns:
            경제지표 또는 None
        """
        params = {
            "series_id": series_id,
            "api_key": self._api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 10,  # 최근 10개 데이터
        }

        for attempt in range(self._retry_count):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.get(self.BASE_URL, params=params)
                    response.raise_for_status()

                data = response.json()

                # 관찰값 추출
                observations = data.get("observations", [])
                if not observations:
                    logger.warning(f"FRED [{series_id}] no observations found")
                    return None

                # 가장 최신 데이터 선택
                latest = observations[0]
                date_str = latest.get("date", "")
                value_str = latest.get("value", "")

                # 빈 값 처리 (FRED는 "." 또는 빈 문자열로 표시)
                if value_str == "." or not value_str:
                    logger.warning(f"FRED [{series_id}] latest value is null")
                    return None

                try:
                    value = float(value_str)
                except ValueError:
                    logger.warning(f"FRED [{series_id}] invalid value format: {value_str}")
                    return None

                try:
                    date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                except ValueError:
                    logger.warning(f"FRED [{series_id}] invalid date format: {date_str}")
                    return None

                return EconomicIndicator(
                    indicator_name=indicator_type.value,
                    indicator_code=series_id,
                    value=value,
                    time=date,
                    source="FRED",
                    country="US",
                )

            except httpx.TimeoutException:
                if attempt < self._retry_count - 1:
                    wait_time = self._base_backoff ** (attempt + 1)
                    logger.debug(f"FRED timeout, retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"FRED [{series_id}] timeout after {self._retry_count} retries")
                    return None

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:  # Rate limit
                    if attempt < self._retry_count - 1:
                        wait_time = self._base_backoff ** (attempt + 1)
                        logger.warning(f"FRED rate limited, retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"FRED [{series_id}] rate limited after {self._retry_count} retries")
                        return None
                else:
                    logger.error(f"FRED [{series_id}] HTTP error: {e.response.status_code}")
                    return None

            except Exception as e:
                if attempt < self._retry_count - 1:
                    wait_time = self._base_backoff ** (attempt + 1)
                    logger.debug(f"FRED error, retrying in {wait_time}s: {e}")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"FRED [{series_id}] failed after {self._retry_count} retries: {e}")
                    return None

        return None

    @staticmethod
    def _get_unit(indicator_type: EconomicIndicatorType) -> str:
        """지표별 단위 반환"""
        units = {
            EconomicIndicatorType.GDP: "Billions of Dollars",
            EconomicIndicatorType.CPI: "Index",
            EconomicIndicatorType.FED_FUNDS_RATE: "%",
            EconomicIndicatorType.TREASURY_2Y: "%",
            EconomicIndicatorType.TREASURY_10Y: "%",
            EconomicIndicatorType.UNEMPLOYMENT: "%",
            EconomicIndicatorType.PMI: "Index",
            EconomicIndicatorType.VIX: "Index",
            EconomicIndicatorType.USD_KRW: "KRW/USD",
        }
        return units.get(indicator_type, "")


# ══════════════════════════════════════
# ECOS 수집기
# ══════════════════════════════════════
class ECOSCollector:
    """
    ECOS (한국은행 경제통계) API 수집기

    https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/{pagingCount}/{통계표코드}/{주기}/{시작일}/{종료일}/{항목코드}
    """

    BASE_URL = "https://ecos.bok.or.kr/api/StatisticSearch"

    def __init__(self):
        settings = get_settings()
        self._api_key = settings.external.ecos_api_key
        self._timeout = 15
        self._retry_count = 3
        self._base_backoff = 2

    @property
    def is_available(self) -> bool:
        """ECOS API 키 설정 여부"""
        return bool(self._api_key)

    async def collect_all(self) -> list[EconomicIndicator]:
        """
        모든 ECOS 지표 수집

        Returns:
            수집된 경제지표 리스트
        """
        if not self.is_available:
            logger.warning("ECOS API key not configured. Skipping ECOS collection.")
            return []

        indicators: list[EconomicIndicator] = []

        for indicator_type, series_info in ECOS_SERIES_MAP.items():
            try:
                data = await self._fetch_series(indicator_type, series_info)
                if data:
                    indicators.append(data)
                    logger.debug(f"ECOS [{indicator_type.value}] collected: {data.value}")
            except Exception as e:
                logger.warning(f"ECOS collection error [{indicator_type.value}]: {e}")
                continue

        logger.info(f"ECOS collection complete: {len(indicators)} indicators")
        return indicators

    async def collect_indicator(self, indicator_type: EconomicIndicatorType) -> Optional[EconomicIndicator]:
        """
        특정 ECOS 지표 수집

        Args:
            indicator_type: 지표 타입

        Returns:
            수집된 경제지표 또는 None
        """
        if not self.is_available:
            logger.warning("ECOS API key not configured.")
            return None

        if indicator_type not in ECOS_SERIES_MAP:
            logger.warning(f"Unsupported ECOS indicator: {indicator_type.value}")
            return None

        series_info = ECOS_SERIES_MAP[indicator_type]
        return await self._fetch_series(indicator_type, series_info)

    async def _fetch_series(
        self,
        indicator_type: EconomicIndicatorType,
        series_info: dict[str, str],
    ) -> Optional[EconomicIndicator]:
        """
        단일 ECOS 시리즈 조회

        Args:
            indicator_type: 지표 타입
            series_info: 통계표코드, 항목코드 등

        Returns:
            경제지표 또는 None
        """
        stat_code = series_info["stat_code"]
        item_code = series_info["item_code"]

        # 날짜 범위 설정 (최근 30일)
        today = datetime.now()
        start_date = (today - timedelta(days=30)).strftime("%Y%m%d")
        end_date = today.strftime("%Y%m%d")

        # 주기 결정 (월간: "M", 분기: "Q", 연간: "A")
        cycle = self._get_cycle(indicator_type)

        # URL 구성
        url = (
            f"{self.BASE_URL}/{self._api_key}/json/kr/1/100/" f"{stat_code}/{cycle}/{start_date}/{end_date}/{item_code}"
        )

        for attempt in range(self._retry_count):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.get(url)
                    response.raise_for_status()

                data = response.json()

                # ECOS 응답 구조 확인
                if data.get("stat_code") != "00":
                    logger.warning(f"ECOS [{stat_code}] error code: {data.get('stat_code')}")
                    return None

                records = data.get("row", [])
                if not records:
                    logger.warning(f"ECOS [{stat_code}] no records found")
                    return None

                # 가장 최신 데이터 선택 (마지막 항목)
                latest = records[-1]
                date_str = latest.get("TIME", "")
                value_str = latest.get("DATA_VALUE", "")

                if not date_str or not value_str:
                    logger.warning(f"ECOS [{stat_code}] incomplete record")
                    return None

                try:
                    value = float(value_str)
                except ValueError:
                    logger.warning(f"ECOS [{stat_code}] invalid value format: {value_str}")
                    return None

                try:
                    # ECOS 날짜 형식: "202312", "2023Q4" 등
                    date = self._parse_ecos_date(date_str, cycle)
                except ValueError:
                    logger.warning(f"ECOS [{stat_code}] invalid date format: {date_str}")
                    return None

                return EconomicIndicator(
                    indicator_name=indicator_type.value,
                    indicator_code=stat_code,
                    value=value,
                    time=date,
                    source="ECOS",
                    country="KR",
                )

            except httpx.TimeoutException:
                if attempt < self._retry_count - 1:
                    wait_time = self._base_backoff ** (attempt + 1)
                    logger.debug(f"ECOS timeout, retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"ECOS [{stat_code}] timeout after {self._retry_count} retries")
                    return None

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:  # Rate limit
                    if attempt < self._retry_count - 1:
                        wait_time = self._base_backoff ** (attempt + 1)
                        logger.warning(f"ECOS rate limited, retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"ECOS [{stat_code}] rate limited after {self._retry_count} retries")
                        return None
                else:
                    logger.error(f"ECOS [{stat_code}] HTTP error: {e.response.status_code}")
                    return None

            except Exception as e:
                if attempt < self._retry_count - 1:
                    wait_time = self._base_backoff ** (attempt + 1)
                    logger.debug(f"ECOS error, retrying in {wait_time}s: {e}")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"ECOS [{stat_code}] failed after {self._retry_count} retries: {e}")
                    return None

        return None

    @staticmethod
    def _get_cycle(indicator_type: EconomicIndicatorType) -> str:
        """지표별 주기 반환 (M: 월간, Q: 분기, A: 연간)"""
        cycles = {
            EconomicIndicatorType.BOK_BASE_RATE: "M",  # 월간
            EconomicIndicatorType.KR_CPI: "M",  # 월간
            EconomicIndicatorType.KR_UNEMPLOYMENT: "M",  # 월간
            EconomicIndicatorType.KR_GDP: "Q",  # 분기
            EconomicIndicatorType.KR_CURRENT_ACCOUNT: "M",  # 월간
        }
        return cycles.get(indicator_type, "M")

    @staticmethod
    def _parse_ecos_date(date_str: str, cycle: str) -> datetime:
        """
        ECOS 날짜 형식 파싱

        Args:
            date_str: "202312", "2023Q4" 등
            cycle: "M" (월간), "Q" (분기), "A" (연간)

        Returns:
            파싱된 datetime
        """
        try:
            if cycle == "M":  # YYYYMM
                return datetime.strptime(date_str, "%Y%m").replace(day=1, tzinfo=timezone.utc)
            elif cycle == "Q":  # YYYYQ#
                year = int(date_str[:4])
                quarter = int(date_str[5])
                month = (quarter - 1) * 3 + 1
                return datetime(year, month, 1, tzinfo=timezone.utc)
            elif cycle == "A":  # YYYY
                return datetime.strptime(date_str, "%Y").replace(month=1, day=1, tzinfo=timezone.utc)
            else:
                # 기본값: YYYYMMDD로 시도
                return datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError as e:
            raise ValueError(f"Failed to parse ECOS date '{date_str}' with cycle '{cycle}': {e}")

    @staticmethod
    def _get_unit(indicator_type: EconomicIndicatorType) -> str:
        """지표별 단위 반환"""
        units = {
            EconomicIndicatorType.BOK_BASE_RATE: "%",
            EconomicIndicatorType.KR_CPI: "Index",
            EconomicIndicatorType.KR_UNEMPLOYMENT: "%",
            EconomicIndicatorType.KR_GDP: "Billions of KRW",
            EconomicIndicatorType.KR_CURRENT_ACCOUNT: "Millions of USD",
        }
        return units.get(indicator_type, "")


# ══════════════════════════════════════
# 경제지표 수집 서비스
# ══════════════════════════════════════
class EconomicCollectorService:
    """
    통합 경제지표 수집 서비스

    FRED + ECOS에서 데이터를 수집하고 TimescaleDB에 저장합니다.
    Redis 캐싱으로 최신값을 관리합니다.
    """

    TABLE_NAME = "economic_indicators"
    CACHE_PREFIX = "economic_indicator:"
    CACHE_TTL = 86400  # 24시간

    def __init__(self):
        self._fred = FREDCollector()
        self._ecos = ECOSCollector()

    async def collect_and_store(self) -> dict[str, Any]:
        """
        전체 지표 수집 후 저장

        Returns:
            {"fred_count": int, "ecos_count": int, "total": int}
        """
        # 병렬 수집
        fred_indicators, ecos_indicators = await asyncio.gather(
            self._fred.collect_all(),
            self._ecos.collect_all(),
        )

        all_indicators = fred_indicators + ecos_indicators

        # TimescaleDB 저장
        await self._store_to_db(all_indicators)

        # Redis 캐싱
        await self._cache_latest(all_indicators)

        result = {
            "fred_count": len(fred_indicators),
            "ecos_count": len(ecos_indicators),
            "total": len(all_indicators),
        }

        logger.info(
            f"Economic indicators collection complete: "
            f"FRED={len(fred_indicators)}, ECOS={len(ecos_indicators)}, Total={len(all_indicators)}"
        )
        return result

    async def collect_indicator(self, indicator_type: EconomicIndicatorType) -> Optional[EconomicIndicator]:
        """
        특정 지표 수집

        Args:
            indicator_type: 지표 타입

        Returns:
            경제지표 또는 None
        """
        if indicator_type in FRED_SERIES_MAP:
            return await self._fred.collect_indicator(indicator_type)
        elif indicator_type in ECOS_SERIES_MAP:
            return await self._ecos.collect_indicator(indicator_type)
        else:
            logger.warning(f"Unsupported indicator type: {indicator_type.value}")
            return None

    async def get_cached(self, indicator_type: EconomicIndicatorType) -> Optional[dict[str, Any]]:
        """
        Redis 캐시에서 지표값 조회

        Args:
            indicator_type: 지표 타입

        Returns:
            캐시된 지표 딕셔너리 또는 None
        """
        try:
            redis = RedisManager.get_client()
            cache_key = f"{self.CACHE_PREFIX}{indicator_type.value}"
            cached = await redis.get(cache_key)

            if cached:
                import json

                return json.loads(cached)
            return None
        except Exception as e:
            logger.warning(f"Cache retrieval error for {indicator_type.value}: {e}")
            return None

    async def _cache_latest(self, indicators: list[EconomicIndicator]) -> None:
        """
        최신 지표값을 Redis에 캐싱

        Args:
            indicators: 경제지표 리스트
        """
        try:
            redis = RedisManager.get_client()
            import json

            for indicator in indicators:
                cache_key = f"{self.CACHE_PREFIX}{indicator.indicator_name}"
                cache_value = json.dumps(indicator.to_dict(), default=str)
                await redis.setex(cache_key, self.CACHE_TTL, cache_value)

            logger.debug(f"Cached {len(indicators)} indicators to Redis")
        except Exception as e:
            logger.warning(f"Cache storage error: {e}")

    async def _store_to_db(self, indicators: list[EconomicIndicator]) -> None:
        """
        TimescaleDB에 경제지표 저장 (UPSERT)

        economic_indicators 테이블에 수집된 지표를 저장합니다.
        동일 (time, indicator_code) PK가 이미 존재하면 값을 갱신합니다.

        Args:
            indicators: 경제지표 리스트
        """
        if not indicators:
            return

        try:
            from sqlalchemy import text as sa_text

            from db.database import async_session_factory

            async with async_session_factory() as session:
                query = sa_text("""
                    INSERT INTO economic_indicators (
                        time, indicator_code, indicator_name, value,
                        country, source
                    ) VALUES (
                        :time, :indicator_code, :indicator_name, :value,
                        :country, :source
                    )
                    ON CONFLICT (time, indicator_code)
                    DO UPDATE SET
                        indicator_name = EXCLUDED.indicator_name,
                        value = EXCLUDED.value
                """)

                for ind in indicators:
                    await session.execute(
                        query,
                        {
                            "time": ind.time,
                            "indicator_code": ind.indicator_code,
                            "indicator_name": ind.indicator_name,
                            "value": ind.value,
                            "country": ind.country,
                            "source": ind.source,
                        },
                    )
                await session.commit()

            logger.info(f"Stored {len(indicators)} indicators to TimescaleDB")
        except Exception as e:
            logger.warning(f"DB storage error (indicators still in cache): {e}")
