"""
드라이런 엔진 테스트

테스트 대상:
  1. DryRunOrder — 가상 주문 데이터 구조
  2. DryRunSession — 세션 생명주기 (시작/종료/실패)
  3. DryRunReport — 종합 리포트 생성
  4. DryRunEngine — 오케스트레이터 (세션 관리, 주문 기록)
  5. OrderExecutor dry_run 모드 — 주문 인터셉트
  6. get_dry_run_engine — 글로벌 인스턴스 싱글톤
"""

from unittest.mock import MagicMock, patch

import pytest

from config.constants import Market, OrderSide, OrderStatus, OrderType
from core.dry_run.engine import (
    DryRunEngine,
    DryRunOrder,
    DryRunReport,
    DryRunSession,
    DryRunStatus,
    get_dry_run_engine,
)
from core.order_executor.executor import OrderExecutor, OrderRequest


# ══════════════════════════════════════
# DryRunOrder 테스트
# ══════════════════════════════════════
class TestDryRunOrder:
    """DryRunOrder 데이터 구조 테스트"""

    def test_create_order(self):
        """기본 주문 생성"""
        order = DryRunOrder(
            order_id="DRY_005930_123456",
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=10,
        )
        assert order.order_id == "DRY_005930_123456"
        assert order.ticker == "005930"
        assert order.market == Market.KRX
        assert order.side == OrderSide.BUY
        assert order.quantity == 10
        assert order.order_type == OrderType.MARKET
        assert order.risk_check_passed is True

    def test_order_with_limit_price(self):
        """지정가 주문 생성"""
        order = DryRunOrder(
            order_id="DRY_AAPL_123456",
            ticker="AAPL",
            market=Market.NASDAQ,
            side=OrderSide.SELL,
            quantity=5,
            order_type=OrderType.LIMIT,
            limit_price=150.0,
        )
        assert order.order_type == OrderType.LIMIT
        assert order.limit_price == 150.0

    def test_order_with_estimated_price(self):
        """추정 금액 계산"""
        order = DryRunOrder(
            order_id="DRY_005930_123456",
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=100,
            estimated_price=70000.0,
            estimated_amount=7000000.0,
        )
        assert order.estimated_price == 70000.0
        assert order.estimated_amount == 7000000.0

    def test_order_risk_blocked(self):
        """리스크 체크 차단된 주문"""
        order = DryRunOrder(
            order_id="DRY_005930_123456",
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=10,
            risk_check_passed=False,
            risk_check_details="일일 손실 한도 초과",
        )
        assert order.risk_check_passed is False
        assert "손실 한도" in order.risk_check_details

    def test_order_to_dict(self):
        """딕셔너리 변환"""
        order = DryRunOrder(
            order_id="DRY_005930_123456",
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=10,
            reason="앙상블 시그널",
        )
        d = order.to_dict()
        assert d["order_id"] == "DRY_005930_123456"
        assert d["ticker"] == "005930"
        assert d["market"] == "KRX"
        assert d["side"] == "BUY"
        assert d["quantity"] == 10
        assert d["reason"] == "앙상블 시그널"
        assert "created_at" in d


