"""OrderSettlementPoller 단위 테스트

체결 조회 API 응답 파싱, 주문 매칭, 상태 업데이트, 폴링 루프,
reconcile 일괄 처리를 검증한다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.constants import OrderStatus
from core.order_executor.settlement_poller import (
    _match_ccld_record,
    _parse_ccld_status,
    _update_order_status,
    poll_after_execution,
    reconcile_all_submitted,
)


class TestParseCcldStatus:
    """KIS 체결 내역 레코드에서 상태/수량/가격 추출 검증"""

    def test_kr_fully_filled(self):
        """국내주식 전량 체결 → FILLED"""
        record = {"ORD_QTY": "100", "TOT_CCLD_QTY": "100", "AVG_PRVS": "50000"}
        status, qty, price = _parse_ccld_status(record, "KRX")
        assert status == OrderStatus.FILLED
        assert qty == 100
        assert price == 50000.0

    def test_kr_partial_fill(self):
        """국내주식 부분 체결 → PARTIAL"""
        record = {"ORD_QTY": "100", "TOT_CCLD_QTY": "30", "AVG_PRVS": "48000"}
        status, qty, price = _parse_ccld_status(record, "KRX")
        assert status == OrderStatus.PARTIAL
        assert qty == 30
        assert price == 48000.0

    def test_kr_no_fill(self):
        """국내주식 미체결 → SUBMITTED"""
        record = {"ORD_QTY": "100", "TOT_CCLD_QTY": "0", "AVG_PRVS": "0"}
        status, qty, price = _parse_ccld_status(record, "KRX")
        assert status == OrderStatus.SUBMITTED
        assert qty == 0

    def test_us_fully_filled(self):
        """해외주식 전량 체결 → FILLED"""
        record = {"FT_ORD_QTY": "50", "FT_CCLD_QTY": "50", "FT_CCLD_UNPR3": "175.50"}
        status, qty, price = _parse_ccld_status(record, "NYSE")
        assert status == OrderStatus.FILLED
        assert qty == 50
        assert price == 175.50

    def test_us_partial_fill(self):
        """해외주식 부분 체결 → PARTIAL"""
        record = {"FT_ORD_QTY": "50", "FT_CCLD_QTY": "20", "FT_CCLD_UNPR3": "170.00"}
        status, qty, price = _parse_ccld_status(record, "NASDAQ")
        assert status == OrderStatus.PARTIAL
        assert qty == 20

    def test_empty_fields_fallback(self):
        """빈 필드가 있어도 에러 없이 SUBMITTED 반환"""
        record = {"ORD_QTY": "", "TOT_CCLD_QTY": "", "AVG_PRVS": ""}
        status, qty, price = _parse_ccld_status(record, "KRX")
        assert status == OrderStatus.SUBMITTED
        assert qty == 0
        assert price == 0.0


class TestMatchCcldRecord:
    """KIS 체결 내역에서 주문 매칭 검증"""

    def test_match_by_order_id(self):
        """주문번호 정확 매칭"""
        records = [
            {"ODNO": "0001234567", "PDNO": "005930"},
            {"ODNO": "0001234568", "PDNO": "035720"},
        ]
        result = _match_ccld_record(records, "0001234567", "005930", "KRX")
        assert result is not None
        assert result["ODNO"] == "0001234567"

    def test_match_by_ticker_fallback(self):
        """order_id가 UUID 폴백일 때 종목코드로 매칭"""
        records = [
            {"ODNO": "0001234567", "PDNO": "005930"},
        ]
        result = _match_ccld_record(records, "KIS_abc123def456", "005930", "KRX")
        assert result is not None
        assert result["PDNO"] == "005930"

    def test_no_match(self):
        """매칭 실패 → None"""
        records = [
            {"ODNO": "0001234567", "PDNO": "005930"},
        ]
        result = _match_ccld_record(records, "no_match_id", "035720", "KRX")
        assert result is None

    def test_empty_records(self):
        """빈 레코드 리스트 → None"""
        result = _match_ccld_record([], "order123", "005930", "KRX")
        assert result is None


class TestUpdateOrderStatus:
    """DB 주문 상태 업데이트 검증"""

    @pytest.mark.asyncio
    async def test_update_submitted_to_filled(self):
        """SUBMITTED → FILLED 정상 전이"""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute.return_value = mock_result

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_session
        mock_ctx.__aexit__.return_value = None

        with patch(
            "core.order_executor.settlement_poller.async_session_factory",
            return_value=mock_ctx,
        ):
            updated = await _update_order_status(
                order_id="test_order_1",
                current_status_str="SUBMITTED",
                new_status=OrderStatus.FILLED,
                filled_quantity=100,
                filled_price=50000.0,
            )

        assert updated is True
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_terminal_state(self):
        """이미 종결 상태(FILLED)이면 업데이트 스킵"""
        updated = await _update_order_status(
            order_id="test_order_2",
            current_status_str="FILLED",
            new_status=OrderStatus.FILLED,
            filled_quantity=100,
            filled_price=50000.0,
        )
        assert updated is False

    @pytest.mark.asyncio
    async def test_skip_same_submitted(self):
        """SUBMITTED → SUBMITTED 동일 상태이면 스킵"""
        updated = await _update_order_status(
            order_id="test_order_3",
            current_status_str="SUBMITTED",
            new_status=OrderStatus.SUBMITTED,
            filled_quantity=0,
            filled_price=0.0,
        )
        assert updated is False


class TestPollAfterExecution:
    """주문 직후 단기 폴링 검증"""

    @pytest.mark.asyncio
    async def test_poll_stops_on_terminal_state(self):
        """DB에서 이미 종결 상태이면 폴링 즉시 중단"""
        mock_kis = AsyncMock()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = ("FILLED",)
        mock_session.execute.return_value = mock_result

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_session
        mock_ctx.__aexit__.return_value = None

        with patch(
            "core.order_executor.settlement_poller.async_session_factory",
            return_value=mock_ctx,
        ):
            await poll_after_execution(
                kis_client=mock_kis,
                order_id="test_order",
                ticker="005930",
                market="KRX",
                interval=0,  # 테스트용 즉시 실행
                max_retries=3,
            )

        # KIS API 호출 없이 종료
        mock_kis.inquire_kr_daily_ccld.assert_not_called()

    @pytest.mark.asyncio
    async def test_poll_updates_on_fill(self):
        """KIS 조회에서 체결 확인 시 DB 업데이트 후 중단"""
        mock_kis = AsyncMock()
        mock_kis.inquire_kr_daily_ccld.return_value = {
            "output1": [
                {
                    "ODNO": "order_123",
                    "PDNO": "005930",
                    "ORD_QTY": "100",
                    "TOT_CCLD_QTY": "100",
                    "AVG_PRVS": "70000",
                }
            ]
        }

        # DB 조회 mock: SUBMITTED 반환
        mock_db_session = AsyncMock()
        mock_db_check = MagicMock()
        mock_db_check.fetchone.return_value = ("SUBMITTED",)
        mock_db_session.execute.return_value = mock_db_check

        mock_db_ctx = AsyncMock()
        mock_db_ctx.__aenter__.return_value = mock_db_session
        mock_db_ctx.__aexit__.return_value = None

        # _update_order_status mock
        with (
            patch(
                "core.order_executor.settlement_poller.async_session_factory",
                return_value=mock_db_ctx,
            ),
            patch(
                "core.order_executor.settlement_poller._update_order_status",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_update,
        ):
            await poll_after_execution(
                kis_client=mock_kis,
                order_id="order_123",
                ticker="005930",
                market="KRX",
                interval=0,
                max_retries=3,
            )

        mock_update.assert_called_once_with(
            "order_123",
            "SUBMITTED",
            OrderStatus.FILLED,
            100,
            70000.0,
        )

    @pytest.mark.asyncio
    async def test_poll_order_not_found_in_db(self):
        """DB에서 주문 미발견 시 폴링 중단"""
        mock_kis = AsyncMock()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = None
        mock_session.execute.return_value = mock_result

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_session
        mock_ctx.__aexit__.return_value = None

        with patch(
            "core.order_executor.settlement_poller.async_session_factory",
            return_value=mock_ctx,
        ):
            await poll_after_execution(
                kis_client=mock_kis,
                order_id="nonexistent",
                ticker="005930",
                market="KRX",
                interval=0,
                max_retries=3,
            )

        mock_kis.inquire_kr_daily_ccld.assert_not_called()


class TestReconcileAllSubmitted:
    """POST_MARKET 일괄 reconcile 검증"""

    @pytest.mark.asyncio
    async def test_reconcile_no_submitted_orders(self):
        """SUBMITTED 주문이 없으면 즉시 종료"""
        mock_kis = AsyncMock()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_session.execute.return_value = mock_result

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_session
        mock_ctx.__aexit__.return_value = None

        with patch(
            "core.order_executor.settlement_poller.async_session_factory",
            return_value=mock_ctx,
        ):
            stats = await reconcile_all_submitted(kis_client=mock_kis)

        assert stats == {"checked": 0, "updated": 0, "errors": 0}

    @pytest.mark.asyncio
    async def test_reconcile_updates_filled_orders(self):
        """SUBMITTED 주문 중 체결된 건 업데이트"""
        mock_kis = AsyncMock()
        mock_kis.inquire_kr_daily_ccld.return_value = {
            "output1": [
                {
                    "ODNO": "ord_001",
                    "PDNO": "060310",
                    "ORD_QTY": "443",
                    "TOT_CCLD_QTY": "443",
                    "AVG_PRVS": "12500",
                }
            ]
        }

        # DB에서 SUBMITTED 주문 반환
        submitted_orders = [
            ("ord_001", "060310", "KRX", "SUBMITTED"),
        ]

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = submitted_orders
        mock_session.execute.return_value = mock_result

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_session
        mock_ctx.__aexit__.return_value = None

        with (
            patch(
                "core.order_executor.settlement_poller.async_session_factory",
                return_value=mock_ctx,
            ),
            patch(
                "core.order_executor.settlement_poller._update_order_status",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_update,
        ):
            stats = await reconcile_all_submitted(kis_client=mock_kis)

        assert stats["checked"] == 1
        assert stats["updated"] == 1
        mock_update.assert_called_once_with(
            "ord_001",
            "SUBMITTED",
            OrderStatus.FILLED,
            443,
            12500.0,
        )

    @pytest.mark.asyncio
    async def test_reconcile_handles_processing_error(self):
        """주문 처리 중 예외 발생 시 errors 카운트 증가"""
        mock_kis = AsyncMock()

        submitted_orders = [
            ("ord_001", "060310", "KRX", "SUBMITTED"),
        ]

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = submitted_orders
        mock_session.execute.return_value = mock_result

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_session
        mock_ctx.__aexit__.return_value = None

        with (
            patch(
                "core.order_executor.settlement_poller.async_session_factory",
                return_value=mock_ctx,
            ),
            patch(
                "core.order_executor.settlement_poller._fetch_kis_ccld_records",
                new_callable=AsyncMock,
                side_effect=Exception("unexpected processing error"),
            ),
        ):
            stats = await reconcile_all_submitted(kis_client=mock_kis)

        assert stats["checked"] == 1
        assert stats["errors"] == 1

    @pytest.mark.asyncio
    async def test_reconcile_market_batch_optimization(self):
        """같은 마켓 주문은 KIS API를 한 번만 호출"""
        mock_kis = AsyncMock()
        mock_kis.inquire_kr_daily_ccld.return_value = {"output1": []}

        submitted_orders = [
            ("ord_001", "005930", "KRX", "SUBMITTED"),
            ("ord_002", "035720", "KRX", "SUBMITTED"),
            ("ord_003", "060310", "KRX", "SUBMITTED"),
        ]

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = submitted_orders
        mock_session.execute.return_value = mock_result

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_session
        mock_ctx.__aexit__.return_value = None

        with patch(
            "core.order_executor.settlement_poller.async_session_factory",
            return_value=mock_ctx,
        ):
            stats = await reconcile_all_submitted(kis_client=mock_kis)

        assert stats["checked"] == 3
        # KRX API 한 번만 호출됨
        assert mock_kis.inquire_kr_daily_ccld.call_count == 1
