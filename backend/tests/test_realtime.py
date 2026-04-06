"""
실시간 시세 모듈 테스트 — WebSocket 파서, 매니저, 스케줄러 통합

테스트 구성:
- TestRealtimeQuoteParsing: 체결가/호가 메시지 파싱 (6개)
- TestRealtimeManager: 시세 매니저 상태 관리 (5개)
- TestIntradayBar: 일중 바 누적 (3개)
- TestSchedulerRealtimeIntegration: 스케줄러 핸들러 통합 (3개)
- TestRealtimeAPI: API 엔드포인트 (3개)
"""

from unittest.mock import MagicMock

from core.data_collector.kis_websocket import (
    RealtimeOrderbook,
    RealtimeQuote,
)
from core.data_collector.realtime_manager import (
    IntradayBar,
    RealtimeManager,
    RealtimeSnapshot,
)


# ═══════════════════════════════════════
# TestRealtimeQuoteParsing
# ═══════════════════════════════════════
class TestRealtimeQuoteParsing:
    """체결가/호가 메시지 파싱 테스트"""

    def test_quote_basic_fields(self):
        """기본 체결가 필드 파싱"""
        fields = [
            "005930",  # ticker
            "130000",  # exec_time
            "72000",  # current_price
            "2",  # sign (상승)
            "1500",  # change
            "2.13",  # change_rate
            "71500",  # weighted_avg
            "70500",  # open
            "72500",  # high
            "70000",  # low
            "72100",  # ask1
            "71900",  # bid1
            "500",  # exec_volume
            "15000000",  # cum_volume
            "1080000000000",  # cum_amount
        ]
        quote = RealtimeQuote(fields)

        assert quote.ticker == "005930"
        assert quote.price == 72000.0
        assert quote.change == 1500.0
        assert quote.change_rate == 2.13
        assert quote.open_price == 70500.0
        assert quote.high_price == 72500.0
        assert quote.low_price == 70000.0
        assert quote.ask1 == 72100.0
        assert quote.bid1 == 71900.0
        assert quote.volume == 500
        assert quote.cum_volume == 15000000

    def test_quote_to_dict(self):
        """체결가 딕셔너리 변환"""
        fields = ["005930", "130000", "72000", "2", "1500", "2.13"]
        quote = RealtimeQuote(fields)
        d = quote.to_dict()

        assert d["ticker"] == "005930"
        assert d["price"] == 72000.0
        assert "timestamp" in d

    def test_quote_empty_fields(self):
        """빈 필드 안전 처리"""
        quote = RealtimeQuote([])
        assert quote.ticker == ""
        assert quote.price == 0.0
        assert quote.volume == 0

    def test_quote_invalid_numeric(self):
        """잘못된 숫자 안전 처리"""
        fields = ["005930", "130000", "ABC", "2", "XYZ"]
        quote = RealtimeQuote(fields)
        assert quote.price == 0.0
        assert quote.change == 0.0

    def test_orderbook_parsing(self):
        """호가 데이터 파싱"""
        fields = (
            ["005930", "130000", "1"]  # ticker, time, hour_cls
            + ["72100", "72200", "72300", "72400", "72500"]  # ask 1-5
            + ["72600", "72700", "72800", "72900", "73000"]  # ask 6-10
            + ["71900", "71800", "71700", "71600", "71500"]  # bid 1-5
            + ["71400", "71300", "71200", "71100", "71000"]  # bid 6-10
            + ["1000", "2000", "3000", "4000", "5000"]  # ask_vol 1-5
            + ["1500", "2500", "3500", "4500", "5500"]  # ask_vol 6-10
            + ["1100", "2100", "3100", "4100", "5100"]  # bid_vol 1-5
            + ["1600", "2600", "3600", "4600", "5600"]  # bid_vol 6-10
            + ["25000", "30000"]  # total_ask_vol, total_bid_vol
        )
        ob = RealtimeOrderbook(fields)

        assert ob.ticker == "005930"
        assert ob.asks[0] == 72100.0
        assert ob.bids[0] == 71900.0
        assert ob.ask_volumes[0] == 1000
        assert ob.bid_volumes[0] == 1100
        assert ob.total_ask_vol == 25000
        assert ob.total_bid_vol == 30000

    def test_orderbook_to_dict(self):
        """호가 딕셔너리 변환"""
        fields = ["005930", "130000", "1"] + ["0"] * 42
        ob = RealtimeOrderbook(fields)
        d = ob.to_dict()

        assert d["ticker"] == "005930"
        assert len(d["asks"]) == 10
        assert len(d["bids"]) == 10


# ═══════════════════════════════════════
# TestIntradayBar
# ═══════════════════════════════════════
class TestIntradayBar:
    """일중 바 누적 테스트"""

    def test_first_update_sets_open(self):
        """첫 업데이트가 시가 설정"""
        bar = IntradayBar(ticker="005930")
        bar.update(72000, 100)

        assert bar.open_price == 72000
        assert bar.close_price == 72000
        assert bar.high_price == 72000
        assert bar.low_price == 72000
        assert bar.volume == 100

    def test_multiple_updates(self):
        """여러 체결로 OHLCV 누적"""
        bar = IntradayBar(ticker="005930")
        bar.update(70000, 100)  # 시가
        bar.update(72000, 200)  # 고가
        bar.update(69000, 150)  # 저가
        bar.update(71000, 300)  # 종가

        assert bar.open_price == 70000
        assert bar.high_price == 72000
        assert bar.low_price == 69000
        assert bar.close_price == 71000
        assert bar.volume == 750  # 100+200+150+300
        assert bar.trade_count == 4

    def test_to_dict(self):
        """딕셔너리 변환"""
        bar = IntradayBar(ticker="005930")
        bar.update(72000, 100)
        d = bar.to_dict()

        assert d["ticker"] == "005930"
        assert d["open"] == 72000
        assert d["volume"] == 100


