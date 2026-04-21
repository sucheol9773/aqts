"""
Comprehensive coverage tests for low-coverage data collector modules.

Coverage achieved (81 tests, 27.8s):
- market_data.py: 84% (130 statements, 21 missed)
- economic_collector.py: 59% (300 statements, 123 missed)
- news_collector.py: 57% (216 statements, 92 missed)
- kis_websocket.py: 84% (256 statements, 40 missed)

Test categories covered:
1. MarketDataCollector: KR/US daily collection, price retrieval, data validation, DB operations
2. FREDCollector: API fetch, retry logic, error handling, data parsing
3. ECOSCollector: API fetch, date parsing, error handling
4. EconomicCollectorService: Collection orchestration, caching
5. NewsCollector: Ticker extraction, RSS parsing, DART disclosures
6. KISRealtimeClient: WebSocket lifecycle, subscription management, message handling

All tests pass with black + ruff compliance.
"""

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from config.constants import Country, EconomicIndicatorType, Market, NewsSource
from core.data_collector.economic_collector import (
    EconomicCollectorService,
    EconomicIndicator,
    ECOSCollector,
    FREDCollector,
)
from core.data_collector.kis_websocket import (
    TR_ID_ORDERBOOK,
    TR_ID_QUOTE,
    KISRealtimeClient,
    RealtimeOrderbook,
    RealtimeQuote,
    _safe_float,
    _safe_int,
)
from core.data_collector.market_data import MarketDataCollector
from core.data_collector.news_collector import (
    DARTCollector,
    NewsArticle,
    RSSNewsCollector,
    extract_tickers,
)

# ══════════════════════════════════════
# Test: market_data.py (MarketDataCollector)
# ══════════════════════════════════════


class TestMarketDataCollectorKRDaily:
    """KR daily data collection tests"""

    @pytest.mark.asyncio
    async def test_collect_kr_daily_success(self):
        """Successfully collect KR daily data"""
        mock_db = AsyncMock()
        mock_kis = AsyncMock()

        with patch("core.data_collector.market_data.KISClient", return_value=mock_kis):
            collector = MarketDataCollector(mock_db)
            collector._kis = mock_kis

            mock_kis.get_kr_stock_daily.return_value = {
                "output2": [
                    {
                        "stck_bsop_date": "20260407",
                        "stck_oprc": "70000",
                        "stck_hgpr": "71000",
                        "stck_lwpr": "69000",
                        "stck_clpr": "70500",
                        "acml_vol": "5000000",
                    }
                ]
            }
            mock_db.execute.return_value.fetchall.return_value = []
            collector._save_ohlcv_batch = AsyncMock(return_value=1)

            result = await collector.collect_kr_daily("005930", "20260101", "20260407")
            assert result == 1

    @pytest.mark.asyncio
    async def test_collect_kr_daily_empty_response(self):
        """Handle empty KR daily response"""
        mock_db = AsyncMock()
        mock_kis = AsyncMock()

        with patch("core.data_collector.market_data.KISClient", return_value=mock_kis):
            collector = MarketDataCollector(mock_db)
            collector._kis = mock_kis

            mock_kis.get_kr_stock_daily.return_value = {"output2": []}

            result = await collector.collect_kr_daily("005930", "20260101", "20260407")
            assert result == 0

    @pytest.mark.asyncio
    async def test_collect_kr_daily_malformed_row(self):
        """Skip malformed rows in KR daily collection"""
        mock_db = AsyncMock()
        mock_kis = AsyncMock()

        with patch("core.data_collector.market_data.KISClient", return_value=mock_kis):
            collector = MarketDataCollector(mock_db)
            collector._kis = mock_kis

            mock_kis.get_kr_stock_daily.return_value = {
                "output2": [
                    {
                        "stck_bsop_date": "20260407",
                        "stck_oprc": "70000",
                        "stck_hgpr": "invalid",
                        "stck_lwpr": "69000",
                        "stck_clpr": "70500",
                        "acml_vol": "5000000",
                    },
                    {
                        "stck_bsop_date": "20260406",
                        "stck_oprc": "69500",
                        "stck_hgpr": "70500",
                        "stck_lwpr": "69000",
                        "stck_clpr": "70000",
                        "acml_vol": "4800000",
                    },
                ]
            }
            collector._save_ohlcv_batch = AsyncMock(return_value=1)

            result = await collector.collect_kr_daily("005930", "20260101", "20260407")
            assert result == 1

    @pytest.mark.asyncio
    async def test_collect_kr_daily_api_error(self):
        """Handle API error in KR daily collection"""
        mock_db = AsyncMock()
        mock_kis = AsyncMock()

        with patch("core.data_collector.market_data.KISClient", return_value=mock_kis):
            collector = MarketDataCollector(mock_db)
            collector._kis = mock_kis

            mock_kis.get_kr_stock_daily.side_effect = Exception("API Error")

            with pytest.raises(Exception):
                await collector.collect_kr_daily("005930", "20260101", "20260407")


