"""
AQTS 테스트 공통 Fixture (conftest.py)

NFR-07 명세:
- 모든 외부 API는 Mock으로 대체
- 테스트용 샘플 데이터는 Fixture로 중앙 관리
- DB는 테스트용 인메모리 또는 별도 테스트 DB 사용
"""

import os


# ══════════════════════════════════════
# 테스트 환경변수 설정 (모듈 임포트 전 실행)
# ══════════════════════════════════════
def pytest_configure(config):
    """테스트 환경에 필요한 환경변수를 설정합니다."""
    test_env_vars = {
        # 테스트 모드 (Rate Limiting 비활성화 등) — 표준 표기 'true'
        "TESTING": "true",
        # KIS
        "KIS_APP_KEY_LIVE": "test_key",
        "KIS_APP_SECRET_LIVE": "test_secret",
        "KIS_ACCOUNT_NO_LIVE": "12345678-01",
        "KIS_APP_KEY_DEMO": "test_key_demo",
        "KIS_APP_SECRET_DEMO": "test_secret_demo",
        "KIS_ACCOUNT_NO_DEMO": "87654321-01",
        "KIS_TRADING_MODE": "BACKTEST",
        # DB
        "DB_PASSWORD": "test_password",
        # Mongo
        "MONGO_PASSWORD": "test_password",
        # Redis
        "REDIS_PASSWORD": "test_password",
        # Anthropic
        "ANTHROPIC_API_KEY": "test-api-key",
        # Telegram
        "TELEGRAM_BOT_TOKEN": "test-bot-token",
        "TELEGRAM_CHAT_ID": "test-chat-id",
        # Dashboard (RBAC v1.29+)
        "DASHBOARD_SECRET_KEY": "test-secret-key",
        "ADMIN_BOOTSTRAP_USERNAME": "admin",
        "ADMIN_BOOTSTRAP_PASSWORD": "test-admin-password",
    }
    for key, value in test_env_vars.items():
        os.environ.setdefault(key, value)


from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from config.constants import (
    AssetType,
    InvestmentStyle,
    Market,
    RiskProfile,
)

# ══════════════════════════════════════
# Event Loop
# ══════════════════════════════════════
# pytest-asyncio 0.23+ auto 모드에서는 프레임워크가 함수별 event loop를
# 자동 관리합니다. session-scoped event_loop fixture는 deprecated이므로
# 제거하고 auto 모드에 위임합니다.


# ══════════════════════════════════════
# Mock KIS Client
# ══════════════════════════════════════
@pytest.fixture
def mock_kis_client():
    """한국투자증권 API Mock"""
    client = AsyncMock()

    # 국내 현재가 응답
    client.get_kr_stock_price.return_value = {
        "output": {
            "stck_prpr": "71400",
            "stck_oprc": "71000",
            "stck_hgpr": "72000",
            "stck_lwpr": "70500",
            "acml_vol": "12345678",
        },
        "rt_cd": "0",
    }

    # 국내 일봉 응답
    client.get_kr_stock_daily.return_value = {
        "output2": [
            {
                "stck_bsop_date": "20260401",
                "stck_oprc": "71000",
                "stck_hgpr": "72000",
                "stck_lwpr": "70500",
                "stck_clpr": "71400",
                "acml_vol": "12345678",
            },
            {
                "stck_bsop_date": "20260402",
                "stck_oprc": "71400",
                "stck_hgpr": "72500",
                "stck_lwpr": "71200",
                "stck_clpr": "72100",
                "acml_vol": "10987654",
            },
        ],
        "rt_cd": "0",
    }

    # 해외 현재가 응답
    client.get_us_stock_price.return_value = {
        "output": {
            "last": "175.50",
            "open": "174.00",
            "high": "176.20",
            "low": "173.80",
            "tvol": "45678901",
        },
        "rt_cd": "0",
    }

    # 잔고 조회 응답
    client.get_kr_balance.return_value = {
        "output1": [
            {
                "pdno": "005930",
                "prdt_name": "삼성전자",
                "hldg_qty": "100",
                "pchs_avg_pric": "70000.00",
                "prpr": "71400",
                "evlu_pfls_amt": "140000",
            },
        ],
        "output2": [
            {
                "dnca_tot_amt": "50000000",
                "tot_evlu_amt": "57140000",
                "pchs_amt_smtl_amt": "50000000",
            },
        ],
        "rt_cd": "0",
    }

    client.is_mock = True
    return client


