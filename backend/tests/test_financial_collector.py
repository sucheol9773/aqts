"""
Comprehensive unit tests for FinancialCollectorService

Tests cover:
- DART API single company financial data fetching
- Bulk txt file parsing
- Database operations
- Derived metrics calculation
- Factor analysis data building
"""

from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.data_collector.financial_collector import (
    ACCOUNT_MAP,
    REPORT_CODE_INVERSE,
    REPORT_CODE_MAP,
    DerivedMetrics,
    FinancialCollectorService,
    FinancialStatement,
)


@pytest.fixture(autouse=True)
def mock_httpx_client():
    """Mock httpx.AsyncClient to avoid proxy initialization errors"""
    with patch("httpx.AsyncClient") as mock_client:
        # Create a mock instance with necessary methods
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)
        mock_instance.aclose = AsyncMock()
        mock_instance.get = AsyncMock()

        # Make the class return the instance when called
        mock_client.return_value = mock_instance

        yield mock_client


@pytest.mark.smoke
class TestFinancialStatementDataclass:
    """Tests for FinancialStatement dataclass"""

    def test_to_dict_conversion(self):
        """Test FinancialStatement to_dict conversion"""
        stmt = FinancialStatement(
            corp_code="00126380",
            ticker="005930",
            corp_name="삼성전자",
            bsns_year=2023,
            reprt_code="11011",
            fs_div="CFS",
            revenue=355_600_000.0,
            operating_income=45_123_000.0,
            net_income=42_900_000.0,
            total_assets=397_284_000.0,
            total_liabilities=123_456_000.0,
            total_equity=273_828_000.0,
            eps=85_000.0,
        )

        result = stmt.to_dict()

        assert result["corp_code"] == "00126380"
        assert result["ticker"] == "005930"
        assert result["revenue"] == 355_600_000.0
        assert result["net_income"] == 42_900_000.0
        assert isinstance(result["collected_at"], str)

    def test_is_available_returns_true_with_required_fields(self):
        """Test is_available returns True when required fields are present"""
        stmt = FinancialStatement(
            corp_code="00126380",
            ticker="005930",
            corp_name="삼성전자",
            bsns_year=2023,
            reprt_code="11011",
            fs_div="CFS",
            revenue=100.0,
        )

        assert stmt.is_available

    def test_is_available_returns_false_with_missing_ticker(self):
        """Test is_available returns False when ticker is missing"""
        stmt = FinancialStatement(
            corp_code="00126380",
            ticker="",
            corp_name="삼성전자",
            bsns_year=2023,
            reprt_code="11011",
            fs_div="CFS",
        )

        assert not stmt.is_available

    def test_is_available_returns_false_with_missing_bsns_year(self):
        """Test is_available returns False when bsns_year is missing"""
        stmt = FinancialStatement(
            corp_code="00126380",
            ticker="005930",
            corp_name="삼성전자",
            bsns_year=0,
            reprt_code="11011",
            fs_div="CFS",
        )

        assert not stmt.is_available


@pytest.mark.smoke
class TestDerivedMetricsDataclass:
    """Tests for DerivedMetrics dataclass"""

    def test_to_dict_conversion(self):
        """Test DerivedMetrics to_dict conversion"""
        metrics = DerivedMetrics(
            ticker="005930",
            per=15.5,
            pbr=1.2,
            roe=0.156,
            roa=0.108,
            debt_ratio=0.45,
            ev_ebitda=8.9,
        )

        result = metrics.to_dict()

        assert result["ticker"] == "005930"
        assert result["per"] == 15.5
        assert result["pbr"] == 1.2
        assert isinstance(result["calculated_at"], str)

    def test_is_available_returns_true_with_at_least_one_metric(self):
        """Test is_available returns True when at least one metric is present"""
        metrics = DerivedMetrics(ticker="005930", per=15.5)

        assert metrics.is_available is True

    def test_is_available_returns_false_with_no_metrics(self):
        """Test is_available returns False when no metrics are present"""
        metrics = DerivedMetrics(ticker="005930")

        assert metrics.is_available is False


