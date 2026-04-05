"""
DailyOHLCVCollector 유닛테스트

일일 OHLCV 자동 수집 배치 서비스 테스트.

테스트 범위:
- BatchCollectionReport 구조 검증
- CollectionResult 필드 검증
- BACKTEST 모드 수집 건너뜀
- 시장 매핑 (_MARKET_TO_EXCHANGE)
- 활성 종목 로드 로직 (mocked DB)
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.data_collector.daily_collector import (
    _KR_MARKETS,
    _MARKET_TO_EXCHANGE,
    _US_MARKETS,
    BatchCollectionReport,
    CollectionResult,
    DailyOHLCVCollector,
)


class TestCollectionResult:
    """CollectionResult 데이터 구조 테스트"""

    def test_default_values(self):
        """기본값 검증"""
        r = CollectionResult(ticker="005930", country="KR")
        assert r.ticker == "005930"
        assert r.country == "KR"
        assert r.records_saved == 0
        assert r.success is False
        assert r.error is None

    def test_success_result(self):
        """성공 결과 생성"""
        r = CollectionResult(
            ticker="AAPL",
            country="US",
            records_saved=5,
            success=True,
        )
        assert r.success is True
        assert r.records_saved == 5


class TestBatchCollectionReport:
    """BatchCollectionReport 데이터 구조 테스트"""

    def test_default_values(self):
        """기본값 검증"""
        report = BatchCollectionReport()
        assert report.total_tickers == 0
        assert report.succeeded == 0
        assert report.failed == 0
        assert report.total_records == 0
        assert isinstance(report.results, list)
        assert isinstance(report.errors, list)

    def test_to_dict(self):
        """to_dict() 변환 검증"""
        report = BatchCollectionReport()
        report.total_tickers = 10
        report.succeeded = 8
        report.failed = 2
        report.total_records = 40
        report.finished_at = datetime.now(timezone.utc)

        d = report.to_dict()
        assert d["total_tickers"] == 10
        assert d["succeeded"] == 8
        assert d["failed"] == 2
        assert d["total_records"] == 40
        assert "elapsed_seconds" in d

    def test_to_dict_before_finish(self):
        """완료 전 to_dict() — elapsed_seconds가 None"""
        report = BatchCollectionReport()
        d = report.to_dict()
        assert d["elapsed_seconds"] is None
        assert d["finished_at"] is None


class TestMarketMapping:
    """시장 매핑 상수 테스트"""

    def test_kr_markets(self):
        """한국 시장 코드"""
        assert "KRX" in _KR_MARKETS

    def test_us_markets(self):
        """미국 시장 코드"""
        assert "NASDAQ" in _US_MARKETS
        assert "NYSE" in _US_MARKETS
        assert "AMEX" in _US_MARKETS

    def test_market_to_exchange_mapping(self):
        """시장→거래소 매핑"""
        assert _MARKET_TO_EXCHANGE["NASDAQ"] == "NAS"
        assert _MARKET_TO_EXCHANGE["NYSE"] == "NYS"
        assert _MARKET_TO_EXCHANGE["AMEX"] == "AMS"


class TestDailyOHLCVCollector:
    """DailyOHLCVCollector 동작 테스트"""

    @pytest.mark.asyncio
    async def test_backtest_mode_skips_collection(self):
        """BACKTEST 모드에서 수집 건너뜀"""
        mock_session = AsyncMock()

        with patch("core.data_collector.daily_collector.get_settings") as mock_settings:
            settings = MagicMock()
            settings.kis.trading_mode = MagicMock()
            settings.kis.trading_mode.__eq__ = lambda self, other: True
            # BACKTEST 모드 시뮬레이션
            from config.settings import TradingMode

            settings.kis.trading_mode = TradingMode.BACKTEST
            mock_settings.return_value = settings

            collector = DailyOHLCVCollector(mock_session)
            report = await collector.collect_all()

        assert report.total_tickers == 0
        assert report.finished_at is not None

    @pytest.mark.asyncio
    async def test_empty_universe_returns_empty_report(self):
        """활성 종목이 없으면 빈 리포트 반환"""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("core.data_collector.daily_collector.get_settings") as mock_settings:
            settings = MagicMock()
            from config.settings import TradingMode

            settings.kis.trading_mode = TradingMode.DEMO
            mock_settings.return_value = settings

            collector = DailyOHLCVCollector(mock_session)
            report = await collector.collect_all()

        assert report.total_tickers == 0
        assert report.succeeded == 0

    @pytest.mark.asyncio
    async def test_collect_single_kr(self):
        """단일 KR 종목 수집 결과 구조"""
        mock_session = AsyncMock()

        with patch("core.data_collector.daily_collector.get_settings") as mock_settings:
            settings = MagicMock()
            from config.settings import TradingMode

            settings.kis.trading_mode = TradingMode.DEMO
            mock_settings.return_value = settings

            collector = DailyOHLCVCollector(mock_session)

            # MarketDataCollector.collect_kr_daily를 모킹
            collector._market_collector.collect_kr_daily = AsyncMock(return_value=3)

            result = await collector.collect_single_ticker("005930", country="KR", market="KRX")

        assert result.success is True
        assert result.records_saved == 3
        assert result.ticker == "005930"

    @pytest.mark.asyncio
    async def test_collect_single_us(self):
        """단일 US 종목 수집 결과 구조"""
        mock_session = AsyncMock()

        with patch("core.data_collector.daily_collector.get_settings") as mock_settings:
            settings = MagicMock()
            from config.settings import TradingMode

            settings.kis.trading_mode = TradingMode.DEMO
            mock_settings.return_value = settings

            collector = DailyOHLCVCollector(mock_session)

            # MarketDataCollector.collect_us_daily를 모킹
            collector._market_collector.collect_us_daily = AsyncMock(return_value=5)

            result = await collector.collect_single_ticker("AAPL", country="US", market="NASDAQ")

        assert result.success is True
        assert result.records_saved == 5
        assert result.ticker == "AAPL"

    @pytest.mark.asyncio
    async def test_unsupported_market_returns_error(self):
        """지원하지 않는 시장 코드 에러"""
        mock_session = AsyncMock()

        with patch("core.data_collector.daily_collector.get_settings") as mock_settings:
            settings = MagicMock()
            from config.settings import TradingMode

            settings.kis.trading_mode = TradingMode.DEMO
            mock_settings.return_value = settings

            collector = DailyOHLCVCollector(mock_session)

            result = await collector.collect_single_ticker("TEST", country="JP", market="TSE")

        assert result.success is False
        assert "지원하지 않는 시장" in result.error
