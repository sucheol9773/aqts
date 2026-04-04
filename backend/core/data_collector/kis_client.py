"""
한국투자증권 OpenAPI 래퍼 모듈

토큰 자동 발급/갱신, REST API 호출, WebSocket 연결을 통합 관리합니다.
KIS_TRADING_MODE (LIVE/DEMO/BACKTEST) 에 따라 자동으로
적절한 API 키, URL, TR_ID를 선택합니다.

BACKTEST 모드에서는 모든 API 호출이 차단됩니다.
"""

import asyncio
import time
from datetime import datetime, timedelta
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from config.logging import logger
from config.settings import TradingMode, get_settings


class KISTokenManager:
    """
    한국투자증권 API 토큰 관리자

    - 접근 토큰 자동 발급 및 캐싱
    - 만료 전 자동 갱신 (만료 10분 전)
    - WebSocket 접속키 관리
    - BACKTEST 모드에서는 작동하지 않음
    """

    def __init__(self):
        self._settings = get_settings().kis
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        self._websocket_key: Optional[str] = None

    async def get_access_token(self) -> str:
        """유효한 접근 토큰 반환 (만료 시 자동 갱신)"""
        if self._settings.is_backtest:
            raise KISAPIError("BACKTEST", "BACKTEST 모드에서는 API 호출이 불가합니다.")

        if self._access_token and self._token_expires_at:
            if datetime.now() < self._token_expires_at - timedelta(minutes=10):
                return self._access_token

        await self._issue_token()
        return self._access_token

    async def _issue_token(self) -> None:
        """접근 토큰 발급"""
        url = f"{self._settings.base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self._settings.app_key,
            "appsecret": self._settings.app_secret,
        }
        timeout = self._settings.api_timeout

        @retry(
            stop=stop_after_attempt(self._settings.api_retry_count),
            wait=wait_exponential(multiplier=1, min=2, max=10),
        )
        async def _do_issue():
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=body)
                response.raise_for_status()
                return response.json()

        data = await _do_issue()
        self._access_token = data["access_token"]
        expires_in = int(data.get("expires_in", 86400))
        self._token_expires_at = datetime.now() + timedelta(seconds=expires_in)

        mode_label = "LIVE" if self._settings.is_live else "DEMO"
        logger.info(
            f"KIS [{mode_label}] access token issued. "
            f"Expires at: {self._token_expires_at.strftime('%Y-%m-%d %H:%M:%S')}"
        )

    async def get_websocket_key(self) -> str:
        """WebSocket 접속키 발급"""
        if self._settings.is_backtest:
            raise KISAPIError("BACKTEST", "BACKTEST 모드에서는 WebSocket 접속이 불가합니다.")

        if self._websocket_key:
            return self._websocket_key

        url = f"{self._settings.base_url}/oauth2/Approval"
        body = {
            "grant_type": "client_credentials",
            "appkey": self._settings.app_key,
            "secretkey": self._settings.app_secret,
        }
        timeout = self._settings.api_timeout

        @retry(
            stop=stop_after_attempt(self._settings.api_retry_count),
            wait=wait_exponential(multiplier=1, min=2, max=10),
        )
        async def _do_approve():
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=body)
                response.raise_for_status()
                return response.json()

        data = await _do_approve()
        self._websocket_key = data["approval_key"]

        mode_label = "LIVE" if self._settings.is_live else "DEMO"
        logger.info(f"KIS [{mode_label}] WebSocket approval key issued.")
        return self._websocket_key


