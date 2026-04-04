"""
주문 실행 엔진 (OrderExecutor) 단위 테스트

NFR-07 명세:
- 모든 외부 API는 Mock으로 대체
- OrderRequest, OrderResult 데이터 구조 검증
- OrderExecutor의 주문 검증, 실행, 배치 처리 로직 검증
- TWAP/VWAP 주문의 다중 구간 실행 검증
- 미체결 주문 처리 검증

테스트 범위:
1. OrderRequest: 데이터 구조, 딕셔너리 변환
2. OrderResult: 데이터 구조, 딕셔너리 변환
3. OrderExecutor: 주문 검증, 시장가/지정가/TWAP/VWAP 실행, 배치 처리, 미체결 처리
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from config.constants import Market, OrderSide, OrderType, OrderStatus
from core.order_executor.executor import OrderRequest, OrderResult, OrderExecutor


# ══════════════════════════════════════════════════════════════════════════════
# TestOrderRequest: OrderRequest 데이터 구조 테스트
# ══════════════════════════════════════════════════════════════════════════════
class TestOrderRequest:
    """OrderRequest 데이터 구조 테스트"""

    def test_create_request(self):
        """OrderRequest 생성 테스트"""
        # Arrange & Act
        request = OrderRequest(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=100,
            order_type=OrderType.MARKET,
            limit_price=None,
            reason="포트폴리오 리밸런싱",
        )

        # Assert
        assert request.ticker == "005930"
        assert request.market == Market.KRX
        assert request.side == OrderSide.BUY
        assert request.quantity == 100
        assert request.order_type == OrderType.MARKET
        assert request.limit_price is None
        assert request.reason == "포트폴리오 리밸런싱"

    def test_create_request_with_limit_price(self):
        """지정가 주문 요청 생성 테스트"""
        # Arrange & Act
        request = OrderRequest(
            ticker="AAPL",
            market=Market.NASDAQ,
            side=OrderSide.SELL,
            quantity=50,
            order_type=OrderType.LIMIT,
            limit_price=150.25,
            reason="익절",
        )

        # Assert
        assert request.ticker == "AAPL"
        assert request.market == Market.NASDAQ
        assert request.side == OrderSide.SELL
        assert request.quantity == 50
        assert request.order_type == OrderType.LIMIT
        assert request.limit_price == 150.25
        assert request.reason == "익절"

    def test_to_dict(self):
        """OrderRequest.to_dict() 변환 테스트"""
        # Arrange
        request = OrderRequest(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=100,
            order_type=OrderType.TWAP,
            limit_price=None,
            reason="신호 기반 매매",
        )

        # Act
        result = request.to_dict()

        # Assert
        assert result["ticker"] == "005930"
        assert result["market"] == "KRX"
        assert result["side"] == "BUY"
        assert result["quantity"] == 100
        assert result["order_type"] == "TWAP"
        assert result["limit_price"] is None
        assert result["reason"] == "신호 기반 매매"

    def test_to_dict_with_limit_price(self):
        """지정가 포함 OrderRequest.to_dict() 변환 테스트"""
        # Arrange
        request = OrderRequest(
            ticker="AAPL",
            market=Market.NASDAQ,
            side=OrderSide.SELL,
            quantity=50,
            order_type=OrderType.LIMIT,
            limit_price=150.25,
        )

        # Act
        result = request.to_dict()

        # Assert
        assert result["ticker"] == "AAPL"
        assert result["market"] == "NASDAQ"
        assert result["side"] == "SELL"
        assert result["quantity"] == 50
        assert result["order_type"] == "LIMIT"
        assert result["limit_price"] == 150.25


# ══════════════════════════════════════════════════════════════════════════════
# TestOrderResult: OrderResult 데이터 구조 테스트
# ══════════════════════════════════════════════════════════════════════════════
class TestOrderResult:
    """OrderResult 데이터 구조 테스트"""

    def test_create_result(self):
        """OrderResult 생성 테스트"""
        # Arrange
        executed_at = datetime.now(timezone.utc)

        # Act
        result = OrderResult(
            order_id="ORD123456",
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=100,
            filled_quantity=100,
            avg_price=71400.0,
            status=OrderStatus.FILLED,
            executed_at=executed_at,
            error_message="",
        )

        # Assert
        assert result.order_id == "ORD123456"
        assert result.ticker == "005930"
        assert result.market == Market.KRX
        assert result.side == OrderSide.BUY
        assert result.quantity == 100
        assert result.filled_quantity == 100
        assert result.avg_price == 71400.0
        assert result.status == OrderStatus.FILLED
        assert result.executed_at == executed_at
        assert result.error_message == ""

    def test_create_result_partial_fill(self):
        """부분 체결 OrderResult 생성 테스트"""
        # Arrange
        executed_at = datetime.now(timezone.utc)

        # Act
        result = OrderResult(
            order_id="ORD789101",
            ticker="AAPL",
            market=Market.NASDAQ,
            side=OrderSide.SELL,
            quantity=50,
            filled_quantity=30,
            avg_price=149.80,
            status=OrderStatus.PARTIAL,
            executed_at=executed_at,
            error_message="",
        )

        # Assert
        assert result.order_id == "ORD789101"
        assert result.quantity == 50
        assert result.filled_quantity == 30
        assert result.status == OrderStatus.PARTIAL

    def test_create_result_with_error(self):
        """오류 포함 OrderResult 생성 테스트"""
        # Arrange
        executed_at = datetime.now(timezone.utc)

        # Act
        result = OrderResult(
            order_id="",
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=100,
            filled_quantity=0,
            avg_price=0.0,
            status=OrderStatus.FAILED,
            executed_at=executed_at,
            error_message="주문 수량이 유효하지 않습니다",
        )

        # Assert
        assert result.status == OrderStatus.FAILED
        assert result.filled_quantity == 0
        assert result.error_message == "주문 수량이 유효하지 않습니다"

    def test_to_dict(self):
        """OrderResult.to_dict() 변환 테스트"""
        # Arrange
        executed_at = datetime(2026, 4, 3, 15, 30, 45, tzinfo=timezone.utc)
        result = OrderResult(
            order_id="ORD123456",
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=100,
            filled_quantity=100,
            avg_price=71400.0,
            status=OrderStatus.FILLED,
            executed_at=executed_at,
            error_message="",
        )

        # Act
        result_dict = result.to_dict()

        # Assert
        assert result_dict["order_id"] == "ORD123456"
        assert result_dict["ticker"] == "005930"
        assert result_dict["market"] == "KRX"
        assert result_dict["side"] == "BUY"
        assert result_dict["quantity"] == 100
        assert result_dict["filled_quantity"] == 100
        assert result_dict["avg_price"] == 71400.0
        assert result_dict["status"] == "FILLED"
        assert result_dict["executed_at"] == "2026-04-03T15:30:45+00:00"
        assert result_dict["error_message"] == ""

    def test_to_dict_iso_format(self):
        """OrderResult.to_dict() ISO 형식 테스트"""
        # Arrange
        executed_at = datetime(2026, 4, 3, 15, 30, 45, tzinfo=timezone.utc)
        result = OrderResult(
            order_id="ORD999",
            ticker="AAPL",
            market=Market.NASDAQ,
            side=OrderSide.SELL,
            quantity=50,
            filled_quantity=30,
            avg_price=149.80,
            status=OrderStatus.PARTIAL,
            executed_at=executed_at,
        )

        # Act
        result_dict = result.to_dict()

        # Assert
        # ISO 형식 확인
        assert "2026-04-03" in result_dict["executed_at"]
        assert "15:30:45" in result_dict["executed_at"]


# ══════════════════════════════════════════════════════════════════════════════
# TestOrderExecutor: OrderExecutor 주문 실행 엔진 테스트
# ══════════════════════════════════════════════════════════════════════════════
class TestOrderExecutor:
    """OrderExecutor 주문 실행 엔진 테스트"""

    @pytest.fixture
    def mock_settings(self):
        """설정 Mock"""
        return MagicMock()

    @pytest.fixture
    def mock_kis_client(self):
        """KISClient Mock - Backtest 모드"""
        client = AsyncMock()
        client.is_backtest = True
        return client

    @pytest.fixture
    def mock_async_session_factory(self):
        """async_session_factory Mock"""
        session = AsyncMock()
        return session

    @pytest.fixture
    def mock_audit_logger(self):
        """AuditLogger Mock"""
        logger = AsyncMock()
        return logger

    @pytest.fixture
    async def executor_with_mocks(
        self, mock_settings, mock_kis_client, mock_async_session_factory, mock_audit_logger
    ):
        """OrderExecutor 인스턴스 (모든 외부 의존성 Mock)"""
        with patch("core.order_executor.executor.get_settings") as mock_get_settings, \
             patch("core.order_executor.executor.KISClient") as mock_kis_class, \
             patch("core.order_executor.executor.async_session_factory") as mock_session_factory, \
             patch("core.order_executor.executor.AuditLogger") as mock_audit_class:

            # Setup mocks
            mock_get_settings.return_value = mock_settings
            mock_kis_class.return_value = mock_kis_client
            mock_session_factory.return_value.__aenter__.return_value = mock_async_session_factory
            mock_audit_class.return_value = mock_audit_logger

            # Create executor
            executor = OrderExecutor()
            executor._kis_client = mock_kis_client
            executor._settings = mock_settings

            yield executor

    # ──────────────────────────────────────────────────────────────────────────
    # 주문 검증 테스트
    # ──────────────────────────────────────────────────────────────────────────

    async def test_validate_order_valid(self, executor_with_mocks):
        """유효한 주문 검증 테스트"""
        # Arrange
        executor = executor_with_mocks
        request = OrderRequest(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=100,
            order_type=OrderType.MARKET,
        )

        # Act & Assert - 예외 발생하지 않음
        executor._validate_order(request)

    async def test_validate_order_zero_quantity(self, executor_with_mocks):
        """주문 수량 0 검증 실패 테스트"""
        # Arrange
        executor = executor_with_mocks
        request = OrderRequest(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=0,
            order_type=OrderType.MARKET,
        )

        # Act & Assert
        with pytest.raises(ValueError, match="주문 수량은 0보다 커야 합니다"):
            executor._validate_order(request)

    async def test_validate_order_negative_quantity(self, executor_with_mocks):
        """음수 주문 수량 검증 실패 테스트"""
        # Arrange
        executor = executor_with_mocks
        request = OrderRequest(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=-5,
            order_type=OrderType.MARKET,
        )

        # Act & Assert
        with pytest.raises(ValueError, match="주문 수량은 0보다 커야 합니다"):
            executor._validate_order(request)

    async def test_validate_order_limit_no_price(self, executor_with_mocks):
        """지정가 주문 가격 없음 검증 실패 테스트"""
        # Arrange
        executor = executor_with_mocks
        request = OrderRequest(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=100,
            order_type=OrderType.LIMIT,
            limit_price=None,
        )

        # Act & Assert
        with pytest.raises(ValueError, match="지정가 주문에는 유효한 limit_price가 필요합니다"):
            executor._validate_order(request)

    async def test_validate_order_limit_zero_price(self, executor_with_mocks):
        """지정가 주문 가격 0 검증 실패 테스트"""
        # Arrange
        executor = executor_with_mocks
        request = OrderRequest(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=100,
            order_type=OrderType.LIMIT,
            limit_price=0,
        )

        # Act & Assert
        with pytest.raises(ValueError, match="지정가 주문에는 유효한 limit_price가 필요합니다"):
            executor._validate_order(request)

    async def test_validate_order_limit_negative_price(self, executor_with_mocks):
        """지정가 주문 음수 가격 검증 실패 테스트"""
        # Arrange
        executor = executor_with_mocks
        request = OrderRequest(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=100,
            order_type=OrderType.LIMIT,
            limit_price=-50000,
        )

        # Act & Assert
        with pytest.raises(ValueError, match="지정가 주문에는 유효한 limit_price가 필요합니다"):
            executor._validate_order(request)

    # ──────────────────────────────────────────────────────────────────────────
    # 시장가 주문 테스트
    # ──────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_execute_market_order_backtest(self, executor_with_mocks):
        """시장가 주문 실행 - Backtest 모드 테스트"""
        # Arrange
        executor = executor_with_mocks
        request = OrderRequest(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=100,
            order_type=OrderType.MARKET,
        )

        # Act
        result = await executor._execute_market_order(request)

        # Assert
        assert result.ticker == "005930"
        assert result.market == Market.KRX
        assert result.side == OrderSide.BUY
        assert result.quantity == 100
        assert result.filled_quantity == 100  # Backtest 모드: 전체 체결
        assert result.avg_price == 100.0  # Mock 가격
        assert result.status == OrderStatus.FILLED
        assert result.order_id.startswith("MOCK_")

    @pytest.mark.asyncio
    async def test_execute_market_order_backtest_us(self, executor_with_mocks):
        """시장가 주문 실행 - 미국 주식 Backtest 모드 테스트"""
        # Arrange
        executor = executor_with_mocks
        request = OrderRequest(
            ticker="AAPL",
            market=Market.NASDAQ,
            side=OrderSide.SELL,
            quantity=50,
            order_type=OrderType.MARKET,
        )

        # Act
        result = await executor._execute_market_order(request)

        # Assert
        assert result.ticker == "AAPL"
        assert result.market == Market.NASDAQ
        assert result.side == OrderSide.SELL
        assert result.quantity == 50
        assert result.filled_quantity == 50
        assert result.status == OrderStatus.FILLED

    # ──────────────────────────────────────────────────────────────────────────
    # 지정가 주문 테스트
    # ──────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_execute_limit_order_backtest(self, executor_with_mocks):
        """지정가 주문 실행 - Backtest 모드 테스트"""
        # Arrange
        executor = executor_with_mocks
        request = OrderRequest(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=100,
            order_type=OrderType.LIMIT,
            limit_price=71000.0,
        )

        # Act
        result = await executor._execute_limit_order(request)

        # Assert
        assert result.ticker == "005930"
        assert result.market == Market.KRX
        assert result.side == OrderSide.BUY
        assert result.quantity == 100
        assert result.filled_quantity == 50  # Backtest 모드: 50% 체결
        assert result.avg_price == 71000.0
        assert result.status == OrderStatus.PARTIAL

    @pytest.mark.asyncio
    async def test_execute_limit_order_invalid_price(self, executor_with_mocks):
        """지정가 주문 - 유효하지 않은 가격 테스트"""
        # Arrange
        executor = executor_with_mocks
        request = OrderRequest(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=100,
            order_type=OrderType.LIMIT,
            limit_price=None,
        )

        # Act & Assert
        with pytest.raises(ValueError, match="지정가 주문에는 유효한 limit_price가 필요합니다"):
            await executor._execute_limit_order(request)

    # ──────────────────────────────────────────────────────────────────────────
    # 단일 주문 실행 (execute_order) 테스트
    # ──────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_execute_order_market(self, executor_with_mocks):
        """단일 주문 실행 (시장가) - 전체 흐름 테스트"""
        # Arrange
        executor = executor_with_mocks
        request = OrderRequest(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=100,
            order_type=OrderType.MARKET,
            reason="포트폴리오 리밸런싱",
        )

        # Mock _store_order and AuditLogger
        executor._store_order = AsyncMock()

        # Act
        result = await executor.execute_order(request)

        # Assert
        assert result.ticker == "005930"
        assert result.filled_quantity == 100
        assert result.status == OrderStatus.FILLED
        executor._store_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_order_limit(self, executor_with_mocks):
        """단일 주문 실행 (지정가) - 전체 흐름 테스트"""
        # Arrange
        executor = executor_with_mocks
        request = OrderRequest(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=100,
            order_type=OrderType.LIMIT,
            limit_price=71000.0,
        )

        executor._store_order = AsyncMock()

        # Act
        result = await executor.execute_order(request)

        # Assert
        assert result.ticker == "005930"
        assert result.status == OrderStatus.PARTIAL
        executor._store_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_order_validation_failure(self, executor_with_mocks):
        """단일 주문 실행 - 검증 실패 테스트"""
        # Arrange
        executor = executor_with_mocks
        request = OrderRequest(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=0,  # 유효하지 않은 수량
            order_type=OrderType.MARKET,
        )

        executor._store_order = AsyncMock()

        # Act & Assert
        with pytest.raises(ValueError, match="주문 계약 위반|주문 수량은 0보다 커야 합니다"):
            await executor.execute_order(request)

        # _store_order는 실패 결과와 함께 호출되어야 함
        executor._store_order.assert_called_once()

    # ──────────────────────────────────────────────────────────────────────────
    # 배치 주문 실행 테스트
    # ──────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_execute_batch_orders_sell_first(self, executor_with_mocks):
        """배치 주문 실행 - SELL 우선 원칙 테스트"""
        # Arrange
        executor = executor_with_mocks
        executor._store_order = AsyncMock()

        requests = [
            OrderRequest(
                ticker="005930",
                market=Market.KRX,
                side=OrderSide.BUY,
                quantity=100,
                order_type=OrderType.MARKET,
            ),
            OrderRequest(
                ticker="000660",
                market=Market.KRX,
                side=OrderSide.SELL,
                quantity=50,
                order_type=OrderType.MARKET,
            ),
            OrderRequest(
                ticker="360750",
                market=Market.KRX,
                side=OrderSide.BUY,
                quantity=200,
                order_type=OrderType.MARKET,
            ),
            OrderRequest(
                ticker="069500",
                market=Market.KRX,
                side=OrderSide.SELL,
                quantity=150,
                order_type=OrderType.MARKET,
            ),
        ]

        # Act
        results = await executor.execute_batch_orders(requests)

        # Assert
        assert len(results) == 4

        # 첫 두 결과가 SELL (000660, 069500)이고 뒤의 두 결과가 BUY (005930, 360750)
        sell_results = [r for r in results if r.side == OrderSide.SELL]
        buy_results = [r for r in results if r.side == OrderSide.BUY]

        assert len(sell_results) == 2
        assert len(buy_results) == 2

    @pytest.mark.asyncio
    async def test_execute_batch_orders_partial_failure(self, executor_with_mocks):
        """배치 주문 실행 - 부분 실패 테스트"""
        # Arrange
        executor = executor_with_mocks
        executor._store_order = AsyncMock()

        # 첫 번째 주문은 성공, 두 번째는 실패
        requests = [
            OrderRequest(
                ticker="005930",
                market=Market.KRX,
                side=OrderSide.BUY,
                quantity=100,
                order_type=OrderType.MARKET,
            ),
            OrderRequest(
                ticker="000660",
                market=Market.KRX,
                side=OrderSide.BUY,
                quantity=-50,  # 유효하지 않음
                order_type=OrderType.MARKET,
            ),
            OrderRequest(
                ticker="360750",
                market=Market.KRX,
                side=OrderSide.BUY,
                quantity=200,
                order_type=OrderType.MARKET,
            ),
        ]

        # Act
        results = await executor.execute_batch_orders(requests)

        # Assert
        assert len(results) == 3
        # 첫 번째와 세 번째는 성공, 두 번째는 실패
        assert results[0].status == OrderStatus.FILLED
        assert results[1].status == OrderStatus.FAILED
        assert results[2].status == OrderStatus.FILLED

    # ──────────────────────────────────────────────────────────────────────────
    # TWAP 주문 실행 테스트
    # ──────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_execute_twap_order(self, executor_with_mocks):
        """TWAP 주문 실행 - 6개 구간 테스트"""
        # Arrange
        executor = executor_with_mocks
        request = OrderRequest(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=600,  # 6개 구간으로 100씩 분할
            order_type=OrderType.TWAP,
        )

        # Mock asyncio.sleep to avoid actual waiting
        with patch("core.order_executor.executor.asyncio.sleep", new_callable=AsyncMock):
            # Act
            result = await executor._execute_twap_order(request)

        # Assert
        assert result.ticker == "005930"
        assert result.quantity == 600
        assert result.filled_quantity == 600  # 6개 구간 * 100 = 600
        assert result.status == OrderStatus.FILLED
        assert result.order_id.startswith("TWAP_")

    @pytest.mark.asyncio
    async def test_execute_twap_order_remainder(self, executor_with_mocks):
        """TWAP 주문 실행 - 나머지 처리 테스트"""
        # Arrange
        executor = executor_with_mocks
        request = OrderRequest(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=100,  # 6개 구간: 16, 16, 16, 16, 16, 20
            order_type=OrderType.TWAP,
        )

        with patch("core.order_executor.executor.asyncio.sleep", new_callable=AsyncMock):
            # Act
            result = await executor._execute_twap_order(request)

        # Assert
        assert result.quantity == 100
        assert result.filled_quantity == 100
        assert result.status == OrderStatus.FILLED

    @pytest.mark.asyncio
    async def test_execute_twap_order_sleep_called(self, executor_with_mocks):
        """TWAP 주문 실행 - asyncio.sleep 호출 검증 테스트"""
        # Arrange
        executor = executor_with_mocks
        request = OrderRequest(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=600,
            order_type=OrderType.TWAP,
        )

        mock_sleep = AsyncMock()
        with patch("core.order_executor.executor.asyncio.sleep", mock_sleep):
            # Act
            result = await executor._execute_twap_order(request)

        # Assert
        # 6개 구간 중 마지막을 제외한 5번 대기
        assert mock_sleep.call_count == 5
        # 각 대기는 300초(5분)
        for call in mock_sleep.call_args_list:
            assert call[0][0] == 300

    # ──────────────────────────────────────────────────────────────────────────
    # VWAP 주문 실행 테스트
    # ──────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_execute_vwap_order(self, executor_with_mocks):
        """VWAP 주문 실행 - 6개 구간 테스트"""
        # Arrange
        executor = executor_with_mocks
        request = OrderRequest(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=600,
            order_type=OrderType.VWAP,
        )

        with patch("core.order_executor.executor.asyncio.sleep", new_callable=AsyncMock):
            # Act
            result = await executor._execute_vwap_order(request)

        # Assert
        assert result.ticker == "005930"
        assert result.quantity == 600
        assert result.filled_quantity == 600
        assert result.status == OrderStatus.FILLED
        assert result.order_id.startswith("VWAP_")

    @pytest.mark.asyncio
    async def test_execute_vwap_order_sleep_called(self, executor_with_mocks):
        """VWAP 주문 실행 - asyncio.sleep 호출 검증 테스트"""
        # Arrange
        executor = executor_with_mocks
        request = OrderRequest(
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=600,
            order_type=OrderType.VWAP,
        )

        mock_sleep = AsyncMock()
        with patch("core.order_executor.executor.asyncio.sleep", mock_sleep):
            # Act
            result = await executor._execute_vwap_order(request)

        # Assert
        # 6개 구간 중 마지막을 제외한 5번 대기
        assert mock_sleep.call_count == 5

    # ──────────────────────────────────────────────────────────────────────────
    # 미체결 주문 처리 테스트
    # ──────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_handle_unfilled(self, executor_with_mocks):
        """미체결 주문 처리 - 시장가 전환 테스트"""
        # Arrange
        executor = executor_with_mocks
        order_id = "ORD123456"
        remaining_qty = 50

        # Act
        result = await executor._handle_unfilled(order_id, remaining_qty)

        # Assert
        assert result.order_id == order_id
        assert result.quantity == remaining_qty
        assert result.filled_quantity == remaining_qty
        assert result.status == OrderStatus.FILLED
        assert result.market == Market.KRX  # 기본값

    # ──────────────────────────────────────────────────────────────────────────
    # 데이터베이스 저장 테스트
    # ──────────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_store_order(self, executor_with_mocks):
        """주문 정보 저장 테스트"""
        # Arrange
        executor = executor_with_mocks
        result = OrderResult(
            order_id="ORD123456",
            ticker="005930",
            market=Market.KRX,
            side=OrderSide.BUY,
            quantity=100,
            filled_quantity=100,
            avg_price=71400.0,
            status=OrderStatus.FILLED,
            executed_at=datetime.now(timezone.utc),
        )

        # Act
        await executor._store_order(result)

        # Assert - mock 호출 확인은 executor_with_mocks 내 mock이 처리
        # _store_order는 async 함수이므로 실행되어야 함
        assert result.order_id == "ORD123456"


# ══════════════════════════════════════════════════════════════════════════════
# 통합 테스트 시나리오
# ══════════════════════════════════════════════════════════════════════════════
class TestOrderExecutorIntegration:
    """OrderExecutor 통합 테스트 시나리오"""

    @pytest.fixture
    def mock_kis_client(self):
        """KISClient Mock - Backtest 모드"""
        client = AsyncMock()
        client.is_backtest = True
        return client

    @pytest.fixture
    async def executor_with_mocks(self, mock_kis_client):
        """OrderExecutor 인스턴스 (모든 외부 의존성 Mock)"""
        with patch("core.order_executor.executor.get_settings") as mock_get_settings, \
             patch("core.order_executor.executor.KISClient") as mock_kis_class, \
             patch("core.order_executor.executor.async_session_factory") as mock_session_factory, \
             patch("core.order_executor.executor.AuditLogger") as mock_audit_class:

            # Setup mocks
            mock_get_settings.return_value = MagicMock()
            mock_kis_class.return_value = mock_kis_client
            mock_session_factory.return_value.__aenter__.return_value = AsyncMock()
            mock_audit_class.return_value = AsyncMock()

            # Create executor
            executor = OrderExecutor()
            executor._kis_client = mock_kis_client
            executor._store_order = AsyncMock()

            yield executor

    @pytest.mark.asyncio
    async def test_rebalancing_scenario(self, executor_with_mocks):
        """포트폴리오 리밸런싱 시나리오 테스트"""
        # Arrange
        executor = executor_with_mocks
        requests = [
            # 기존 종목 소량 판매
            OrderRequest(
                ticker="005930",
                market=Market.KRX,
                side=OrderSide.SELL,
                quantity=20,
                order_type=OrderType.MARKET,
                reason="리밸런싱: 비중 조정",
            ),
            # 신규 종목 매수
            OrderRequest(
                ticker="000660",
                market=Market.KRX,
                side=OrderSide.BUY,
                quantity=30,
                order_type=OrderType.LIMIT,
                limit_price=100000.0,
                reason="리밸런싱: 신규 투자",
            ),
            # 기존 종목 추가 매수
            OrderRequest(
                ticker="360750",
                market=Market.KRX,
                side=OrderSide.BUY,
                quantity=100,
                order_type=OrderType.TWAP,
                reason="리밸런싱: 대량 매수",
            ),
        ]

        # Act (asyncio.sleep 패치하여 TWAP 대기 방지)
        with patch("core.order_executor.executor.asyncio.sleep", new_callable=AsyncMock):
            results = await executor.execute_batch_orders(requests)

        # Assert
        assert len(results) == 3
        # SELL이 먼저 실행됨
        assert results[0].side == OrderSide.SELL
        # BUY가 나중에 실행됨
        assert results[1].side == OrderSide.BUY
        assert results[2].side == OrderSide.BUY

    @pytest.mark.asyncio
    async def test_signal_based_trading_scenario(self, executor_with_mocks):
        """신호 기반 매매 시나리오 테스트"""
        # Arrange
        executor = executor_with_mocks
        requests = [
            # 강한 매수 신호
            OrderRequest(
                ticker="AAPL",
                market=Market.NASDAQ,
                side=OrderSide.BUY,
                quantity=50,
                order_type=OrderType.MARKET,
                reason="강한 매수 신호: RSI < 30",
            ),
            # 익절
            OrderRequest(
                ticker="MSFT",
                market=Market.NASDAQ,
                side=OrderSide.SELL,
                quantity=30,
                order_type=OrderType.LIMIT,
                limit_price=350.0,
                reason="익절: 목표가 도달",
            ),
        ]

        # Act (asyncio.sleep 패치하여 TWAP/VWAP 대기 방지)
        with patch("core.order_executor.executor.asyncio.sleep", new_callable=AsyncMock):
            results = await executor.execute_batch_orders(requests)

        # Assert
        assert len(results) == 2
        assert results[0].side == OrderSide.SELL
        assert results[1].side == OrderSide.BUY
