"""
Unit tests for AQTS Phase 5 API layer

Tests cover:
1. Schema validation and creation
2. AuthService (password hashing, JWT token creation/verification)
3. API route integration tests
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, status
from jose import jwt

# Auth and middleware
from api.middleware.auth import AuthService
from api.schemas.alerts import AlertResponse, AlertStatsResponse
from api.schemas.auth import LoginRequest, RefreshTokenRequest, TokenResponse

# Schemas
from api.schemas.common import APIResponse, ErrorResponse, PaginatedResponse
from api.schemas.orders import BatchOrderResponse, OrderCreateRequest, OrderResponse
from api.schemas.portfolio import PerformanceResponse, PortfolioSummaryResponse, PositionResponse
from api.schemas.profile import ProfileResponse

# Settings
from config.settings import get_settings

# ══════════════════════════════════════════════════════════════════════════════
# Schema Tests (12+ tests)
# ══════════════════════════════════════════════════════════════════════════════


class TestCommonSchemas:
    """Test common response schemas."""

    def test_api_response_with_data(self):
        """Test APIResponse creation with data."""
        data = {"key": "value"}
        response = APIResponse(success=True, data=data, message="Success")

        assert response.success is True
        assert response.data == data
        assert response.message == "Success"
        assert response.timestamp is not None
        assert isinstance(response.timestamp, datetime)

    def test_api_response_success_flag(self):
        """Test APIResponse with success=False."""
        response = APIResponse(success=False, message="Failed")

        assert response.success is False
        assert response.data is None
        assert response.message == "Failed"

    def test_api_response_timestamp_utc(self):
        """Test APIResponse timestamp is created with UTC time."""
        before = datetime.utcnow()
        response = APIResponse(success=True, data=None)
        after = datetime.utcnow()

        assert before <= response.timestamp <= after

    def test_paginated_response_creation(self):
        """Test PaginatedResponse creation."""
        items = [{"id": 1}, {"id": 2}, {"id": 3}]
        response = PaginatedResponse(items=items, total=100, page=1, page_size=3)

        assert response.items == items
        assert response.total == 100
        assert response.page == 1
        assert response.page_size == 3

    def test_paginated_response_second_page(self):
        """Test PaginatedResponse with page=2."""
        response = PaginatedResponse(items=[], total=100, page=2, page_size=50)

        assert response.page == 2
        assert response.page_size == 50
        assert response.items == []

    def test_error_response_creation(self):
        """Test ErrorResponse creation."""
        response = ErrorResponse(error_code="VALIDATION_ERROR", detail="Invalid input provided")

        assert response.error_code == "VALIDATION_ERROR"
        assert response.detail == "Invalid input provided"

    def test_error_response_unauthorized(self):
        """Test ErrorResponse for unauthorized error."""
        response = ErrorResponse(error_code="UNAUTHORIZED", detail="Invalid authentication credentials")

        assert response.error_code == "UNAUTHORIZED"


class TestAuthSchemas:
    """Test authentication-related schemas."""

    def test_login_request_validation_min_length(self):
        """Test LoginRequest password validation with min_length=1."""
        # Valid password with username
        request = LoginRequest(username="admin", password="test-dashboard-password")
        assert request.username == "admin"
        assert request.password == "test-dashboard-password"

        # Empty password should fail
        with pytest.raises(ValueError):
            LoginRequest(username="admin", password="")

        # Missing username should fail
        with pytest.raises(ValueError):
            LoginRequest(password="test-dashboard-password")

    def test_login_request_single_char_password(self):
        """Test LoginRequest accepts single character password."""
        request = LoginRequest(username="admin", password="a")
        assert request.username == "admin"
        assert request.password == "a"

    def test_token_response_creation(self):
        """Test TokenResponse creation."""
        response = TokenResponse(access_token="access_token_123", refresh_token="refresh_token_456", expires_in=3600)

        assert response.access_token == "access_token_123"
        assert response.refresh_token == "refresh_token_456"
        assert response.token_type == "bearer"
        assert response.expires_in == 3600

    def test_token_response_default_token_type(self):
        """Test TokenResponse default token_type is 'bearer'."""
        response = TokenResponse(access_token="token", refresh_token="refresh", expires_in=3600)

        assert response.token_type == "bearer"

    def test_refresh_token_request(self):
        """Test RefreshTokenRequest creation."""
        request = RefreshTokenRequest(refresh_token="refresh_token_abc")
        assert request.refresh_token == "refresh_token_abc"


class TestPortfolioSchemas:
    """Test portfolio-related schemas."""

    def test_position_response_creation(self):
        """Test PositionResponse creation."""
        position = PositionResponse(
            ticker="005930",
            market="KRX",
            quantity=100,
            avg_price=70000.0,
            current_price=71400.0,
            unrealized_pnl=140000.0,
            weight=0.25,
        )

        assert position.ticker == "005930"
        assert position.market == "KRX"
        assert position.quantity == 100
        assert position.avg_price == 70000.0
        assert position.weight == 0.25

    def test_position_response_weight_boundary(self):
        """Test PositionResponse weight validation (0.0 to 1.0)."""
        # Valid weights
        for weight in [0.0, 0.5, 1.0]:
            position = PositionResponse(
                ticker="AAPL",
                market="NYSE",
                quantity=50,
                avg_price=150.0,
                current_price=155.0,
                unrealized_pnl=250.0,
                weight=weight,
            )
            assert position.weight == weight

    def test_portfolio_summary_response_creation(self):
        """Test PortfolioSummaryResponse creation."""
        summary = PortfolioSummaryResponse(
            total_value=50_000_000,
            cash_krw=10_000_000,
            cash_usd=5000.0,
            daily_return=0.015,
            unrealized_pnl=1_500_000,
            realized_pnl=500_000,
            position_count=5,
            positions=[],
        )

        assert summary.total_value == 50_000_000
        assert summary.cash_krw == 10_000_000
        assert summary.cash_usd == 5000.0
        assert summary.daily_return == 0.015
        assert summary.position_count == 5

    def test_portfolio_summary_with_positions(self):
        """Test PortfolioSummaryResponse with positions list."""
        positions = [
            PositionResponse(
                ticker="005930",
                market="KRX",
                quantity=100,
                avg_price=70000.0,
                current_price=71400.0,
                unrealized_pnl=140000.0,
                weight=0.5,
            )
        ]

        summary = PortfolioSummaryResponse(
            total_value=50_000_000,
            cash_krw=10_000_000,
            cash_usd=5000.0,
            daily_return=0.0,
            unrealized_pnl=0,
            realized_pnl=0,
            position_count=1,
            positions=positions,
        )

        assert len(summary.positions) == 1
        assert summary.positions[0].ticker == "005930"

    def test_performance_response_creation(self):
        """Test PerformanceResponse creation."""
        performance = PerformanceResponse(
            period="1M", return_pct=2.5, mdd=-3.2, sharpe=1.8, volatility=1.2, win_rate=0.65
        )

        assert performance.period == "1M"
        assert performance.return_pct == 2.5
        assert performance.mdd == -3.2
        assert performance.sharpe == 1.8
        assert performance.volatility == 1.2
        assert performance.win_rate == 0.65


class TestOrderSchemas:
    """Test order-related schemas."""

    def test_order_create_request_with_all_fields(self):
        """Test OrderCreateRequest with all fields."""
        request = OrderCreateRequest(
            ticker="005930",
            market="KRX",
            side="BUY",
            quantity=100,
            order_type="LIMIT",
            limit_price=71000.0,
            reason="Rebalancing",
        )

        assert request.ticker == "005930"
        assert request.market == "KRX"
        assert request.side == "BUY"
        assert request.quantity == 100
        assert request.order_type == "LIMIT"
        assert request.limit_price == 71000.0
        assert request.reason == "Rebalancing"

    def test_order_create_request_quantity_validation(self):
        """Test OrderCreateRequest quantity > 0."""
        # Valid quantity
        request = OrderCreateRequest(ticker="AAPL", market="NYSE", side="BUY", quantity=1, order_type="MARKET")
        assert request.quantity == 1

        # Invalid quantity (0)
        with pytest.raises(ValueError):
            OrderCreateRequest(ticker="AAPL", market="NYSE", side="BUY", quantity=0, order_type="MARKET")

    def test_order_response_creation(self):
        """Test OrderResponse creation."""
        now = datetime.now(timezone.utc)
        response = OrderResponse(
            order_id="ORD-001",
            ticker="005930",
            market="KRX",
            side="BUY",
            quantity=100,
            order_type="MARKET",
            status="PENDING",
            filled_price=71000.0,
            filled_at=now,
        )

        assert response.order_id == "ORD-001"
        assert response.ticker == "005930"
        assert response.status == "PENDING"

    def test_batch_order_response_creation(self):
        """Test BatchOrderResponse creation."""
        results = [
            OrderResponse(
                order_id="ORD-1",
                ticker="005930",
                market="KRX",
                side="BUY",
                quantity=100,
                order_type="MARKET",
                status="PENDING",
            )
        ]

        response = BatchOrderResponse(results=results, total=1, success_count=1, fail_count=0)

        assert response.total == 1
        assert response.success_count == 1
        assert response.fail_count == 0
        assert len(response.results) == 1


class TestProfileSchemas:
    """Test profile-related schemas."""

    def test_profile_response_creation(self):
        """Test ProfileResponse creation."""
        response = ProfileResponse(
            risk_profile="BALANCED",
            investment_style="DISCRETIONARY",
            investment_goal="WEALTH_GROWTH",
            initial_capital=50_000_000,
            max_loss_tolerance=0.10,
        )

        assert response.risk_profile == "BALANCED"
        assert response.investment_style == "DISCRETIONARY"
        assert response.investment_goal == "WEALTH_GROWTH"
        assert response.initial_capital == 50_000_000


class TestAlertSchemas:
    """Test alert-related schemas."""

    def test_alert_response_creation(self):
        """Test AlertResponse creation."""
        now = datetime.now(timezone.utc)
        response = AlertResponse(
            id="ALERT-001",
            alert_type="DAILY_REPORT",
            level="INFO",
            title="Daily Report",
            message="Your daily portfolio report",
            status="UNREAD",
            created_at=now,
        )

        assert response.id == "ALERT-001"
        assert response.alert_type == "DAILY_REPORT"
        assert response.level == "INFO"
        assert response.status == "UNREAD"


# ══════════════════════════════════════════════════════════════════════════════
# AuthService Tests (10+ tests)
# ══════════════════════════════════════════════════════════════════════════════


class TestAuthService:
    """Test AuthService password and token operations."""

    def test_hash_password_creates_bcrypt_hash(self):
        """Test hash_password creates a valid bcrypt hash."""
        password = "test_password_123"
        hashed = AuthService.hash_password(password)

        # Bcrypt hashes start with $2a$ or $2b$
        assert hashed.startswith(("$2a$", "$2b$"))
        assert len(hashed) > 20

    def test_hash_password_different_hashes_for_same_password(self):
        """Test hash_password generates different hashes for same password."""
        password = "test_password"
        hash1 = AuthService.hash_password(password)
        hash2 = AuthService.hash_password(password)

        assert hash1 != hash2

    def test_verify_password_round_trip(self):
        """Test verify_password correctly verifies hashed password."""
        password = "test_password_123"
        hashed = AuthService.hash_password(password)

        assert AuthService.verify_password(password, hashed) is True

    def test_verify_password_fails_with_wrong_password(self):
        """Test verify_password returns False with wrong password."""
        password = "correct_password"
        hashed = AuthService.hash_password(password)

        assert AuthService.verify_password("wrong_password", hashed) is False

    def test_create_access_token_returns_jwt(self):
        """Test create_access_token returns a valid JWT token."""
        data = {"sub": "admin"}
        token = AuthService.create_access_token(data)

        assert isinstance(token, str)
        assert len(token) > 0
        # JWT has 3 parts separated by dots
        assert token.count(".") == 2

    def test_create_access_token_can_be_decoded(self):
        """Test create_access_token produces decodable JWT."""
        data = {"sub": "admin"}
        token = AuthService.create_access_token(data)

        settings = get_settings()
        decoded = jwt.decode(token, settings.dashboard.secret_key, algorithms=["HS256"])

        assert decoded["sub"] == "admin"
        assert "exp" in decoded

    def test_create_refresh_token_returns_jwt(self):
        """Test create_refresh_token returns a valid JWT token."""
        data = {"sub": "admin"}
        token = AuthService.create_refresh_token(data)

        assert isinstance(token, str)
        assert token.count(".") == 2

    def test_create_refresh_token_has_longer_expiry(self):
        """Test create_refresh_token has longer expiry than access token."""
        data = {"sub": "admin"}
        access_token = AuthService.create_access_token(data)
        refresh_token = AuthService.create_refresh_token(data)

        settings = get_settings()
        access_decoded = jwt.decode(access_token, settings.dashboard.secret_key, algorithms=["HS256"])
        refresh_decoded = jwt.decode(refresh_token, settings.dashboard.secret_key, algorithms=["HS256"])

        # Refresh token should expire later than access token
        assert refresh_decoded["exp"] > access_decoded["exp"]

    def test_verify_token_with_valid_token(self):
        """Test verify_token succeeds with valid token."""
        data = {"sub": "admin"}
        token = AuthService.create_access_token(data)

        decoded = AuthService.verify_token(token)

        assert decoded["sub"] == "admin"
        assert "exp" in decoded

    def test_verify_token_fails_with_invalid_token(self):
        """Test verify_token raises HTTPException with invalid token."""
        invalid_token = "invalid.token.here"

        with pytest.raises(HTTPException) as exc_info:
            AuthService.verify_token(invalid_token)

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Invalid authentication credentials" in exc_info.value.detail

    def test_verify_token_fails_with_expired_token(self):
        """Test verify_token raises HTTPException with expired token."""
        settings = get_settings()

        # Create an expired token
        expired_data = {"sub": "admin", "exp": datetime.now(timezone.utc) - timedelta(hours=1)}
        expired_token = jwt.encode(expired_data, settings.dashboard.secret_key, algorithm="HS256")

        with pytest.raises(HTTPException) as exc_info:
            AuthService.verify_token(expired_token)

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.asyncio
    async def test_authenticate_with_plaintext_password(self, test_user_admin):
        """Test authenticate with correct password against bcrypt hash."""
        from unittest.mock import AsyncMock

        # Create a mock db_session that returns the test_user_admin
        db_session = MagicMock()
        db_session.commit = AsyncMock()

        # Mock the execute method to return test_user_admin
        async def mock_execute(query):
            result = MagicMock()
            scalars_obj = MagicMock()
            scalars_obj.first = MagicMock(return_value=test_user_admin)
            result.scalars = MagicMock(return_value=scalars_obj)
            return result

        db_session.execute = mock_execute

        # test_user_admin created with "test-admin-password"
        access_token, refresh_token = await AuthService.authenticate(
            username="admin",
            password="test-admin-password",
            db_session=db_session,
        )

        assert isinstance(access_token, str)
        assert isinstance(refresh_token, str)
        assert access_token.count(".") == 2
        assert refresh_token.count(".") == 2

    @pytest.mark.asyncio
    async def test_authenticate_with_wrong_password(self, test_user_admin):
        """Test authenticate raises HTTPException with wrong password."""
        from unittest.mock import AsyncMock

        db_session = MagicMock()
        db_session.commit = AsyncMock()

        async def mock_execute(query):
            result = MagicMock()
            scalars_obj = MagicMock()
            scalars_obj.first = MagicMock(return_value=test_user_admin)
            result.scalars = MagicMock(return_value=scalars_obj)
            return result

        db_session.execute = mock_execute

        with pytest.raises(HTTPException) as exc_info:
            await AuthService.authenticate(
                username="admin",
                password="wrong-password",
                db_session=db_session,
            )

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.asyncio
    async def test_authenticate_returns_admin_subject(self, test_user_admin):
        """Test authenticate returns tokens with admin subject."""
        from unittest.mock import AsyncMock

        db_session = MagicMock()
        db_session.commit = AsyncMock()

        async def mock_execute(query):
            result = MagicMock()
            scalars_obj = MagicMock()
            scalars_obj.first = MagicMock(return_value=test_user_admin)
            result.scalars = MagicMock(return_value=scalars_obj)
            return result

        db_session.execute = mock_execute

        access_token, _ = await AuthService.authenticate(
            username="admin",
            password="test-admin-password",
            db_session=db_session,
        )

        payload = AuthService.verify_token(access_token)
        assert payload["sub"] == "admin"

    @pytest.mark.asyncio
    async def test_authenticate_with_bcrypt_hashed_password(self, test_user_admin):
        """Test authenticate with bcrypt hashed password."""
        from unittest.mock import AsyncMock

        db_session = MagicMock()
        db_session.commit = AsyncMock()

        async def mock_execute(query):
            result = MagicMock()
            scalars_obj = MagicMock()
            scalars_obj.first = MagicMock(return_value=test_user_admin)
            result.scalars = MagicMock(return_value=scalars_obj)
            return result

        db_session.execute = mock_execute

        # test_user_admin is already created with bcrypt hashed password
        plain_password = "test-admin-password"

        # Should authenticate with plain password
        access_token, refresh_token = await AuthService.authenticate(
            username="admin",
            password=plain_password,
            db_session=db_session,
        )

        assert isinstance(access_token, str)
        assert isinstance(refresh_token, str)


# ══════════════════════════════════════════════════════════════════════════════
# Route Integration Tests (10+ tests)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestAuthRoutes:
    """Test authentication API routes."""

    async def test_login_with_correct_password(self):
        """Test POST /api/auth/login with correct username and password."""
        from httpx import ASGITransport, AsyncClient

        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "test-admin-password"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "data" in data
            assert "access_token" in data["data"]
            assert "refresh_token" in data["data"]
            assert data["data"]["token_type"] == "bearer"

    async def test_login_with_wrong_password(self):
        """Test POST /api/auth/login with wrong password."""
        from httpx import ASGITransport, AsyncClient

        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "wrong-password"},
            )

            assert response.status_code == 401

    async def test_login_missing_username(self):
        """Test POST /api/auth/login with missing username."""
        from httpx import ASGITransport, AsyncClient

        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/auth/login", json={"password": "test-admin-password"})

            # Missing required field should be 422
            assert response.status_code == 422

    async def test_login_empty_password(self):
        """Test POST /api/auth/login with empty password."""
        from httpx import ASGITransport, AsyncClient

        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": ""},
            )

            assert response.status_code == 422

    async def test_get_me_with_valid_token(self):
        """Test GET /api/auth/me with valid token."""
        from httpx import ASGITransport, AsyncClient

        from main import app

        # First login to get token
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            login_response = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "test-admin-password"},
            )

            token = login_response.json()["data"]["access_token"]

            # Now test GET /api/auth/me
            headers = {"Authorization": f"Bearer {token}"}
            response = await client.get("/api/auth/me", headers=headers)

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["data"]["username"] == "admin"

    async def test_get_me_without_token(self):
        """Test GET /api/auth/me without authorization header."""
        from httpx import ASGITransport, AsyncClient

        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/auth/me")

            # 인증 헤더 미제공 시 401 Unauthorized (RFC 7235)
            assert response.status_code == 401

    async def test_refresh_token_with_valid_refresh_token(self):
        """Test POST /api/auth/refresh with valid refresh token."""
        from httpx import ASGITransport, AsyncClient

        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Login first
            login_response = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "test-admin-password"},
            )

            refresh_token = login_response.json()["data"]["refresh_token"]

            # Refresh token
            response = await client.post("/api/auth/refresh", json={"refresh_token": refresh_token})

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "access_token" in data["data"]
            assert "refresh_token" in data["data"]

    async def test_refresh_token_with_invalid_token(self):
        """Test POST /api/auth/refresh with invalid token."""
        from httpx import ASGITransport, AsyncClient

        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/auth/refresh", json={"refresh_token": "invalid.token.here"})

            assert response.status_code == 401


@pytest.mark.asyncio
class TestPortfolioRoutes:
    """Test portfolio API routes."""

    async def test_get_portfolio_summary_with_auth(self):
        """Test GET /api/portfolio/summary with authentication."""
        from httpx import ASGITransport, AsyncClient

        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Login
            login_response = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "test-admin-password"},
            )
            token = login_response.json()["data"]["access_token"]

            # Get portfolio summary
            headers = {"Authorization": f"Bearer {token}"}
            response = await client.get("/api/portfolio/summary", headers=headers)

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "data" in data
            assert "total_value" in data["data"]

    async def test_get_portfolio_positions_with_auth(self):
        """Test GET /api/portfolio/positions with authentication."""
        from httpx import ASGITransport, AsyncClient

        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Login
            login_response = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "test-admin-password"},
            )
            token = login_response.json()["data"]["access_token"]

            # Get positions
            headers = {"Authorization": f"Bearer {token}"}
            response = await client.get("/api/portfolio/positions", headers=headers)

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert isinstance(data["data"], list)


@pytest.mark.asyncio
class TestOrderRoutes:
    """Test order API routes."""

    async def test_create_order_with_auth(self):
        """Test POST /api/orders/ with authentication."""
        from httpx import ASGITransport, AsyncClient

        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Login
            login_response = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "test-admin-password"},
            )
            token = login_response.json()["data"]["access_token"]

            # Create order
            headers = {"Authorization": f"Bearer {token}"}
            order_data = {"ticker": "005930", "market": "KRX", "side": "BUY", "quantity": 100, "order_type": "MARKET"}
            response = await client.post("/api/orders/", json=order_data, headers=headers)

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "data" in data

    async def test_create_order_without_auth(self):
        """Test POST /api/orders/ without authentication."""
        from httpx import ASGITransport, AsyncClient

        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            order_data = {"ticker": "005930", "market": "KRX", "side": "BUY", "quantity": 100, "order_type": "MARKET"}
            response = await client.post("/api/orders/", json=order_data)

            # 인증 헤더 미제공 시 401 Unauthorized (RFC 7235)
            assert response.status_code == 401

    async def test_get_orders_with_auth(self):
        """Test GET /api/orders/ with authentication."""
        from httpx import ASGITransport, AsyncClient

        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Login
            login_response = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "test-admin-password"},
            )
            token = login_response.json()["data"]["access_token"]

            # Get orders
            headers = {"Authorization": f"Bearer {token}"}
            response = await client.get("/api/orders/", headers=headers)

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert isinstance(data["data"], list)

    async def test_create_batch_orders_with_auth(self):
        """Test POST /api/orders/batch with authentication."""
        from httpx import ASGITransport, AsyncClient

        from main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Login
            login_response = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "test-admin-password"},
            )
            token = login_response.json()["data"]["access_token"]

            # Create batch orders
            headers = {"Authorization": f"Bearer {token}"}
            batch_data = {
                "orders": [
                    {"ticker": "005930", "market": "KRX", "side": "BUY", "quantity": 100, "order_type": "MARKET"}
                ]
            }
            response = await client.post("/api/orders/batch", json=batch_data, headers=headers)

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "data" in data


# ══════════════════════════════════════════════════════════════════════════════
# Edge Cases and Error Handling Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestAuthServiceEdgeCases:
    """Test edge cases in AuthService."""

    def test_create_access_token_with_empty_data(self):
        """Test create_access_token with empty data dict."""
        token = AuthService.create_access_token({})

        assert isinstance(token, str)
        decoded = AuthService.verify_token(token)
        assert "exp" in decoded

    def test_create_access_token_with_custom_payload(self):
        """Test create_access_token with custom payload."""
        data = {"sub": "user123", "role": "admin", "scope": "full"}
        token = AuthService.create_access_token(data)
        decoded = AuthService.verify_token(token)

        assert decoded["sub"] == "user123"
        assert decoded["role"] == "admin"
        assert decoded["scope"] == "full"


class TestSchemaValidation:
    """Test schema validation edge cases."""

    def test_order_create_request_with_negative_limit_price(self):
        """Test OrderCreateRequest rejects negative limit_price."""
        with pytest.raises(ValueError):
            OrderCreateRequest(
                ticker="AAPL", market="NYSE", side="BUY", quantity=100, order_type="LIMIT", limit_price=-1000.0
            )

    def test_profile_response_with_positive_capital(self):
        """Test ProfileResponse accepts positive initial_capital."""
        response = ProfileResponse(
            risk_profile="BALANCED",
            investment_style="DISCRETIONARY",
            investment_goal="WEALTH_GROWTH",
            initial_capital=1_000_000.0,
        )

        assert response.initial_capital == 1_000_000.0

    def test_portfolio_summary_position_count_validation(self):
        """Test PortfolioSummaryResponse position_count is non-negative."""
        summary = PortfolioSummaryResponse(
            total_value=50_000_000,
            cash_krw=10_000_000,
            cash_usd=5000.0,
            daily_return=0.0,
            unrealized_pnl=0,
            realized_pnl=0,
            position_count=0,
            positions=[],
        )

        assert summary.position_count == 0

    def test_alert_stats_by_level_dict(self):
        """Test AlertStatsResponse by_level dictionary."""
        stats = AlertStatsResponse(total=10, unread=3, by_level={"INFO": 5, "WARNING": 3, "ERROR": 2, "CRITICAL": 0})

        assert stats.total == 10
        assert stats.by_level["INFO"] == 5
        assert stats.by_level["WARNING"] == 3


# ══════════════════════════════════════════════════════════════════════════════
# Token Expiry Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestTokenExpiry:
    """Test token expiry handling."""

    def test_access_token_expiry_in_hours(self):
        """Test access token expiry is set in hours."""
        settings = get_settings()
        data = {"sub": "admin"}
        token = AuthService.create_access_token(data)

        decoded = jwt.decode(token, settings.dashboard.secret_key, algorithms=["HS256"])

        # Calculate expiry seconds
        now_timestamp = datetime.now(timezone.utc).timestamp()
        expiry_seconds = decoded["exp"] - now_timestamp

        # Should be approximately 8 hours (28800 seconds)
        expected_seconds = settings.dashboard.access_token_expire_hours * 3600
        # Allow 5 second tolerance
        assert abs(expiry_seconds - expected_seconds) < 5

    def test_refresh_token_expiry_in_days(self):
        """Test refresh token expiry is set in days."""
        settings = get_settings()
        data = {"sub": "admin"}
        token = AuthService.create_refresh_token(data)

        decoded = jwt.decode(token, settings.dashboard.secret_key, algorithms=["HS256"])

        # Calculate expiry seconds
        now_timestamp = datetime.now(timezone.utc).timestamp()
        expiry_seconds = decoded["exp"] - now_timestamp

        # Should be approximately 7 days
        expected_seconds = settings.dashboard.refresh_token_expire_days * 86400
        # Allow 5 second tolerance
        assert abs(expiry_seconds - expected_seconds) < 5