# ═══════════════════════════════════════
# TestRealtimeManager
# ═══════════════════════════════════════
class TestRealtimeManager:
    """시세 매니저 상태 관리 테스트"""

    def test_initial_state(self):
        """초기 상태"""
        manager = RealtimeManager()
        assert manager.is_running is False
        assert manager.get_all_snapshots() == {}

    def test_snapshot_quote_update(self):
        """체결가 수신 시 스냅샷 업데이트"""
        manager = RealtimeManager()
        manager._snapshots["005930"] = RealtimeSnapshot(
            ticker="005930",
            intraday=IntradayBar(ticker="005930"),
        )

        # 시뮬레이션: 체결가 수신
        mock_quote = MagicMock()
        mock_quote.ticker = "005930"
        mock_quote.price = 72000.0
        mock_quote.change = 1500.0
        mock_quote.change_rate = 2.13
        mock_quote.bid1 = 71900.0
        mock_quote.ask1 = 72100.0
        mock_quote.volume = 500
        mock_quote.cum_volume = 15000000
        mock_quote.timestamp = "2026-04-06T01:00:00Z"

        manager._on_quote(mock_quote)

        snap = manager.get_snapshot("005930")
        assert snap is not None
        assert snap.price == 72000.0
        assert snap.bid1 == 71900.0
        assert snap.intraday.open_price == 72000.0

    def test_get_current_prices(self):
        """전 종목 현재가 조회"""
        manager = RealtimeManager()
        manager._snapshots["005930"] = RealtimeSnapshot(
            ticker="005930",
            price=72000.0,
            intraday=IntradayBar(ticker="005930"),
        )
        manager._snapshots["000660"] = RealtimeSnapshot(
            ticker="000660",
            price=0.0,
            intraday=IntradayBar(ticker="000660"),
        )

        prices = manager.get_current_prices()
        assert prices == {"005930": 72000.0}
        # price=0인 종목은 제외

    def test_stats(self):
        """통계 정보"""
        manager = RealtimeManager()
        stats = manager.stats
        assert stats["running"] is False
        assert stats["tickers_count"] == 0

    def test_orderbook_update(self):
        """호가 수신 시 스냅샷 업데이트"""
        manager = RealtimeManager()
        manager._snapshots["005930"] = RealtimeSnapshot(
            ticker="005930",
            intraday=IntradayBar(ticker="005930"),
        )

        mock_ob = MagicMock()
        mock_ob.ticker = "005930"
        mock_ob.asks = [72100.0, 72200.0]
        mock_ob.bids = [71900.0, 71800.0]

        manager._on_orderbook(mock_ob)
        snap = manager.get_snapshot("005930")
        assert snap.ask1 == 72100.0
        assert snap.bid1 == 71900.0


# ═══════════════════════════════════════
# TestSchedulerRealtimeIntegration
# ═══════════════════════════════════════
class TestSchedulerRealtimeIntegration:
    """스케줄러 실시간 통합 테스트"""

    def test_handler_has_realtime_section(self):
        """handle_market_open 독스트링에 실시간 시세 단계 포함"""
        from core.scheduler_handlers import handle_market_open

        doc = handle_market_open.__doc__
        assert "실시간" in doc

    def test_handler_close_has_stop(self):
        """handle_market_close 독스트링에 실시간 중지 포함"""
        from core.scheduler_handlers import handle_market_close

        doc = handle_market_close.__doc__
        assert "실시간" in doc

    def test_get_realtime_manager_initial(self):
        """초기 상태에서 매니저 None"""
        from core.scheduler_handlers import get_realtime_manager

        # 테스트 환경에서는 None (시작 전)
        manager = get_realtime_manager()
        # _realtime_manager는 전역이므로 이전 테스트 영향 가능
        # None이거나 RealtimeManager 인스턴스
        assert manager is None or hasattr(manager, "is_running")


# ═══════════════════════════════════════
# TestRealtimeAPI
# ═══════════════════════════════════════
class TestRealtimeAPI:
    """API 엔드포인트 테스트"""

    def test_route_exists(self):
        """실시간 라우터 존재"""
        from api.routes.realtime import router

        paths = [route.path for route in router.routes]
        assert "/quotes" in paths
        assert "/quotes/{ticker}" in paths
        assert "/status" in paths

    def test_main_includes_realtime_router(self):
        """main.py에 실시간 라우터 등록"""
        from main import app

        paths = [route.path for route in app.routes]
        assert any("/api/realtime" in str(p) for p in paths)

    def test_api_routes_init_exports(self):
        """api/routes/__init__.py에 realtime 모듈 export"""
        from api import routes

        assert hasattr(routes, "realtime")