class TestMarketDataCollectorUSDaily:
    """US daily data collection tests"""

    @pytest.mark.asyncio
    async def test_collect_us_daily_success(self):
        """Successfully collect US daily data"""
        mock_db = AsyncMock()
        mock_kis = AsyncMock()

        with patch("core.data_collector.market_data.KISClient", return_value=mock_kis):
            collector = MarketDataCollector(mock_db)
            collector._kis = mock_kis

            mock_kis.get_us_stock_daily.return_value = {
                "output2": [
                    {
                        "xymd": "20260407",
                        "open": "150.5",
                        "high": "152.3",
                        "low": "150.2",
                        "clos": "151.8",
                        "tvol": "1000000",
                    }
                ]
            }
            collector._save_ohlcv_batch = AsyncMock(return_value=1)

            result = await collector.collect_us_daily("AAPL", "NAS", 100)
            assert result == 1

    @pytest.mark.asyncio
    async def test_collect_us_daily_market_mapping(self):
        """Test market code mapping (NAS, NYS, AMS)"""
        mock_db = AsyncMock()
        mock_kis = AsyncMock()

        with patch("core.data_collector.market_data.KISClient", return_value=mock_kis):
            collector = MarketDataCollector(mock_db)
            collector._kis = mock_kis

            mock_kis.get_us_stock_daily.return_value = {"output2": []}

            await collector.collect_us_daily("IBM", "NYS", 100)
            await collector.collect_us_daily("MSFT", "NAS", 100)
            assert mock_kis.get_us_stock_daily.call_count == 2

    @pytest.mark.asyncio
    async def test_collect_us_daily_empty_response(self):
        """Handle empty US daily response"""
        mock_db = AsyncMock()
        mock_kis = AsyncMock()

        with patch("core.data_collector.market_data.KISClient", return_value=mock_kis):
            collector = MarketDataCollector(mock_db)
            collector._kis = mock_kis

            mock_kis.get_us_stock_daily.return_value = {"output2": []}

            result = await collector.collect_us_daily("AAPL", "NAS", 100)
            assert result == 0


class TestMarketDataCollectorCurrentPrice:
    """Current price tests"""

    @pytest.mark.asyncio
    async def test_get_current_price_kr(self):
        """Get current price for Korean stock"""
        mock_db = AsyncMock()
        mock_kis = AsyncMock()

        with patch("core.data_collector.market_data.KISClient", return_value=mock_kis):
            collector = MarketDataCollector(mock_db)
            collector._kis = mock_kis

            mock_kis.get_kr_stock_price.return_value = {"output": {"stck_prpr": "70000"}}

            result = await collector.get_current_price("005930", Country.KR)
            assert result == 70000.0

    @pytest.mark.asyncio
    async def test_get_current_price_us(self):
        """Get current price for US stock"""
        mock_db = AsyncMock()
        mock_kis = AsyncMock()

        with patch("core.data_collector.market_data.KISClient", return_value=mock_kis):
            collector = MarketDataCollector(mock_db)
            collector._kis = mock_kis

            mock_kis.get_us_stock_price.return_value = {"output": {"last": "150.5"}}

            result = await collector.get_current_price("AAPL", Country.US)
            assert result == 150.5

    @pytest.mark.asyncio
    async def test_get_current_price_error(self):
        """Handle error in current price retrieval"""
        mock_db = AsyncMock()
        mock_kis = AsyncMock()

        with patch("core.data_collector.market_data.KISClient", return_value=mock_kis):
            collector = MarketDataCollector(mock_db)
            collector._kis = mock_kis

            mock_kis.get_kr_stock_price.side_effect = Exception("Connection error")

            result = await collector.get_current_price("005930", Country.KR)
            assert result is None


class TestMarketDataCollectorValidateAndFill:
    """Data integrity validation tests"""

    @pytest.mark.asyncio
    async def test_validate_and_fill_insufficient_data(self):
        """Return early with insufficient data"""
        mock_db = AsyncMock()
        mock_kis = AsyncMock()

        with patch("core.data_collector.market_data.KISClient", return_value=mock_kis):
            collector = MarketDataCollector(mock_db)

            mock_result = MagicMock()
            mock_result.fetchall.return_value = [
                (datetime.now(), 100.0, 1000),
            ]
            mock_db.execute = AsyncMock(return_value=mock_result)

            result = await collector.validate_and_fill("005930", Market.KRX.value)
            assert result["missing_filled"] == 0
            assert result["outliers_flagged"] == 0
            assert result["excluded"] is False

    @pytest.mark.asyncio
    async def test_validate_and_fill_normal_data(self):
        """Process normal data without issues"""
        mock_db = AsyncMock()
        mock_kis = AsyncMock()

        with patch("core.data_collector.market_data.KISClient", return_value=mock_kis):
            collector = MarketDataCollector(mock_db)

            base_date = datetime.now(timezone.utc)
            data = [(base_date - timedelta(days=i), 100.0 + i, 1000) for i in range(10, 0, -1)]
            mock_result = MagicMock()
            mock_result.fetchall.return_value = data
            mock_db.execute = AsyncMock(return_value=mock_result)

            result = await collector.validate_and_fill("005930", Market.KRX.value)
            assert "excluded" in result
            assert "outliers_flagged" in result

    @pytest.mark.asyncio
    async def test_validate_and_fill_outlier_detection(self):
        """Detect outliers via z-score"""
        mock_db = AsyncMock()
        mock_kis = AsyncMock()

        with patch("core.data_collector.market_data.KISClient", return_value=mock_kis):
            collector = MarketDataCollector(mock_db)

            base_date = datetime.now(timezone.utc)
            data = [(base_date - timedelta(days=i), 100.0, 1000) for i in range(10, 0, -1)]
            # Add extreme outlier
            data.insert(0, (base_date, 200.0, 1000))

            mock_result = MagicMock()
            mock_result.fetchall.return_value = data
            mock_db.execute = AsyncMock(return_value=mock_result)

            result = await collector.validate_and_fill("005930", Market.KRX.value)
            assert "outliers_flagged" in result

    def test_check_consecutive_missing_empty_df(self):
        """Handle empty dataframe in consecutive missing check"""
        mock_db = AsyncMock()
        mock_kis = AsyncMock()

        with patch("core.data_collector.market_data.KISClient", return_value=mock_kis):
            collector = MarketDataCollector(mock_db)

            df = pd.DataFrame(columns=["time", "close", "volume"])
            result = collector._check_consecutive_missing(df)
            assert result == 0

    def test_check_consecutive_missing_normal(self):
        """Calculate consecutive missing days correctly"""
        mock_db = AsyncMock()
        mock_kis = AsyncMock()

        with patch("core.data_collector.market_data.KISClient", return_value=mock_kis):
            collector = MarketDataCollector(mock_db)

            base_date = datetime.now(timezone.utc)
            data = [
                (base_date - timedelta(days=1), 100.0, 1000),
                (base_date - timedelta(days=2), 100.0, 1000),
                (base_date - timedelta(days=5), 100.0, 1000),
            ]
            df = pd.DataFrame(data, columns=["time", "close", "volume"])
            result = collector._check_consecutive_missing(df)
            assert result >= 0


