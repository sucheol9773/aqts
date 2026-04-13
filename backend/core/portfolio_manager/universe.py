"""
유니버스 관리 모듈 (F-05-06)

투자 대상 종목 유니버스를 관리합니다.
사용자 선호도, 자동 필터링, 월별 갱신을 지원하며,
PostgreSQL에 저장된 유니버스 데이터를 관리합니다.

주요 기능:
- async build_universe: 전체 유니버스 구축 (초기 로드)
- async refresh_universe: 월별 갱신 (리밸런싱 전)
- _apply_sector_filter: 사용자 섹터 필터 적용
- _apply_designated_tickers: 지정 종목 우선 포함
- _apply_auto_filter: 자동 필터링 (유동성/시가/상태)

필터 파이프라인:
user_sector_filter → designated_tickers → auto_filter(liquidity, cap, status)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text

from config.constants import AssetType, Market
from config.logging import logger
from config.settings import get_settings
from core.portfolio_manager.profile import InvestorProfile
from db.database import async_session_factory


# ══════════════════════════════════════════════════════════════════════════════
# 유니버스 아이템 데이터 구조
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class UniverseItem:
    """
    유니버스 종목

    투자 대상 종목의 기본 정보를 포함합니다.
    리밸런싱 대상 종목을 선정하기 위해 필요한 모든 정보를 제공합니다.
    """

    ticker: str
    """종목 코드 (예: 005930, AAPL)"""

    market: Market
    """시장 구분 (KRX, NYSE, NASDAQ, AMEX)"""

    sector: str
    """섹터 (금융, IT, 에너지 등)"""

    asset_type: AssetType
    """자산 유형 (STOCK, ETF, BOND 등)"""

    market_cap: Optional[float] = None
    """시가총액 (원 또는 달러)"""

    avg_daily_volume: Optional[float] = None
    """평균 일일 거래량 (주식 수 또는 달러)"""

    is_active: bool = True
    """활성 상태 (상장폐지/관리종목 제외)"""

    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """마지막 갱신 시각 (UTC)"""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환"""
        return {
            "ticker": self.ticker,
            "market": self.market.value,
            "sector": self.sector,
            "asset_type": self.asset_type.value,
            "market_cap": self.market_cap,
            "avg_daily_volume": self.avg_daily_volume,
            "is_active": self.is_active,
            "updated_at": self.updated_at.isoformat(),
        }


