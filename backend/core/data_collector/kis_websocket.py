"""
KIS WebSocket 실시간 시세 수신 모듈

한국투자증권 OpenAPI WebSocket을 통해 실시간 체결가/호가 데이터를 수신합니다.

주요 기능:
- WebSocket 자동 연결/재연결 (지수 백오프)
- 종목 구독/해제: 실시간 체결가(H0STCNT0), 호가(H0STASP0)
- 수신 데이터 파싱: 체결가, 호가 → 구조화된 dict
- 콜백 기반 데이터 전달: on_quote, on_orderbook 등록
- Redis 캐시: 최신 시세 자동 저장
- Heartbeat(PINGPONG) 자동 응답

KIS WebSocket 프로토콜:
    구독 요청: {"header": {"approval_key": ..., "tr_type": "1", "content-type": "utf-8"},
                "body": {"input": {"tr_id": "H0STCNT0", "tr_key": "005930"}}}
    해제 요청: tr_type = "2"
    수신 형식: '0|H0STCNT0|002|...' (헤더|TR_ID|건수|데이터)
    PINGPONG:  '1|...' → 같은 메시지 echo

사용법:
    ws = KISRealtimeClient()
    ws.on_quote = my_quote_handler    # 체결가 콜백
    ws.on_orderbook = my_hoga_handler # 호가 콜백

    await ws.connect()
    await ws.subscribe("005930")      # 삼성전자 구독
    await ws.subscribe("000660")      # SK하이닉스 구독
    # ... 수신 루프 자동 실행 ...
    await ws.unsubscribe("005930")
    await ws.disconnect()
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import websockets
from websockets.exceptions import ConnectionClosed

from config.logging import logger
from config.settings import get_settings
from core.data_collector.kis_client import KISTokenManager

# KIS WebSocket TR_ID 매핑
TR_ID_QUOTE = "H0STCNT0"  # 실시간 체결가
TR_ID_ORDERBOOK = "H0STASP0"  # 실시간 호가 (10단계)

# 체결가 데이터 필드 (H0STCNT0 응답 '|' 구분, 총 46개)
QUOTE_FIELDS = [
    "ticker",
    "exec_time",
    "current_price",
    "sign",
    "change",
    "change_rate",
    "weighted_avg_price",
    "open",
    "high",
    "low",
    "ask1",
    "bid1",
    "exec_volume",
    "cum_volume",
    "cum_amount",
    "sell_cum_volume",
    "buy_cum_volume",
    "turnover_rate",
    "prev_cum_volume_rate",
    "ask_count",
    "bid_count",
    "net_bid_count",
    "vol_power",
    "exec_no",
    "exec_type",
    "ask_exec_volume",
    "bid_exec_volume",
    "confirm_yn",
    "reserved1",
    "prev_close",
    "new_capital_sign",
    "new_capital_change_rate",
    "reserved2",
    "capital_acum",
    "capital_turn_rate",
    "reserved3",
    "reserved4",
    "reserved5",
    "market_cap",
    "reserved6",
    "reserved7",
    "vi_type",
    "reserved8",
    "reserved9",
    "reserved10",
    "reserved11",
]

# 호가 핵심 필드 인덱스 (H0STASP0, 총 59개 필드에서 주요 항목)
ORDERBOOK_MAIN_FIELDS = [
    "ticker",
    "exec_time",
    "hour_cls",
    "ask1",
    "ask2",
    "ask3",
    "ask4",
    "ask5",
    "ask6",
    "ask7",
    "ask8",
    "ask9",
    "ask10",
    "bid1",
    "bid2",
    "bid3",
    "bid4",
    "bid5",
    "bid6",
    "bid7",
    "bid8",
    "bid9",
    "bid10",
    "ask_vol1",
    "ask_vol2",
    "ask_vol3",
    "ask_vol4",
    "ask_vol5",
    "ask_vol6",
    "ask_vol7",
    "ask_vol8",
    "ask_vol9",
    "ask_vol10",
    "bid_vol1",
    "bid_vol2",
    "bid_vol3",
    "bid_vol4",
    "bid_vol5",
    "bid_vol6",
    "bid_vol7",
    "bid_vol8",
    "bid_vol9",
    "bid_vol10",
    "total_ask_vol",
    "total_bid_vol",
]


class RealtimeQuote:
    """실시간 체결가 데이터"""

    __slots__ = [
        "ticker",
        "price",
        "change",
        "change_rate",
        "volume",
        "cum_volume",
        "cum_amount",
        "open_price",
        "high_price",
        "low_price",
        "ask1",
        "bid1",
        "exec_time",
        "timestamp",
    ]

    def __init__(self, raw_fields: list[str]):
        self.ticker = raw_fields[0] if len(raw_fields) > 0 else ""
        self.exec_time = raw_fields[1] if len(raw_fields) > 1 else ""
        self.price = _safe_float(raw_fields, 2)
        self.change = _safe_float(raw_fields, 4)
        self.change_rate = _safe_float(raw_fields, 5)
        self.open_price = _safe_float(raw_fields, 7)
        self.high_price = _safe_float(raw_fields, 8)
        self.low_price = _safe_float(raw_fields, 9)
        self.ask1 = _safe_float(raw_fields, 10)
        self.bid1 = _safe_float(raw_fields, 11)
        self.volume = _safe_int(raw_fields, 12)
        self.cum_volume = _safe_int(raw_fields, 13)
        self.cum_amount = _safe_float(raw_fields, 14)
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "price": self.price,
            "change": self.change,
            "change_rate": self.change_rate,
            "volume": self.volume,
            "cum_volume": self.cum_volume,
            "cum_amount": self.cum_amount,
            "open": self.open_price,
            "high": self.high_price,
            "low": self.low_price,
            "ask1": self.ask1,
            "bid1": self.bid1,
            "exec_time": self.exec_time,
            "timestamp": self.timestamp,
        }


class RealtimeOrderbook:
    """실시간 호가 데이터"""

    __slots__ = [
        "ticker",
        "exec_time",
        "asks",
        "bids",
        "ask_volumes",
        "bid_volumes",
        "total_ask_vol",
        "total_bid_vol",
        "timestamp",
    ]

    def __init__(self, raw_fields: list[str]):
        self.ticker = raw_fields[0] if len(raw_fields) > 0 else ""
        self.exec_time = raw_fields[1] if len(raw_fields) > 1 else ""

        # 매도호가 10단계 (인덱스 3~12)
        self.asks = [_safe_float(raw_fields, i) for i in range(3, 13)]
        # 매수호가 10단계 (인덱스 13~22)
        self.bids = [_safe_float(raw_fields, i) for i in range(13, 23)]
        # 매도잔량 10단계 (인덱스 23~32)
        self.ask_volumes = [_safe_int(raw_fields, i) for i in range(23, 33)]
        # 매수잔량 10단계 (인덱스 33~42)
        self.bid_volumes = [_safe_int(raw_fields, i) for i in range(33, 43)]

        self.total_ask_vol = _safe_int(raw_fields, 43)
        self.total_bid_vol = _safe_int(raw_fields, 44)
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "exec_time": self.exec_time,
            "asks": self.asks,
            "bids": self.bids,
            "ask_volumes": self.ask_volumes,
            "bid_volumes": self.bid_volumes,
            "total_ask_vol": self.total_ask_vol,
            "total_bid_vol": self.total_bid_vol,
            "timestamp": self.timestamp,
        }


def _safe_float(fields: list[str], idx: int) -> float:
    """안전한 float 변환"""
    try:
        return float(fields[idx]) if idx < len(fields) else 0.0
    except (ValueError, IndexError):
        return 0.0


def _safe_int(fields: list[str], idx: int) -> int:
    """안전한 int 변환"""
    try:
        return int(fields[idx]) if idx < len(fields) else 0
    except (ValueError, IndexError):
        return 0


# 콜백 타입
QuoteCallback = Callable[[RealtimeQuote], Any]
OrderbookCallback = Callable[[RealtimeOrderbook], Any]


class KISRealtimeClient:
    """
    KIS WebSocket 실시간 시세 클라이언트

    주요 기능:
    - 자동 연결/재연결 (지수 백오프: 1s → 2s → 4s → ... → 60s)
    - 종목 구독/해제 (체결가 + 호가)
    - PINGPONG 자동 응답
    - 콜백 기반 데이터 전달
    - Redis 캐시 (선택적)
    """

    MAX_RECONNECT_DELAY = 60  # 최대 재연결 대기시간 (초)
    MAX_SUBSCRIPTIONS = 40  # KIS 최대 동시 구독 수

    def __init__(
        self,
        subscribe_orderbook: bool = False,
        redis_cache: bool = True,
    ):
        """
        Args:
            subscribe_orderbook: True면 호가도 함께 구독
            redis_cache: True면 Redis에 최신 시세 캐시
        """
        self._settings = get_settings().kis
        self._token_manager = KISTokenManager()
        self._subscribe_orderbook = subscribe_orderbook
        self._redis_cache = redis_cache

        # WebSocket 상태
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connected = False
        self._subscribed_tickers: set[str] = set()
        self._receive_task: Optional[asyncio.Task] = None
        self._reconnect_delay = 1.0

        # 콜백
        self.on_quote: Optional[QuoteCallback] = None
        self.on_orderbook: Optional[OrderbookCallback] = None
        self.on_disconnect: Optional[Callable] = None

        # 통계
        self._stats = {
            "messages_received": 0,
            "quotes_processed": 0,
            "orderbooks_processed": 0,
            "reconnections": 0,
            "errors": 0,
        }

    async def connect(self) -> bool:
        """
        WebSocket 연결

        Returns:
            연결 성공 여부
        """
        if self._settings.is_backtest:
            logger.warning("[KISWebSocket] BACKTEST 모드: WebSocket 비활성화")
            return False

        try:
            await self._token_manager.get_websocket_key()
            ws_url = self._settings.active_credential.websocket_url

            logger.info(f"[KISWebSocket] Connecting to {ws_url}...")

            self._ws = await websockets.connect(
                ws_url,
                ping_interval=30,
                ping_timeout=10,
                close_timeout=5,
            )
            self._connected = True
            self._reconnect_delay = 1.0

            # 수신 루프 시작
            self._receive_task = asyncio.create_task(self._receive_loop())

            mode_label = "LIVE" if self._settings.is_live else "DEMO"
            logger.info(f"[KISWebSocket] Connected [{mode_label}] " f"(subscriptions: {len(self._subscribed_tickers)})")
            return True

        except Exception as e:
            logger.error(f"[KISWebSocket] Connection failed: {e}")
            self._connected = False
            return False

    async def disconnect(self):
        """WebSocket 연결 종료"""
        self._connected = False

        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        if self._ws:
            await self._ws.close()
            self._ws = None

        self._subscribed_tickers.clear()
        logger.info("[KISWebSocket] Disconnected")

    async def subscribe(self, ticker: str) -> bool:
        """
        종목 실시간 시세 구독

        Args:
            ticker: 종목코드 (예: "005930")

        Returns:
            성공 여부
        """
        if not self._connected or self._ws is None:
            logger.warning(f"[KISWebSocket] Not connected, cannot subscribe {ticker}")
            return False

        if len(self._subscribed_tickers) >= self.MAX_SUBSCRIPTIONS:
            logger.warning(f"[KISWebSocket] Max subscriptions ({self.MAX_SUBSCRIPTIONS}) reached")
            return False

        if ticker in self._subscribed_tickers:
            return True

        try:
            ws_key = await self._token_manager.get_websocket_key()

            # 체결가 구독
            await self._send_subscribe(ws_key, TR_ID_QUOTE, ticker, subscribe=True)

            # 호가 구독 (선택적)
            if self._subscribe_orderbook:
                await self._send_subscribe(ws_key, TR_ID_ORDERBOOK, ticker, subscribe=True)

            self._subscribed_tickers.add(ticker)
            logger.info(f"[KISWebSocket] Subscribed: {ticker} " f"(total: {len(self._subscribed_tickers)})")
            return True

        except Exception as e:
            logger.error(f"[KISWebSocket] Subscribe {ticker} failed: {e}")
            return False

    async def unsubscribe(self, ticker: str) -> bool:
        """종목 구독 해제"""
        if not self._connected or self._ws is None:
            return False

        if ticker not in self._subscribed_tickers:
            return True

        try:
            ws_key = await self._token_manager.get_websocket_key()

            await self._send_subscribe(ws_key, TR_ID_QUOTE, ticker, subscribe=False)
            if self._subscribe_orderbook:
                await self._send_subscribe(ws_key, TR_ID_ORDERBOOK, ticker, subscribe=False)

            self._subscribed_tickers.discard(ticker)
            logger.info(f"[KISWebSocket] Unsubscribed: {ticker}")
            return True

        except Exception as e:
            logger.error(f"[KISWebSocket] Unsubscribe {ticker} failed: {e}")
            return False

    async def subscribe_batch(self, tickers: list[str]) -> int:
        """
        여러 종목 일괄 구독

        Args:
            tickers: 종목코드 리스트

        Returns:
            성공 구독 수
        """
        success = 0
        for ticker in tickers:
            if await self.subscribe(ticker):
                success += 1
            # KIS rate limit 준수
            await asyncio.sleep(0.1)
        return success

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    @property
    def subscribed_tickers(self) -> set[str]:
        return self._subscribed_tickers.copy()

    @property
    def stats(self) -> dict:
        return self._stats.copy()

    # ── 내부 메서드 ──

    async def _send_subscribe(
        self,
        ws_key: str,
        tr_id: str,
        ticker: str,
        subscribe: bool = True,
    ):
        """구독/해제 메시지 전송"""
        msg = {
            "header": {
                "approval_key": ws_key,
                "custtype": "P",
                "tr_type": "1" if subscribe else "2",
                "content-type": "utf-8",
            },
            "body": {
                "input": {
                    "tr_id": tr_id,
                    "tr_key": ticker,
                },
            },
        }
        await self._ws.send(json.dumps(msg))

    async def _receive_loop(self):
        """WebSocket 수신 루프"""
        try:
            while self._connected and self._ws is not None:
                try:
                    raw = await self._ws.recv()
                    self._stats["messages_received"] += 1
                    await self._handle_message(raw)
                except ConnectionClosed:
                    logger.warning("[KISWebSocket] Connection closed")
                    break

        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"[KISWebSocket] Receive loop error: {e}")
            self._stats["errors"] += 1

        # 재연결
        if self._connected:
            await self._reconnect()

    async def _handle_message(self, raw: str):
        """수신 메시지 처리"""
        if not raw:
            return

        # PINGPONG 응답
        first_char = raw[0]
        if first_char == "1":
            # PINGPONG: echo back
            await self._ws.send(raw)
            return

        if first_char == "0":
            # 데이터 메시지: '0|TR_ID|건수|필드1^필드2^...'
            parts = raw.split("|", 3)
            if len(parts) < 4:
                return

            tr_id = parts[1]
            data_str = parts[3]

            # 여러 건의 데이터가 '^' 또는 '\x01'로 구분될 수 있음
            records = data_str.split("^")

            if tr_id == TR_ID_QUOTE:
                quote = RealtimeQuote(records)
                self._stats["quotes_processed"] += 1

                if self.on_quote:
                    try:
                        result = self.on_quote(quote)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.debug(f"[KISWebSocket] Quote callback error: {e}")

                # Redis 캐시
                if self._redis_cache:
                    await self._cache_quote(quote)

            elif tr_id == TR_ID_ORDERBOOK:
                orderbook = RealtimeOrderbook(records)
                self._stats["orderbooks_processed"] += 1

                if self.on_orderbook:
                    try:
                        result = self.on_orderbook(orderbook)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.debug(f"[KISWebSocket] Orderbook callback error: {e}")

        else:
            # JSON 응답 (구독 확인 등)
            try:
                data = json.loads(raw)
                header = data.get("header", {})
                tr_id = header.get("tr_id", "")
                msg_cd = header.get("msg_cd", "")

                if msg_cd:
                    logger.debug(
                        f"[KISWebSocket] Response: tr_id={tr_id}, " f"msg_cd={msg_cd}, msg={header.get('msg1', '')}"
                    )
            except json.JSONDecodeError:
                pass

    async def _cache_quote(self, quote: RealtimeQuote):
        """Redis에 최신 시세 캐시"""
        try:
            from db.database import RedisManager

            redis = RedisManager.get_client()
            key = f"quote:realtime:{quote.ticker}"
            await redis.set(
                key,
                json.dumps(quote.to_dict()),
                ex=300,  # 5분 TTL (장중 지속 갱신)
            )
        except Exception:
            pass  # Redis 실패 무시

    async def _reconnect(self):
        """자동 재연결 (지수 백오프)"""
        self._stats["reconnections"] += 1
        saved_tickers = self._subscribed_tickers.copy()
        self._subscribed_tickers.clear()

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        logger.info(
            f"[KISWebSocket] Reconnecting in {self._reconnect_delay:.0f}s... "
            f"(attempt #{self._stats['reconnections']})"
        )
        await asyncio.sleep(self._reconnect_delay)

        # 지수 백오프
        self._reconnect_delay = min(self._reconnect_delay * 2, self.MAX_RECONNECT_DELAY)

        success = await self.connect()
        if success and saved_tickers:
            # 이전 구독 복원
            restored = await self.subscribe_batch(list(saved_tickers))
            logger.info(f"[KISWebSocket] Reconnected, " f"restored {restored}/{len(saved_tickers)} subscriptions")

        if self.on_disconnect:
            try:
                result = self.on_disconnect()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass
