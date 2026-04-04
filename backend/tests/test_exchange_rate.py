"""
Comprehensive unit tests for ExchangeRate and ExchangeRateManager.

Tests cover:
- ExchangeRate dataclass creation and serialization
- ExchangeRateManager caching strategy with TTL based on market hours
- Multiple data source fallbacks (Cache → KIS → FRED)
- Currency conversion and portfolio value calculations
- Market hours detection (KST 09:00-15:30 weekdays only)
- Error handling and edge cases
"""

from datetime import datetime, time
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from core.portfolio_manager.exchange_rate import (
    ExchangeRate,
    ExchangeRateManager,
)

# ============================================================================
# TestExchangeRate - Dataclass Tests
# ============================================================================


class TestExchangeRate:
    """Tests for ExchangeRate dataclass."""

    def test_create_exchange_rate(self):
        """Test creating an ExchangeRate instance with all fields."""
        fetched_at = datetime.now(ZoneInfo("UTC"))
        rate = ExchangeRate(
            pair="USD/KRW",
            rate=1350.50,
            source="KIS",
            fetched_at=fetched_at,
        )

        assert rate.pair == "USD/KRW"
        assert rate.rate == 1350.50
        assert rate.source == "KIS"
        assert rate.fetched_at == fetched_at

    def test_to_dict(self):
        """Test converting ExchangeRate to dictionary."""
        fetched_at = datetime(2026, 4, 3, 12, 0, 0, tzinfo=ZoneInfo("UTC"))
        rate = ExchangeRate(
            pair="USD/KRW",
            rate=1350.50,
            source="KIS",
            fetched_at=fetched_at,
        )

        result = rate.to_dict()

        assert result["pair"] == "USD/KRW"
        assert result["rate"] == 1350.50
        assert result["source"] == "KIS"
        assert result["fetched_at"] == fetched_at.isoformat()


# ============================================================================
# TestExchangeRateManager - Fixtures
# ============================================================================


@pytest.fixture
def mock_settings():
    """Mock settings object."""
    settings = MagicMock()
    settings.external.fred_api_key = "test-fred-key"
    settings.kis.api_timeout = 30
    return settings


@pytest.fixture
def mock_kis_client():
    """Mock KISClient."""
    return MagicMock()


@pytest.fixture
def mock_redis_manager():
    """Mock RedisManager."""
    redis_client = AsyncMock()
    return MagicMock(get_client=MagicMock(return_value=redis_client))


@pytest.fixture
def exchange_rate_manager(mock_settings, mock_kis_client, mock_redis_manager):
    """
    Fixture for ExchangeRateManager with mocked dependencies.

    Patches:
    - get_settings() to return mock_settings
    - KISClient() to return mock_kis_client
    - RedisManager to return mock_redis_manager
    """
    with (
        patch(
            "core.portfolio_manager.exchange_rate.get_settings",
            return_value=mock_settings,
        ),
        patch(
            "core.portfolio_manager.exchange_rate.KISClient",
            return_value=mock_kis_client,
        ),
        patch(
            "core.portfolio_manager.exchange_rate.RedisManager",
            return_value=mock_redis_manager,
        ),
    ):
        manager = ExchangeRateManager()
        yield manager


# ============================================================================
# TestExchangeRateManager - Cache Tests
# ============================================================================


