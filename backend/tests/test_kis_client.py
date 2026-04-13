"""
KIS Client 유닛테스트

테스트 대상: core/data_collector/kis_client.py
목표 커버리지: 80% (Data Collector 모듈)

테스트 범위:
- 토큰 발급 및 캐싱 로직
- TradingMode (LIVE/DEMO/BACKTEST) 분기
- TR_ID 자동 선택
- BACKTEST 모드 가드
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.settings import TradingMode
from core.data_collector.kis_client import KISAPIError, KISClient, KISTokenManager


def _mock_kis_settings(trading_mode=TradingMode.DEMO):
    """KIS 설정 Mock 생성"""
    mock = MagicMock()
    mock.trading_mode = trading_mode
    mock.is_live = trading_mode == TradingMode.LIVE
    mock.is_demo = trading_mode == TradingMode.DEMO
    mock.is_backtest = trading_mode == TradingMode.BACKTEST
    mock.app_key = "test_key"
    mock.app_secret = "test_secret"
    mock.account_no = "12345678"
    mock.account_prod = "01"
    mock.base_url = "https://mock.api.com"
    mock.websocket_url = "ws://mock.api.com:31000"
    mock.api_timeout = 10
    mock.api_retry_count = 1
    mock.token_retry_count = 1
    mock.token_retry_max_wait = 60
    return mock


class TestKISTokenManager:
    """토큰 관리자 테스트"""

    @pytest.mark.asyncio
    async def test_token_issuance_demo_mode(self):
        """DEMO 모드에서 토큰 정상 발급"""
        with patch("core.data_collector.kis_client.get_settings") as mock_gs:
            mock_gs.return_value.kis = _mock_kis_settings(TradingMode.DEMO)
            manager = KISTokenManager()

            mock_resp = MagicMock()
            mock_resp.json.return_value = {"access_token": "tok_123", "expires_in": 86400}
            mock_resp.raise_for_status = MagicMock()

            with patch("httpx.AsyncClient") as mock_cls:
                mock_client = AsyncMock()
                mock_client.post.return_value = mock_resp
                mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

                token = await manager.get_access_token()
                assert token == "tok_123"

    @pytest.mark.asyncio
    async def test_token_caching(self):
        """유효 기간 내 캐싱된 토큰 반환"""
        with patch("core.data_collector.kis_client.get_settings") as mock_gs:
            mock_gs.return_value.kis = _mock_kis_settings(TradingMode.DEMO)
            manager = KISTokenManager()
            manager._access_token = "cached_token"
            manager._token_expires_at = datetime.now() + timedelta(hours=12)

            token = await manager.get_access_token()
            assert token == "cached_token"

    @pytest.mark.asyncio
    async def test_token_refresh_near_expiry(self):
        """만료 10분 전 자동 갱신"""
        with patch("core.data_collector.kis_client.get_settings") as mock_gs:
            mock_gs.return_value.kis = _mock_kis_settings(TradingMode.DEMO)
            manager = KISTokenManager()
            manager._access_token = "old_token"
            manager._token_expires_at = datetime.now() + timedelta(minutes=5)

            mock_resp = MagicMock()
            mock_resp.json.return_value = {"access_token": "new_token", "expires_in": 86400}
            mock_resp.raise_for_status = MagicMock()

            with patch("httpx.AsyncClient") as mock_cls:
                mock_client = AsyncMock()
                mock_client.post.return_value = mock_resp
                mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

                token = await manager.get_access_token()
                assert token == "new_token"

    @pytest.mark.asyncio
    async def test_backtest_mode_blocks_token(self):
        """BACKTEST 모드에서 토큰 발급 차단"""
        with patch("core.data_collector.kis_client.get_settings") as mock_gs:
            mock_gs.return_value.kis = _mock_kis_settings(TradingMode.BACKTEST)
            manager = KISTokenManager()

            with pytest.raises(KISAPIError) as exc:
                await manager.get_access_token()
            assert exc.value.code == "BACKTEST"


class TestKISClientTrId:
    """TR_ID 자동 선택 테스트"""

    def test_demo_mode_virtual_tr_id(self):
        with patch("core.data_collector.kis_client.get_settings") as mock_gs:
            mock_gs.return_value.kis = _mock_kis_settings(TradingMode.DEMO)
            client = KISClient()
            assert client._get_tr_id("TTTC0802U", "VTTC0802U") == "VTTC0802U"

    def test_live_mode_real_tr_id(self):
        with patch("core.data_collector.kis_client.get_settings") as mock_gs:
            mock_gs.return_value.kis = _mock_kis_settings(TradingMode.LIVE)
            client = KISClient()
            assert client._get_tr_id("TTTC0802U", "VTTC0802U") == "TTTC0802U"


class TestKISClientOrders:
    """주문 API 테스트"""

    @pytest.mark.asyncio
    async def test_kr_buy_order_demo_tr_id(self):
        """DEMO 모드 매수 → 모의투자 TR_ID"""
        with patch("core.data_collector.kis_client.get_settings") as mock_gs:
            mock_gs.return_value.kis = _mock_kis_settings(TradingMode.DEMO)
            client = KISClient()
            client._request = AsyncMock(return_value={"rt_cd": "0", "output": {}})

            await client.place_kr_order("005930", "BUY", 10, 71400, "00")
            assert client._request.call_args.args[2] == "VTTC0802U"

    @pytest.mark.asyncio
    async def test_kr_sell_order_demo_tr_id(self):
        """DEMO 모드 매도 → 모의투자 TR_ID"""
        with patch("core.data_collector.kis_client.get_settings") as mock_gs:
            mock_gs.return_value.kis = _mock_kis_settings(TradingMode.DEMO)
            client = KISClient()
            client._request = AsyncMock(return_value={"rt_cd": "0", "output": {}})

            await client.place_kr_order("005930", "SELL", 10, 71400, "00")
            assert client._request.call_args.args[2] == "VTTC0801U"

    @pytest.mark.asyncio
    async def test_backtest_mode_blocks_orders(self):
        """BACKTEST 모드에서 주문 차단"""
        with patch("core.data_collector.kis_client.get_settings") as mock_gs:
            mock_gs.return_value.kis = _mock_kis_settings(TradingMode.BACKTEST)
            client = KISClient()

            with pytest.raises(KISAPIError) as exc:
                await client.place_kr_order("005930", "BUY", 10)
            assert exc.value.code == "BACKTEST"


class TestKISClientQueries:
    """시세 조회 테스트"""

    @pytest.mark.asyncio
    async def test_kr_stock_price(self):
        with patch("core.data_collector.kis_client.get_settings") as mock_gs:
            mock_gs.return_value.kis = _mock_kis_settings(TradingMode.DEMO)
            client = KISClient()
            client._request = AsyncMock(return_value={"output": {"stck_prpr": "71400"}, "rt_cd": "0"})

            result = await client.get_kr_stock_price("005930")
            assert result["output"]["stck_prpr"] == "71400"

    @pytest.mark.asyncio
    async def test_us_stock_price(self):
        with patch("core.data_collector.kis_client.get_settings") as mock_gs:
            mock_gs.return_value.kis = _mock_kis_settings(TradingMode.DEMO)
            client = KISClient()
            client._request = AsyncMock(return_value={"output": {"last": "175.50"}, "rt_cd": "0"})

            result = await client.get_us_stock_price("AAPL")
            assert result["output"]["last"] == "175.50"


class TestKISAPIError:
    """에러 클래스 테스트"""

    def test_error_format(self):
        err = KISAPIError("EGW00123", "토큰 만료")
        assert "EGW00123" in str(err)
        assert "토큰 만료" in str(err)

    def test_error_attributes(self):
        err = KISAPIError("1", "test")
        assert err.code == "1"
        assert err.message == "test"

    def test_backtest_error(self):
        err = KISAPIError("BACKTEST", "차단")
        assert err.code == "BACKTEST"