# ══════════════════════════════════════
# DryRunSession 테스트
# ══════════════════════════════════════
class TestDryRunSession:
    """DryRunSession 생명주기 테스트"""

    def test_create_session(self):
        """세션 생성 시 기본 상태"""
        session = DryRunSession()
        assert session.status == DryRunStatus.RUNNING
        assert session.session_id is not None
        assert len(session.session_id) > 0
        assert session.orders == []
        assert session.ended_at is None

    def test_add_order(self):
        """세션에 주문 추가"""
        session = DryRunSession()
        order = DryRunOrder(
            order_id="DRY_005930_1",
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=10,
        )
        session.add_order(order)
        assert len(session.orders) == 1
        assert session.orders[0].ticker == "005930"

    def test_add_multiple_orders(self):
        """세션에 여러 주문 추가"""
        session = DryRunSession()
        for i in range(5):
            order = DryRunOrder(
                order_id=f"DRY_005930_{i}",
                ticker="005930",
                market=Market.KRX,
                side=OrderSide.BUY if i % 2 == 0 else OrderSide.SELL,
                quantity=10 + i,
            )
            session.add_order(order)
        assert len(session.orders) == 5

    def test_complete_session(self):
        """세션 정상 완료"""
        session = DryRunSession()
        session.complete()
        assert session.status == DryRunStatus.COMPLETED
        assert session.ended_at is not None

    def test_fail_session(self):
        """세션 실패 처리"""
        session = DryRunSession()
        session.fail("파이프라인 오류 발생")
        assert session.status == DryRunStatus.FAILED
        assert session.ended_at is not None
        assert session.error_message == "파이프라인 오류 발생"

    def test_session_summary(self):
        """세션 요약 통계"""
        session = DryRunSession()
        # BUY 2건, SELL 1건, 리스크 차단 1건
        session.add_order(
            DryRunOrder(
                order_id="DRY_1",
                ticker="005930",
                market=Market.KRX,
                side=OrderSide.BUY,
                quantity=10,
                estimated_price=70000.0,
                estimated_amount=700000.0,
            )
        )
        session.add_order(
            DryRunOrder(
                order_id="DRY_2",
                ticker="035720",
                market=Market.KRX,
                side=OrderSide.BUY,
                quantity=5,
                estimated_price=50000.0,
                estimated_amount=250000.0,
            )
        )
        session.add_order(
            DryRunOrder(
                order_id="DRY_3",
                ticker="005930",
                market=Market.KRX,
                side=OrderSide.SELL,
                quantity=3,
                estimated_price=70000.0,
                estimated_amount=210000.0,
            )
        )
        session.add_order(
            DryRunOrder(
                order_id="DRY_4",
                ticker="000660",
                market=Market.KRX,
                side=OrderSide.BUY,
                quantity=20,
                risk_check_passed=False,
                risk_check_details="한도 초과",
            )
        )

        summary = session.get_summary()
        assert summary["total_orders"] == 4
        assert summary["buy_orders"] == 3
        assert summary["sell_orders"] == 1
        assert summary["blocked_by_risk"] == 1
        assert summary["total_buy_amount"] == 950000.0
        assert summary["total_sell_amount"] == 210000.0
        assert summary["net_amount"] == 740000.0
        assert set(summary["unique_tickers"]) == {"005930", "035720", "000660"}

    def test_session_to_dict(self):
        """세션 딕셔너리 변환"""
        session = DryRunSession()
        session.add_order(
            DryRunOrder(
                order_id="DRY_1",
                ticker="005930",
                market=Market.KRX,
                side=OrderSide.BUY,
                quantity=10,
            )
        )
        session.complete()

        d = session.to_dict()
        assert d["status"] == "COMPLETED"
        assert d["ended_at"] is not None
        assert len(d["orders"]) == 1
        assert "summary" in d

    def test_empty_session_summary(self):
        """빈 세션 요약"""
        session = DryRunSession()
        summary = session.get_summary()
        assert summary["total_orders"] == 0
        assert summary["buy_orders"] == 0
        assert summary["sell_orders"] == 0


# ══════════════════════════════════════
# DryRunReport 테스트
# ══════════════════════════════════════
class TestDryRunReport:
    """DryRunReport 종합 리포트 테스트"""

    def test_empty_report(self):
        """빈 리포트"""
        report = DryRunReport()
        d = report.to_dict()
        assert d["total_sessions"] == 0
        assert d["total_orders"] == 0

    def test_report_with_sessions(self):
        """세션 포함 리포트"""
        report = DryRunReport()

        s1 = DryRunSession()
        s1.add_order(
            DryRunOrder(
                order_id="DRY_1",
                ticker="005930",
                market=Market.KRX,
                side=OrderSide.BUY,
                quantity=10,
            )
        )
        s1.complete()
        report.add_session(s1)

        s2 = DryRunSession()
        s2.fail("테스트 실패")
        report.add_session(s2)

        d = report.to_dict()
        assert d["total_sessions"] == 2
        assert d["completed_sessions"] == 1
        assert d["failed_sessions"] == 1
        assert d["total_orders"] == 1