class KISClient:
    """
    한국투자증권 OpenAPI REST 클라이언트

    주요 기능:
    - KIS_TRADING_MODE에 따른 자동 인증/URL/TR_ID 선택
    - Rate Limit 준수 (초당 18건, 여유분 2건)
    - 에러 핸들링 및 환경변수 기반 재시도
    - BACKTEST 모드 가드 (API 호출 차단)
    """

    _rate_limit_semaphore = asyncio.Semaphore(18)
    _last_request_time: float = 0
    _min_interval: float = 0.05  # 50ms

    def __init__(self):
        self._settings = get_settings().kis
        self._token_manager = KISTokenManager()

    @property
    def trading_mode(self) -> TradingMode:
        return self._settings.trading_mode

    @property
    def is_live(self) -> bool:
        return self._settings.is_live

    @property
    def is_demo(self) -> bool:
        return self._settings.is_demo

    @property
    def is_backtest(self) -> bool:
        return self._settings.is_backtest

    def _get_tr_id(self, live_id: str, demo_id: str) -> str:
        """거래 모드에 따른 TR_ID 반환"""
        if self.is_live:
            return live_id
        return demo_id

    def _ensure_not_backtest(self) -> None:
        """BACKTEST 모드에서 API 호출 시도 시 차단"""
        if self.is_backtest:
            raise KISAPIError(
                "BACKTEST",
                "BACKTEST 모드에서는 실제 API 호출이 불가합니다. " "DEMO 또는 LIVE 모드로 전환하세요.",
            )

    async def _get_auth_headers(self, tr_id: str) -> dict:
        """인증 헤더 생성"""
        token = await self._token_manager.get_access_token()
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self._settings.app_key,
            "appsecret": self._settings.app_secret,
            "tr_id": tr_id,
        }

    async def _rate_limit_wait(self) -> None:
        """Rate Limit 준수를 위한 대기"""
        now = time.monotonic()
        elapsed = now - KISClient._last_request_time
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        KISClient._last_request_time = time.monotonic()

    async def _request(
        self,
        method: str,
        path: str,
        tr_id: str,
        params: Optional[dict] = None,
        body: Optional[dict] = None,
    ) -> dict:
        """공통 API 요청 메서드"""
        self._ensure_not_backtest()
        timeout = self._settings.api_timeout
        retry_count = self._settings.api_retry_count

        @retry(
            stop=stop_after_attempt(retry_count),
            wait=wait_exponential(multiplier=1, min=2, max=10),
        )
        async def _do_request():
            async with self._rate_limit_semaphore:
                await self._rate_limit_wait()

                url = f"{self._settings.base_url}{path}"
                headers = await self._get_auth_headers(tr_id)

                async with httpx.AsyncClient(timeout=timeout) as client:
                    if method == "GET":
                        response = await client.get(url, headers=headers, params=params)
                    elif method == "POST":
                        response = await client.post(url, headers=headers, json=body)
                    else:
                        raise ValueError(f"Unsupported HTTP method: {method}")

                    response.raise_for_status()
                    data = response.json()

                rt_cd = data.get("rt_cd")
                if rt_cd and rt_cd != "0":
                    error_msg = data.get("msg1", "Unknown KIS API error")
                    logger.error(f"KIS API error [{tr_id}]: {error_msg}")
                    raise KISAPIError(rt_cd, error_msg)

                return data

        return await _do_request()

    # ══════════════════════════════════════
    # 시세 조회 API
    # ══════════════════════════════════════
    async def get_kr_stock_price(self, ticker: str) -> dict:
        """국내주식 현재가 조회"""
        tr_id = "FHKST01010100"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
        }
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id,
            params=params,
        )

    async def get_kr_stock_daily(self, ticker: str, start_date: str, end_date: str, period: str = "D") -> dict:
        """국내주식 기간별 시세 조회 (일/주/월)"""
        tr_id = "FHKST03010100"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
            "FID_INPUT_DATE_1": start_date,
            "FID_INPUT_DATE_2": end_date,
            "FID_PERIOD_DIV_CODE": period,
            "FID_ORG_ADJ_PRC": "0",
        }
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            tr_id,
            params=params,
        )

    async def get_us_stock_price(self, ticker: str, exchange: str = "NAS") -> dict:
        """해외주식 현재가 조회"""
        tr_id = "HHDFS00000300"
        params = {"AUTH": "", "EXCD": exchange, "SYMB": ticker}
        return await self._request(
            "GET",
            "/uapi/overseas-price/v1/quotations/price",
            tr_id,
            params=params,
        )

    async def get_us_stock_daily(self, ticker: str, period: str = "D", count: int = 100, exchange: str = "NAS") -> dict:
        """해외주식 기간별 시세 조회"""
        tr_id = "HHDFS76240000"
        params = {
            "AUTH": "",
            "EXCD": exchange,
            "SYMB": ticker,
            "GUBN": "0",
            "BYMD": "",
            "MODP": "1",
        }
        return await self._request(
            "GET",
            "/uapi/overseas-price/v1/quotations/dailyprice",
            tr_id,
            params=params,
        )

    # ══════════════════════════════════════
    # 주문 API
    # ══════════════════════════════════════
    async def place_kr_order(
        self,
        ticker: str,
        side: str,
        quantity: int,
        price: int = 0,
        order_type: str = "01",
    ) -> dict:
        """
        국내주식 주문

        Args:
            ticker: 종목코드
            side: BUY / SELL
            quantity: 수량
            price: 가격 (시장가 시 0)
            order_type: 00(지정가), 01(시장가)
        """
        if side == "BUY":
            tr_id = self._get_tr_id("TTTC0802U", "VTTC0802U")
        else:
            tr_id = self._get_tr_id("TTTC0801U", "VTTC0801U")

        body = {
            "CANO": self._settings.account_no,
            "ACNT_PRDT_CD": self._settings.account_prod,
            "PDNO": ticker,
            "ORD_DVSN": order_type,
            "ORD_QTY": str(quantity),
            "ORD_UNPR": str(price),
        }

        mode_label = self.trading_mode.value
        logger.info(f"KIS [{mode_label}] Order: {side} {ticker} " f"qty={quantity} price={price} type={order_type}")

        return await self._request(
            "POST",
            "/uapi/domestic-stock/v1/trading/order-cash",
            tr_id,
            body=body,
        )

    async def place_us_order(
        self,
        ticker: str,
        side: str,
        quantity: int,
        price: float = 0,
        exchange: str = "NASD",
    ) -> dict:
        """
        해외주식 주문

        Args:
            ticker: 종목코드
            side: BUY / SELL
            quantity: 수량
            price: 가격 (시장가 시 0)
            exchange: NASD(나스닥), NYSE(뉴욕), AMEX
        """
        if side == "BUY":
            tr_id = self._get_tr_id("TTTS0308U", "VTTS0308U")
        else:
            tr_id = self._get_tr_id("TTTS0307U", "VTTS0307U")

        order_dvsn = "00" if price > 0 else "32"

        body = {
            "CANO": self._settings.account_no,
            "ACNT_PRDT_CD": self._settings.account_prod,
            "OVRS_EXCG_CD": exchange,
            "PDNO": ticker,
            "ORD_DVSN": order_dvsn,
            "ORD_QTY": str(quantity),
            "OVRS_ORD_UNPR": str(price),
        }

        mode_label = self.trading_mode.value
        logger.info(f"KIS [{mode_label}] US Order: {side} {ticker}@{exchange} " f"qty={quantity} price={price}")

        return await self._request(
            "POST",
            "/uapi/overseas-stock/v1/trading/order",
            tr_id,
            body=body,
        )

    # ══════════════════════════════════════
    # 잔고 조회 API
    # ══════════════════════════════════════
    async def get_kr_balance(self) -> dict:
        """국내주식 잔고 조회"""
        tr_id = self._get_tr_id("TTTC8434R", "VTTC8434R")
        params = {
            "CANO": self._settings.account_no,
            "ACNT_PRDT_CD": self._settings.account_prod,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        return await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            tr_id,
            params=params,
        )

    async def get_us_balance(self) -> dict:
        """해외주식 잔고 조회"""
        tr_id = self._get_tr_id("TTTS3012R", "VTTS3012R")
        params = {
            "CANO": self._settings.account_no,
            "ACNT_PRDT_CD": self._settings.account_prod,
            "OVRS_EXCG_CD": "NASD",
            "TR_CRCY_CD": "USD",
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }
        return await self._request(
            "GET",
            "/uapi/overseas-stock/v1/trading/inquire-balance",
            tr_id,
            params=params,
        )

    # ══════════════════════════════════════
    # 환율 조회
    # ══════════════════════════════════════
    async def get_exchange_rate(self) -> dict:
        """현재 환율 조회 (USD/KRW)"""
        tr_id = "CTRP6504R"
        params = {
            "CANO": self._settings.account_no,
            "ACNT_PRDT_CD": self._settings.account_prod,
            "OVRS_EXCG_CD": "NASD",
            "TR_CRCY_CD": "USD",
        }
        return await self._request(
            "GET",
            "/uapi/overseas-stock/v1/trading/inquire-present-balance",
            tr_id,
            params=params,
        )


class KISAPIError(Exception):
    """한국투자증권 API 에러"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"KIS API Error [{code}]: {message}")