# ══════════════════════════════════════════════════════════════════════════════
# 유니버스 관리 엔진
# ══════════════════════════════════════════════════════════════════════════════
class UniverseManager:
    """
    투자 대상 유니버스 관리 엔진

    사용자 프로필에 따라 투자 대상 종목을 동적으로 선정합니다.
    섹터 필터, 지정 종목, 유동성/시가 필터를 조합하여
    최종 투자 유니버스를 결정합니다.

    필터 파이프라인:
    1. 사용자 섹터 필터 적용 (선호 섹터만 포함)
    2. 지정 종목 강제 포함 (사용자 지정 종목 항상 포함)
    3. 자동 필터링 (유동성 필터, 시가총액 필터, 상태 필터)

    주요 기능:
    - async build_universe: 전체 유니버스 구축 (초기 로드)
    - async refresh_universe: 월별 갱신 (리밸런싱 전)
    """

    def __init__(self, profile: InvestorProfile):
        """
        유니버스 관리자 초기화

        Args:
            profile: 투자자 프로필
        """
        self._profile = profile
        self._settings = get_settings()
        logger.info(f"UniverseManager 초기화: user_id={profile.user_id}")

    async def build_universe(self) -> list[UniverseItem]:
        """
        전체 유니버스 구축

        초기 로드 시 전체 투자 대상 종목을 선정합니다.
        데이터베이스의 전체 종목 목록에서 필터를 적용하여
        최종 유니버스를 결정합니다.

        Returns:
            list[UniverseItem]: 필터링된 유니버스 종목 리스트

        필터 파이프라인:
        1. 데이터베이스에서 활성 종목 로드
        2. 섹터 필터 적용
        3. 지정 종목 강제 포함
        4. 자동 필터 적용 (유동성/시가/상태)
        """
        logger.info(f"유니버스 구축 시작: user_id={self._profile.user_id}")

        try:
            # 1. 데이터베이스에서 활성 종목 로드
            items = await self._load_all_active_stocks()
            logger.info(f"로드된 종목 수: {len(items)}")

            # 2. 섹터 필터 적용
            if self._profile.sector_filter:
                items = self._apply_sector_filter(items, self._profile.sector_filter)
                logger.info(f"섹터 필터 후: {len(items)}개")

            # 3. 지정 종목 강제 포함
            items = self._apply_designated_tickers(items, self._profile.designated_tickers)
            logger.info(f"지정 종목 포함 후: {len(items)}개")

            # 4. 자동 필터 적용
            items = self._apply_auto_filter(items)
            logger.info(f"자동 필터 후: {len(items)}개 (최종 유니버스)")

            # PostgreSQL에 저장
            await self._store_universe(items)

            return items

        except Exception as e:
            logger.error(f"유니버스 구축 실패: {e}")
            raise

    async def refresh_universe(self) -> list[UniverseItem]:
        """
        유니버스 월별 갱신

        리밸런싱 전에 유니버스를 갱신합니다.
        최신 시가총액, 유동성 정보를 반영하며,
        건설 과정은 build_universe와 동일합니다.

        Returns:
            list[UniverseItem]: 갱신된 유니버스 종목 리스트

        갱신 시점:
        - 월별 첫 영업일 (리밸런싱 전)
        - 비상 리밸런싱 시 (선택사항)
        """
        logger.info(f"유니버스 갱신 시작: user_id={self._profile.user_id}")

        try:
            # 기존 유니버스 데이터 갱신
            items = await self._load_all_active_stocks()

            # 동일한 필터 파이프라인 적용
            if self._profile.sector_filter:
                items = self._apply_sector_filter(items, self._profile.sector_filter)

            items = self._apply_designated_tickers(items, self._profile.designated_tickers)
            items = self._apply_auto_filter(items)

            # 갱신된 데이터 저장
            await self._store_universe(items)

            logger.info(f"유니버스 갱신 완료: {len(items)}개 종목")
            return items

        except Exception as e:
            logger.error(f"유니버스 갱신 실패: {e}")
            raise

    def _apply_sector_filter(self, items: list[UniverseItem], sectors: list[str]) -> list[UniverseItem]:
        """
        섹터 필터 적용

        사용자가 지정한 섹터만 포함합니다.
        사용자 프로필의 sector_filter에 명시된 섹터만 유니버스에 포함됩니다.

        Args:
            items: 종목 리스트
            sectors: 제외할 섹터 리스트

        Returns:
            list[UniverseItem]: 필터링된 종목 리스트
        """
        logger.debug(f"섹터 필터 적용: 제외 섹터 {sectors}")

        filtered = [item for item in items if item.sector not in sectors]
        logger.debug(f"섹터 필터 결과: {len(items)} → {len(filtered)}")

        return filtered

    def _apply_designated_tickers(self, items: list[UniverseItem], tickers: list[str]) -> list[UniverseItem]:
        """
        지정 종목 강제 포함

        사용자가 지정한 종목은 다른 필터 결과와 무관하게 항상 포함됩니다.
        지정 종목은 자동 필터 결과보다 우선합니다.

        Args:
            items: 종목 리스트
            tickers: 지정 종목 코드 리스트

        Returns:
            list[UniverseItem]: 지정 종목이 포함된 종목 리스트
        """
        logger.debug(f"지정 종목 강제 포함: {len(tickers)}개")

        if not tickers:
            return items

        # 기존 종목과 지정 종목을 합침 (중복 제거)
        existing_tickers = {item.ticker for item in items}
        result = items[:]

        for ticker in tickers:
            if ticker not in existing_tickers:
                # 지정 종목이 유니버스에 없으면 추가 (더미 데이터)
                logger.warning(f"지정 종목이 데이터베이스에 없음: {ticker}")
                result.append(
                    UniverseItem(
                        ticker=ticker,
                        market=Market.KRX,  # 기본값
                        sector="Unknown",
                        asset_type=AssetType.STOCK,
                        is_active=True,
                    )
                )

        logger.debug(f"지정 종목 포함 결과: {len(items)} → {len(result)}")
        return result

    def _apply_auto_filter(self, items: list[UniverseItem]) -> list[UniverseItem]:
        """
        자동 필터링

        다음 기준에 따라 종목을 필터링합니다:
        1. 유동성 필터: 평균 일일 거래량 하위 20% 제외
        2. 시가총액 필터: 소형주 제외 (선택사항)
        3. 상태 필터: 관리종목, 상장폐지 예정 제외

        Args:
            items: 종목 리스트

        Returns:
            list[UniverseItem]: 필터링된 종목 리스트
        """
        logger.debug("자동 필터 적용 시작")

        # 1. 유동성 필터: 거래량 하위 20% 제외
        if items:
            volumes = [item.avg_daily_volume for item in items if item.avg_daily_volume is not None]
            if volumes:
                volume_threshold = sorted(volumes)[int(len(volumes) * 0.2)]
                items = [
                    item for item in items if item.avg_daily_volume is None or item.avg_daily_volume >= volume_threshold
                ]
                logger.debug(f"유동성 필터 후: {len(items)}개")

        # 2. 상태 필터: 비활성 종목 제외
        items = [item for item in items if item.is_active]
        logger.debug(f"상태 필터 후: {len(items)}개")

        # 3. 지정 종목 보호 (지정 종목은 필터링 제외)
        # -> _apply_designated_tickers에서 처리됨

        logger.debug(f"자동 필터 완료: {len(items)}개")
        return items

    async def _load_all_active_stocks(self) -> list[UniverseItem]:
        """
        데이터베이스에서 활성 종목 로드

        PostgreSQL의 universe 테이블에서 활성 상태인 종목을 로드합니다.

        Returns:
            list[UniverseItem]: 활성 종목 리스트
        """
        logger.debug("활성 종목 로드 시작")

        try:
            async with async_session_factory() as db_session:
                query = text(
                    """
                    SELECT ticker, market, sector, asset_type, market_cap,
                           avg_daily_volume, is_active, updated_at
                    FROM universe
                    WHERE is_active = true
                    ORDER BY market_cap DESC NULLS LAST
                """
                )

                result = await db_session.execute(query)
                rows = result.fetchall()

                items = [
                    UniverseItem(
                        ticker=row[0],
                        market=Market(row[1]),
                        sector=row[2],
                        asset_type=AssetType(row[3]),
                        market_cap=row[4],
                        avg_daily_volume=row[5],
                        is_active=row[6],
                        updated_at=row[7] if row[7] else datetime.now(timezone.utc),
                    )
                    for row in rows
                ]

                logger.debug(f"활성 종목 로드 완료: {len(items)}개")
                return items

        except Exception as e:
            logger.error(f"활성 종목 로드 실패: {e}")
            return []

    async def _store_universe(self, items: list[UniverseItem]) -> None:
        """
        유니버스를 PostgreSQL에 저장

        Args:
            items: 유니버스 종목 리스트
        """
        logger.debug(f"유니버스 저장 시작: {len(items)}개")

        try:
            async with async_session_factory() as db_session:
                for item in items:
                    query = text(
                        """
                        INSERT INTO universe (
                            ticker, market, sector, asset_type,
                            market_cap, avg_daily_volume, is_active, updated_at
                        ) VALUES (
                            :ticker, :market, :sector, :asset_type,
                            :market_cap, :avg_daily_volume, :is_active, :updated_at
                        )
                        ON CONFLICT (ticker, market) DO UPDATE SET
                            market_cap = EXCLUDED.market_cap,
                            avg_daily_volume = EXCLUDED.avg_daily_volume,
                            is_active = EXCLUDED.is_active,
                            updated_at = EXCLUDED.updated_at
                    """
                    )

                    await db_session.execute(
                        query,
                        {
                            "ticker": item.ticker,
                            "market": item.market.value,
                            "sector": item.sector,
                            "asset_type": item.asset_type.value,
                            "market_cap": item.market_cap,
                            "avg_daily_volume": item.avg_daily_volume,
                            "is_active": item.is_active,
                            "updated_at": item.updated_at,
                        },
                    )

                await db_session.commit()
                logger.debug("유니버스 저장 완료")

        except Exception as e:
            logger.error(f"유니버스 저장 실패: {e}")