# ══════════════════════════════════════
# DryRunEngine 테스트
# ══════════════════════════════════════
class TestDryRunEngine:
    """DryRunEngine 오케스트레이터 테스트"""

    def test_init(self):
        """엔진 초기화"""
        engine = DryRunEngine()
        assert engine.current_session is None
        assert engine.sessions == []

    def test_start_session(self):
        """세션 시작"""
        engine = DryRunEngine()
        session = engine.start_session()
        assert session.status == DryRunStatus.RUNNING
        assert engine.current_session is session

    def test_end_session(self):
        """세션 종료"""
        engine = DryRunEngine()
        engine.start_session()
        session = engine.end_session()
        assert session.status == DryRunStatus.COMPLETED
        assert engine.current_session is None

    def test_end_session_with_error(self):
        """세션 오류 종료"""
        engine = DryRunEngine()
        engine.start_session()
        session = engine.end_session(error="테스트 오류")
        assert session.status == DryRunStatus.FAILED
        assert session.error_message == "테스트 오류"

    def test_end_session_no_active(self):
        """활성 세션 없이 종료 시도"""
        engine = DryRunEngine()
        result = engine.end_session()
        assert result is None

    def test_record_order(self):
        """주문 기록"""
        engine = DryRunEngine()
        engine.start_session()
        order = engine.record_order(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=10,
            reason="테스트 매수",
            estimated_price=70000.0,
        )
        assert order.ticker == "005930"
        assert order.estimated_amount == 700000.0
        assert order.order_id.startswith("DRY_005930_")
        assert len(engine.current_session.orders) == 1

    def test_record_order_auto_start(self):
        """세션 없이 주문 기록 시 자동 시작"""
        engine = DryRunEngine()
        order = engine.record_order(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=10,
        )
        assert engine.current_session is not None
        assert len(engine.current_session.orders) == 1

    def test_multiple_sessions(self):
        """다중 세션"""
        engine = DryRunEngine()

        engine.start_session()
        engine.record_order(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=10,
        )
        engine.end_session()

        engine.start_session()
        engine.record_order(
            ticker="035720",
            market=Market.KRX,
            side=OrderSide.SELL,
            quantity=5,
        )
        engine.end_session()

        assert len(engine.sessions) == 2
        assert engine.sessions[0].orders[0].ticker == "005930"
        assert engine.sessions[1].orders[0].ticker == "035720"

    def test_get_session(self):
        """세션 ID로 조회"""
        engine = DryRunEngine()
        session = engine.start_session()
        sid = session.session_id
        engine.end_session()

        found = engine.get_session(sid)
        assert found is not None
        assert found.session_id == sid

    def test_get_session_not_found(self):
        """존재하지 않는 세션 조회"""
        engine = DryRunEngine()
        result = engine.get_session("nonexistent-id")
        assert result is None

    def test_get_report(self):
        """리포트 생성"""
        engine = DryRunEngine()
        engine.start_session()
        engine.record_order(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=10,
        )
        engine.end_session()

        report = engine.get_report()
        assert len(report.sessions) == 1
        d = report.to_dict()
        assert d["total_orders"] == 1

    def test_clear_sessions(self):
        """세션 초기화"""
        engine = DryRunEngine()
        engine.start_session()
        engine.record_order(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=10,
        )
        engine.end_session()

        count = engine.clear_sessions()
        assert count == 1
        assert engine.sessions == []
        assert engine.current_session is None


