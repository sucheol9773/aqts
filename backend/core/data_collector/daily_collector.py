"""
일일 OHLCV 자동 수집 서비스 (Daily OHLCV Batch Collector)

스케줄러의 PRE_MARKET 이벤트에서 호출되어
유니버스의 모든 활성 종목에 대해 최근 일봉 데이터를 수집합니다.

동작 방식:
  1. DB universe 테이블에서 활성(is_active=True) 종목 조회
  2. 종목별로 최근 N 영업일(기본 5일) 일봉 데이터 수집
  3. KIS API rate limit (18 req/sec) 준수
  4. 수집 결과 요약 반환

설계 원칙:
  - 단일 종목 실패가 전체 배치를 중단하지 않음
  - 이미 존재하는 데이터는 UPSERT (중복 안전)
  - BACKTEST 모드에서는 수집 건너뜀 (KIS API 차단)
  - 미국 시장은 거래소(NAS/NYS/AMS) 정보를 market 필드에서 매핑
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.logging import logger
from config.settings import TradingMode, get_settings
from core.data_collector.market_data import MarketDataCollector


@dataclass
class CollectionResult:
    """단일 종목 수집 결과"""

    ticker: str
    country: str
    records_saved: int = 0
    success: bool = False
    error: Optional[str] = None


@dataclass
class BatchCollectionReport:
    """배치 수집 리포트"""

    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None
    total_tickers: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    total_records: int = 0
    results: list[CollectionResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "total_tickers": self.total_tickers,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "skipped": self.skipped,
            "total_records": self.total_records,
            "elapsed_seconds": (
                round((self.finished_at - self.started_at).total_seconds(), 1) if self.finished_at else None
            ),
        }


# 시장 코드 → 거래소 매핑
_MARKET_TO_EXCHANGE = {
    "NASDAQ": "NAS",
    "NYSE": "NYS",
    "AMEX": "AMS",
}

_KR_MARKETS = {"KRX"}
_US_MARKETS = {"NASDAQ", "NYSE", "AMEX"}


class DailyOHLCVCollector:
    """
    일일 OHLCV 자동 수집 서비스

    스케줄러에서 장 전(08:30)에 호출하여
    유니버스 전 종목의 최근 일봉 데이터를 DB에 적재합니다.

    Usage:
        async with async_session_factory() as session:
            collector = DailyOHLCVCollector(session)
            report = await collector.collect_all()
            print(report.to_dict())
    """

    def __init__(
        self,
        db_session: AsyncSession,
        lookback_days: int = 5,
    ):
        self._db = db_session
        self._market_collector = MarketDataCollector(db_session)
        self._lookback_days = lookback_days
        self._settings = get_settings()

    async def collect_all(
        self,
        country: Optional[str] = None,
    ) -> BatchCollectionReport:
        """
        유니버스 전 종목 OHLCV 수집

        Args:
            country: "KR" 또는 "US" (None이면 둘 다)

        Returns:
            BatchCollectionReport
        """
        report = BatchCollectionReport()

        # BACKTEST 모드 체크
        if self._settings.kis.trading_mode == TradingMode.BACKTEST:
            logger.warning("[DailyCollector] BACKTEST 모드 — 수집 건너뜀")
            report.finished_at = datetime.now(timezone.utc)
            return report

        # 유니버스에서 활성 종목 조회
        tickers = await self._load_active_tickers(country)
        report.total_tickers = len(tickers)

        if not tickers:
            logger.warning("[DailyCollector] 활성 종목이 없습니다")
            report.finished_at = datetime.now(timezone.utc)
            return report

        logger.info(f"[DailyCollector] 수집 시작: {len(tickers)}개 종목, " f"lookback={self._lookback_days}일")

        # 종목별 순차 수집 (KIS rate limit 준수)
        for ticker_info in tickers:
            ticker = ticker_info["ticker"]
            market = ticker_info["market"]
            ticker_country = ticker_info["country"]

            try:
                result = await self._collect_single(ticker, market, ticker_country)
                report.results.append(result)

                if result.success:
                    report.succeeded += 1
                    report.total_records += result.records_saved
                else:
                    report.failed += 1
                    if result.error:
                        report.errors.append(f"{ticker}: {result.error}")

            except Exception as e:
                report.failed += 1
                report.errors.append(f"{ticker}: {type(e).__name__}: {e}")
                report.results.append(
                    CollectionResult(
                        ticker=ticker,
                        country=ticker_country,
                        error=str(e),
                    )
                )
                logger.error(f"[DailyCollector] {ticker} 수집 실패: {e}")

        report.finished_at = datetime.now(timezone.utc)

        logger.info(
            f"[DailyCollector] 수집 완료: "
            f"{report.succeeded}/{report.total_tickers} 성공, "
            f"{report.failed} 실패, "
            f"{report.total_records}건 저장, "
            f"{report.to_dict()['elapsed_seconds']}초"
        )

        return report

    async def collect_single_ticker(
        self,
        ticker: str,
        country: str = "KR",
        market: str = "KRX",
    ) -> CollectionResult:
        """
        단일 종목 수집 (수동 트리거용)

        Args:
            ticker: 종목코드
            country: 국가 코드
            market: 시장 코드

        Returns:
            CollectionResult
        """
        return await self._collect_single(ticker, market, country)

    async def _collect_single(
        self,
        ticker: str,
        market: str,
        country: str,
    ) -> CollectionResult:
        """단일 종목 OHLCV 수집"""
        result = CollectionResult(ticker=ticker, country=country)

        if market in _KR_MARKETS:
            records = await self._collect_kr(ticker)
        elif market in _US_MARKETS:
            exchange = _MARKET_TO_EXCHANGE.get(market, "NAS")
            records = await self._collect_us(ticker, exchange)
        else:
            result.error = f"지원하지 않는 시장: {market}"
            return result

        result.records_saved = records
        result.success = True

        if records > 0:
            logger.debug(f"[DailyCollector] {ticker} ({market}): {records}건 저장")

        return result

    async def _collect_kr(self, ticker: str) -> int:
        """한국 종목 일봉 수집"""
        end_date = date.today()
        start_date = end_date - timedelta(days=self._lookback_days + 5)
        # +5: 주말/공휴일 여유분

        return await self._market_collector.collect_kr_daily(
            ticker,
            start_date.strftime("%Y%m%d"),
            end_date.strftime("%Y%m%d"),
        )

    async def _collect_us(self, ticker: str, exchange: str) -> int:
        """미국 종목 일봉 수집"""
        # KIS US API는 count 기반 (최근 N개 일봉)
        return await self._market_collector.collect_us_daily(
            ticker,
            exchange=exchange,
            count=self._lookback_days + 5,
        )

    async def _load_active_tickers(
        self,
        country: Optional[str] = None,
    ) -> list[dict]:
        """DB에서 활성 종목 목록 조회"""
        if country:
            query = text("""
                SELECT ticker, market, country
                FROM universe
                WHERE is_active = TRUE AND country = :country
                ORDER BY market, ticker
            """)
            rows = await self._db.execute(query, {"country": country})
        else:
            query = text("""
                SELECT ticker, market, country
                FROM universe
                WHERE is_active = TRUE
                ORDER BY country, market, ticker
            """)
            rows = await self._db.execute(query)

        return [{"ticker": r[0], "market": r[1], "country": r[2]} for r in rows.fetchall()]