class TestExchangeRateManagerCache:
    """Tests for caching behavior."""

    @pytest.mark.asyncio
    async def test_get_current_rate_from_cache(self, exchange_rate_manager):
        """Test getting rate from cache returns source='CACHE'."""
        cached_rate = ExchangeRate(
            pair="USD/KRW",
            rate=1350.0,
            source="CACHE",
            fetched_at=datetime.now(ZoneInfo("UTC")),
        )

        exchange_rate_manager._get_cached_rate = AsyncMock(return_value=cached_rate)

        result = await exchange_rate_manager.get_current_rate("USD/KRW")

        assert result.source == "CACHE"
        assert result.rate == 1350.0
        assert result.pair == "USD/KRW"
        exchange_rate_manager._get_cached_rate.assert_called_once_with("USD/KRW")

    @pytest.mark.asyncio
    async def test_get_current_rate_from_kis(self, exchange_rate_manager):
        """Test getting rate from KIS when cache misses."""
        exchange_rate_manager._get_cached_rate = AsyncMock(return_value=None)
        exchange_rate_manager.fetch_from_kis = AsyncMock(return_value=1350.0)
        exchange_rate_manager._cache_rate = AsyncMock()

        result = await exchange_rate_manager.get_current_rate("USD/KRW")

        assert result.source == "KIS"
        assert result.rate == 1350.0
        exchange_rate_manager.fetch_from_kis.assert_called_once()
        exchange_rate_manager._cache_rate.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_current_rate_fred_fallback(self, exchange_rate_manager):
        """Test falling back to FRED when KIS fails."""
        exchange_rate_manager._get_cached_rate = AsyncMock(return_value=None)
        exchange_rate_manager.fetch_from_kis = AsyncMock(side_effect=Exception("KIS API error"))
        exchange_rate_manager.fetch_from_fred = AsyncMock(return_value=1345.0)
        exchange_rate_manager._cache_rate = AsyncMock()

        result = await exchange_rate_manager.get_current_rate("USD/KRW")

        assert result.source == "FRED"
        assert result.rate == 1345.0
        exchange_rate_manager.fetch_from_fred.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_cached_rate(self, exchange_rate_manager, mock_redis_manager):
        """Test retrieving rate from Redis cache."""
        import json as _json

        cached_data = {
            "pair": "USD/KRW",
            "rate": 1350.0,
            "source": "KIS",
            "fetched_at": "2026-04-03T12:00:00+00:00",
        }
        # _get_cached_rate calls RedisManager.get_client() at module level,
        # so we patch it directly on the manager's method scope
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_json.dumps(cached_data))

        with patch("core.portfolio_manager.exchange_rate.RedisManager") as mock_rm:
            mock_rm.get_client.return_value = mock_client
            result = await exchange_rate_manager._get_cached_rate("USD/KRW")

        assert result is not None
        assert result.rate == 1350.0
        assert result.source == "CACHE"
        mock_client.get.assert_called_once_with("exchange_rate:USD_KRW")

    @pytest.mark.asyncio
    async def test_get_cached_rate_miss(self, exchange_rate_manager, mock_redis_manager):
        """Test cache miss returns None."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=None)

        with patch("core.portfolio_manager.exchange_rate.RedisManager") as mock_rm:
            mock_rm.get_client.return_value = mock_client
            result = await exchange_rate_manager._get_cached_rate("USD/KRW")

        assert result is None

    @pytest.mark.asyncio
    async def test_cache_rate_market_hours(self, exchange_rate_manager, mock_redis_manager):
        """Test caching rate with market hours TTL (300 seconds)."""
        exchange_rate_manager._is_market_hours = MagicMock(return_value=True)
        mock_client = AsyncMock()
        mock_client.setex = AsyncMock()

        rate = ExchangeRate(
            pair="USD/KRW",
            rate=1350.0,
            source="KIS",
            fetched_at=datetime.now(ZoneInfo("UTC")),
        )

        with patch("core.portfolio_manager.exchange_rate.RedisManager") as mock_rm:
            mock_rm.get_client.return_value = mock_client
            await exchange_rate_manager._cache_rate(rate)

        mock_client.setex.assert_called_once()
        call_args = mock_client.setex.call_args
        assert call_args[0][0] == "exchange_rate:USD_KRW"
        assert call_args[0][1] == 300  # MARKET_HOURS_TTL

    @pytest.mark.asyncio
    async def test_cache_rate_off_hours(self, exchange_rate_manager, mock_redis_manager):
        """Test caching rate with off-hours TTL (86400 seconds)."""
        exchange_rate_manager._is_market_hours = MagicMock(return_value=False)
        mock_client = AsyncMock()
        mock_client.setex = AsyncMock()

        rate = ExchangeRate(
            pair="USD/KRW",
            rate=1350.0,
            source="KIS",
            fetched_at=datetime.now(ZoneInfo("UTC")),
        )

        with patch("core.portfolio_manager.exchange_rate.RedisManager") as mock_rm:
            mock_rm.get_client.return_value = mock_client
            await exchange_rate_manager._cache_rate(rate)

        mock_client.setex.assert_called_once()
        call_args = mock_client.setex.call_args
        assert call_args[0][1] == 86400  # OFF_HOURS_TTL


# ============================================================================
# TestExchangeRateManager - Data Fetching Tests
# ============================================================================


class TestExchangeRateManagerFetching:
    """Tests for fetching exchange rates from various sources."""

    @pytest.mark.asyncio
    async def test_fetch_from_kis(self, exchange_rate_manager, mock_kis_client):
        """Test fetching rate from KIS client."""
        mock_kis_client.get_exchange_rate = AsyncMock(return_value={"exchange_rate": 1350.0})

        result = await exchange_rate_manager.fetch_from_kis()

        assert result == 1350.0
        mock_kis_client.get_exchange_rate.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_from_kis_invalid_rate(self, exchange_rate_manager, mock_kis_client):
        """Test KIS fetching with invalid (zero) rate raises ValueError."""
        mock_kis_client.get_exchange_rate = AsyncMock(return_value={"exchange_rate": 0})

        with pytest.raises(ValueError, match="유효하지 않은 환율"):
            await exchange_rate_manager.fetch_from_kis()

    @pytest.mark.asyncio
    async def test_fetch_from_kis_negative_rate(self, exchange_rate_manager, mock_kis_client):
        """Test KIS fetching with negative rate raises ValueError."""
        mock_kis_client.get_exchange_rate = AsyncMock(return_value={"exchange_rate": -100.0})

        with pytest.raises(ValueError, match="유효하지 않은 환율"):
            await exchange_rate_manager.fetch_from_kis()

    @pytest.mark.asyncio
    async def test_fetch_from_fred(self, exchange_rate_manager):
        """Test fetching rate from FRED API."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"observations": [{"value": "1345.0"}]}

        with patch("core.portfolio_manager.exchange_rate.httpx.AsyncClient") as mock_http:
            mock_http_instance = AsyncMock()
            mock_http_instance.__aenter__ = AsyncMock(return_value=mock_http_instance)
            mock_http_instance.__aexit__ = AsyncMock(return_value=None)
            mock_http_instance.get = AsyncMock(return_value=mock_response)
            mock_http.return_value = mock_http_instance

            result = await exchange_rate_manager.fetch_from_fred()

            assert result == 1345.0
            mock_http_instance.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_from_fred_no_observations(self, exchange_rate_manager):
        """Test FRED API response with no observations raises error."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"observations": []}

        with patch("core.portfolio_manager.exchange_rate.httpx.AsyncClient") as mock_http:
            mock_http_instance = AsyncMock()
            mock_http_instance.__aenter__ = AsyncMock(return_value=mock_http_instance)
            mock_http_instance.__aexit__ = AsyncMock(return_value=None)
            mock_http_instance.get = AsyncMock(return_value=mock_response)
            mock_http.return_value = mock_http_instance

            with pytest.raises(Exception):
                await exchange_rate_manager.fetch_from_fred()


# ============================================================================
# TestExchangeRateManager - Market Hours Tests
# ============================================================================


class TestExchangeRateManagerMarketHours:
    """Tests for market hours detection (KST 09:00-15:30 weekdays).

    _is_market_hours() 내부는 datetime.now(timezone.utc) + timedelta(hours=9)로 KST를 계산하므로
    UTC 기준으로 모킹해야 합니다.
    KST 10:00 Mon = UTC 01:00 Mon
    KST 14:00 Mon = UTC 05:00 Mon
    KST 09:00 Mon = UTC 00:00 Mon
    KST 15:30 Mon = UTC 06:30 Mon
    KST 16:00 Mon = UTC 07:00 Mon
    KST 08:00 Mon = UTC Sun 23:00  (주의: weekday 변경!)
    """

    def _make_utc(self, year, month, day, hour, minute=0):
        """UTC timezone-aware datetime 생성"""
        from datetime import timezone as tz

        return datetime(year, month, day, hour, minute, 0, tzinfo=tz.utc)

    def test_is_market_hours_weekday_morning(self, exchange_rate_manager):
        """KST 10:00 Mon (UTC 01:00 Mon) → True"""
        # 2026-04-06 is Monday; UTC 01:00 → KST 10:00
        utc_time = self._make_utc(2026, 4, 6, 1, 0)

        with patch("core.portfolio_manager.exchange_rate.datetime") as mock_dt:
            mock_dt.now.return_value = utc_time
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            # timedelta is used directly from datetime module import, not datetime.timedelta
            result = exchange_rate_manager._is_market_hours()

        assert result is True

    def test_is_market_hours_weekday_afternoon(self, exchange_rate_manager):
        """KST 14:00 Mon (UTC 05:00 Mon) → True"""
        utc_time = self._make_utc(2026, 4, 6, 5, 0)

        with patch("core.portfolio_manager.exchange_rate.datetime") as mock_dt:
            mock_dt.now.return_value = utc_time
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = exchange_rate_manager._is_market_hours()

        assert result is True

    def test_is_market_hours_at_market_open(self, exchange_rate_manager):
        """KST 09:00 Mon (UTC 00:00 Mon) → True"""
        utc_time = self._make_utc(2026, 4, 6, 0, 0)

        with patch("core.portfolio_manager.exchange_rate.datetime") as mock_dt:
            mock_dt.now.return_value = utc_time
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = exchange_rate_manager._is_market_hours()

        assert result is True

    def test_is_market_hours_at_market_close(self, exchange_rate_manager):
        """KST 15:30 Mon (UTC 06:30 Mon) → True"""
        utc_time = self._make_utc(2026, 4, 6, 6, 30)

        with patch("core.portfolio_manager.exchange_rate.datetime") as mock_dt:
            mock_dt.now.return_value = utc_time
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = exchange_rate_manager._is_market_hours()

        assert result is True

    def test_is_market_hours_after_close(self, exchange_rate_manager):
        """KST 16:00 Mon (UTC 07:00 Mon) → False"""
        utc_time = self._make_utc(2026, 4, 6, 7, 0)

        with patch("core.portfolio_manager.exchange_rate.datetime") as mock_dt:
            mock_dt.now.return_value = utc_time
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = exchange_rate_manager._is_market_hours()

        assert result is False

    def test_is_market_hours_before_open(self, exchange_rate_manager):
        """KST 08:00 Mon (UTC Sun 23:00) → False (also wrong weekday)"""
        utc_time = self._make_utc(2026, 4, 5, 23, 0)  # Sunday UTC

        with patch("core.portfolio_manager.exchange_rate.datetime") as mock_dt:
            mock_dt.now.return_value = utc_time
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = exchange_rate_manager._is_market_hours()

        assert result is False

    def test_is_market_hours_weekend_saturday(self, exchange_rate_manager):
        """KST 10:00 Sat (UTC 01:00 Sat) → False"""
        utc_time = self._make_utc(2026, 4, 4, 1, 0)  # Saturday UTC

        with patch("core.portfolio_manager.exchange_rate.datetime") as mock_dt:
            mock_dt.now.return_value = utc_time
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            result = exchange_rate_manager._is_market_hours()

            assert result is False

    def test_is_market_hours_weekend_sunday(self, exchange_rate_manager):
        """Test market hours returns False on Sunday."""
        utc_time = self._make_utc(2026, 4, 5, 1, 0)  # Sunday UTC

        with patch("core.portfolio_manager.exchange_rate.datetime") as mock_dt:
            mock_dt.now.return_value = utc_time
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            result = exchange_rate_manager._is_market_hours()

            assert result is False


# ============================================================================
# TestExchangeRateManager - Currency Conversion Tests
# ============================================================================


class TestExchangeRateManagerConversion:
    """Tests for currency conversion calculations."""

    @pytest.mark.asyncio
    async def test_convert_to_krw(self, exchange_rate_manager):
        """Test converting USD to KRW with rate 1350.0."""
        exchange_rate_manager.get_current_rate = AsyncMock(
            return_value=ExchangeRate(
                pair="USD/KRW",
                rate=1350.0,
                source="KIS",
                fetched_at=datetime.now(ZoneInfo("UTC")),
            )
        )

        result = await exchange_rate_manager.convert_to_krw(1000)

        assert result == 1_350_000.0

    @pytest.mark.asyncio
    async def test_convert_to_krw_zero_amount(self, exchange_rate_manager):
        """Test converting zero USD returns zero KRW."""
        exchange_rate_manager.get_current_rate = AsyncMock(
            return_value=ExchangeRate(
                pair="USD/KRW",
                rate=1350.0,
                source="KIS",
                fetched_at=datetime.now(ZoneInfo("UTC")),
            )
        )

        result = await exchange_rate_manager.convert_to_krw(0)

        assert result == 0.0

    @pytest.mark.asyncio
    async def test_convert_to_krw_fractional_amount(self, exchange_rate_manager):
        """Test converting fractional USD amount."""
        exchange_rate_manager.get_current_rate = AsyncMock(
            return_value=ExchangeRate(
                pair="USD/KRW",
                rate=1350.0,
                source="KIS",
                fetched_at=datetime.now(ZoneInfo("UTC")),
            )
        )

        result = await exchange_rate_manager.convert_to_krw(0.5)

        assert result == 675.0


# ============================================================================
# TestExchangeRateManager - Portfolio Value Tests
# ============================================================================


class TestExchangeRateManagerPortfolioValue:
    """Tests for portfolio value calculations in KRW."""

    @pytest.mark.asyncio
    async def test_get_portfolio_krw_value_with_mixed_holdings(self, exchange_rate_manager):
        """Test calculating portfolio KRW value with mixed holdings."""
        exchange_rate_manager.get_current_rate = AsyncMock(
            return_value=ExchangeRate(
                pair="USD/KRW",
                rate=1350.0,
                source="KIS",
                fetched_at=datetime.now(ZoneInfo("UTC")),
            )
        )

        portfolio = {
            "krw_holdings": [{"value": 1_000_000}],
            "usd_holdings": [{"value": 500}],
            "cash_krw": 500_000,
            "cash_usd": 1000,
        }

        # Expected: 1M + 500*1350 + 500K + 1000*1350 = 1M + 675K + 500K + 1.35M = 3.525M
        result = await exchange_rate_manager.get_portfolio_krw_value(portfolio)

        assert result == 3_525_000.0

    @pytest.mark.asyncio
    async def test_get_portfolio_krw_value_krw_only(self, exchange_rate_manager):
        """Test portfolio with KRW holdings only."""
        portfolio = {
            "krw_holdings": [{"value": 2_000_000}],
            "usd_holdings": [],
            "cash_krw": 1_000_000,
            "cash_usd": 0,
        }

        result = await exchange_rate_manager.get_portfolio_krw_value(portfolio)

        assert result == 3_000_000.0

    @pytest.mark.asyncio
    async def test_get_portfolio_krw_value_usd_only(self, exchange_rate_manager):
        """Test portfolio with USD holdings only."""
        exchange_rate_manager.get_current_rate = AsyncMock(
            return_value=ExchangeRate(
                pair="USD/KRW",
                rate=1350.0,
                source="KIS",
                fetched_at=datetime.now(ZoneInfo("UTC")),
            )
        )

        portfolio = {
            "krw_holdings": [],
            "usd_holdings": [{"value": 1000}],
            "cash_krw": 0,
            "cash_usd": 1000,
        }

        # Expected: 1000*1350 + 1000*1350 = 2.7M
        result = await exchange_rate_manager.get_portfolio_krw_value(portfolio)

        assert result == 2_700_000.0

    @pytest.mark.asyncio
    async def test_get_portfolio_krw_value_empty_portfolio(self, exchange_rate_manager):
        """Test empty portfolio returns 0."""
        portfolio = {
            "krw_holdings": [],
            "usd_holdings": [],
            "cash_krw": 0,
            "cash_usd": 0,
        }

        result = await exchange_rate_manager.get_portfolio_krw_value(portfolio)

        assert result == 0.0


# ============================================================================
# TestExchangeRateManager - Cache Key Tests
# ============================================================================


class TestExchangeRateManagerCacheKey:
    """Tests for cache key constants."""

    def test_cache_key_constant(self, exchange_rate_manager):
        """Test that CACHE_KEY is correctly defined."""
        assert exchange_rate_manager.CACHE_KEY == "exchange_rate:USD_KRW"

    def test_market_hours_constants(self, exchange_rate_manager):
        """Test market hours time constants."""
        assert exchange_rate_manager.MARKET_HOURS_START == time(9, 0)
        assert exchange_rate_manager.MARKET_HOURS_END == time(15, 30)

    def test_ttl_constants(self, exchange_rate_manager):
        """Test TTL constants."""
        assert exchange_rate_manager.MARKET_HOURS_TTL == 300
        assert exchange_rate_manager.OFF_HOURS_TTL == 86400


# ============================================================================
# TestExchangeRateManager - Error Handling Tests
# ============================================================================


class TestExchangeRateManagerErrorHandling:
    """Tests for error handling and edge cases."""

    @pytest.mark.asyncio
    async def test_get_current_rate_all_sources_fail(self, exchange_rate_manager):
        """Test that exception is raised when all sources fail."""
        exchange_rate_manager._get_cached_rate = AsyncMock(return_value=None)
        exchange_rate_manager.fetch_from_kis = AsyncMock(side_effect=Exception("KIS failed"))
        exchange_rate_manager.fetch_from_fred = AsyncMock(side_effect=Exception("FRED failed"))

        with pytest.raises(Exception):
            await exchange_rate_manager.get_current_rate("USD/KRW")

    @pytest.mark.asyncio
    async def test_fetch_from_fred_invalid_response(self, exchange_rate_manager):
        """Test FRED fetching with invalid response format."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()  # httpx: sync method
        mock_response.json.return_value = {}

        with patch("core.portfolio_manager.exchange_rate.httpx.AsyncClient") as mock_http:
            mock_http_instance = AsyncMock()
            mock_http_instance.__aenter__ = AsyncMock(return_value=mock_http_instance)
            mock_http_instance.__aexit__ = AsyncMock(return_value=None)
            mock_http_instance.get = AsyncMock(return_value=mock_response)
            mock_http.return_value = mock_http_instance

            with pytest.raises(Exception):
                await exchange_rate_manager.fetch_from_fred()

    @pytest.mark.asyncio
    async def test_convert_to_krw_negative_amount(self, exchange_rate_manager):
        """Test converting negative USD amount."""
        exchange_rate_manager.get_current_rate = AsyncMock(
            return_value=ExchangeRate(
                pair="USD/KRW",
                rate=1350.0,
                source="KIS",
                fetched_at=datetime.now(ZoneInfo("UTC")),
            )
        )

        result = await exchange_rate_manager.convert_to_krw(-100)

        assert result == -135_000.0

    @pytest.mark.asyncio
    async def test_cache_rate_with_none_fetched_at(self, exchange_rate_manager, mock_redis_manager):
        """Test caching rate handles None fetched_at gracefully (logs warning, no crash)."""
        exchange_rate_manager._is_market_hours = MagicMock(return_value=True)

        rate = ExchangeRate(
            pair="USD/KRW",
            rate=1350.0,
            source="KIS",
            fetched_at=None,
        )

        # None.isoformat() 에러 → 캐시 저장 실패 로그만 남기고 예외 없이 종료
        await exchange_rate_manager._cache_rate(rate)
        # 예외가 발생하지 않으면 성공