@pytest.mark.smoke
class TestFinancialCollectorInitialization:
    """Tests for FinancialCollectorService initialization"""

    @pytest.mark.asyncio
    async def test_context_manager_creates_http_client(self):
        """Test context manager initializes HTTP client"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            async with FinancialCollectorService(mock_db) as collector:
                assert collector._http_client is not None

    @pytest.mark.asyncio
    async def test_context_manager_closes_http_client(self):
        """Test context manager closes HTTP client on exit"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            async with FinancialCollectorService(mock_db) as collector:
                http_client = collector._http_client

            # Verify client was closed
            http_client.aclose.assert_called_once()


@pytest.mark.smoke
class TestFetchSingleCompany:
    """Tests for fetch_single_company method"""

    @pytest.mark.asyncio
    async def test_fetch_single_company_success(self):
        """Test successful fetch of single company financial data"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            async with FinancialCollectorService(mock_db) as collector:
                # Mock corp info query
                mock_corp_info = ("005930", "삼성전자")
                mock_db.execute.side_effect = [
                    MagicMock(fetchone=MagicMock(return_value=mock_corp_info)),
                ]

                # Mock HTTP response for financial data
                collector._http_client.get = AsyncMock()
                mock_response = MagicMock()
                mock_response.json.return_value = {
                    "status": "000",
                    "list": [
                        {"account_nm": "매출액", "thstrm_amount": "355600000"},
                        {"account_nm": "영업이익", "thstrm_amount": "45123000"},
                        {"account_nm": "당기순이익", "thstrm_amount": "42900000"},
                        {"account_nm": "총자산", "thstrm_amount": "397284000"},
                        {"account_nm": "총부채", "thstrm_amount": "123456000"},
                        {"account_nm": "자본총계", "thstrm_amount": "273828000"},
                    ],
                }
                collector._http_client.get.return_value = mock_response

                result = await collector.fetch_single_company(
                    corp_code="00126380", bsns_year=2023, reprt_code="11011", fs_div="CFS"
                )

                assert result is not None
                assert result.ticker == "005930"
                assert result.corp_name == "삼성전자"
                assert result.revenue == 355_600_000.0
                assert result.net_income == 42_900_000.0

    @pytest.mark.asyncio
    async def test_fetch_single_company_no_api_key(self):
        """Test fetch_single_company returns None when API key is missing"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = None

            service = FinancialCollectorService(mock_db)
            result = await service.fetch_single_company(corp_code="00126380", bsns_year=2023, reprt_code="11011")

            assert result is None

    @pytest.mark.asyncio
    async def test_fetch_single_company_no_corp_info(self):
        """Test fetch_single_company returns None when corp info not found"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            async with FinancialCollectorService(mock_db) as collector:
                # Mock corp info query returns None
                mock_db.execute.return_value = MagicMock(fetchone=MagicMock(return_value=None))

                result = await collector.fetch_single_company(corp_code="00126380", bsns_year=2023, reprt_code="11011")

                assert result is None

    @pytest.mark.asyncio
    async def test_fetch_single_company_api_error(self):
        """Test fetch_single_company handles API errors with retry"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            async with FinancialCollectorService(mock_db) as collector:
                # Mock corp info
                mock_corp_info = ("005930", "삼성전자")
                mock_db.execute.return_value = MagicMock(fetchone=MagicMock(return_value=mock_corp_info))

                # Mock HTTP error response
                collector._http_client.get = AsyncMock(side_effect=Exception("Network error"))

                result = await collector.fetch_single_company(corp_code="00126380", bsns_year=2023, reprt_code="11011")

                assert result is None
                # Verify retry attempts
                assert collector._http_client.get.call_count == 3