# ══════════════════════════════════════
# Mock Claude API
# ══════════════════════════════════════
@pytest.fixture
def mock_claude_client():
    """Anthropic Claude API Mock"""
    client = AsyncMock()

    client.messages.create.return_value = MagicMock(
        content=[
            MagicMock(
                text='{"ticker": "005930", "score": 0.65, "confidence": 0.8, '
                '"summary": "삼성전자 반도체 수출 호조 전망", '
                '"factors": ["반도체 수출 증가", "AI 메모리 수요 확대"]}'
            )
        ]
    )
    return client


# ══════════════════════════════════════
# Mock Telegram Bot
# ══════════════════════════════════════
@pytest.fixture
def mock_telegram_bot():
    """텔레그램 Bot API Mock"""
    bot = AsyncMock()
    bot.send_message.return_value = MagicMock(message_id=12345)
    return bot


# ══════════════════════════════════════
# 샘플 시세 데이터
# ══════════════════════════════════════
@pytest.fixture
def sample_ohlcv_data():
    """테스트용 OHLCV 샘플 데이터"""
    import numpy as np
    import pandas as pd

    np.random.seed(42)
    dates = pd.bdate_range(start="2025-01-02", periods=60)
    base_price = 70000

    prices = [base_price]
    for _ in range(59):
        change = np.random.normal(0, 0.015)
        prices.append(prices[-1] * (1 + change))

    df = pd.DataFrame(
        {
            "time": dates,
            "ticker": "005930",
            "market": Market.KRX.value,
            "open": [p * (1 + np.random.uniform(-0.005, 0.005)) for p in prices],
            "high": [p * (1 + np.random.uniform(0.005, 0.02)) for p in prices],
            "low": [p * (1 - np.random.uniform(0.005, 0.02)) for p in prices],
            "close": prices,
            "volume": [int(np.random.uniform(5e6, 2e7)) for _ in prices],
            "interval": "1d",
        }
    )
    return df


# ══════════════════════════════════════
# 샘플 사용자 프로필
# ══════════════════════════════════════
@pytest.fixture
def sample_user_profile():
    """테스트용 사용자 투자 프로필"""
    return {
        "investment_types": [AssetType.STOCK.value, AssetType.ETF.value, AssetType.BOND.value],
        "risk_profile": RiskProfile.BALANCED.value,
        "seed_amount": 50_000_000,
        "investment_goal": "WEALTH_GROWTH",
        "investment_style": InvestmentStyle.DISCRETIONARY.value,
        "loss_tolerance": -0.10,
        "sector_filter": ["IT", "Healthcare", "Finance"],
        "designated_tickers": ["005930", "000660"],
        "rebalancing_frequency": "MONTHLY",
    }


# ══════════════════════════════════════
# 샘플 포트폴리오
# ══════════════════════════════════════
@pytest.fixture
def sample_portfolio():
    """테스트용 포트폴리오"""
    return [
        {
            "ticker": "005930",
            "market": "KRX",
            "quantity": 100,
            "avg_price": 70000,
            "current_price": 71400,
            "target_weight": 0.182,
        },
        {
            "ticker": "360750",
            "market": "KRX",
            "quantity": 480,
            "avg_price": 15800,
            "current_price": 18230,
            "target_weight": 0.155,
        },
        {
            "ticker": "069500",
            "market": "KRX",
            "quantity": 190,
            "avg_price": 35700,
            "current_price": 37850,
            "target_weight": 0.128,
        },
        {
            "ticker": "000660",
            "market": "KRX",
            "quantity": 30,
            "avg_price": 161700,
            "current_price": 198500,
            "target_weight": 0.103,
        },
        {
            "ticker": "136340",
            "market": "KRX",
            "quantity": 46,
            "avg_price": 102400,
            "current_price": 105120,
            "target_weight": 0.085,
        },
    ]