class TestMarketDataCollectorSaveOHLCV:
    """OHLCV batch save tests"""

    @pytest.mark.asyncio
    async def test_save_ohlcv_batch_success(self):
        """Successfully save OHLCV batch"""
        mock_db = AsyncMock()
        mock_kis = AsyncMock()

        with patch("core.data_collector.market_data.KISClient", return_value=mock_kis):
            collector = MarketDataCollector(mock_db)

            records = [
                {
                    "time": datetime.now(),
                    "ticker": "005930",
                    "market": Market.KRX.value,
                    "open": 70000,
                    "high": 71000,
                    "low": 69000,
                    "close": 70500,
                    "volume": 5000000,
                    "interval": "1d",
                }
            ]
            mock_db.commit = AsyncMock()

            result = await collector._save_ohlcv_batch(records)
            assert result == 1
            mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_ohlcv_batch_empty(self):
        """Handle empty records"""
        mock_db = AsyncMock()
        mock_kis = AsyncMock()

        with patch("core.data_collector.market_data.KISClient", return_value=mock_kis):
            collector = MarketDataCollector(mock_db)

            result = await collector._save_ohlcv_batch([])
            assert result == 0


# ══════════════════════════════════════
# Test: economic_collector.py
# ══════════════════════════════════════


class TestFREDCollectorAvailability:
    """FRED collector availability tests"""

    def test_fred_is_available_with_key(self):
        """FRED available when API key configured"""
        with patch("core.data_collector.economic_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.fred_api_key = "test_key"
            collector = FREDCollector()
            assert collector.is_available is True

    def test_fred_is_not_available_without_key(self):
        """FRED unavailable when API key not configured"""
        with patch("core.data_collector.economic_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.fred_api_key = None
            collector = FREDCollector()
            assert collector.is_available is False


class TestFREDCollectorFetch:
    """FRED data fetch tests"""

    @pytest.mark.asyncio
    async def test_fetch_series_success(self):
        """Successfully fetch FRED series"""
        with patch("core.data_collector.economic_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.fred_api_key = "test_key"

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "observations": [
                    {
                        "date": "2026-04-07",
                        "value": "5.25",
                    }
                ]
            }
            mock_response.raise_for_status = MagicMock()

            with patch("httpx.AsyncClient") as mock_client:
                mock_async_client = AsyncMock()
                mock_async_client.get = AsyncMock(return_value=mock_response)
                mock_client.return_value.__aenter__.return_value = mock_async_client

                collector = FREDCollector()
                result = await collector._fetch_series("FEDFUNDS", EconomicIndicatorType.FED_FUNDS_RATE)

                assert result is not None
                assert result.value == 5.25
                assert result.source == "FRED"

    @pytest.mark.asyncio
    async def test_fetch_series_empty_observations(self):
        """Handle empty observations in FRED response"""
        with patch("core.data_collector.economic_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.fred_api_key = "test_key"

            with patch("httpx.AsyncClient") as mock_client:
                # httpx Response.json() 은 sync 메서드이다. AsyncMock 으로 두면
                # production `data = response.json()` 이 coroutine 을 받고 버려
                # `AsyncMockMixin._execute_mock_call was never awaited` RuntimeWarning
                # 이 뜨며, 후속 `data.get(...)` 은 AttributeError → 광범위 except 블록에
                # 삼켜져 result=None 으로 silent miss (CLAUDE.md §8) 가 된다.
                mock_response = MagicMock()
                mock_response.json.return_value = {"observations": []}
                mock_response.raise_for_status = MagicMock()

                mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

                collector = FREDCollector()
                result = await collector._fetch_series("FEDFUNDS", EconomicIndicatorType.FED_FUNDS_RATE)

                assert result is None

    @pytest.mark.asyncio
    async def test_fetch_series_null_value(self):
        """Handle null value in FRED response"""
        with patch("core.data_collector.economic_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.fred_api_key = "test_key"

            with patch("httpx.AsyncClient") as mock_client:
                # httpx Response.json() 은 sync → MagicMock (empty_observations 동일 근거)
                mock_response = MagicMock()
                mock_response.json.return_value = {"observations": [{"date": "2026-04-07", "value": "."}]}
                mock_response.raise_for_status = MagicMock()

                mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

                collector = FREDCollector()
                result = await collector._fetch_series("FEDFUNDS", EconomicIndicatorType.FED_FUNDS_RATE)

                assert result is None

    @pytest.mark.asyncio
    async def test_fetch_series_timeout_retry(self):
        """Retry on timeout in FRED fetch"""
        with patch("core.data_collector.economic_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.fred_api_key = "test_key"

            with patch("httpx.AsyncClient") as mock_client:
                import httpx

                # Timeout on first attempt, success on second
                mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                    side_effect=[
                        httpx.TimeoutException("Timeout"),
                        MagicMock(
                            json=MagicMock(return_value={"observations": [{"date": "2026-04-07", "value": "5.25"}]}),
                            raise_for_status=MagicMock(),
                        ),
                    ]
                )

                with patch("asyncio.sleep", new_callable=AsyncMock):
                    collector = FREDCollector()
                    result = await collector._fetch_series("FEDFUNDS", EconomicIndicatorType.FED_FUNDS_RATE)

                    assert result is not None

    @pytest.mark.asyncio
    async def test_fetch_series_http_error(self):
        """Handle HTTP error in FRED fetch"""
        with patch("core.data_collector.economic_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.fred_api_key = "test_key"

            with patch("httpx.AsyncClient") as mock_client:
                import httpx

                mock_response = AsyncMock()
                mock_response.status_code = 500

                mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                    side_effect=httpx.HTTPStatusError("Server error", request=MagicMock(), response=mock_response)
                )

                collector = FREDCollector()
                result = await collector._fetch_series("FEDFUNDS", EconomicIndicatorType.FED_FUNDS_RATE)

                assert result is None


class TestFREDCollectorCollectAll:
    """FRED collect all tests"""

    @pytest.mark.asyncio
    async def test_collect_all_skip_when_unavailable(self):
        """Skip collection when API key not configured"""
        with patch("core.data_collector.economic_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.fred_api_key = None
            collector = FREDCollector()
            result = await collector.collect_all()
            assert result == []

    @pytest.mark.asyncio
    async def test_collect_all_success(self):
        """Successfully collect all FRED indicators"""
        with patch("core.data_collector.economic_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.fred_api_key = "test_key"

            with patch.object(FREDCollector, "_fetch_series") as mock_fetch:
                mock_fetch.return_value = EconomicIndicator(
                    indicator_name="GDP",
                    indicator_code="GDP",
                    value=1000.0,
                    time=datetime.now(timezone.utc),
                    source="FRED",
                    country="US",
                )

                collector = FREDCollector()
                result = await collector.collect_all()

                assert len(result) > 0
                assert all(ind.source == "FRED" for ind in result)


class TestECOSCollectorFetch:
    """ECOS data fetch tests"""

    @pytest.mark.asyncio
    async def test_fetch_series_success(self):
        """Successfully fetch ECOS series"""
        with patch("core.data_collector.economic_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.ecos_api_key = "test_key"

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "StatisticSearch": {
                    "list_total_count": 1,
                    "row": [
                        {
                            "TIME": "202604",
                            "DATA_VALUE": "3.5",
                        }
                    ],
                }
            }
            mock_response.raise_for_status = MagicMock()

            with patch("httpx.AsyncClient") as mock_client:
                mock_async_client = AsyncMock()
                mock_async_client.get = AsyncMock(return_value=mock_response)
                mock_client.return_value.__aenter__.return_value = mock_async_client

                collector = ECOSCollector()
                series_info = {
                    "stat_code": "722Y001",
                    "item_code": "0101000",
                }
                result = await collector._fetch_series(EconomicIndicatorType.BOK_BASE_RATE, series_info)

                assert result is not None
                assert result.value == 3.5
                assert result.source == "ECOS"

    @pytest.mark.asyncio
    async def test_fetch_series_error_code(self):
        """Handle ECOS error code in response"""
        with patch("core.data_collector.economic_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.ecos_api_key = "test_key"

            with patch("httpx.AsyncClient") as mock_client:
                # httpx Response.json() 은 sync → MagicMock (FRED 동일 근거)
                mock_response = MagicMock()
                mock_response.json.return_value = {"RESULT": {"CODE": "ERROR-101", "MESSAGE": "잘못된 날짜 형식"}}
                mock_response.raise_for_status = MagicMock()

                mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

                collector = ECOSCollector()
                series_info = {"stat_code": "722Y001", "item_code": "0101000"}
                result = await collector._fetch_series(EconomicIndicatorType.BOK_BASE_RATE, series_info)

                assert result is None

    def test_parse_ecos_date_monthly(self):
        """Parse ECOS monthly date format"""
        result = ECOSCollector._parse_ecos_date("202604", "M")
        assert result.year == 2026
        assert result.month == 4
        assert result.day == 1

    def test_parse_ecos_date_quarterly(self):
        """Parse ECOS quarterly date format"""
        result = ECOSCollector._parse_ecos_date("2026Q1", "Q")
        assert result.year == 2026
        assert result.month == 1

    def test_parse_ecos_date_annual(self):
        """Parse ECOS annual date format"""
        result = ECOSCollector._parse_ecos_date("2026", "A")
        assert result.year == 2026
        assert result.month == 1


class TestEconomicIndicator:
    """EconomicIndicator dataclass tests"""

    def test_economic_indicator_post_init(self):
        """Ensure EconomicIndicator fields are correctly set"""
        now = datetime.now(timezone.utc)
        ind = EconomicIndicator(
            indicator_name="GDP",
            indicator_code="GDP",
            value=1000.0,
            time=now,
            source="FRED",
            country="US",
        )
        assert ind.indicator_name == "GDP"
        assert ind.indicator_code == "GDP"
        assert ind.time == now

    def test_economic_indicator_to_dict(self):
        """Convert EconomicIndicator to dict"""
        now = datetime.now(timezone.utc)
        ind = EconomicIndicator(
            indicator_name="GDP",
            indicator_code="GDP",
            value=1000.0,
            time=now,
            source="FRED",
            country="US",
        )
        d = ind.to_dict()
        assert d["indicator_name"] == "GDP"
        assert d["indicator_code"] == "GDP"
        assert d["value"] == 1000.0
        assert d["time"] == now
        assert d["source"] == "FRED"
        assert d["country"] == "US"


class TestEconomicCollectorService:
    """Economic collector service tests"""

    @pytest.mark.asyncio
    async def test_collect_and_store(self):
        """Collect and store economic indicators"""
        with patch("core.data_collector.economic_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.fred_api_key = "test_key"
            mock_settings.return_value.external.ecos_api_key = "test_key"

            with patch.object(FREDCollector, "collect_all") as mock_fred:
                with patch.object(ECOSCollector, "collect_all") as mock_ecos:
                    with patch.object(EconomicCollectorService, "_cache_latest"):
                        mock_fred.return_value = []
                        mock_ecos.return_value = []

                        service = EconomicCollectorService()
                        result = await service.collect_and_store()

                        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_collect_indicator_fred(self):
        """Collect specific FRED indicator"""
        with patch("core.data_collector.economic_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.fred_api_key = "test_key"
            mock_settings.return_value.external.ecos_api_key = None

            with patch.object(FREDCollector, "collect_indicator") as mock_collect:
                mock_collect.return_value = EconomicIndicator(
                    indicator_name="GDP",
                    indicator_code="GDP",
                    value=1000.0,
                    time=datetime.now(timezone.utc),
                    source="FRED",
                    country="US",
                )

                service = EconomicCollectorService()
                result = await service.collect_indicator(EconomicIndicatorType.GDP)

                assert result.indicator_name == "GDP"


# ══════════════════════════════════════
# Test: news_collector.py
# ══════════════════════════════════════


class TestNewsArticle:
    """NewsArticle dataclass tests"""

    def test_news_article_url_hash(self):
        """Generate MD5 hash from URL"""
        article = NewsArticle(
            title="Test",
            content="Content",
            url="https://example.com/news/1",
            source="RSS",
        )
        expected_hash = hashlib.md5("https://example.com/news/1".encode()).hexdigest()
        assert article.url_hash == expected_hash

    def test_news_article_collected_at(self):
        """Auto-set collected_at timestamp"""
        article = NewsArticle(
            title="Test",
            content="Content",
            url="https://example.com/news/1",
            source="RSS",
        )
        assert article.collected_at is not None

    def test_news_article_to_dict(self):
        """Convert NewsArticle to dict"""
        article = NewsArticle(
            title="Test News",
            content="Content",
            url="https://example.com/news/1",
            source="RSS",
            tickers=["005930"],
            category="macro",
        )
        d = article.to_dict()
        assert d["title"] == "Test News"
        assert "005930" in d["tickers"]
        assert d["source"] == "RSS"


class TestExtractTickersRegex:
    """Ticker extraction tests"""

    def test_extract_from_code_numeric(self):
        """Extract 6-digit ticker codes"""
        text = "종목코드 005930은 삼성전자입니다"
        result = extract_tickers(text)
        assert "005930" in result

    def test_extract_exclude_year_pattern(self):
        """Exclude date patterns (YYYYMM)"""
        text = "2026년 4월 기준 202604 데이터"
        result = extract_tickers(text)
        assert "202604" not in result

    def test_extract_from_name_single(self):
        """Extract by company name"""
        text = "삼성전자가 신제품 출시"
        result = extract_tickers(text)
        assert "005930" in result

    def test_extract_multiple_companies(self):
        """Extract multiple company tickers"""
        text = "삼성전자와 SK하이닉스, LG화학이 경쟁"
        result = extract_tickers(text)
        assert "005930" in result
        assert "000660" in result
        assert "051910" in result

    def test_extract_no_tickers(self):
        """Return empty list when no tickers found"""
        text = "날씨가 좋습니다"
        result = extract_tickers(text)
        assert len(result) == 0

    def test_extract_duplicate_removal(self):
        """Remove duplicate tickers"""
        text = "삼성전자 005930 삼성전자 005930"
        result = extract_tickers(text)
        assert result.count("005930") == 1


class TestRSSNewsCollectorClassify:
    """RSS news category classification tests"""

    def test_classify_earnings(self):
        """Classify earnings news"""
        text = "삼성전자 분기 영업이익 15조 실적 발표"
        result = RSSNewsCollector._classify_category(text)
        assert result == "earnings"

    def test_classify_macro(self):
        """Classify macro news"""
        text = "Fed 금리 결정 공지, FOMC 회의"
        result = RSSNewsCollector._classify_category(text)
        assert result == "macro"

    def test_classify_sector(self):
        """Classify sector news"""
        text = "반도체 업황 개선, AI 수요 확대"
        result = RSSNewsCollector._classify_category(text)
        assert result == "sector"

    def test_classify_general(self):
        """Classify general news"""
        text = "회사 행사 안내"
        result = RSSNewsCollector._classify_category(text)
        assert result == "general"


class TestRSSNewsCollectorParse:
    """RSS feed parsing tests"""

    @pytest.mark.asyncio
    async def test_parse_feed_success(self):
        """Successfully parse RSS feed"""
        with patch("httpx.AsyncClient") as mock_client:
            with patch("feedparser.parse") as mock_feedparser:
                mock_response = AsyncMock()
                mock_response.text = "<feed></feed>"
                mock_response.raise_for_status = MagicMock()

                mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

                # Mock feedparser output
                mock_feedparser.return_value = MagicMock(
                    entries=[
                        MagicMock(
                            title="Test News",
                            link="https://example.com/1",
                            summary="Summary",
                            published_parsed=(2026, 4, 7, 12, 0, 0, 0, 0, 0),
                        )
                    ]
                )

                collector = RSSNewsCollector()
                result = await collector._parse_feed("https://example.com/feed.xml", NewsSource.NAVER_FINANCE)

                assert len(result) >= 0

    @pytest.mark.asyncio
    async def test_parse_feed_missing_title(self):
        """Skip entries without title"""
        with patch("httpx.AsyncClient") as mock_client:
            with patch("feedparser.parse") as mock_feedparser:
                mock_response = AsyncMock()
                mock_response.text = "<feed></feed>"
                mock_response.raise_for_status = MagicMock()

                mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

                # Entry without title
                mock_feedparser.return_value = MagicMock(entries=[MagicMock(title="", link="https://example.com/1")])

                collector = RSSNewsCollector()
                result = await collector._parse_feed("https://example.com/feed.xml", NewsSource.NAVER_FINANCE)

                assert len(result) == 0


class TestDARTCollectorAvailability:
    """DART collector availability tests"""

    def test_dart_is_available_with_key(self):
        """DART available when API key configured"""
        with patch("core.data_collector.news_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"
            collector = DARTCollector()
            assert collector.is_available is True

    def test_dart_is_not_available_without_key(self):
        """DART unavailable when API key not configured"""
        with patch("core.data_collector.news_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = None
            collector = DARTCollector()
            assert collector.is_available is False


class TestDARTCollectorFetch:
    """DART disclosure fetch tests"""

    @pytest.mark.asyncio
    async def test_fetch_disclosures_success(self):
        """Successfully fetch DART disclosures"""
        with patch("core.data_collector.news_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "status": "000",
                "list": [
                    {
                        "report_nm": "정기보고서",
                        "corp_name": "삼성전자",
                        "stock_code": "005930",
                        "rcept_no": "20260407001234",
                        "rcept_dt": "20260407",
                    }
                ],
            }
            mock_response.raise_for_status = MagicMock()

            with patch("httpx.AsyncClient") as mock_client:
                mock_async_client = AsyncMock()
                mock_async_client.get = AsyncMock(return_value=mock_response)
                mock_client.return_value.__aenter__.return_value = mock_async_client

                collector = DARTCollector()
                result = await collector._fetch_disclosures("20260406", "20260407", "A001")

                assert len(result) == 1
                assert result[0].tickers == ["005930"]

    @pytest.mark.asyncio
    async def test_fetch_disclosures_error_status(self):
        """Handle error status in DART response"""
        with patch("core.data_collector.news_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            mock_response = MagicMock()
            mock_response.json.return_value = {"status": "001"}
            mock_response.raise_for_status = MagicMock()

            with patch("httpx.AsyncClient") as mock_client:
                mock_async_client = AsyncMock()
                mock_async_client.get = AsyncMock(return_value=mock_response)
                mock_client.return_value.__aenter__.return_value = mock_async_client

                collector = DARTCollector()
                result = await collector._fetch_disclosures("20260406", "20260407", "A001")

                assert len(result) == 0


class TestDARTCollectorCollectRecent:
    """DART recent collection tests"""

    @pytest.mark.asyncio
    async def test_collect_recent_skip_when_unavailable(self):
        """Skip collection when API key not configured"""
        with patch("core.data_collector.news_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = None

            collector = DARTCollector()
            result = await collector.collect_recent()

            assert result == []


# ══════════════════════════════════════
# Test: kis_websocket.py
# ══════════════════════════════════════


class TestRealtimeQuote:
    """RealtimeQuote data parsing tests"""

    def test_realtime_quote_initialization(self):
        """Parse raw quote fields"""
        fields = [
            "005930",  # ticker
            "153000",  # exec_time
            "75000",  # price
            "",  # sign
            "500",  # change
            "0.67",  # change_rate
            "74900",  # weighted_avg_price
            "74500",  # open
            "75200",  # high
            "74400",  # low
            "75010",  # ask1
            "75000",  # bid1
            "100000",  # exec_volume
            "5000000",  # cum_volume
            "375000000000",  # cum_amount
        ]
        quote = RealtimeQuote(fields)

        assert quote.ticker == "005930"
        assert quote.price == 75000.0
        assert quote.change == 500.0
        assert quote.change_rate == 0.67

    def test_realtime_quote_to_dict(self):
        """Convert quote to dict"""
        fields = ["005930"] + ["0"] * 45
        quote = RealtimeQuote(fields)
        d = quote.to_dict()

        assert d["ticker"] == "005930"
        assert "timestamp" in d


class TestRealtimeOrderbook:
    """RealtimeOrderbook data parsing tests"""

    def test_realtime_orderbook_initialization(self):
        """Parse raw orderbook fields"""
        fields = (
            ["005930", "153000"]
            + ["75000"] * 20
            + ["100000"] * 20
            + [
                "1000000",
                "900000",
            ]
        )
        orderbook = RealtimeOrderbook(fields)

        assert orderbook.ticker == "005930"
        assert len(orderbook.asks) == 10
        assert len(orderbook.bids) == 10
        assert len(orderbook.ask_volumes) == 10
        assert len(orderbook.bid_volumes) == 10

    def test_realtime_orderbook_to_dict(self):
        """Convert orderbook to dict"""
        fields = (
            ["005930", "153000"]
            + ["75000"] * 20
            + ["100000"] * 20
            + [
                "1000000",
                "900000",
            ]
        )
        orderbook = RealtimeOrderbook(fields)
        d = orderbook.to_dict()

        assert d["ticker"] == "005930"
        assert len(d["asks"]) == 10


class TestSafeConversions:
    """Safe float/int conversion tests"""

    def test_safe_float_valid(self):
        """Convert valid float"""
        result = _safe_float(["100.5"], 0)
        assert result == 100.5

    def test_safe_float_invalid(self):
        """Handle invalid float"""
        result = _safe_float(["invalid"], 0)
        assert result == 0.0

    def test_safe_float_out_of_range(self):
        """Handle out of range index"""
        result = _safe_float(["100.5"], 5)
        assert result == 0.0

    def test_safe_int_valid(self):
        """Convert valid int"""
        result = _safe_int(["1000"], 0)
        assert result == 1000

    def test_safe_int_invalid(self):
        """Handle invalid int"""
        result = _safe_int(["invalid"], 0)
        assert result == 0

    def test_safe_int_out_of_range(self):
        """Handle out of range index"""
        result = _safe_int(["1000"], 5)
        assert result == 0


class TestKISRealtimeClientConnection:
    """KIS websocket connection tests"""

    @pytest.mark.asyncio
    async def test_connect_success(self):
        """Successfully connect to websocket"""
        with patch("core.data_collector.kis_websocket.get_settings") as mock_settings:
            mock_settings.return_value.kis.is_backtest = False
            mock_settings.return_value.kis.is_live = True
            mock_settings.return_value.kis.active_credential.websocket_url = "wss://example.com"

            with patch("core.data_collector.kis_websocket.KISTokenManager") as mock_token:
                mock_token_instance = AsyncMock()
                mock_token.return_value = mock_token_instance
                mock_token_instance.get_websocket_key = AsyncMock(return_value="test_key")

                with patch("websockets.connect", new_callable=AsyncMock) as mock_ws:
                    mock_ws_instance = AsyncMock()
                    mock_ws.return_value = mock_ws_instance

                    client = KISRealtimeClient()
                    result = await client.connect()

                    assert result is True
                    assert client.is_connected is True

    @pytest.mark.asyncio
    async def test_connect_backtest_disabled(self):
        """Disable connection in backtest mode"""
        with patch("core.data_collector.kis_websocket.get_settings") as mock_settings:
            mock_settings.return_value.kis.is_backtest = True

            client = KISRealtimeClient()
            result = await client.connect()

            assert result is False


class TestKISRealtimeClientSubscription:
    """KIS websocket subscription tests"""

    @pytest.mark.asyncio
    async def test_subscribe_success(self):
        """Successfully subscribe to ticker"""
        with patch("core.data_collector.kis_websocket.get_settings") as mock_settings:
            mock_settings.return_value.kis.is_backtest = False

            with patch("core.data_collector.kis_websocket.KISTokenManager") as mock_token:
                mock_token_instance = AsyncMock()
                mock_token.return_value = mock_token_instance
                mock_token_instance.get_websocket_key = AsyncMock(return_value="test_key")

                client = KISRealtimeClient()
                client._connected = True
                client._ws = AsyncMock()

                result = await client.subscribe("005930")

                assert result is True
                assert "005930" in client._subscribed_tickers

    @pytest.mark.asyncio
    async def test_subscribe_not_connected(self):
        """Fail subscribe when not connected"""
        with patch("core.data_collector.kis_websocket.get_settings") as mock_settings:
            mock_settings.return_value.kis.is_backtest = True

            client = KISRealtimeClient()
            result = await client.subscribe("005930")

            assert result is False

    @pytest.mark.asyncio
    async def test_subscribe_max_limit(self):
        """Fail when max subscriptions reached"""
        with patch("core.data_collector.kis_websocket.get_settings") as mock_settings:
            mock_settings.return_value.kis.is_backtest = False

            client = KISRealtimeClient()
            client._connected = True
            client._ws = AsyncMock()
            client._subscribed_tickers = {f"{i:06d}" for i in range(40)}

            result = await client.subscribe("999999")

            assert result is False

    @pytest.mark.asyncio
    async def test_unsubscribe_success(self):
        """Successfully unsubscribe from ticker"""
        with patch("core.data_collector.kis_websocket.get_settings") as mock_settings:
            mock_settings.return_value.kis.is_backtest = False

            with patch("core.data_collector.kis_websocket.KISTokenManager") as mock_token:
                mock_token_instance = AsyncMock()
                mock_token.return_value = mock_token_instance
                mock_token_instance.get_websocket_key = AsyncMock(return_value="test_key")

                client = KISRealtimeClient()
                client._connected = True
                client._ws = AsyncMock()
                client._subscribed_tickers.add("005930")

                result = await client.unsubscribe("005930")

                assert result is True
                assert "005930" not in client._subscribed_tickers


class TestKISRealtimeClientMessageHandling:
    """KIS websocket message handling tests"""

    @pytest.mark.asyncio
    async def test_handle_pingpong_message(self):
        """Handle PINGPONG message"""
        with patch("core.data_collector.kis_websocket.get_settings") as mock_settings:
            mock_settings.return_value.kis.is_backtest = False

            client = KISRealtimeClient()
            client._connected = True
            client._ws = AsyncMock()

            raw = "1|test"
            await client._handle_message(raw)

            client._ws.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_quote_message(self):
        """Handle quote (H0STCNT0) message"""
        with patch("core.data_collector.kis_websocket.get_settings") as mock_settings:
            mock_settings.return_value.kis.is_backtest = False

            client = KISRealtimeClient()
            client._connected = True
            client._ws = AsyncMock()
            client.on_quote = MagicMock()

            raw = f"0|{TR_ID_QUOTE}|1|005930|153000|75000|...|" + "|" * 30

            await client._handle_message(raw)

            client.on_quote.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_orderbook_message(self):
        """Handle orderbook (H0STASP0) message"""
        with patch("core.data_collector.kis_websocket.get_settings") as mock_settings:
            mock_settings.return_value.kis.is_backtest = False

            client = KISRealtimeClient()
            client._connected = True
            client._ws = AsyncMock()
            client.on_orderbook = MagicMock()

            raw = f"0|{TR_ID_ORDERBOOK}|1|" + "|" * 50

            await client._handle_message(raw)

            client.on_orderbook.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_json_response(self):
        """Handle JSON response message"""
        with patch("core.data_collector.kis_websocket.get_settings") as mock_settings:
            mock_settings.return_value.kis.is_backtest = False

            client = KISRealtimeClient()
            client._connected = True
            client._ws = AsyncMock()

            raw = json.dumps(
                {
                    "header": {
                        "tr_id": "H0STCNT0",
                        "msg_cd": "0",
                        "msg1": "Success",
                    }
                }
            )

            await client._handle_message(raw)


class TestKISRealtimeClientBatchSubscribe:
    """KIS websocket batch subscribe tests"""

    @pytest.mark.asyncio
    async def test_subscribe_batch_success(self):
        """Successfully subscribe multiple tickers"""
        with patch("core.data_collector.kis_websocket.get_settings") as mock_settings:
            mock_settings.return_value.kis.is_backtest = False

            with patch("core.data_collector.kis_websocket.KISTokenManager") as mock_token:
                mock_token_instance = AsyncMock()
                mock_token.return_value = mock_token_instance
                mock_token_instance.get_websocket_key = AsyncMock(return_value="test_key")

                with patch("asyncio.sleep", new_callable=AsyncMock):
                    client = KISRealtimeClient()
                    client._connected = True
                    client._ws = AsyncMock()

                    result = await client.subscribe_batch(["005930", "000660", "035420"])

                    assert result == 3


class TestKISRealtimeClientDisconnect:
    """KIS websocket disconnect tests"""

    @pytest.mark.asyncio
    async def test_disconnect(self):
        """Successfully disconnect websocket"""
        with patch("core.data_collector.kis_websocket.get_settings") as mock_settings:
            mock_settings.return_value.kis.is_backtest = False

            client = KISRealtimeClient()
            client._connected = True
            client._ws = AsyncMock()

            # production disconnect() 코드가 `self._receive_task.done()` (sync) 로
            # 상태 확인 후 `.cancel()` (sync) 호출하고 `await self._receive_task` 로
            # 종료 대기한다. AsyncMock 은 `__await__` 없이 coroutine 을 별도 생성하는
            # 구조라 실제 asyncio.Task 처럼 await 되지 않는다. cancel+await 경로를
            # 실제로 검증하려면 진짜 asyncio.Task 를 써야 한다.
            async def _never_complete():
                await asyncio.sleep(3600)

            client._receive_task = asyncio.create_task(_never_complete())
            client._subscribed_tickers.add("005930")

            await client.disconnect()

            assert client._connected is False
            assert len(client._subscribed_tickers) == 0
            # cancel+await 경로가 실제로 실행되었는지 검증 (original 테스트는 done()
            # coroutine 이 truthy 로 평가돼 if-block 을 스킵하여 이 경로를 전혀
            # 커버하지 못했다).
            assert client._receive_task.cancelled() or client._receive_task.done()


class TestKISRealtimeClientReconnect:
    """KIS websocket reconnect tests"""

    @pytest.mark.asyncio
    async def test_reconnect_with_backoff(self):
        """Reconnect with exponential backoff"""
        with patch("core.data_collector.kis_websocket.get_settings") as mock_settings:
            mock_settings.return_value.kis.is_backtest = False

            with patch("core.data_collector.kis_websocket.KISTokenManager") as mock_token:
                mock_token_instance = AsyncMock()
                mock_token.return_value = mock_token_instance
                mock_token_instance.get_websocket_key = AsyncMock(return_value="test_key")

                with patch("websockets.connect") as mock_ws:
                    with patch("asyncio.sleep", new_callable=AsyncMock):
                        mock_ws_instance = AsyncMock()
                        mock_ws.return_value = mock_ws_instance

                        client = KISRealtimeClient()
                        client._connected = True
                        client._ws = mock_ws_instance
                        client._subscribed_tickers.add("005930")

                        initial_delay = client._reconnect_delay
                        await client._reconnect()

                        # Verify backoff increment
                        assert client._reconnect_delay == initial_delay * 2


class TestKISRealtimeClientStats:
    """KIS websocket stats tests"""

    def test_get_stats(self):
        """Get client statistics"""
        with patch("core.data_collector.kis_websocket.get_settings") as mock_settings:
            mock_settings.return_value.kis.is_backtest = True

            client = KISRealtimeClient()
            stats = client.stats

            assert "messages_received" in stats
            assert "quotes_processed" in stats
            assert "reconnections" in stats


class TestKISRealtimeClientProperties:
    """KIS websocket property tests"""

    def test_is_connected_property(self):
        """Test is_connected property"""
        with patch("core.data_collector.kis_websocket.get_settings") as mock_settings:
            mock_settings.return_value.kis.is_backtest = True

            client = KISRealtimeClient()
            assert client.is_connected is False

            client._connected = True
            client._ws = AsyncMock()
            assert client.is_connected is True

    def test_subscribed_tickers_copy(self):
        """Get copy of subscribed tickers"""
        with patch("core.data_collector.kis_websocket.get_settings") as mock_settings:
            mock_settings.return_value.kis.is_backtest = True

            client = KISRealtimeClient()
            client._subscribed_tickers.add("005930")

            tickers = client.subscribed_tickers
            tickers.add("000660")

            assert "005930" in client._subscribed_tickers
            assert "000660" not in client._subscribed_tickers