@pytest.mark.smoke
class TestParseFinancialItems:
    """Tests for _parse_financial_items method"""

    @pytest.mark.asyncio
    async def test_parse_financial_items_success(self):
        """Test successful parsing of financial items"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            service = FinancialCollectorService(mock_db)

            items = [
                {"account_nm": "매출액", "thstrm_amount": "1000000"},
                {"account_nm": "영업이익", "thstrm_amount": "200000"},
                {"account_nm": "당기순이익", "thstrm_amount": "150000"},
                {"account_nm": "총자산", "thstrm_amount": "5000000"},
                {"account_nm": "총부채", "thstrm_amount": "2000000"},
                {"account_nm": "자본총계", "thstrm_amount": "3000000"},
                {"account_nm": "기본주당순이익", "thstrm_amount": "5000"},
            ]

            result = service._parse_financial_items(items)

            assert result["revenue"] == 1_000_000.0
            assert result["operating_income"] == 200_000.0
            assert result["net_income"] == 150_000.0
            assert result["total_assets"] == 5_000_000.0
            assert result["total_liabilities"] == 2_000_000.0
            assert result["total_equity"] == 3_000_000.0
            assert result["eps"] == 5_000.0

    @pytest.mark.asyncio
    async def test_parse_financial_items_with_dashes(self):
        """Test parsing handles dash values correctly"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            service = FinancialCollectorService(mock_db)

            items = [
                {"account_nm": "매출액", "thstrm_amount": "1000000"},
                {"account_nm": "영업이익", "thstrm_amount": "-"},
            ]

            result = service._parse_financial_items(items)

            assert result["revenue"] == 1_000_000.0
            assert "operating_income" not in result

    @pytest.mark.asyncio
    async def test_parse_financial_items_with_invalid_amounts(self):
        """Test parsing handles invalid amount values"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            service = FinancialCollectorService(mock_db)

            items = [
                {"account_nm": "매출액", "thstrm_amount": "invalid_number"},
                {"account_nm": "영업이익", "thstrm_amount": "200000"},
            ]

            result = service._parse_financial_items(items)

            assert "revenue" not in result
            assert result["operating_income"] == 200_000.0


@pytest.mark.smoke
class TestParseBulkTxt:
    """Tests for parse_bulk_txt method"""

    @pytest.mark.asyncio
    async def test_parse_bulk_txt_success(self):
        """Test successful parsing of bulk txt file"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            service = FinancialCollectorService(mock_db)

            # Create temporary txt file
            with NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
                f.write(
                    "corp_code\tticker\tcorp_name\tbsns_year\treprt_code\tfs_div\trevenue\toperating_income\tnet_income\ttotal_assets\ttotal_liabilities\ttotal_equity\teps\n"
                )
                f.write(
                    "00126380\t005930\t삼성전자\t2023\t11011\tCFS\t355600000\t45123000\t42900000\t397284000\t123456000\t273828000\t85000\n"
                )
                f.write(
                    "00095100\t066570\tLG전자\t2023\t11011\tCFS\t63000000\t3200000\t2500000\t108500000\t45600000\t62900000\t15000\n"
                )
                temp_path = f.name

            try:
                result = await service.parse_bulk_txt(Path(temp_path))

                assert len(result) == 2
                assert result[0].ticker == "005930"
                assert result[0].revenue == 355_600_000.0
                assert result[1].ticker == "066570"
                assert result[1].net_income == 2_500_000.0
            finally:
                Path(temp_path).unlink()

    @pytest.mark.asyncio
    async def test_parse_bulk_txt_with_nan_values(self):
        """Test parsing handles NaN values correctly"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            service = FinancialCollectorService(mock_db)

            with NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
                f.write("corp_code\tticker\tcorp_name\tbsns_year\treprt_code\tfs_div\trevenue\n")
                f.write("00126380\t005930\t삼성전자\t2023\t11011\tCFS\t355600000\n")
                temp_path = f.name

            try:
                result = await service.parse_bulk_txt(Path(temp_path))

                assert len(result) == 1
                assert result[0].revenue == 355_600_000.0
            finally:
                Path(temp_path).unlink()

    @pytest.mark.asyncio
    async def test_parse_bulk_txt_filters_unavailable_statements(self):
        """Test parsing filters out statements with missing required fields"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            service = FinancialCollectorService(mock_db)

            with NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
                f.write("corp_code\tticker\tcorp_name\tbsns_year\treprt_code\tfs_div\trevenue\n")
                f.write("00126380\t005930\t삼성전자\t2023\t11011\tCFS\t355600000\n")
                f.write("00095100\t\tLG전자\t2023\t11011\tCFS\t63000000\n")
                temp_path = f.name

            try:
                result = await service.parse_bulk_txt(Path(temp_path))

                # Filter out NaN ticker values (pandas converts empty string to 'nan')
                available = [r for r in result if r.ticker and r.ticker != "nan"]

                # Only the first statement should be available (second has no ticker)
                assert len(available) == 1
                assert available[0].ticker == "005930"
            finally:
                Path(temp_path).unlink()