# ============================================================================
# TestExchangeRateManager - Integration-style Tests
# ============================================================================


class TestExchangeRateManagerIntegration:
    """Integration-style tests combining multiple features."""

    @pytest.mark.asyncio
    async def test_full_workflow_cache_miss_kis_success(self, exchange_rate_manager, mock_kis_client):
        """Test full workflow: cache miss → KIS success → cache write."""
        exchange_rate_manager._get_cached_rate = AsyncMock(return_value=None)
        mock_kis_client.get_exchange_rate = AsyncMock(return_value={"exchange_rate": 1350.0})
        exchange_rate_manager._cache_rate = AsyncMock()
        exchange_rate_manager._is_market_hours = MagicMock(return_value=True)

        result = await exchange_rate_manager.get_current_rate("USD/KRW")

        assert result.source == "KIS"
        assert result.rate == 1350.0
        exchange_rate_manager._cache_rate.assert_called_once()

    @pytest.mark.asyncio
    async def test_full_workflow_cache_hit_no_fetch(self, exchange_rate_manager):
        """Test full workflow: cache hit → no fetching."""
        cached_rate = ExchangeRate(
            pair="USD/KRW",
            rate=1350.0,
            source="CACHE",
            fetched_at=datetime.now(ZoneInfo("UTC")),
        )
        exchange_rate_manager._get_cached_rate = AsyncMock(return_value=cached_rate)
        exchange_rate_manager.fetch_from_kis = AsyncMock()

        result = await exchange_rate_manager.get_current_rate("USD/KRW")

        assert result.source == "CACHE"
        exchange_rate_manager.fetch_from_kis.assert_not_called()

    @pytest.mark.asyncio
    async def test_full_workflow_portfolio_calculation(self, exchange_rate_manager, mock_kis_client):
        """Test complete workflow: get rate → convert → calculate portfolio value."""
        mock_kis_client.get_exchange_rate = AsyncMock(return_value={"exchange_rate": 1350.0})
        exchange_rate_manager._get_cached_rate = AsyncMock(return_value=None)
        exchange_rate_manager._cache_rate = AsyncMock()

        portfolio = {
            "krw_holdings": [{"value": 1_000_000}],
            "usd_holdings": [{"value": 100}],
            "cash_krw": 500_000,
            "cash_usd": 200,
        }

        result = await exchange_rate_manager.get_portfolio_krw_value(portfolio)

        # 1M + 100*1350 + 500K + 200*1350 = 1M + 135K + 500K + 270K = 1.905M
        assert result == 1_905_000.0