# ══════════════════════════════════════
# 샘플 뉴스 데이터
# ══════════════════════════════════════
@pytest.fixture
def sample_news_data():
    """테스트용 뉴스 데이터"""
    return [
        {
            "title": "삼성전자, AI 반도체 수출 사상 최대",
            "content": "삼성전자가 AI용 HBM 메모리 반도체 수출이 전년 대비 45% 증가하며 분기 사상 최대 실적을 기록했다.",
            "source": "naver_news",
            "published_at": datetime(2026, 4, 2, 9, 30),
            "tickers": ["005930"],
            "category": "finance",
        },
        {
            "title": "Fed, 금리 동결 시사",
            "content": "미국 연방준비제도이사회가 다음 FOMC 회의에서 금리를 동결할 가능성을 시사했다.",
            "source": "reuters",
            "published_at": datetime(2026, 4, 2, 7, 0),
            "tickers": [],
            "category": "macro",
        },
    ]


# ══════════════════════════════════════
# RBAC 테스트 Fixture (v1.29+)
# ══════════════════════════════════════
@pytest.fixture
def admin_token():
    """Admin 역할 JWT 토큰"""
    from api.middleware.auth import AuthService

    data = {
        "sub": "admin",
        "uid": "test-admin-uuid",
        "role": "admin",
    }
    return AuthService.create_access_token(data)


@pytest.fixture
def operator_token():
    """Operator 역할 JWT 토큰"""
    from api.middleware.auth import AuthService

    data = {
        "sub": "operator",
        "uid": "test-operator-uuid",
        "role": "operator",
    }
    return AuthService.create_access_token(data)


@pytest.fixture
def viewer_token():
    """Viewer 역할 JWT 토큰"""
    from api.middleware.auth import AuthService

    data = {
        "sub": "viewer",
        "uid": "test-viewer-uuid",
        "role": "viewer",
    }
    return AuthService.create_access_token(data)


# ══════════════════════════════════════
# Database Fixtures (v1.29+)
# ══════════════════════════════════════
@pytest.fixture
def test_user_admin():
    """Test admin user object (for mocking)"""
    from datetime import datetime, timezone

    from api.middleware.auth import AuthService
    from db.models.user import Role, User

    admin_role = Role(id=1, name="admin", description="Administrator")
    now = datetime.now(timezone.utc)
    user = User(
        id="test-admin-uuid",
        username="admin",
        password_hash=AuthService.hash_password("test-admin-password"),
        email="admin@test.local",
        role_id=1,
        is_active=True,
        is_locked=False,
        totp_enabled=False,
        totp_secret=None,
        failed_login_attempts=0,
        created_at=now,
        updated_at=now,
    )
    user.role = admin_role
    return user


@pytest.fixture
def test_user_operator():
    """Test operator user object (for mocking)"""
    from datetime import datetime, timezone

    from api.middleware.auth import AuthService
    from db.models.user import Role, User

    operator_role = Role(id=2, name="operator", description="Operator")
    now = datetime.now(timezone.utc)
    user = User(
        id="test-operator-uuid",
        username="operator",
        password_hash=AuthService.hash_password("test-operator-password"),
        email="operator@test.local",
        role_id=2,
        is_active=True,
        is_locked=False,
        totp_enabled=False,
        totp_secret=None,
        failed_login_attempts=0,
        created_at=now,
        updated_at=now,
    )
    user.role = operator_role
    return user


@pytest.fixture
def test_user_viewer():
    """Test viewer user object (for mocking)"""
    from datetime import datetime, timezone

    from api.middleware.auth import AuthService
    from db.models.user import Role, User

    viewer_role = Role(id=3, name="viewer", description="Viewer")
    now = datetime.now(timezone.utc)
    user = User(
        id="test-viewer-uuid",
        username="viewer",
        password_hash=AuthService.hash_password("test-viewer-password"),
        email="viewer@test.local",
        role_id=3,
        is_active=True,
        is_locked=False,
        totp_enabled=False,
        totp_secret=None,
        failed_login_attempts=0,
        created_at=now,
        updated_at=now,
    )
    user.role = viewer_role
    return user


