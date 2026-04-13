"""
Comprehensive test coverage for low-coverage API route modules.

Tests cover all branches in:
1. api/routes/market.py
2. api/routes/portfolio.py
3. api/routes/orders.py
4. api/routes/audit.py
5. api/routes/realtime.py
6. api/routes/profile.py
7. api/routes/alerts.py
8. api/routes/param_sensitivity.py
9. api/routes/oos.py
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from config.constants import Market, OrderSide, OrderStatus

# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_user():
    from api.middleware.auth import AuthenticatedUser

    return AuthenticatedUser(id="test_user", username="test_user", role="admin")


@pytest.fixture
def mock_db():
    session = AsyncMock()
    return session


# ══════════════════════════════════════════════════════════════════════════════
# 1. Market Routes (api/routes/market.py)
# ══════════════════════════════════════════════════════════════════════════════


class TestMarketRoutes:
    @pytest.mark.asyncio
    async def test_get_exchange_rate_success(self, mock_user):
        from api.routes.market import get_exchange_rate

        mock_mgr = AsyncMock()
        mock_mgr.get_current_rate.return_value = MagicMock(
            pair="USD/KRW",
            rate=1298.5,
            source="KIS",
            fetched_at=datetime.now(timezone.utc),
        )
        with patch("api.routes.market.ExchangeRateManager", return_value=mock_mgr):
            resp = await get_exchange_rate(current_user=mock_user)
        assert resp.success is True
        assert resp.data["rate"] == 1298.5

    @pytest.mark.asyncio
    async def test_get_exchange_rate_error(self, mock_user):
        from api.routes.market import get_exchange_rate

        mock_mgr = AsyncMock()
        mock_mgr.get_current_rate.side_effect = Exception("fail")
        with patch("api.routes.market.ExchangeRateManager", return_value=mock_mgr):
            resp = await get_exchange_rate(current_user=mock_user)
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_get_market_indices_success(self, mock_user):
        from api.routes.market import get_market_indices

        mock_kis = AsyncMock()
        mock_kis.get_kr_stock_price.return_value = {
            "stck_prpr": "2850",
            "prdy_vrss": "15",
            "prdy_ctrt": "0.5",
        }
        mock_kis.get_us_stock_price.return_value = {
            "last": "450",
            "diff": "2",
            "rate": "0.4",
        }
        with patch("api.routes.market.KISClient", return_value=mock_kis, create=True):
            # KISClient is imported inside function, patch at the import location
            with patch(
                "core.data_collector.kis_client.KISClient",
                return_value=mock_kis,
            ):
                resp = await get_market_indices(current_user=mock_user)
        assert resp.success is True
        assert isinstance(resp.data, list)
        assert len(resp.data) == 4  # 2 KR + 2 US

    @pytest.mark.asyncio
    async def test_get_market_indices_kr_error(self, mock_user):
        """KR API errors → fallback zeros, US succeeds."""
        from api.routes.market import get_market_indices

        mock_kis = AsyncMock()
        mock_kis.get_kr_stock_price.side_effect = Exception("KR fail")
        mock_kis.get_us_stock_price.return_value = {
            "last": "450",
            "diff": "2",
            "rate": "0.4",
        }
        with patch(
            "core.data_collector.kis_client.KISClient",
            return_value=mock_kis,
        ):
            resp = await get_market_indices(current_user=mock_user)
        assert resp.success is True
        # First 2 indices should have value=0 (KR fallback)
        assert resp.data[0]["value"] == 0
        assert resp.data[1]["value"] == 0

    @pytest.mark.asyncio
    async def test_get_market_indices_total_error(self, mock_user):
        from api.routes.market import get_market_indices

        with patch(
            "core.data_collector.kis_client.KISClient",
            side_effect=Exception("total fail"),
        ):
            resp = await get_market_indices(current_user=mock_user)
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_get_economic_indicators_all(self, mock_user):
        """source=None → both FRED and ECOS."""
        from api.routes.market import get_economic_indicators

        mock_svc = MagicMock()
        mock_fred = AsyncMock()
        mock_ecos = AsyncMock()
        mock_fred.collect_all.return_value = [
            MagicMock(
                indicator_name="GDP",
                value=2.5,
                date=datetime(2026, 1, 1),
            )
        ]
        mock_ecos.collect_all.return_value = [
            MagicMock(
                indicator_name="CPI",
                value=3.1,
                date=datetime(2026, 1, 1),
            )
        ]
        mock_svc._fred = mock_fred
        mock_svc._ecos = mock_ecos
        with patch(
            "api.routes.market.EconomicCollectorService",
            return_value=mock_svc,
        ):
            resp = await get_economic_indicators(source=None, current_user=mock_user)
        assert resp.success is True
        assert len(resp.data) == 2

    @pytest.mark.asyncio
    async def test_get_economic_indicators_fred_only(self, mock_user):
        from api.routes.market import get_economic_indicators

        mock_svc = MagicMock()
        mock_fred = AsyncMock()
        mock_fred.collect_all.return_value = [MagicMock(indicator_name="GDP", value=2.5, date=None)]
        mock_svc._fred = mock_fred
        mock_svc._ecos = AsyncMock()
        with patch(
            "api.routes.market.EconomicCollectorService",
            return_value=mock_svc,
        ):
            resp = await get_economic_indicators(source="FRED", current_user=mock_user)
        assert resp.success is True
        assert all(i["source"] == "FRED" for i in resp.data)

    @pytest.mark.asyncio
    async def test_get_economic_indicators_fred_error(self, mock_user):
        """FRED fails but ECOS succeeds → partial data."""
        from api.routes.market import get_economic_indicators

        mock_svc = MagicMock()
        mock_fred = AsyncMock()
        mock_fred.collect_all.side_effect = Exception("FRED down")
        mock_ecos = AsyncMock()
        mock_ecos.collect_all.return_value = [MagicMock(indicator_name="CPI", value=3.1, date=None)]
        mock_svc._fred = mock_fred
        mock_svc._ecos = mock_ecos
        with patch(
            "api.routes.market.EconomicCollectorService",
            return_value=mock_svc,
        ):
            resp = await get_economic_indicators(source=None, current_user=mock_user)
        assert resp.success is True
        assert len(resp.data) == 1  # Only ECOS

    @pytest.mark.asyncio
    async def test_get_universe_with_profile(self, mock_user):
        from api.routes.market import get_universe

        mock_profile_mgr = AsyncMock()
        mock_profile = MagicMock()
        mock_profile_mgr.get_profile.return_value = mock_profile

        mock_universe_mgr = AsyncMock()
        mock_item = MagicMock()
        mock_item.to_dict.return_value = {"ticker": "005930", "name": "삼성전자"}
        mock_universe_mgr.build_universe.return_value = [mock_item]

        with patch(
            "api.routes.market.InvestorProfileManager",
            return_value=mock_profile_mgr,
        ):
            with patch(
                "api.routes.market.UniverseManager",
                return_value=mock_universe_mgr,
            ):
                resp = await get_universe(current_user=mock_user)
        assert resp.success is True
        assert len(resp.data) == 1

    @pytest.mark.asyncio
    async def test_get_universe_no_profile(self, mock_user):
        """No profile → creates default profile."""
        from api.routes.market import get_universe

        mock_profile_mgr = AsyncMock()
        mock_profile_mgr.get_profile.return_value = None

        mock_universe_mgr = AsyncMock()
        mock_universe_mgr.build_universe.return_value = []

        with patch(
            "api.routes.market.InvestorProfileManager",
            return_value=mock_profile_mgr,
        ):
            with patch(
                "api.routes.market.UniverseManager",
                return_value=mock_universe_mgr,
            ):
                resp = await get_universe(current_user=mock_user)
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_get_universe_error(self, mock_user):
        from api.routes.market import get_universe

        with patch(
            "api.routes.market.InvestorProfileManager",
            side_effect=Exception("fail"),
        ):
            resp = await get_universe(current_user=mock_user)
        assert resp.success is False


# ══════════════════════════════════════════════════════════════════════════════
# 2. Portfolio Routes (api/routes/portfolio.py)
# ══════════════════════════════════════════════════════════════════════════════


class TestPortfolioRoutes:
    @pytest.mark.asyncio
    async def test_get_portfolio_summary_with_positions(self, mock_user, mock_db):
        from api.routes.portfolio import get_portfolio_summary

        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("005930", "KRX", 100, 7_100_000, 100),  # net_qty, cost, bought
        ]
        mock_db.execute.return_value = mock_result

        with patch("api.routes.portfolio.get_settings") as mock_settings:
            mock_settings.return_value.risk.initial_capital_krw = 50_000_000
            resp = await get_portfolio_summary(current_user=mock_user, db=mock_db)
        assert resp.success is True
        assert resp.data.position_count == 1

    @pytest.mark.asyncio
    async def test_get_portfolio_summary_db_error(self, mock_user, mock_db):
        from api.routes.portfolio import get_portfolio_summary

        mock_db.execute.side_effect = Exception("DB fail")

        with patch("api.routes.portfolio.get_settings") as mock_settings:
            mock_settings.return_value.risk.initial_capital_krw = 50_000_000
            resp = await get_portfolio_summary(current_user=mock_user, db=mock_db)
        assert resp.success is True  # Falls back to empty positions
        assert resp.data.position_count == 0

    @pytest.mark.asyncio
    async def test_get_positions_with_data(self, mock_user, mock_db):
        from api.routes.portfolio import get_positions

        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("005930", "KRX", 50, 3_550_000, 50),
            ("000660", "KRX", 30, 888_000, 30),
        ]
        mock_db.execute.return_value = mock_result
        resp = await get_positions(current_user=mock_user, db=mock_db)
        assert resp.success is True
        assert len(resp.data) == 2

    @pytest.mark.asyncio
    async def test_get_positions_db_error(self, mock_user, mock_db):
        from api.routes.portfolio import get_positions

        mock_db.execute.side_effect = Exception("DB fail")
        resp = await get_positions(current_user=mock_user, db=mock_db)
        assert resp.success is True
        assert len(resp.data) == 0

    @pytest.mark.asyncio
    async def test_get_performance_default_period(self, mock_user, mock_db):
        from api.routes.portfolio import get_performance

        mock_result = MagicMock()
        mock_result.fetchone.return_value = (15000.0, 10, 5)
        mock_db.execute.return_value = mock_result

        with patch("api.routes.portfolio.get_settings") as mock_settings:
            mock_settings.return_value.risk.initial_capital_krw = 50_000_000
            resp = await get_performance(period="1M", current_user=mock_user, db=mock_db)
        assert resp.success is True
        assert resp.data.period == "1M"

    @pytest.mark.asyncio
    async def test_get_performance_db_error(self, mock_user, mock_db):
        from api.routes.portfolio import get_performance

        mock_db.execute.side_effect = Exception("DB fail")
        resp = await get_performance(period="1Y", current_user=mock_user, db=mock_db)
        assert resp.success is True
        assert resp.data.return_pct == 0.0

    @pytest.mark.asyncio
    async def test_get_value_history_with_data(self, mock_user, mock_db):
        from datetime import date

        from api.routes.portfolio import get_value_history

        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            (date(2026, 4, 1), -500_000.0),
            (date(2026, 4, 2), 200_000.0),
        ]
        mock_db.execute.return_value = mock_result

        with patch("api.routes.portfolio.get_settings") as mock_settings:
            mock_settings.return_value.risk.initial_capital_krw = 50_000_000
            resp = await get_value_history(period="1W", current_user=mock_user, db=mock_db)
        assert resp.success is True
        assert len(resp.data) == 2

    @pytest.mark.asyncio
    async def test_get_value_history_db_error(self, mock_user, mock_db):
        from api.routes.portfolio import get_value_history

        mock_db.execute.side_effect = Exception("DB fail")
        resp = await get_value_history(period="1M", current_user=mock_user, db=mock_db)
        assert resp.success is True
        assert len(resp.data) == 0


class TestPortfolioConstructRoute:
    """POST /api/portfolio/construct 엔드포인트 테스트"""

    @pytest.mark.asyncio
    async def test_construct_success(self, mock_user, mock_db):
        """앙상블 시그널 기반 포트폴리오 구성 성공"""
        from api.routes.portfolio import construct_portfolio
        from api.schemas.portfolio import ConstructionRequest

        # Redis mock: 앙상블 시그널 2종목
        mock_redis = AsyncMock()
        mock_redis.keys.return_value = [
            "ensemble:latest:005930",
            "ensemble:latest:000660",
        ]
        mock_redis.get.side_effect = [
            '{"ensemble_signal": 0.35, "regime": "TRENDING_UP"}',
            '{"ensemble_signal": -0.10, "regime": "SIDEWAYS"}',
        ]

        # DB mock: 유니버스 조회 + 빈 포지션
        universe_result = MagicMock()
        universe_result.fetchall.return_value = [
            ("005930", "IT", "KRX"),
            ("000660", "Semiconductor", "KRX"),
        ]
        position_result = MagicMock()
        position_result.fetchall.return_value = []
        mock_db.execute.side_effect = [universe_result, position_result]

        with (
            patch("api.routes.portfolio.RedisManager") as mock_rm,
            patch("api.routes.portfolio.get_settings") as mock_settings,
        ):
            mock_rm.get_client.return_value = mock_redis
            mock_settings.return_value.risk.initial_capital_krw = 50_000_000

            req = ConstructionRequest(
                method="mean_variance",
                risk_profile="BALANCED",
                seed_capital=50_000_000,
            )
            resp = await construct_portfolio(req=req, current_user=mock_user, db=mock_db)

        assert resp.success is True
        assert resp.data.optimization_method == "mean_variance"
        assert resp.data.stock_count >= 0
        assert 0.0 <= resp.data.cash_ratio <= 1.0

    @pytest.mark.asyncio
    async def test_construct_no_signals(self, mock_user, mock_db):
        """앙상블 시그널 없을 때 실패 응답"""
        from api.routes.portfolio import construct_portfolio
        from api.schemas.portfolio import ConstructionRequest

        mock_redis = AsyncMock()
        mock_redis.keys.return_value = []

        with (
            patch("api.routes.portfolio.RedisManager") as mock_rm,
            patch("api.routes.portfolio.get_settings") as mock_settings,
        ):
            mock_rm.get_client.return_value = mock_redis
            mock_settings.return_value.risk.initial_capital_krw = 50_000_000

            req = ConstructionRequest()
            resp = await construct_portfolio(req=req, current_user=mock_user, db=mock_db)

        assert resp.success is False
        assert "앙상블 시그널이 없습니다" in resp.message

    @pytest.mark.asyncio
    async def test_construct_invalid_risk_profile(self, mock_user, mock_db):
        """유효하지 않은 risk_profile 거부"""
        from api.routes.portfolio import construct_portfolio
        from api.schemas.portfolio import ConstructionRequest

        with patch("api.routes.portfolio.get_settings") as mock_settings:
            mock_settings.return_value.risk.initial_capital_krw = 50_000_000

            req = ConstructionRequest(risk_profile="INVALID_PROFILE")
            resp = await construct_portfolio(req=req, current_user=mock_user, db=mock_db)

        assert resp.success is False
        assert "유효하지 않은 risk_profile" in resp.message


# ══════════════════════════════════════════════════════════════════════════════
# 3. Orders Routes (api/routes/orders.py)
# ══════════════════════════════════════════════════════════════════════════════


class TestOrderRoutes:
    @pytest.mark.asyncio
    async def test_order_result_to_response(self):
        from api.routes.orders import _order_result_to_response

        mock_result = MagicMock()
        mock_result.order_id = "ORD-001"
        mock_result.ticker = "005930"
        mock_result.market = Market.KRX
        mock_result.side = OrderSide.BUY
        mock_result.quantity = 10
        mock_result.status = OrderStatus.FILLED
        mock_result.avg_price = 71000.0
        mock_result.executed_at = datetime.now(timezone.utc)

        resp = _order_result_to_response(mock_result, "MARKET", "test")
        assert resp.order_id == "ORD-001"
        assert resp.status == "FILLED"

    @pytest.mark.asyncio
    async def test_create_order_success(self, mock_user, mock_db):
        from api.routes.orders import create_order
        from api.schemas.orders import OrderCreateRequest

        body = OrderCreateRequest(
            ticker="005930",
            market="KRX",
            side="BUY",
            quantity=10,
            order_type="MARKET",
            reason="test buy",
        )

        mock_result = MagicMock()
        mock_result.order_id = "ORD-001"
        mock_result.ticker = "005930"
        mock_result.market = Market.KRX
        mock_result.side = OrderSide.BUY
        mock_result.quantity = 10
        mock_result.status = OrderStatus.FILLED
        mock_result.avg_price = 71000.0
        mock_result.executed_at = datetime.now(timezone.utc)
        mock_result.to_dict.return_value = {}

        mock_executor = AsyncMock()
        mock_executor.execute_order.return_value = mock_result

        mock_request = MagicMock()
        mock_request.state = MagicMock()
        mock_request.app = MagicMock()
        mock_request.client = MagicMock(host="127.0.0.1")
        mock_request.headers = {}

        mock_audit_instance = AsyncMock()
        mock_audit_cls = MagicMock(return_value=mock_audit_instance)

        with patch("api.routes.orders.OrderExecutor", return_value=mock_executor):
            with patch("api.routes.orders.AuditLogger", mock_audit_cls):
                with patch("api.routes.orders.limiter"):
                    order_user = SimpleNamespace(id="user-cov-1", username="u", role="operator")
                    resp = await create_order(
                        request=mock_request,
                        order_body=body,
                        idempotency_key="cov-create-order-success-001",
                        current_user=order_user,
                        db=mock_db,
                    )
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_create_order_error(self, mock_user, mock_db):
        from api.routes.orders import create_order
        from api.schemas.orders import OrderCreateRequest

        body = OrderCreateRequest(
            ticker="005930",
            market="KRX",
            side="BUY",
            quantity=10,
            order_type="MARKET",
        )

        mock_executor = AsyncMock()
        mock_executor.execute_order.side_effect = Exception("exec fail")

        mock_request = MagicMock()
        mock_request.state = MagicMock()
        mock_request.app = MagicMock()
        mock_request.client = MagicMock(host="127.0.0.1")
        mock_request.headers = {}

        with patch("api.routes.orders.OrderExecutor", return_value=mock_executor):
            with patch("api.routes.orders.limiter"):
                order_user = SimpleNamespace(id="user-cov-2", username="u", role="operator")
                resp = await create_order(
                    request=mock_request,
                    order_body=body,
                    idempotency_key="cov-create-order-error-001",
                    current_user=order_user,
                    db=mock_db,
                )
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_get_orders_no_filter(self, mock_user, mock_db):
        from api.routes.orders import get_orders

        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            (
                "ORD-001",
                "005930",
                "KRX",
                "BUY",
                10,
                10,
                71000.0,
                "FILLED",
                datetime.now(timezone.utc),
                None,
            )
        ]
        mock_db.execute.return_value = mock_result
        resp = await get_orders(status=None, limit=50, current_user=mock_user, db=mock_db)
        assert resp.success is True
        assert len(resp.data) == 1

    @pytest.mark.asyncio
    async def test_get_orders_with_status_filter(self, mock_user, mock_db):
        from api.routes.orders import get_orders

        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_db.execute.return_value = mock_result
        resp = await get_orders(status="FILLED", limit=50, current_user=mock_user, db=mock_db)
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_get_orders_db_error(self, mock_user, mock_db):
        from api.routes.orders import get_orders

        mock_db.execute.side_effect = Exception("DB fail")
        resp = await get_orders(status=None, limit=50, current_user=mock_user, db=mock_db)
        assert resp.success is True
        assert len(resp.data) == 0

    @pytest.mark.asyncio
    async def test_get_order_found(self, mock_user, mock_db):
        from api.routes.orders import get_order

        mock_result = MagicMock()
        mock_result.fetchone.return_value = (
            "ORD-001",
            "005930",
            "KRX",
            "BUY",
            10,
            10,
            71000.0,
            "FILLED",
            datetime.now(timezone.utc),
            None,
        )
        mock_db.execute.return_value = mock_result
        resp = await get_order(order_id="ORD-001", current_user=mock_user, db=mock_db)
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_get_order_not_found(self, mock_user, mock_db):
        from api.routes.orders import get_order

        mock_result = MagicMock()
        mock_result.fetchone.return_value = None
        mock_db.execute.return_value = mock_result
        with pytest.raises(HTTPException) as exc:
            await get_order(order_id="NOTFOUND", current_user=mock_user, db=mock_db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_cancel_order_success(self, mock_user, mock_db):
        from api.routes.orders import cancel_order

        # First call: check status → PENDING
        # Second call: update
        mock_result = MagicMock()
        mock_result.fetchone.return_value = ("PENDING",)
        mock_db.execute.return_value = mock_result

        mock_audit_instance = AsyncMock()
        mock_audit_cls = MagicMock(return_value=mock_audit_instance)

        with patch("api.routes.orders.AuditLogger", mock_audit_cls):
            resp = await cancel_order(order_id="ORD-001", current_user=mock_user, db=mock_db)
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_cancel_order_not_found(self, mock_user, mock_db):
        from api.routes.orders import cancel_order

        mock_result = MagicMock()
        mock_result.fetchone.return_value = None
        mock_db.execute.return_value = mock_result
        with pytest.raises(HTTPException) as exc:
            await cancel_order(order_id="NOTFOUND", current_user=mock_user, db=mock_db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_cancel_order_non_cancellable(self, mock_user, mock_db):
        """FILLED 주문 취소 시 OrderStateMachine 이 409 로 차단한다.

        근거: docs/security/security-integrity-roadmap.md §7.3 — 종결 상태
        (FILLED/CANCELLED/FAILED) 에서의 취소 시도는 HTTPException(409,
        INVALID_ORDER_TRANSITION) 으로 fail-closed 거부된다. 이전 구현은
        200 + success=False 로 응답했으나 HTTP 의미와 맞지 않아 교체되었다.
        """
        from fastapi import HTTPException

        from api.routes.orders import cancel_order

        mock_result = MagicMock()
        mock_result.fetchone.return_value = ("FILLED",)
        mock_db.execute.return_value = mock_result
        with pytest.raises(HTTPException) as excinfo:
            await cancel_order(order_id="ORD-001", current_user=mock_user, db=mock_db)
        assert excinfo.value.status_code == 409
        assert excinfo.value.detail["error_code"] == "INVALID_ORDER_TRANSITION"
        assert excinfo.value.detail["context"]["current_status"] == "FILLED"
        assert excinfo.value.detail["context"]["target_status"] == "CANCELLED"


# ══════════════════════════════════════════════════════════════════════════════
# 4. Audit Routes (api/routes/audit.py)
# ══════════════════════════════════════════════════════════════════════════════


class TestAuditRoutes:
    @pytest.mark.asyncio
    async def test_create_decision_success(self, mock_user):
        from api.routes.audit import create_decision

        mock_store = MagicMock()
        mock_record = MagicMock()
        mock_store.create.return_value = mock_record

        with patch("api.routes.audit.get_decision_store", return_value=mock_store):
            resp = await create_decision(decision_id=None, current_user=mock_user)
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_create_decision_with_id(self, mock_user):
        from api.routes.audit import create_decision

        mock_store = MagicMock()
        mock_store.create.return_value = MagicMock()

        with patch("api.routes.audit.get_decision_store", return_value=mock_store):
            resp = await create_decision(decision_id="DEC-001", current_user=mock_user)
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_create_decision_error(self, mock_user):
        from api.routes.audit import create_decision

        mock_store = MagicMock()
        mock_store.create.side_effect = Exception("fail")

        with patch("api.routes.audit.get_decision_store", return_value=mock_store):
            resp = await create_decision(decision_id=None, current_user=mock_user)
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_get_decision_found(self, mock_user):
        from api.routes.audit import get_decision

        mock_store = MagicMock()
        mock_store.get.return_value = MagicMock()

        with patch("api.routes.audit.get_decision_store", return_value=mock_store):
            resp = await get_decision(decision_id="DEC-001", current_user=mock_user)
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_get_decision_not_found(self, mock_user):
        from api.routes.audit import get_decision

        mock_store = MagicMock()
        mock_store.get.return_value = None

        with patch("api.routes.audit.get_decision_store", return_value=mock_store):
            resp = await get_decision(decision_id="NOTFOUND", current_user=mock_user)
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_list_decisions_no_filter(self, mock_user):
        from api.routes.audit import list_decisions

        mock_store = MagicMock()
        mock_store.query.return_value = [MagicMock(), MagicMock()]

        with patch("api.routes.audit.get_decision_store", return_value=mock_store):
            resp = await list_decisions(
                start_date=None,
                end_date=None,
                limit=100,
                current_user=mock_user,
            )
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_list_decisions_with_date_range(self, mock_user):
        from api.routes.audit import list_decisions

        mock_store = MagicMock()
        mock_store.query.return_value = []

        with patch("api.routes.audit.get_decision_store", return_value=mock_store):
            resp = await list_decisions(
                start_date="2026-03-01",
                end_date="2026-04-01",
                limit=100,
                current_user=mock_user,
            )
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_list_decisions_invalid_start_date(self, mock_user):
        from api.routes.audit import list_decisions

        mock_store = MagicMock()

        with patch("api.routes.audit.get_decision_store", return_value=mock_store):
            resp = await list_decisions(
                start_date="bad-date",
                end_date=None,
                limit=100,
                current_user=mock_user,
            )
        assert resp.success is False
        assert "Invalid start_date" in resp.message

    @pytest.mark.asyncio
    async def test_list_decisions_invalid_end_date(self, mock_user):
        from api.routes.audit import list_decisions

        mock_store = MagicMock()

        with patch("api.routes.audit.get_decision_store", return_value=mock_store):
            resp = await list_decisions(
                start_date=None,
                end_date="bad-date",
                limit=100,
                current_user=mock_user,
            )
        assert resp.success is False
        assert "Invalid end_date" in resp.message

    @pytest.mark.asyncio
    async def test_update_decision_step_success(self, mock_user):
        from api.routes.audit import update_decision_step

        mock_store = MagicMock()
        mock_store.update_step.return_value = MagicMock()

        with patch("api.routes.audit.get_decision_store", return_value=mock_store):
            resp = await update_decision_step(
                decision_id="DEC-001",
                step_name="step2_features",
                step_data={"features": [1, 2, 3]},
                current_user=mock_user,
            )
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_update_decision_step_not_found(self, mock_user):
        from api.routes.audit import update_decision_step

        mock_store = MagicMock()
        mock_store.update_step.return_value = None

        with patch("api.routes.audit.get_decision_store", return_value=mock_store):
            resp = await update_decision_step(
                decision_id="NOTFOUND",
                step_name="step1_input_snapshot",
                step_data={},
                current_user=mock_user,
            )
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_update_decision_step_invalid_step(self, mock_user):
        from api.routes.audit import update_decision_step

        mock_store = MagicMock()
        mock_store.update_step.side_effect = ValueError("Invalid step")

        with patch("api.routes.audit.get_decision_store", return_value=mock_store):
            resp = await update_decision_step(
                decision_id="DEC-001",
                step_name="invalid_step",
                step_data={},
                current_user=mock_user,
            )
        assert resp.success is False


# ══════════════════════════════════════════════════════════════════════════════
# 5. Realtime Routes (api/routes/realtime.py)
# ══════════════════════════════════════════════════════════════════════════════


class TestRealtimeRoutes:
    @pytest.mark.asyncio
    async def test_get_all_quotes_manager_none(self, mock_user):
        from api.routes.realtime import get_all_quotes

        with patch(
            "core.scheduler_handlers.get_realtime_manager",
            return_value=None,
        ):
            resp = await get_all_quotes(current_user=mock_user)
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_get_all_quotes_not_running(self, mock_user):
        from api.routes.realtime import get_all_quotes

        mock_mgr = MagicMock()
        mock_mgr.is_running = False

        with patch(
            "core.scheduler_handlers.get_realtime_manager",
            return_value=mock_mgr,
        ):
            resp = await get_all_quotes(current_user=mock_user)
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_get_all_quotes_success(self, mock_user):
        from api.routes.realtime import get_all_quotes

        mock_mgr = MagicMock()
        mock_mgr.is_running = True
        mock_snap = MagicMock()
        mock_snap.price = 71400
        mock_snap.to_dict.return_value = {"price": 71400}
        mock_mgr.get_all_snapshots.return_value = {"005930": mock_snap}

        with patch(
            "core.scheduler_handlers.get_realtime_manager",
            return_value=mock_mgr,
        ):
            resp = await get_all_quotes(current_user=mock_user)
        assert resp.success is True
        assert resp.data["count"] == 1

    @pytest.mark.asyncio
    async def test_get_ticker_quote_manager_none(self, mock_user):
        from api.routes.realtime import get_ticker_quote

        with patch(
            "core.scheduler_handlers.get_realtime_manager",
            return_value=None,
        ):
            resp = await get_ticker_quote(ticker="005930", current_user=mock_user)
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_get_ticker_quote_not_subscribed(self, mock_user):
        from api.routes.realtime import get_ticker_quote

        mock_mgr = MagicMock()
        mock_mgr.is_running = True
        mock_mgr.get_snapshot.return_value = None

        with patch(
            "core.scheduler_handlers.get_realtime_manager",
            return_value=mock_mgr,
        ):
            resp = await get_ticker_quote(ticker="UNKNOWN", current_user=mock_user)
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_get_ticker_quote_found(self, mock_user):
        from api.routes.realtime import get_ticker_quote

        mock_mgr = MagicMock()
        mock_mgr.is_running = True
        mock_snap = MagicMock()
        mock_snap.to_dict.return_value = {"ticker": "005930", "price": 71400}
        mock_mgr.get_snapshot.return_value = mock_snap

        with patch(
            "core.scheduler_handlers.get_realtime_manager",
            return_value=mock_mgr,
        ):
            resp = await get_ticker_quote(ticker="005930", current_user=mock_user)
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_get_realtime_status_manager_none(self, mock_user):
        from api.routes.realtime import get_realtime_status

        with patch(
            "core.scheduler_handlers.get_realtime_manager",
            return_value=None,
        ):
            resp = await get_realtime_status(current_user=mock_user)
        assert resp.success is True
        assert resp.data["running"] is False

    @pytest.mark.asyncio
    async def test_get_realtime_status_with_data(self, mock_user):
        from api.routes.realtime import get_realtime_status

        mock_mgr = MagicMock()
        mock_mgr.stats = {"running": True, "tickers": 10}

        with patch(
            "core.scheduler_handlers.get_realtime_manager",
            return_value=mock_mgr,
        ):
            resp = await get_realtime_status(current_user=mock_user)
        assert resp.success is True


# ══════════════════════════════════════════════════════════════════════════════
# 6. Profile Routes (api/routes/profile.py)
# ══════════════════════════════════════════════════════════════════════════════


class TestProfileRoutes:
    @pytest.mark.asyncio
    async def test_get_profile_found(self, mock_user):
        from api.routes.profile import get_profile
        from config.constants import InvestmentStyle, RiskProfile

        mock_mgr = AsyncMock()
        mock_profile = MagicMock()
        mock_profile.risk_profile = RiskProfile.BALANCED
        mock_profile.investment_style = InvestmentStyle.ADVISORY
        mock_profile.investment_goal = "WEALTH_GROWTH"
        mock_profile.seed_amount = 50_000_000
        mock_profile.loss_tolerance = 0.10
        mock_profile.created_at = datetime.now(timezone.utc)
        mock_profile.updated_at = datetime.now(timezone.utc)
        mock_mgr.get_profile.return_value = mock_profile

        with patch(
            "api.routes.profile.InvestorProfileManager",
            return_value=mock_mgr,
        ):
            resp = await get_profile(current_user=mock_user)
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_get_profile_not_found_default(self, mock_user):
        from api.routes.profile import get_profile

        mock_mgr = AsyncMock()
        mock_mgr.get_profile.return_value = None

        with patch(
            "api.routes.profile.InvestorProfileManager",
            return_value=mock_mgr,
        ):
            resp = await get_profile(current_user=mock_user)
        assert resp.success is True
        assert "기본 프로필" in resp.message

    @pytest.mark.asyncio
    async def test_get_profile_error(self, mock_user):
        from api.routes.profile import get_profile

        mock_mgr = AsyncMock()
        mock_mgr.get_profile.side_effect = Exception("fail")

        with patch(
            "api.routes.profile.InvestorProfileManager",
            return_value=mock_mgr,
        ):
            resp = await get_profile(current_user=mock_user)
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_update_profile_new(self, mock_user):
        from api.routes.profile import update_profile
        from api.schemas.profile import ProfileUpdateRequest
        from config.constants import InvestmentStyle, RiskProfile

        body = ProfileUpdateRequest(
            risk_profile="AGGRESSIVE",
            investment_style="DISCRETIONARY",
            investment_goal="WEALTH_GROWTH",
            initial_capital=100_000_000,
            max_loss_tolerance=0.20,
        )

        mock_mgr = AsyncMock()
        mock_mgr.get_profile.return_value = None  # no existing profile
        mock_new = MagicMock()
        mock_new.risk_profile = RiskProfile.AGGRESSIVE
        mock_new.investment_style = InvestmentStyle.DISCRETIONARY
        mock_new.investment_goal = "WEALTH_GROWTH"
        mock_new.seed_amount = 100_000_000
        mock_new.loss_tolerance = 0.20
        mock_new.created_at = datetime.now(timezone.utc)
        mock_new.updated_at = datetime.now(timezone.utc)
        mock_mgr.create_profile.return_value = mock_new

        with patch(
            "api.routes.profile.InvestorProfileManager",
            return_value=mock_mgr,
        ):
            resp = await update_profile(request=body, current_user=mock_user)
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_update_profile_existing(self, mock_user):
        from api.routes.profile import update_profile
        from api.schemas.profile import ProfileUpdateRequest
        from config.constants import InvestmentStyle, RiskProfile

        body = ProfileUpdateRequest(
            risk_profile="BALANCED",
            investment_style="ADVISORY",
            investment_goal="WEALTH_GROWTH",
            initial_capital=50_000_000,
            max_loss_tolerance=0.15,
        )

        mock_mgr = AsyncMock()
        existing = MagicMock()
        mock_mgr.get_profile.return_value = existing
        updated = MagicMock()
        updated.risk_profile = RiskProfile.BALANCED
        updated.investment_style = InvestmentStyle.ADVISORY
        updated.investment_goal = "WEALTH_GROWTH"
        updated.seed_amount = 50_000_000
        updated.loss_tolerance = 0.15
        updated.created_at = datetime.now(timezone.utc)
        updated.updated_at = datetime.now(timezone.utc)
        mock_mgr.update_profile.return_value = updated

        with patch(
            "api.routes.profile.InvestorProfileManager",
            return_value=mock_mgr,
        ):
            resp = await update_profile(request=body, current_user=mock_user)
        assert resp.success is True


# ══════════════════════════════════════════════════════════════════════════════
# 7. Alerts Routes (api/routes/alerts.py)
# ══════════════════════════════════════════════════════════════════════════════


class TestAlertsRoutes:
    @pytest.mark.asyncio
    async def test_get_alerts_no_filter(self, mock_user):
        from api.routes.alerts import get_alerts

        mock_mgr = AsyncMock()
        mock_mgr.get_alerts.return_value = []
        mock_mgr.get_unread_count.return_value = 0

        resp = await get_alerts(
            limit=50,
            offset=0,
            alert_type=None,
            level=None,
            current_user=mock_user,
            manager=mock_mgr,
        )
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_get_alerts_with_filters(self, mock_user):
        from api.routes.alerts import get_alerts

        mock_mgr = AsyncMock()
        mock_mgr.get_alerts.return_value = []
        mock_mgr.get_unread_count.return_value = 0

        resp = await get_alerts(
            limit=10,
            offset=0,
            alert_type="DAILY_REPORT",
            level="INFO",
            current_user=mock_user,
            manager=mock_mgr,
        )
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_get_alerts_error(self, mock_user):
        from api.routes.alerts import get_alerts

        mock_mgr = AsyncMock()
        mock_mgr.get_alerts.side_effect = Exception("fail")

        resp = await get_alerts(
            limit=50,
            offset=0,
            alert_type=None,
            level=None,
            current_user=mock_user,
            manager=mock_mgr,
        )
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_get_alert_stats(self, mock_user):
        from api.routes.alerts import get_alert_stats

        mock_mgr = AsyncMock()
        mock_mgr.get_alert_stats.return_value = {
            "total": 50,
            "unread": 10,
            "by_type": {},
            "by_level": {},
        }

        resp = await get_alert_stats(current_user=mock_user, manager=mock_mgr)
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_mark_alert_read_success(self, mock_user):
        from api.routes.alerts import mark_alert_read

        mock_mgr = AsyncMock()
        mock_mgr.mark_alert_read.return_value = True

        resp = await mark_alert_read(
            alert_id="ALERT-001",
            current_user=mock_user,
            manager=mock_mgr,
        )
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_mark_alert_read_not_found(self, mock_user):
        from api.routes.alerts import mark_alert_read

        mock_mgr = AsyncMock()
        mock_mgr.mark_alert_read.return_value = False

        resp = await mark_alert_read(
            alert_id="NOTFOUND",
            current_user=mock_user,
            manager=mock_mgr,
        )
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_mark_all_alerts_read(self, mock_user):
        from api.routes.alerts import mark_all_alerts_read

        mock_mgr = AsyncMock()
        mock_mgr.mark_all_read.return_value = 5

        resp = await mark_all_alerts_read(current_user=mock_user, manager=mock_mgr)
        assert resp.success is True


# ══════════════════════════════════════════════════════════════════════════════
# 8. Param Sensitivity Routes (api/routes/param_sensitivity.py)
# ══════════════════════════════════════════════════════════════════════════════


class TestParamSensitivityRoutes:
    @pytest.mark.asyncio
    async def test_run_sensitivity_success(self):
        from api.routes.param_sensitivity import run_sensitivity

        request = MagicMock()
        request.strategy_version = "v1.0"
        request.tickers = ["005930"]
        request.sweep_method = "oat"

        mock_run = MagicMock()
        mock_run.to_summary_dict.return_value = {"status": "completed"}

        mock_engine = MagicMock()
        mock_engine.run.return_value = mock_run

        with patch(
            "api.routes.param_sensitivity.ParamSensitivityEngine",
            return_value=mock_engine,
        ):
            with patch(
                "api.routes.param_sensitivity._generate_sample_data",
                return_value=(MagicMock(), MagicMock()),
            ):
                resp = await run_sensitivity(request=request)
        assert resp["status"] == "success"

    @pytest.mark.asyncio
    async def test_get_latest_no_run(self):
        import api.routes.param_sensitivity as ps_module
        from api.routes.param_sensitivity import get_latest

        ps_module._latest_run = None
        with pytest.raises(HTTPException) as exc:
            await get_latest()
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_latest_with_run(self):
        import api.routes.param_sensitivity as ps_module
        from api.routes.param_sensitivity import get_latest

        mock_run = MagicMock()
        mock_run.to_dict.return_value = {"run_id": "RUN-001"}
        ps_module._latest_run = mock_run

        resp = await get_latest()
        assert resp["status"] == "success"
        ps_module._latest_run = None  # cleanup

    @pytest.mark.asyncio
    async def test_get_tornado_no_run(self):
        import api.routes.param_sensitivity as ps_module
        from api.routes.param_sensitivity import get_tornado

        ps_module._latest_run = None
        with pytest.raises(HTTPException) as exc:
            await get_tornado(metric="sharpe")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_tornado_invalid_metric(self):
        import api.routes.param_sensitivity as ps_module
        from api.routes.param_sensitivity import get_tornado

        ps_module._latest_run = MagicMock()
        with pytest.raises(HTTPException) as exc:
            await get_tornado(metric="invalid")
        assert exc.value.status_code == 400
        ps_module._latest_run = None

    @pytest.mark.asyncio
    async def test_get_tornado_valid_metric(self):
        import api.routes.param_sensitivity as ps_module
        from api.routes.param_sensitivity import get_tornado

        mock_run = MagicMock()
        mock_run.param_ranges = []
        mock_run.elasticities = {}
        ps_module._latest_run = mock_run

        with patch("api.routes.param_sensitivity.SensitivityAnalyzer") as mock_cls:
            mock_analyzer = MagicMock()
            mock_analyzer.tornado_ranking.return_value = [{"param": "threshold", "impact": 0.5}]
            mock_cls.return_value = mock_analyzer
            resp = await get_tornado(metric="sharpe")

        assert resp["status"] == "success"
        ps_module._latest_run = None


# ══════════════════════════════════════════════════════════════════════════════
# 9. OOS Routes (api/routes/oos.py)
# ══════════════════════════════════════════════════════════════════════════════


class TestOOSRoutes:
    @pytest.mark.asyncio
    async def test_create_oos_run_new(self, mock_user):
        from api.routes.oos import create_oos_run
        from core.oos.models import OOSRunRequest

        run_request = OOSRunRequest(
            strategy_version="v1.0",
            train_months=12,
            test_months=3,
            tickers=["005930"],
        )

        mock_result = MagicMock()
        mock_result.run_id = "OOS-001"
        mock_result.status = MagicMock(value="COMPLETED")
        mock_result.overall_gate = "PASS"

        mock_mgr = MagicMock()
        mock_mgr.find_existing_run.return_value = None
        mock_mgr.submit_run.return_value = mock_result

        mock_request = MagicMock()

        with patch("api.routes.oos.OOSJobManager", return_value=mock_mgr):
            with patch(
                "api.routes.oos._generate_sample_data",
                return_value=(MagicMock(), MagicMock()),
            ):
                resp = await create_oos_run(
                    request=mock_request,
                    run_request=run_request,
                    current_user=mock_user,
                )
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_create_oos_run_existing(self, mock_user):
        from api.routes.oos import create_oos_run
        from core.oos.models import OOSRunRequest

        run_request = OOSRunRequest(
            strategy_version="v1.0",
            train_months=12,
            test_months=3,
            tickers=["005930"],
        )

        existing = MagicMock()
        existing.run_id = "OOS-001"
        existing.status = MagicMock(value="COMPLETED")

        mock_mgr = MagicMock()
        mock_mgr.find_existing_run.return_value = existing

        mock_request = MagicMock()

        with patch("api.routes.oos.OOSJobManager", return_value=mock_mgr):
            resp = await create_oos_run(
                request=mock_request,
                run_request=run_request,
                current_user=mock_user,
            )
        assert resp.success is True
        assert resp.data["run_id"] == "OOS-001"

    @pytest.mark.asyncio
    async def test_create_oos_run_error(self, mock_user):
        from api.routes.oos import create_oos_run
        from core.oos.models import OOSRunRequest

        run_request = OOSRunRequest(
            strategy_version="v1.0",
            train_months=12,
            test_months=3,
            tickers=["005930"],
        )

        mock_mgr = MagicMock()
        mock_mgr.find_existing_run.side_effect = Exception("fail")

        mock_request = MagicMock()

        with patch("api.routes.oos.OOSJobManager", return_value=mock_mgr):
            resp = await create_oos_run(
                request=mock_request,
                run_request=run_request,
                current_user=mock_user,
            )
        assert resp.success is False

    @pytest.mark.asyncio
    async def test_get_latest_oos_run_none(self, mock_user):
        from api.routes.oos import get_latest_oos_run

        mock_mgr = MagicMock()
        mock_mgr.get_latest.return_value = None

        with patch("api.routes.oos.OOSJobManager", return_value=mock_mgr):
            resp = await get_latest_oos_run(current_user=mock_user)
        assert resp.success is True
        assert "No OOS runs" in resp.data["message"]

    @pytest.mark.asyncio
    async def test_get_latest_oos_run_with_data(self, mock_user):
        from api.routes.oos import get_latest_oos_run

        mock_run = MagicMock()
        mock_run.to_dict.return_value = {"run_id": "OOS-001", "status": "COMPLETED"}

        mock_mgr = MagicMock()
        mock_mgr.get_latest.return_value = mock_run

        with patch("api.routes.oos.OOSJobManager", return_value=mock_mgr):
            resp = await get_latest_oos_run(current_user=mock_user)
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_get_oos_gate_status(self, mock_user):
        from api.routes.oos import get_oos_gate_status

        mock_mgr = MagicMock()
        mock_mgr.get_gate_status.return_value = {"overall": "PASS"}

        with patch("api.routes.oos.OOSJobManager", return_value=mock_mgr):
            resp = await get_oos_gate_status(current_user=mock_user)
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_get_oos_run_found(self, mock_user):
        from api.routes.oos import get_oos_run

        mock_run = MagicMock()
        mock_run.to_dict.return_value = {"run_id": "OOS-001"}

        mock_mgr = MagicMock()
        mock_mgr.get_run.return_value = mock_run

        with patch("api.routes.oos.OOSJobManager", return_value=mock_mgr):
            resp = await get_oos_run(run_id="OOS-001", current_user=mock_user)
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_get_oos_run_not_found(self, mock_user):
        from api.routes.oos import get_oos_run

        mock_mgr = MagicMock()
        mock_mgr.get_run.return_value = None

        with patch("api.routes.oos.OOSJobManager", return_value=mock_mgr):
            resp = await get_oos_run(run_id="NOTFOUND", current_user=mock_user)
        assert resp.success is False