# ══════════════════════════════════════
# OrderExecutor dry_run 모드 테스트
# ══════════════════════════════════════
class TestOrderExecutorDryRun:
    """OrderExecutor dry_run=True 모드 테스트"""

    @patch("core.order_executor.executor.get_settings")
    @patch("core.order_executor.executor.KISClient")
    def test_dry_run_flag(self, mock_kis, mock_settings):
        """dry_run 플래그 설정"""
        executor = OrderExecutor(dry_run=True)
        assert executor.dry_run is True

    @patch("core.order_executor.executor.get_settings")
    @patch("core.order_executor.executor.KISClient")
    def test_default_not_dry_run(self, mock_kis, mock_settings):
        """기본값은 dry_run=False"""
        executor = OrderExecutor()
        assert executor.dry_run is False

    @pytest.mark.asyncio
    @patch("core.order_executor.executor.async_session_factory")
    @patch("core.order_executor.executor.order_request_to_contract")
    @patch("core.order_executor.executor.get_settings")
    @patch("core.order_executor.executor.KISClient")
    @patch("core.order_executor.executor.get_dry_run_engine")
    async def test_market_order_dry_run(self, mock_engine_fn, mock_kis, mock_settings, mock_contract, mock_db):
        """시장가 주문 드라이런: API 호출 없이 가상 주문 기록"""
        # DryRunEngine mock 설정
        mock_engine = MagicMock()
        mock_engine_fn.return_value = mock_engine

        # DB session mock
        mock_session = MagicMock()
        mock_session.__aenter__ = MagicMock(return_value=mock_session)
        mock_session.__aexit__ = MagicMock(return_value=None)
        mock_db.return_value = mock_session
        mock_session.execute = MagicMock(return_value=None)
        mock_session.commit = MagicMock(return_value=None)

        executor = OrderExecutor(dry_run=True)

        request = OrderRequest(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=10,
            reason="드라이런 테스트",
        )

        result = await executor._execute_market_order(request)

        # 결과 검증
        assert result.order_id.startswith("DRY_005930_")
        assert result.status == OrderStatus.FILLED
        assert result.filled_quantity == 10
        assert result.avg_price == 0.0  # 드라이런은 실제 가격 없음

        # DryRunEngine에 기록 호출 확인
        mock_engine.record_order.assert_called_once()
        call_kwargs = mock_engine.record_order.call_args
        assert call_kwargs[1]["ticker"] == "005930"
        assert call_kwargs[1]["side"] == OrderSide.BUY
        assert call_kwargs[1]["quantity"] == 10

        # KIS API 미호출 확인
        mock_kis_instance = mock_kis.return_value
        mock_kis_instance.place_kr_order.assert_not_called()

    @pytest.mark.asyncio
    @patch("core.order_executor.executor.async_session_factory")
    @patch("core.order_executor.executor.order_request_to_contract")
    @patch("core.order_executor.executor.get_settings")
    @patch("core.order_executor.executor.KISClient")
    @patch("core.order_executor.executor.get_dry_run_engine")
    async def test_limit_order_dry_run(self, mock_engine_fn, mock_kis, mock_settings, mock_contract, mock_db):
        """지정가 주문 드라이런: API 호출 없이 가상 주문 기록"""
        mock_engine = MagicMock()
        mock_engine_fn.return_value = mock_engine

        mock_session = MagicMock()
        mock_session.__aenter__ = MagicMock(return_value=mock_session)
        mock_session.__aexit__ = MagicMock(return_value=None)
        mock_db.return_value = mock_session
        mock_session.execute = MagicMock(return_value=None)
        mock_session.commit = MagicMock(return_value=None)

        executor = OrderExecutor(dry_run=True)

        request = OrderRequest(
            ticker="AAPL",
            market=Market.NASDAQ,
            side=OrderSide.SELL,
            quantity=20,
            order_type=OrderType.LIMIT,
            limit_price=150.0,
            reason="드라이런 지정가 테스트",
        )

        result = await executor._execute_limit_order(request)

        assert result.order_id.startswith("DRY_AAPL_")
        assert result.status == OrderStatus.PARTIAL
        assert result.filled_quantity == 10  # 50% 체결
        assert result.avg_price == 150.0

        mock_engine.record_order.assert_called_once()
        call_kwargs = mock_engine.record_order.call_args
        assert call_kwargs[1]["limit_price"] == 150.0


# ══════════════════════════════════════
# 글로벌 인스턴스 테스트
# ══════════════════════════════════════
class TestGetDryRunEngine:
    """get_dry_run_engine 싱글톤 테스트"""

    def test_returns_engine(self):
        """엔진 인스턴스 반환"""
        # 모듈 레벨 싱글톤이므로 리셋 필요
        import core.dry_run.engine as module

        module._engine_instance = None
        engine = get_dry_run_engine()
        assert isinstance(engine, DryRunEngine)

    def test_singleton(self):
        """동일 인스턴스 반환"""
        import core.dry_run.engine as module

        module._engine_instance = None
        e1 = get_dry_run_engine()
        e2 = get_dry_run_engine()
        assert e1 is e2