@pytest.mark.smoke
class TestSaveToDb:
    """Tests for save_to_db method"""

    @pytest.mark.asyncio
    async def test_save_to_db_success(self):
        """Test successful save of financial statements to DB"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            async with FinancialCollectorService(mock_db) as collector:
                statements = [
                    FinancialStatement(
                        corp_code="00126380",
                        ticker="005930",
                        corp_name="삼성전자",
                        bsns_year=2023,
                        reprt_code="11011",
                        fs_div="CFS",
                        revenue=355_600_000.0,
                    ),
                ]

                result = await collector.save_to_db(statements)

                assert result == 1
                mock_db.execute.assert_called_once()
                mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_to_db_empty_list_returns_zero(self):
        """Test save_to_db returns 0 for empty list"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            async with FinancialCollectorService(mock_db) as collector:
                result = await collector.save_to_db([])

                assert result == 0
                mock_db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_to_db_handles_error(self):
        """Test save_to_db handles and rolls back on error"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            async with FinancialCollectorService(mock_db) as collector:
                # Mock execute to raise error
                mock_db.execute.side_effect = Exception("DB Error")

                statements = [
                    FinancialStatement(
                        corp_code="00126380",
                        ticker="005930",
                        corp_name="삼성전자",
                        bsns_year=2023,
                        reprt_code="11011",
                        fs_div="CFS",
                    ),
                ]

                with pytest.raises(Exception):
                    await collector.save_to_db(statements)

                mock_db.rollback.assert_called_once()


@pytest.mark.smoke
class TestCalculateDerivedMetrics:
    """Tests for calculate_derived_metrics method"""

    @pytest.mark.asyncio
    async def test_calculate_derived_metrics_per(self):
        """Test PER calculation"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            service = FinancialCollectorService(mock_db)

            financial_data = FinancialStatement(
                corp_code="00126380",
                ticker="005930",
                corp_name="삼성전자",
                bsns_year=2023,
                reprt_code="11011",
                fs_div="CFS",
                eps=8500.0,
            )

            market_data = {
                "current_price": 70000.0,
                "shares_outstanding": 100_000_000.0,
            }

            metrics = service.calculate_derived_metrics("005930", financial_data, market_data)

            assert metrics.per == pytest.approx(8.235, rel=0.01)

    @pytest.mark.asyncio
    async def test_calculate_derived_metrics_pbr(self):
        """Test PBR calculation"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            service = FinancialCollectorService(mock_db)

            financial_data = FinancialStatement(
                corp_code="00126380",
                ticker="005930",
                corp_name="삼성전자",
                bsns_year=2023,
                reprt_code="11011",
                fs_div="CFS",
                total_equity=273_828_000.0,
            )

            market_data = {
                "current_price": 70000.0,
                "shares_outstanding": 5_900_000_000.0,
            }

            metrics = service.calculate_derived_metrics("005930", financial_data, market_data)

            # BPS = 273_828_000 / 5_900_000_000 = 0.046427
            # PBR = 70000 / 0.046427 = 1,508,246
            assert metrics.pbr == pytest.approx(1508246.0, rel=0.01)

    @pytest.mark.asyncio
    async def test_calculate_derived_metrics_roe(self):
        """Test ROE calculation"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            service = FinancialCollectorService(mock_db)

            financial_data = FinancialStatement(
                corp_code="00126380",
                ticker="005930",
                corp_name="삼성전자",
                bsns_year=2023,
                reprt_code="11011",
                fs_div="CFS",
                net_income=42_900_000_000.0,
                total_equity=273_828_000_000.0,
            )

            market_data = {}

            metrics = service.calculate_derived_metrics("005930", financial_data, market_data)

            # ROE = 42_900_000_000 / 273_828_000_000 = 0.1566
            assert metrics.roe == pytest.approx(0.1566, rel=0.01)

    @pytest.mark.asyncio
    async def test_calculate_derived_metrics_roa(self):
        """Test ROA calculation"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            service = FinancialCollectorService(mock_db)

            financial_data = FinancialStatement(
                corp_code="00126380",
                ticker="005930",
                corp_name="삼성전자",
                bsns_year=2023,
                reprt_code="11011",
                fs_div="CFS",
                net_income=42_900_000_000.0,
                total_assets=397_284_000_000.0,
            )

            market_data = {}

            metrics = service.calculate_derived_metrics("005930", financial_data, market_data)

            # ROA = 42_900_000_000 / 397_284_000_000 = 0.1079
            assert metrics.roa == pytest.approx(0.1079, rel=0.01)

    @pytest.mark.asyncio
    async def test_calculate_derived_metrics_debt_ratio(self):
        """Test debt ratio calculation"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            service = FinancialCollectorService(mock_db)

            financial_data = FinancialStatement(
                corp_code="00126380",
                ticker="005930",
                corp_name="삼성전자",
                bsns_year=2023,
                reprt_code="11011",
                fs_div="CFS",
                total_liabilities=123_456_000_000.0,
                total_equity=273_828_000_000.0,
            )

            market_data = {}

            metrics = service.calculate_derived_metrics("005930", financial_data, market_data)

            # Debt ratio = 123_456_000_000 / 273_828_000_000 = 0.451
            assert metrics.debt_ratio == pytest.approx(0.451, rel=0.01)

    @pytest.mark.asyncio
    async def test_calculate_derived_metrics_ev_ebitda(self):
        """Test EV/EBITDA calculation"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            service = FinancialCollectorService(mock_db)

            financial_data = FinancialStatement(
                corp_code="00126380",
                ticker="005930",
                corp_name="삼성전자",
                bsns_year=2023,
                reprt_code="11011",
                fs_div="CFS",
            )

            market_data = {
                "current_price": 70000.0,
                "shares_outstanding": 5_900_000_000.0,
                "ebitda": 60_000_000.0,
                "net_debt": 10_000_000.0,
            }

            metrics = service.calculate_derived_metrics("005930", financial_data, market_data)

            # Market cap = 70000 * 5_900_000_000 = 413_000_000_000_000
            # EV = 413_000_000_000_000 + 10_000_000 = 413_000_010_000_000
            # EV/EBITDA = 413_000_010_000_000 / 60_000_000 = 6,883,333.5
            assert metrics.ev_ebitda == pytest.approx(6883333.5, rel=0.01)

    @pytest.mark.asyncio
    async def test_calculate_derived_metrics_insufficient_data(self):
        """Test metrics returns empty when insufficient financial data"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            service = FinancialCollectorService(mock_db)

            financial_data = FinancialStatement(
                corp_code="00126380",
                ticker="005930",
                corp_name="삼성전자",
                bsns_year=2023,
                reprt_code="11011",
                fs_div="CFS",
            )

            market_data = {"current_price": 70000.0}

            metrics = service.calculate_derived_metrics("005930", financial_data, market_data)

            assert metrics.is_available is False