@pytest.fixture
def db_session(test_user_admin, test_user_operator, test_user_viewer):
    """Mock AsyncSession for unit tests - supports mutable state"""
    from unittest.mock import AsyncMock, MagicMock

    # Create an AsyncMock for the session itself
    session = AsyncMock()

    # Keep a reference to test users to support modifications (mutable state)
    # Users are keyed by username for easy lookup
    users_db = {
        "admin": test_user_admin,
        "operator": test_user_operator,
        "viewer": test_user_viewer,
    }

    # Mock execute to return the test user when queried by username
    async def mock_execute(query):
        # Build result object
        result = MagicMock()
        scalars_obj = MagicMock()

        # Try to extract username from query or just return admin for any select(User) query
        # In the authenticate method, it does: select(User).where(User.username == username)
        # We'll just return the test_user for admin queries and None for others
        found_user = None
        query_str = str(query).lower()

        # For "admin" or default, return test_user_admin
        # This handles both explicit "admin" queries and the test case
        if "select" in query_str and "user" in query_str.lower():
            # Default to returning admin user for select(User) queries
            # In real tests, the username parameter is passed separately
            found_user = test_user_admin

        scalars_obj.first = MagicMock(return_value=found_user)
        scalars_obj.all = MagicMock(return_value=list(users_db.values()))
        result.scalars = MagicMock(return_value=scalars_obj)
        return result

    session.execute = mock_execute
    # Make sure methods are directly callable as coroutines
    session.commit = AsyncMock(return_value=None)
    session.refresh = AsyncMock(return_value=None)  # Refresh is a no-op for in-memory objects
    session.rollback = AsyncMock(return_value=None)
    return session


# ══════════════════════════════════════
# Integration Testing Fixtures
# ══════════════════════════════════════
@pytest.fixture
def authenticated_app(test_user_admin, test_user_operator, test_user_viewer):
    """FastAPI app with dependency overrides for integration tests"""
    from unittest.mock import AsyncMock, MagicMock

    from db.database import get_db_session
    from main import app

    # Build in-memory user repository
    users_db = {
        "admin": test_user_admin,
        "operator": test_user_operator,
        "viewer": test_user_viewer,
    }

    # Create mock AsyncSession
    async def get_mock_db_session():
        session = AsyncMock()

        # Keep state for the session
        _users = dict(users_db)

        async def mock_execute_impl(query, *args, **kwargs):
            """Mock execute that handles both select() and text() queries"""
            from sqlalchemy.sql import Select

            result = MagicMock()
            scalars_obj = MagicMock()

            # Handle select() queries
            if isinstance(query, Select):
                # Get the table being selected
                query_str = str(query).lower()

                # Check if it's a User query
                if "user" in query_str:
                    # Check for where clause
                    if hasattr(query, "whereclause") and query.whereclause is not None:
                        # This is a select(User).where(...) query
                        # We need to return the matching user
                        # For now, return the first (admin) user as default
                        # This is a simplification - in real scenarios, we'd need to parse the clause
                        found_user = next(iter(_users.values()), None)
                    else:
                        # No where clause: return all users
                        found_user = None
                        scalars_obj.all = MagicMock(return_value=list(_users.values()))

                    scalars_obj.first = MagicMock(return_value=found_user)
                    result.scalars = MagicMock(return_value=scalars_obj)
                    return result

            # For text() and other queries, return empty result
            scalars_obj.first = MagicMock(return_value=None)
            scalars_obj.all = MagicMock(return_value=[])
            result.scalars = MagicMock(return_value=scalars_obj)
            return result

        # Use AsyncMock's side_effect to call our implementation
        session.execute = AsyncMock(side_effect=mock_execute_impl)
        session.commit = AsyncMock(return_value=None)
        session.refresh = AsyncMock(return_value=None)
        session.rollback = AsyncMock(return_value=None)
        session.get = AsyncMock(return_value=None)

        return session

    # Override the get_db_session dependency
    app.dependency_overrides[get_db_session] = get_mock_db_session

    yield app

    # Cleanup: remove overrides
    app.dependency_overrides.clear()
