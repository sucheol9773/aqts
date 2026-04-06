"""
실시간 시세 관리 서비스 (Realtime Quote Manager)

KIS WebSocket을 통해 수신한 실시간 시세를 관리하고,
스케줄러 및 RL 추론 파이프라인에 최신 데이터를 제공합니다.

주요 기능:
- WebSocket 라이프사이클 관리 (장 시작 → 장 종료)
- 구독 관리: 유니버스 전 종목 자동 구독
- 인메모리 캐시: 종목별 최신 시세 + 일중 OHLCV 누적
- 스냅샷: 현재 시세 일괄 조회
- Redis 동기화: 외부 서비스(API)에서 조회 가능

사용법:
    manager = RealtimeManager()
    await manager.start(tickers=["005930", "000660"])
    # ... 장중 ...
    snapshot = manager.get_snapshot("005930")
    all_quotes = manager.get_all_snapshots()
    await manager.stop()
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from config.logging import logger


@dataclass
class IntradayBar:
    """일중 시세 누적 바"""

    ticker: str
    open_price: float = 0.0
    high_price: float = 0.0
    low_price: float = 0.0
    close_price: float = 0.0
    volume: int = 0
    trade_count: int = 0
    first_update: str = ""
    last_update: str = ""

    def update(self, price: float, vol: int = 0):
        """새 체결가로 바 업데이트"""
        if self.open_price == 0:
            self.open_price = price
            self.first_update = datetime.now(timezone.utc).isoformat()

        self.close_price = price
        self.high_price = max(self.high_price, price) if self.high_price > 0 else price
        self.low_price = min(self.low_price, price) if self.low_price > 0 else price
        self.volume += vol
        self.trade_count += 1
        self.last_update = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "open": self.open_price,
            "high": self.high_price,
            "low": self.low_price,
            "close": self.close_price,
            "volume": self.volume,
            "trade_count": self.trade_count,
            "first_update": self.first_update,
            "last_update": self.last_update,
        }


@dataclass
class RealtimeSnapshot:
    """종목별 실시간 스냅샷"""

    ticker: str
    price: float = 0.0
    change: float = 0.0
    change_rate: float = 0.0
    bid1: float = 0.0
    ask1: float = 0.0
    volume: int = 0
    cum_volume: int = 0
    intraday: IntradayBar = field(default_factory=lambda: IntradayBar(ticker=""))
    last_update: str = ""

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "price": self.price,
            "change": self.change,
            "change_rate": self.change_rate,
            "bid1": self.bid1,
            "ask1": self.ask1,
            "volume": self.volume,
            "cum_volume": self.cum_volume,
            "intraday": self.intraday.to_dict(),
            "last_update": self.last_update,
        }


class RealtimeManager:
    """
    실시간 시세 관리 서비스

    스케줄러에서 장 시작/종료 시 start()/stop() 호출.
    장중에는 자동으로 WebSocket 수신 + 캐시 갱신.
    """

    def __init__(self, subscribe_orderbook: bool = False):
        """
        Args:
            subscribe_orderbook: 호가 데이터도 구독할지 여부
        """
        self._subscribe_orderbook = subscribe_orderbook
        self._ws_client = None
        self._snapshots: dict[str, RealtimeSnapshot] = {}
        self._running = False
        self._tickers: list[str] = []

    async def start(self, tickers: list[str]) -> bool:
        """
        실시간 수신 시작

        Args:
            tickers: 구독할 종목 리스트

        Returns:
            성공 여부
        """
        from core.data_collector.kis_websocket import KISRealtimeClient

        self._tickers = tickers

        # 스냅샷 초기화
        for ticker in tickers:
            self._snapshots[ticker] = RealtimeSnapshot(
                ticker=ticker,
                intraday=IntradayBar(ticker=ticker),
            )

        # WebSocket 클라이언트 생성
        self._ws_client = KISRealtimeClient(
            subscribe_orderbook=self._subscribe_orderbook,
            redis_cache=True,
        )
        self._ws_client.on_quote = self._on_quote
        if self._subscribe_orderbook:
            self._ws_client.on_orderbook = self._on_orderbook

        # 연결
        connected = await self._ws_client.connect()
        if not connected:
            logger.warning("[RealtimeManager] WebSocket 연결 실패")
            return False

        # 일괄 구독
        subscribed = await self._ws_client.subscribe_batch(tickers)
        self._running = True

        logger.info(f"[RealtimeManager] Started: " f"{subscribed}/{len(tickers)} tickers subscribed")
        return True

    async def stop(self):
        """실시간 수신 중지"""
        self._running = False
        if self._ws_client:
            await self._ws_client.disconnect()
            self._ws_client = None
        logger.info(
            f"[RealtimeManager] Stopped "
            f"({sum(1 for s in self._snapshots.values() if s.price > 0)} "
            f"tickers with data)"
        )

    def get_snapshot(self, ticker: str) -> RealtimeSnapshot | None:
        """단일 종목 스냅샷 조회"""
        return self._snapshots.get(ticker)

    def get_all_snapshots(self) -> dict[str, RealtimeSnapshot]:
        """전체 스냅샷 조회"""
        return self._snapshots.copy()

    def get_current_prices(self) -> dict[str, float]:
        """전 종목 현재가 딕셔너리"""
        return {ticker: snap.price for ticker, snap in self._snapshots.items() if snap.price > 0}

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict:
        """통계 정보"""
        if self._ws_client:
            ws_stats = self._ws_client.stats
        else:
            ws_stats = {}

        return {
            "running": self._running,
            "tickers_count": len(self._tickers),
            "tickers_with_data": sum(1 for s in self._snapshots.values() if s.price > 0),
            "ws_stats": ws_stats,
        }

    # ── 콜백 핸들러 ──

    def _on_quote(self, quote):
        """체결가 수신 콜백"""
        ticker = quote.ticker
        if ticker not in self._snapshots:
            self._snapshots[ticker] = RealtimeSnapshot(
                ticker=ticker,
                intraday=IntradayBar(ticker=ticker),
            )

        snap = self._snapshots[ticker]
        snap.price = quote.price
        snap.change = quote.change
        snap.change_rate = quote.change_rate
        snap.bid1 = quote.bid1
        snap.ask1 = quote.ask1
        snap.volume = quote.volume
        snap.cum_volume = quote.cum_volume
        snap.last_update = quote.timestamp

        # 일중 바 업데이트
        snap.intraday.update(quote.price, quote.volume)

    def _on_orderbook(self, orderbook):
        """호가 수신 콜백"""
        ticker = orderbook.ticker
        if ticker not in self._snapshots:
            return

        snap = self._snapshots[ticker]
        if orderbook.asks:
            snap.ask1 = orderbook.asks[0]
        if orderbook.bids:
            snap.bid1 = orderbook.bids[0]