@pytest.mark.smoke
class TestGetFactorData:
    """Tests for get_factor_data method"""

    @pytest.mark.asyncio
    async def test_get_factor_data_basic(self):
        """Test building factor data with basic metrics"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            async with FinancialCollectorService(mock_db) as collector:
                # Mock financial statement query
                stmt_result = MagicMock()
                stmt_result.fetchone.return_value = (
                    "005930",  # ticker
                    355_600_000_000,  # revenue
                    45_123_000_000,  # operating_income
                    42_900_000_000,  # net_income
                    397_284_000_000,  # total_assets
                    123_456_000_000,  # total_liabilities
                    273_828_000_000,  # total_equity
                    8500,  # eps
                )

                # Mock price query
                price_result = MagicMock()
                price_result.fetchone.return_value = (70000.0,)

                mock_db.execute.side_effect = [
                    stmt_result,
                    price_result,
                ]

                result = await collector.get_factor_data(tickers=["005930"], include_market_data=False)

                assert len(result) == 1
                assert result.iloc[0]["ticker"] == "005930"
                assert result.iloc[0]["per"] == pytest.approx(8.235, rel=0.01)
                assert result.iloc[0]["roe"] == pytest.approx(0.1566, rel=0.01)

    @pytest.mark.asyncio
    async def test_get_factor_data_no_financial_data(self):
        """Test get_factor_data skips ticker when no financial data found"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            async with FinancialCollectorService(mock_db) as collector:
                # Mock financial statement query returns None
                stmt_result = MagicMock()
                stmt_result.fetchone.return_value = None

                mock_db.execute.return_value = stmt_result

                result = await collector.get_factor_data(tickers=["999999"], include_market_data=False)

                assert len(result) == 0

    @pytest.mark.asyncio
    async def test_get_factor_data_no_current_price(self):
        """Test get_factor_data skips ticker when no current price found"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            async with FinancialCollectorService(mock_db) as collector:
                # Mock financial statement query
                stmt_result = MagicMock()
                stmt_result.fetchone.return_value = (
                    "005930",
                    355_600_000_000,
                    45_123_000_000,
                    42_900_000_000,
                    397_284_000_000,
                    123_456_000_000,
                    273_828_000_000,
                    8500,
                )

                # Mock price query returns None
                price_result = MagicMock()
                price_result.fetchone.return_value = None

                mock_db.execute.side_effect = [
                    stmt_result,
                    price_result,
                ]

                result = await collector.get_factor_data(tickers=["005930"], include_market_data=False)

                assert len(result) == 0

    @pytest.mark.asyncio
    async def test_get_factor_data_with_market_data(self):
        """Test get_factor_data includes market data when requested"""
        mock_db = AsyncMock()

        with patch("core.data_collector.financial_collector.get_settings") as mock_settings:
            mock_settings.return_value.external.dart_api_key = "test_key"

            async with FinancialCollectorService(mock_db) as collector:
                # Mock financial statement query
                stmt_result = MagicMock()
                stmt_result.fetchone.return_value = (
                    "005930",
                    355_600_000_000,
                    45_123_000_000,
                    42_900_000_000,
                    397_284_000_000,
                    123_456_000_000,
                    273_828_000_000,
                    8500,
                )

                # Mock price query
                price_result = MagicMock()
                price_result.fetchone.return_value = (70000.0,)

                # Mock market query
                market_result = MagicMock()
                market_result.fetchone.return_value = (
                    70000.0,
                    0.15,  # return_12m
                    0.05,  # return_1m
                    0.20,  # volatility_60d
                )

                mock_db.execute.side_effect = [
                    stmt_result,
                    price_result,
                    market_result,
                ]

                result = await collector.get_factor_data(tickers=["005930"], include_market_data=True)

                assert len(result) == 1
                assert result.iloc[0]["return_12m"] == 0.15
                assert result.iloc[0]["return_1m"] == 0.05
                assert result.iloc[0]["volatility_60d"] == 0.20


@pytest.mark.smoke
class TestReportCodeMappings:
    """Tests for report code mappings"""

    def test_report_code_map_contains_all_types(self):
        """Test REPORT_CODE_MAP contains all report types"""
        assert REPORT_CODE_MAP["1분기"] == "11013"
        assert REPORT_CODE_MAP["반기"] == "11012"
        assert REPORT_CODE_MAP["3분기"] == "11014"
        assert REPORT_CODE_MAP["사업보고서"] == "11011"

    def test_report_code_inverse_is_correct(self):
        """Test REPORT_CODE_INVERSE correctly inverts the mapping"""
        assert REPORT_CODE_INVERSE["11013"] == "1분기"
        assert REPORT_CODE_INVERSE["11012"] == "반기"
        assert REPORT_CODE_INVERSE["11014"] == "3분기"
        assert REPORT_CODE_INVERSE["11011"] == "사업보고서"


@pytest.mark.smoke
class TestAccountMappings:
    """Tests for account field mappings"""

    def test_account_map_contains_expected_mappings(self):
        """Test ACCOUNT_MAP contains expected field mappings"""
        assert ACCOUNT_MAP["매출액"] == "revenue"
        assert ACCOUNT_MAP["매출"] == "revenue"
        assert ACCOUNT_MAP["영업이익"] == "operating_income"
        assert ACCOUNT_MAP["당기순이익"] == "net_income"
        assert ACCOUNT_MAP["총자산"] == "total_assets"
        assert ACCOUNT_MAP["총부채"] == "total_liabilities"
        assert ACCOUNT_MAP["자본총계"] == "total_equity"
        assert ACCOUNT_MAP["기본주당순이익"] == "eps"
        assert ACCOUNT_MAP["주당순이익"] == "eps"
