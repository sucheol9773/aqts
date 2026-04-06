"""
인프라 계층 테스트 (mock 기반)

테스트 범위:
  - database.py: MongoDBManager, RedisManager, get_db_session, Base
  - audit_log.py: AuditLogger.log (성공/실패)
  - settings.py: KISSettings, DatabaseSettings, MongoSettings, RedisSettings, AppSettings
  - constants.py: Enum 값, 매핑 테이블, 상수 무결성
  - logging.py: setup_logging
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.constants import (
    AI_CACHE_TTL,
    DART_API_BASE_URL,
    DATA_INTEGRITY,
    ENSEMBLE_DEFAULT_WEIGHTS,
    HOLDING_PERIOD_MAP,
    NEWS_RSS_FEEDS,
    PORTFOLIO_CONSTRAINTS,
    TRANSACTION_COSTS,
    WS_REFRESH_INTERVALS,
    AlertType,
    AssetType,
    Country,
    EconomicDataSource,
    EconomicIndicatorType,
    InvestmentGoal,
    InvestmentStyle,
    Market,
    NewsSource,
    OpinionAction,
    OpinionType,
    OrderSide,
    OrderStatus,
    OrderType,
    RebalancingFrequency,
    RebalancingType,
    RiskProfile,
    SentimentMode,
    SignalDirection,
    StrategyType,
)
from config.settings import (
    AppSettings,
    DatabaseSettings,
    KISCredential,
    KISSettings,
    MongoSettings,
    RedisSettings,
    RiskManagementSettings,
    TradingMode,
)


# ============================================================
# constants.py — Enum 값 무결성 테스트
# ============================================================
class TestMarketEnums:
    def test_market_values(self):
        assert Market.KRX.value == "KRX"
        assert Market.NYSE.value == "NYSE"
        assert Market.NASDAQ.value == "NASDAQ"
        assert Market.AMEX.value == "AMEX"
        assert len(Market) == 4

    def test_asset_type_values(self):
        assert AssetType.STOCK.value == "STOCK"
        assert AssetType.ETF.value == "ETF"
        assert AssetType.CASH.value == "CASH"
        assert len(AssetType) == 6

    def test_country_values(self):
        assert Country.KR.value == "KR"
        assert Country.US.value == "US"
        assert len(Country) == 2


class TestProfileEnums:
    def test_risk_profile(self):
        assert RiskProfile.CONSERVATIVE.value == "CONSERVATIVE"
        assert RiskProfile.AGGRESSIVE.value == "AGGRESSIVE"
        assert len(RiskProfile) == 4

    def test_investment_style(self):
        assert InvestmentStyle.DISCRETIONARY.value == "DISCRETIONARY"
        assert InvestmentStyle.ADVISORY.value == "ADVISORY"
        assert len(InvestmentStyle) == 2

    def test_investment_goal(self):
        assert len(InvestmentGoal) == 4


class TestOrderEnums:
    def test_order_side(self):
        assert OrderSide.BUY.value == "BUY"
        assert OrderSide.SELL.value == "SELL"

    def test_order_type(self):
        assert OrderType.MARKET.value == "MARKET"
        assert OrderType.TWAP.value == "TWAP"
        assert OrderType.VWAP.value == "VWAP"
        assert len(OrderType) == 4

    def test_order_status(self):
        assert OrderStatus.PENDING.value == "PENDING"
        assert OrderStatus.FILLED.value == "FILLED"
        assert OrderStatus.CANCELLED.value == "CANCELLED"
        assert len(OrderStatus) == 6


class TestSignalEnums:
    def test_signal_direction(self):
        assert SignalDirection.BUY.value == "BUY"
        assert SignalDirection.HOLD.value == "HOLD"
        assert len(SignalDirection) == 3

    def test_strategy_type(self):
        assert StrategyType.FACTOR.value == "FACTOR"
        assert StrategyType.RISK_PARITY.value == "RISK_PARITY"
        assert len(StrategyType) == 6

    def test_sentiment_mode(self):
        assert SentimentMode.SCORE.value == "SCORE"
        assert SentimentMode.OPINION.value == "OPINION"

    def test_opinion_action(self):
        assert OpinionAction.STRONG_BUY.value == "STRONG_BUY"
        assert OpinionAction.STRONG_SELL.value == "STRONG_SELL"
        assert len(OpinionAction) == 5

    def test_opinion_type(self):
        assert OpinionType.STOCK.value == "STOCK"
        assert OpinionType.MACRO.value == "MACRO"
        assert len(OpinionType) == 3


class TestOtherEnums:
    def test_rebalancing_type(self):
        assert RebalancingType.SCHEDULED.value == "SCHEDULED"
        assert RebalancingType.EMERGENCY.value == "EMERGENCY"
        assert len(RebalancingType) == 3

    def test_rebalancing_frequency(self):
        assert RebalancingFrequency.MONTHLY.value == "MONTHLY"
        assert len(RebalancingFrequency) == 3

    def test_alert_type(self):
        assert AlertType.DAILY_REPORT.value == "DAILY_REPORT"
        assert AlertType.SYSTEM_ERROR.value == "SYSTEM_ERROR"
        assert len(AlertType) == 5

    def test_news_source(self):
        assert NewsSource.NAVER_FINANCE.value == "NAVER_FINANCE"
        assert NewsSource.DART.value == "DART"
        assert len(NewsSource) == 6

    def test_economic_data_source(self):
        assert EconomicDataSource.FRED.value == "FRED"
        assert EconomicDataSource.ECOS.value == "ECOS"

    def test_economic_indicator_type(self):
        assert EconomicIndicatorType.GDP.value == "GDP"
        assert EconomicIndicatorType.BOK_BASE_RATE.value == "BOK_BASE_RATE"
        assert len(EconomicIndicatorType) == 14


# ============================================================
# constants.py — 매핑 테이블/상수 무결성
# ============================================================
class TestConstantMappings:
    def test_holding_period_map_all_profiles(self):
        """모든 RiskProfile에 대한 매핑 존재"""
        for profile in RiskProfile:
            assert profile in HOLDING_PERIOD_MAP
            entry = HOLDING_PERIOD_MAP[profile]
            assert "min_days" in entry
            assert "max_days" in entry
            assert entry["min_days"] < entry["max_days"]

    def test_transaction_costs_all_countries(self):
        """모든 Country에 대한 거래비용 존재"""
        for country in Country:
            assert country in TRANSACTION_COSTS
            costs = TRANSACTION_COSTS[country]
            assert "commission_rate" in costs
            assert "slippage_rate" in costs
            assert costs["commission_rate"] >= 0

    def test_portfolio_constraints(self):
        assert PORTFOLIO_CONSTRAINTS["max_single_weight"] == 0.20
        assert PORTFOLIO_CONSTRAINTS["max_sector_weight"] == 0.40
        assert PORTFOLIO_CONSTRAINTS["min_positions"] == 5

    def test_data_integrity_constants(self):
        assert DATA_INTEGRITY["max_consecutive_missing_days"] == 3
        assert DATA_INTEGRITY["outlier_sigma_threshold"] == 3.0
        assert DATA_INTEGRITY["kr_daily_limit_pct"] == 0.30

    def test_ws_refresh_intervals(self):
        assert "portfolio" in WS_REFRESH_INTERVALS
        assert "alerts" in WS_REFRESH_INTERVALS
        assert WS_REFRESH_INTERVALS["alerts"] == 0  # 즉시

    def test_news_rss_feeds_all_sources(self):
        """뉴스 소스별 RSS 피드 URL 존재"""
        for source in [NewsSource.NAVER_FINANCE, NewsSource.HANKYUNG, NewsSource.MAEKYUNG, NewsSource.REUTERS]:
            assert source in NEWS_RSS_FEEDS
            assert len(NEWS_RSS_FEEDS[source]) >= 1

    def test_ai_cache_ttl(self):
        assert AI_CACHE_TTL[SentimentMode.SCORE] == 3600
        assert AI_CACHE_TTL[SentimentMode.OPINION] == 14400

    def test_ensemble_default_weights_all_profiles(self):
        """모든 RiskProfile에 대한 앙상블 가중치 합 ≈ 1.0"""
        for profile in RiskProfile:
            assert profile in ENSEMBLE_DEFAULT_WEIGHTS
            weights = ENSEMBLE_DEFAULT_WEIGHTS[profile]
            total = sum(weights.values())
            assert abs(total - 1.0) < 0.01, f"{profile}: weight sum = {total}"

    def test_dart_api_base_url(self):
        assert DART_API_BASE_URL.startswith("https://")


# ============================================================
# settings.py — KISCredential
# ============================================================
class TestKISCredential:
    def test_default_credential(self):
        cred = KISCredential()
        assert cred.app_key == ""
        assert cred.app_secret == ""
        assert cred.account_prod == "01"

    def test_custom_credential(self):
        cred = KISCredential(app_key="test_key", app_secret="test_secret", account_no="12345678")
        assert cred.app_key == "test_key"
        assert cred.account_no == "12345678"


# ============================================================
# settings.py — KISSettings
# ============================================================
class TestKISSettings:
    def test_default_mode_in_test_env(self):
        """conftest.py에서 KIS_TRADING_MODE=BACKTEST 설정 → 테스트 환경에서는 BACKTEST"""
        s = KISSettings()
        assert s.trading_mode == TradingMode.BACKTEST
        assert s.is_backtest is True
        assert s.is_live is False
        assert s.is_demo is False

    def test_code_default_is_demo(self, monkeypatch):
        """코드 기본값은 DEMO (환경변수 미설정 시)"""
        monkeypatch.delenv("KIS_TRADING_MODE", raising=False)
        s = KISSettings()
        assert s.trading_mode == TradingMode.DEMO
        assert s.is_demo is True

    def test_live_mode(self):
        s = KISSettings(trading_mode=TradingMode.LIVE, live_app_key="live_key")
        assert s.is_live is True
        assert s.active_credential.app_key == "live_key"

    def test_demo_mode_credential(self):
        s = KISSettings(trading_mode=TradingMode.DEMO, demo_app_key="demo_key")
        assert s.active_credential.app_key == "demo_key"

    def test_backtest_mode_empty_credential(self):
        s = KISSettings(trading_mode=TradingMode.BACKTEST)
        cred = s.active_credential
        assert cred.app_key == ""
        assert cred.base_url == ""

    def test_convenience_properties(self):
        s = KISSettings(trading_mode=TradingMode.DEMO, demo_app_key="dk", demo_app_secret="ds")
        assert s.app_key == "dk"
        assert s.app_secret == "ds"


# ============================================================
# settings.py — DatabaseSettings
# ============================================================
class TestDatabaseSettings:
    def test_default_values(self, monkeypatch):
        """코드 기본값 검증 (환경변수 오염 방지)"""
        monkeypatch.delenv("DB_HOST", raising=False)
        monkeypatch.delenv("DB_PORT", raising=False)
        monkeypatch.delenv("DB_NAME", raising=False)
        monkeypatch.delenv("DB_USER", raising=False)
        monkeypatch.delenv("DB_POOL_SIZE", raising=False)
        monkeypatch.delenv("DB_MAX_OVERFLOW", raising=False)
        s = DatabaseSettings(password="test_pass")
        assert s.host == "postgres"
        assert s.port == 5432
        assert s.name == "aqts"
        assert s.pool_size == 20

    def test_async_url(self):
        s = DatabaseSettings(host="localhost", port=5433, name="testdb", user="user", password="pass")
        assert s.async_url == "postgresql+asyncpg://user:pass@localhost:5433/testdb"

    def test_sync_url(self):
        s = DatabaseSettings(host="localhost", port=5433, name="testdb", user="user", password="pass")
        assert s.sync_url == "postgresql+psycopg2://user:pass@localhost:5433/testdb"


# ============================================================
# settings.py — MongoSettings
# ============================================================
class TestMongoSettings:
    def test_default_values(self, monkeypatch):
        """코드 기본값 검증 (CI 환경변수 오염 방지)"""
        monkeypatch.delenv("MONGO_HOST", raising=False)
        monkeypatch.delenv("MONGO_PORT", raising=False)
        monkeypatch.delenv("MONGO_DB", raising=False)
        monkeypatch.delenv("MONGO_USER", raising=False)
        s = MongoSettings(password="test_pass")
        assert s.host == "mongodb"
        assert s.port == 27017
        assert s.db == "aqts"

    def test_uri(self):
        s = MongoSettings(host="localhost", port=27018, db="testdb", user="user", password="pass")
        assert s.uri == "mongodb://user:pass@localhost:27018/testdb?authSource=admin"


# ============================================================
# settings.py — RedisSettings
# ============================================================
class TestRedisSettings:
    def test_default_values(self, monkeypatch):
        """코드 기본값 검증 (CI 환경변수 오염 방지)"""
        monkeypatch.delenv("REDIS_HOST", raising=False)
        monkeypatch.delenv("REDIS_PORT", raising=False)
        monkeypatch.delenv("REDIS_DB", raising=False)
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)
        s = RedisSettings(password="test_pass")
        assert s.host == "redis"
        assert s.port == 6379
        assert s.db == 0

    def test_url(self):
        s = RedisSettings(host="localhost", port=6380, password="pass", db=1)
        assert s.url == "redis://:pass@localhost:6380/1"


# ============================================================
# settings.py — RiskManagementSettings
# ============================================================
class TestRiskManagementSettings:
    def test_defaults(self, monkeypatch):
        # 이전 테스트에서 설정된 환경변수 오염 방지
        monkeypatch.delenv("MAX_DRAWDOWN", raising=False)
        s = RiskManagementSettings()
        assert s.initial_capital_krw == 50_000_000
        assert s.daily_loss_limit_krw == 5_000_000
        assert s.max_positions == 20
        assert s.max_drawdown == 0.15
        assert s.stop_loss_percent == -0.10

    def test_custom_values(self):
        """alias를 통해 값을 전달 (pydantic-settings alias 방식)"""
        s = RiskManagementSettings(INITIAL_CAPITAL_KRW=100_000_000, MAX_POSITIONS=30)
        assert s.initial_capital_krw == 100_000_000
        assert s.max_positions == 30


# ============================================================
# settings.py — AppSettings
# ============================================================
class TestAppSettings:
    def test_default_environment(self):
        s = AppSettings()
        assert s.environment == "development"
        assert s.is_production is False
        assert s.log_level == "INFO"

    def test_production_detection(self, monkeypatch):
        """environment='production' → is_production=True"""
        monkeypatch.setenv("ENVIRONMENT", "production")
        s = AppSettings()
        assert s.is_production is True

    def test_is_live_trading(self, monkeypatch):
        """production 환경 + LIVE 모드 → is_live_trading=True"""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("KIS_TRADING_MODE", "LIVE")
        s = AppSettings()
        assert s.is_live_trading is True

    def test_not_live_trading_in_dev(self, monkeypatch):
        """development 환경에서는 LIVE 모드여도 is_live_trading=False"""
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.setenv("KIS_TRADING_MODE", "LIVE")
        s = AppSettings()
        assert s.is_live_trading is False

    def test_cors_default(self):
        s = AppSettings()
        assert "localhost:3000" in s.cors_allowed_origins


# ============================================================
# database.py — MongoDBManager (mock 기반)
# ============================================================
class TestMongoDBManager:
    @pytest.fixture(autouse=True)
    def reset_manager(self):
        from db.database import MongoDBManager

        MongoDBManager._client = None
        MongoDBManager._db = None
        yield
        MongoDBManager._client = None
        MongoDBManager._db = None

    def test_get_db_not_connected_raises(self):
        from db.database import MongoDBManager

        with pytest.raises(RuntimeError, match="MongoDB not connected"):
            MongoDBManager.get_db()

    def test_get_collection_not_connected_raises(self):
        from db.database import MongoDBManager

        with pytest.raises(RuntimeError, match="MongoDB not connected"):
            MongoDBManager.get_collection("test")

    @pytest.mark.asyncio
    async def test_connect_sets_client(self):
        from db.database import MongoDBManager

        with patch("db.database.AsyncIOMotorClient") as MockClient:
            mock_client = MagicMock()
            mock_client.__getitem__ = MagicMock(return_value="mock_db")
            MockClient.return_value = mock_client

            await MongoDBManager.connect()
            assert MongoDBManager._client is not None
            assert MongoDBManager._db is not None

    @pytest.mark.asyncio
    async def test_disconnect_closes_client(self):
        from db.database import MongoDBManager

        mock_client = MagicMock()
        MongoDBManager._client = mock_client
        await MongoDBManager.disconnect()
        mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected(self):
        from db.database import MongoDBManager

        MongoDBManager._client = None
        await MongoDBManager.disconnect()  # Should not raise

    def test_get_collection_returns_collection(self):
        from db.database import MongoDBManager

        mock_db = MagicMock()
        mock_db.__getitem__ = MagicMock(return_value="mock_collection")
        MongoDBManager._db = mock_db
        result = MongoDBManager.get_collection("audit_logs")
        mock_db.__getitem__.assert_called_with("audit_logs")


# ============================================================
# database.py — RedisManager (mock 기반)
# ============================================================
class TestRedisManager:
    @pytest.fixture(autouse=True)
    def reset_manager(self):
        from db.database import RedisManager

        RedisManager._client = None
        yield
        RedisManager._client = None

    def test_get_client_not_connected_raises(self):
        from db.database import RedisManager

        with pytest.raises(RuntimeError, match="Redis not connected"):
            RedisManager.get_client()

    @pytest.mark.asyncio
    async def test_connect_sets_client(self):
        from db.database import RedisManager

        with patch("db.database.Redis") as MockRedis:
            mock_redis = MagicMock()
            MockRedis.from_url = MagicMock(return_value=mock_redis)

            await RedisManager.connect()
            assert RedisManager._client is not None
            MockRedis.from_url.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_closes_client(self):
        from db.database import RedisManager

        mock_client = AsyncMock()
        RedisManager._client = mock_client
        await RedisManager.disconnect()
        mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected(self):
        from db.database import RedisManager

        RedisManager._client = None
        await RedisManager.disconnect()  # Should not raise


# ============================================================
# database.py — get_db_session (mock 기반)
# ============================================================
class TestGetDbSession:
    @pytest.mark.asyncio
    async def test_session_commit_on_success(self):
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_session.close = AsyncMock()

        mock_factory = MagicMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("db.database.async_session_factory", mock_factory):
            from db.database import get_db_session

            gen = get_db_session()
            session = await gen.__anext__()
            assert session == mock_session

    @pytest.mark.asyncio
    async def test_session_rollback_on_error(self):
        """commit 실패 시 rollback이 호출되고 예외가 재발생됨"""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock(side_effect=Exception("DB Error"))
        mock_session.rollback = AsyncMock()
        mock_session.close = AsyncMock()

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_ctx)

        with patch("db.database.async_session_factory", mock_factory):
            from db.database import get_db_session

            gen = get_db_session()
            session = await gen.__anext__()
            # Generator yield 후 commit 호출 → 실패 → rollback → raise
            with pytest.raises(Exception, match="DB Error"):
                await gen.__anext__()


# ============================================================
# database.py — Base
# ============================================================
class TestBase:
    def test_base_is_declarative(self):
        from db.database import Base

        assert hasattr(Base, "metadata")
        assert hasattr(Base, "__tablename__") or hasattr(Base, "registry")


# ============================================================
# audit_log.py — AuditLogger (mock 기반)
# ============================================================
class TestAuditLogger:
    @pytest.mark.asyncio
    async def test_log_success(self):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()

        from db.repositories.audit_log import AuditLogger

        logger = AuditLogger(mock_session)
        await logger.log(
            action_type="ORDER_PLACED",
            module="order_executor",
            description="AAPL 매수 10주",
            before_state={"position": 0},
            after_state={"position": 10},
            metadata={"ticker": "AAPL"},
        )
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_log_without_optional_fields(self):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()

        from db.repositories.audit_log import AuditLogger

        logger = AuditLogger(mock_session)
        await logger.log(
            action_type="SYSTEM_START",
            module="main",
            description="시스템 시작",
        )
        mock_session.execute.assert_called_once()
        # before_state, after_state, metadata가 None으로 전달됨
        call_args = mock_session.execute.call_args
        params = call_args[0][1]
        assert params["before_state"] is None
        assert params["after_state"] is None
        assert params["metadata"] is None

    @pytest.mark.asyncio
    async def test_log_failure_does_not_raise(self):
        """감사 로그 실패가 메인 로직을 중단시키지 않음"""
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=Exception("DB connection lost"))

        from db.repositories.audit_log import AuditLogger

        logger = AuditLogger(mock_session)
        # Should not raise
        await logger.log(
            action_type="REBALANCING",
            module="portfolio_manager",
            description="리밸런싱 실행",
        )

    @pytest.mark.asyncio
    async def test_log_serializes_state_as_json(self):
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()

        from db.repositories.audit_log import AuditLogger

        logger = AuditLogger(mock_session)
        before = {"price": 50000, "qty": 5}
        after = {"price": 50000, "qty": 15}

        await logger.log(
            action_type="ORDER_FILLED",
            module="executor",
            description="체결 완료",
            before_state=before,
            after_state=after,
        )

        call_args = mock_session.execute.call_args
        params = call_args[0][1]
        # JSON 문자열로 직렬화되어야 함
        parsed_before = json.loads(params["before_state"])
        assert parsed_before["price"] == 50000
        parsed_after = json.loads(params["after_state"])
        assert parsed_after["qty"] == 15


# ============================================================
# logging.py — setup_logging (mock 기반)
# ============================================================
class TestSetupLogging:
    def test_setup_logging_dev(self):
        """개발 환경에서는 콘솔 출력만 설정"""
        mock_settings = MagicMock()
        mock_settings.log_level = "DEBUG"
        mock_settings.is_production = False
        mock_settings.environment = "development"

        with patch("config.logging.get_settings", return_value=mock_settings):
            with patch("config.logging.logger") as mock_logger:
                from config.logging import setup_logging

                setup_logging()
                mock_logger.remove.assert_called_once()
                # 콘솔 1개만 추가 (파일 로그 없음)
                assert mock_logger.add.call_count == 1

    def test_setup_logging_production(self):
        """운영 환경에서는 콘솔 + 파일 2개 설정"""
        mock_settings = MagicMock()
        mock_settings.log_level = "INFO"
        mock_settings.is_production = True
        mock_settings.environment = "production"

        with patch("config.logging.get_settings", return_value=mock_settings):
            with patch("config.logging.logger") as mock_logger:
                from config.logging import setup_logging

                setup_logging()
                mock_logger.remove.assert_called_once()
                # 콘솔 1 + 일반 로그 1 + 에러 로그 1 = 3
                assert mock_logger.add.call_count == 3
