"""WebSocket 체결 통보 관련 유닛테스트.

테스트 대상:
1. RealtimeExecutionNotice 파싱 (국내/해외)
2. _aes_cbc_base64_decrypt 복호화
3. KISRealtimeClient 체결 통보 관련 상태 관리
4. ws_execution_handler 콜백 로직
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.constants import OrderStatus
from core.data_collector.kis_websocket import (
    _EXEC_NOTICE_TR_IDS,
    TR_ID_EXEC_NOTICE_DEMO,
    TR_ID_EXEC_NOTICE_LIVE,
    TR_ID_EXEC_NOTICE_OVERSEAS_DEMO,
    TR_ID_EXEC_NOTICE_OVERSEAS_LIVE,
    RealtimeExecutionNotice,
    _aes_cbc_base64_decrypt,
)

# ═══════════════════════════════════════════════════════════════════════
# RealtimeExecutionNotice 파싱 테스트
# ═══════════════════════════════════════════════════════════════════════


class TestRealtimeExecutionNoticeDomestic:
    """국내 체결 통보 파싱 (H0STCNI0/H0STCNI9)"""

    @pytest.fixture
    def domestic_filled_fields(self):
        """국내 체결 통보 — 체결 완료 (CNTG_YN=2)"""
        # 26개 필드 (EXEC_NOTICE_DOMESTIC_FIELDS 순서)
        return [
            "CUST001",  # 0: CUST_ID
            "50012345-01",  # 1: ACNT_NO
            "0000012345",  # 2: ODER_NO (주문번호)
            "0000000000",  # 3: OODER_NO (원주문번호)
            "02",  # 4: SELN_BYOV_CLS (02: 매수)
            "0",  # 5: RCTF_CLS
            "00",  # 6: ODER_KIND
            "0",  # 7: ODER_COND
            "005930",  # 8: STCK_SHRN_ISCD (종목코드)
            "10",  # 9: CNTG_QTY (체결수량)
            "72000",  # 10: CNTG_UNPR (체결단가)
            "100530",  # 11: STCK_CNTG_HOUR (체결시간)
            "0",  # 12: RFUS_YN (거부여부)
            "2",  # 13: CNTG_YN (체결여부: 2=체결)
            "Y",  # 14: ACPT_YN
            "001",  # 15: BRNC_NO
            "10",  # 16: ODER_QTY (주문수량)
            "테스트계좌",  # 17: ACNT_NAME
            "0",  # 18: ORD_COND_PRC
            "01",  # 19: ORD_EXG_GB
            "N",  # 20: POPUP_YN
            "",  # 21: FILLER
            "00",  # 22: CRDT_CLS
            "",  # 23: CRDT_LOAN_DATE
            "20260414",  # 24: CNTG_ISNM40
            "72000",  # 25: ODER_PRC
        ]

    @pytest.fixture
    def domestic_accepted_fields(self):
        """국내 체결 통보 — 접수 (CNTG_YN=1)"""
        fields = [
            "CUST001",
            "50012345-01",
            "0000012345",
            "0000000000",
            "02",
            "0",
            "00",
            "0",
            "005930",
            "0",  # 체결수량 0
            "0",  # 체결단가 0
            "100530",
            "0",
            "1",  # CNTG_YN=1 (접수)
            "Y",
            "001",
            "10",
            "테스트계좌",
            "72000",
            "01",
            "N",
            "",
            "00",
            "",
            "20260414",
            "72000",
        ]
        return fields

    def test_domestic_filled_notice_parsing(self, domestic_filled_fields):
        notice = RealtimeExecutionNotice(domestic_filled_fields, is_overseas=False)
        assert notice.order_no == "0000012345"
        assert notice.ticker == "005930"
        assert notice.side == "02"
        assert notice.filled_qty == 10
        assert notice.filled_price == 72000.0
        assert notice.order_qty == 10
        assert notice.is_filled is True
        assert notice.is_rejected is False
        assert notice.is_overseas is False

    def test_domestic_accepted_notice_parsing(self, domestic_accepted_fields):
        notice = RealtimeExecutionNotice(domestic_accepted_fields, is_overseas=False)
        assert notice.order_no == "0000012345"
        assert notice.ticker == "005930"
        assert notice.filled_qty == 0
        assert notice.is_filled is False
        assert notice.is_rejected is False

    def test_domestic_rejected_notice(self):
        """거부 통보: RFUS_YN=1"""
        fields = [""] * 26
        fields[2] = "0000012345"
        fields[8] = "005930"
        fields[12] = "1"  # RFUS_YN=1 (거부)
        fields[13] = "1"  # CNTG_YN=1 (접수)

        notice = RealtimeExecutionNotice(fields, is_overseas=False)
        assert notice.is_rejected is True
        assert notice.is_filled is False

    def test_domestic_partial_fill(self):
        """부분 체결: CNTG_QTY < ODER_QTY"""
        fields = [""] * 26
        fields[2] = "0000012345"
        fields[8] = "005930"
        fields[9] = "5"  # 체결 5주
        fields[10] = "72000"
        fields[12] = "0"  # 거부 아님
        fields[13] = "2"  # 체결
        fields[16] = "10"  # 주문 10주

        notice = RealtimeExecutionNotice(fields, is_overseas=False)
        assert notice.is_filled is True
        assert notice.filled_qty == 5
        assert notice.order_qty == 10

    def test_to_dict(self, domestic_filled_fields):
        notice = RealtimeExecutionNotice(domestic_filled_fields, is_overseas=False)
        d = notice.to_dict()
        assert d["order_no"] == "0000012345"
        assert d["ticker"] == "005930"
        assert d["filled_qty"] == 10
        assert d["filled_price"] == 72000.0
        assert d["is_filled"] is True
        assert d["is_overseas"] is False
        assert "timestamp" in d


class TestRealtimeExecutionNoticeOverseas:
    """해외 체결 통보 파싱 (H0GSCNI0/H0GSCNI9)"""

    @pytest.fixture
    def overseas_filled_fields(self):
        """해외 체결 통보 — 체결 완료"""
        # 25개 필드 (EXEC_NOTICE_OVERSEAS_FIELDS 순서)
        return [
            "CUST001",  # 0: CUST_ID
            "50012345-01",  # 1: ACNT_NO
            "US00012345",  # 2: ODER_NO
            "0000000000",  # 3: OODER_NO
            "02",  # 4: SELN_BYOV_CLS (매수)
            "0",  # 5: RCTF_CLS
            "00",  # 6: ODER_KIND2
            "AAPL",  # 7: STCK_SHRN_ISCD (종목코드)
            "5",  # 8: CNTG_QTY (체결수량)
            "185.50",  # 9: CNTG_UNPR (체결단가)
            "143025",  # 10: STCK_CNTG_HOUR
            "0",  # 11: RFUS_YN
            "2",  # 12: CNTG_YN (체결)
            "Y",  # 13: ACPT_YN
            "001",  # 14: BRNC_NO
            "5",  # 15: ODER_QTY (주문수량)
            "테스트계좌",  # 16: ACNT_NAME
            "20260414",  # 17: CNTG_ISNM
            "0",  # 18: ODER_COND
            "00",  # 19: DEBT_GB
            "",  # 20: DEBT_DATE
            "093000",  # 21: START_TM
            "160000",  # 22: END_TM
            "0",  # 23: TM_DIV_TP
            "185.50",  # 24: CNTG_UNPR12
        ]

    def test_overseas_filled_notice_parsing(self, overseas_filled_fields):
        notice = RealtimeExecutionNotice(overseas_filled_fields, is_overseas=True)
        assert notice.order_no == "US00012345"
        assert notice.ticker == "AAPL"
        assert notice.side == "02"
        assert notice.filled_qty == 5
        assert notice.filled_price == 185.50
        assert notice.order_qty == 5
        assert notice.is_filled is True
        assert notice.is_overseas is True

    def test_overseas_order_price(self, overseas_filled_fields):
        notice = RealtimeExecutionNotice(overseas_filled_fields, is_overseas=True)
        assert notice.order_price == 185.50


# ═══════════════════════════════════════════════════════════════════════
# AES 복호화 테스트
# ═══════════════════════════════════════════════════════════════════════


class TestAesCbcBase64Decrypt:
    """_aes_cbc_base64_decrypt 함수 테스트"""

    def test_encrypt_decrypt_roundtrip(self):
        """암호화 후 복호화하면 원문이 복원된다."""
        from base64 import b64encode

        from cryptography.hazmat.primitives.ciphers import (
            Cipher,
            algorithms,
            modes,
        )
        from cryptography.hazmat.primitives.padding import PKCS7

        key = "12345678901234567890123456789012"  # 32바이트
        iv = "1234567890123456"  # 16바이트
        plaintext = "CUST001^50012345^0000012345^005930^10^72000"

        # 암호화
        padder = PKCS7(algorithms.AES.block_size).padder()
        padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
        cipher = Cipher(
            algorithms.AES(key.encode("utf-8")),
            modes.CBC(iv.encode("utf-8")),
        )
        encryptor = cipher.encryptor()
        encrypted = encryptor.update(padded) + encryptor.finalize()
        cipher_text = b64encode(encrypted).decode("utf-8")

        # 복호화
        result = _aes_cbc_base64_decrypt(key, iv, cipher_text)
        assert result == plaintext

    def test_invalid_key_raises(self):
        """잘못된 key/iv로 복호화 시 예외 발생"""
        with pytest.raises(Exception):
            _aes_cbc_base64_decrypt("short", "iv", "dGVzdA==")


# ═══════════════════════════════════════════════════════════════════════
# TR_ID 상수 테스트
# ═══════════════════════════════════════════════════════════════════════


class TestExecNoticeTrIds:
    """체결 통보 TR_ID 상수 검증"""

    def test_domestic_tr_ids(self):
        assert TR_ID_EXEC_NOTICE_LIVE == "H0STCNI0"
        assert TR_ID_EXEC_NOTICE_DEMO == "H0STCNI9"

    def test_overseas_tr_ids(self):
        assert TR_ID_EXEC_NOTICE_OVERSEAS_LIVE == "H0GSCNI0"
        assert TR_ID_EXEC_NOTICE_OVERSEAS_DEMO == "H0GSCNI9"

    def test_exec_notice_set_contains_all(self):
        assert len(_EXEC_NOTICE_TR_IDS) == 4
        assert TR_ID_EXEC_NOTICE_LIVE in _EXEC_NOTICE_TR_IDS
        assert TR_ID_EXEC_NOTICE_DEMO in _EXEC_NOTICE_TR_IDS
        assert TR_ID_EXEC_NOTICE_OVERSEAS_LIVE in _EXEC_NOTICE_TR_IDS
        assert TR_ID_EXEC_NOTICE_OVERSEAS_DEMO in _EXEC_NOTICE_TR_IDS


# ═══════════════════════════════════════════════════════════════════════
# ws_execution_handler 테스트
# ═══════════════════════════════════════════════════════════════════════


class TestHandleExecutionNotice:
    """handle_execution_notice() 콜백 테스트"""

    def _make_notice(
        self,
        is_filled=True,
        is_rejected=False,
        order_no="0000012345",
        ticker="005930",
        filled_qty=10,
        filled_price=72000.0,
        order_qty=10,
    ):
        notice = MagicMock(spec=RealtimeExecutionNotice)
        notice.is_filled = is_filled
        notice.is_rejected = is_rejected
        notice.order_no = order_no
        notice.ticker = ticker
        notice.filled_qty = filled_qty
        notice.filled_price = filled_price
        notice.order_qty = order_qty
        return notice

    @pytest.mark.asyncio
    async def test_skip_on_accepted_notice(self):
        """접수 통보(is_filled=False)는 DB 갱신하지 않는다."""
        from core.order_executor.ws_execution_handler import (
            handle_execution_notice,
        )

        notice = self._make_notice(is_filled=False)
        with patch("core.order_executor.ws_execution_handler._find_order_by_kis_order_no") as mock_find:
            await handle_execution_notice(notice)
            mock_find.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_on_rejected_notice(self):
        """거부 통보(is_rejected=True)는 DB 갱신하지 않는다."""
        from core.order_executor.ws_execution_handler import (
            handle_execution_notice,
        )

        notice = self._make_notice(is_rejected=True)
        with patch("core.order_executor.ws_execution_handler._find_order_by_kis_order_no") as mock_find:
            await handle_execution_notice(notice)
            mock_find.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_when_no_matching_order(self):
        """매칭 주문이 없으면 DB 갱신하지 않는다."""
        from core.order_executor.ws_execution_handler import (
            handle_execution_notice,
        )

        notice = self._make_notice()
        with (
            patch(
                "core.order_executor.ws_execution_handler._find_order_by_kis_order_no",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "core.order_executor.ws_execution_handler._update_order_status",
                new_callable=AsyncMock,
            ) as mock_update,
        ):
            await handle_execution_notice(notice)
            mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_filled_notice_updates_db(self):
        """체결 통보 수신 시 DB를 FILLED로 갱신한다."""
        from core.order_executor.ws_execution_handler import (
            handle_execution_notice,
        )

        notice = self._make_notice(filled_qty=10, order_qty=10, filled_price=72000.0)
        with (
            patch(
                "core.order_executor.ws_execution_handler._find_order_by_kis_order_no",
                new_callable=AsyncMock,
                return_value={
                    "order_id": "0000012345",
                    "status": "SUBMITTED",
                },
            ),
            patch(
                "core.order_executor.ws_execution_handler._update_order_status",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_update,
        ):
            await handle_execution_notice(notice)
            mock_update.assert_called_once_with(
                order_id="0000012345",
                current_status_str="SUBMITTED",
                new_status=OrderStatus.FILLED,
                filled_quantity=10,
                filled_price=72000.0,
            )

    @pytest.mark.asyncio
    async def test_partial_fill_updates_db(self):
        """부분 체결 시 PARTIAL로 갱신한다."""
        from core.order_executor.ws_execution_handler import (
            handle_execution_notice,
        )

        notice = self._make_notice(filled_qty=5, order_qty=10)
        with (
            patch(
                "core.order_executor.ws_execution_handler._find_order_by_kis_order_no",
                new_callable=AsyncMock,
                return_value={
                    "order_id": "0000012345",
                    "status": "SUBMITTED",
                },
            ),
            patch(
                "core.order_executor.ws_execution_handler._update_order_status",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_update,
        ):
            await handle_execution_notice(notice)
            mock_update.assert_called_once()
            call_kwargs = mock_update.call_args.kwargs
            assert call_kwargs["new_status"] == OrderStatus.PARTIAL

    @pytest.mark.asyncio
    async def test_zero_filled_qty_skips(self):
        """체결수량 0이면 DB 갱신하지 않는다."""
        from core.order_executor.ws_execution_handler import (
            handle_execution_notice,
        )

        notice = self._make_notice(filled_qty=0)
        with (
            patch(
                "core.order_executor.ws_execution_handler._find_order_by_kis_order_no",
                new_callable=AsyncMock,
                return_value={
                    "order_id": "0000012345",
                    "status": "SUBMITTED",
                },
            ),
            patch(
                "core.order_executor.ws_execution_handler._update_order_status",
                new_callable=AsyncMock,
            ) as mock_update,
        ):
            await handle_execution_notice(notice)
            mock_update.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
# KISRealtimeClient 체결 통보 상태 관리 테스트
# ═══════════════════════════════════════════════════════════════════════


class TestKISRealtimeClientExecNoticeState:
    """KISRealtimeClient 체결 통보 관련 상태 검증"""

    @patch("core.data_collector.kis_websocket.get_settings")
    def test_initial_state(self, mock_settings):
        """초기 상태: 체결 통보 미구독"""
        from core.data_collector.kis_websocket import KISRealtimeClient

        mock_kis = MagicMock()
        mock_kis.is_backtest = False
        mock_settings.return_value.kis = mock_kis

        client = KISRealtimeClient()
        assert client._exec_notice_subscribed is False
        assert client._exec_notice_aes_key is None
        assert client._exec_notice_aes_iv is None
        assert client.on_exec_notice is None
        assert client._stats["exec_notices_processed"] == 0

    @patch("core.data_collector.kis_websocket.get_settings")
    def test_exec_notice_subscribed_property(self, mock_settings):
        """exec_notice_subscribed 프로퍼티 검증"""
        from core.data_collector.kis_websocket import KISRealtimeClient

        mock_kis = MagicMock()
        mock_kis.is_backtest = False
        mock_settings.return_value.kis = mock_kis

        client = KISRealtimeClient()
        assert client.exec_notice_subscribed is False
        client._exec_notice_subscribed = True
        assert client.exec_notice_subscribed is True

    @patch("core.data_collector.kis_websocket.get_settings")
    @pytest.mark.asyncio
    async def test_subscribe_exec_notice_requires_hts_id(self, mock_settings):
        """HTS ID 미설정 시 구독 실패"""
        from core.data_collector.kis_websocket import KISRealtimeClient

        mock_kis = MagicMock()
        mock_kis.is_backtest = False
        mock_kis.hts_id = ""
        mock_settings.return_value.kis = mock_kis

        client = KISRealtimeClient()
        client._connected = True
        client._ws = MagicMock()

        result = await client.subscribe_exec_notice()
        assert result is False
        assert client._exec_notice_subscribed is False

    @patch("core.data_collector.kis_websocket.get_settings")
    @pytest.mark.asyncio
    async def test_subscribe_exec_notice_not_connected(self, mock_settings):
        """미연결 시 구독 실패"""
        from core.data_collector.kis_websocket import KISRealtimeClient

        mock_kis = MagicMock()
        mock_kis.is_backtest = False
        mock_kis.hts_id = "test_hts_id"
        mock_settings.return_value.kis = mock_kis

        client = KISRealtimeClient()
        result = await client.subscribe_exec_notice()
        assert result is False


class TestHandleMessageExecNotice:
    """_handle_message에서 체결 통보 분기 검증"""

    @patch("core.data_collector.kis_websocket.get_settings")
    @pytest.mark.asyncio
    async def test_json_response_extracts_aes_keys(self, mock_settings):
        """구독 응답 JSON에서 AES key/iv를 추출한다."""
        import json

        from core.data_collector.kis_websocket import KISRealtimeClient

        mock_kis = MagicMock()
        mock_kis.is_backtest = False
        mock_settings.return_value.kis = mock_kis

        client = KISRealtimeClient()
        client._connected = True
        client._ws = MagicMock()

        json_msg = json.dumps(
            {
                "header": {
                    "tr_id": "H0STCNI9",
                    "msg_cd": "OPSP0000",
                    "encrypt": "Y",
                },
                "body": {
                    "rt_cd": "0",
                    "msg1": "SUBSCRIBE SUCCESS",
                    "output": {
                        "iv": "test_iv_1234567",
                        "key": "test_key_12345678901234567890123",
                    },
                },
            }
        )

        client._handle_json_response(json_msg)
        assert client._exec_notice_aes_key == "test_key_12345678901234567890123"
        assert client._exec_notice_aes_iv == "test_iv_1234567"


class TestEdgeCases:
    """경계 조건 테스트"""

    def test_empty_fields_domestic(self):
        """빈 필드 리스트에서도 예외 없이 파싱된다."""
        notice = RealtimeExecutionNotice([], is_overseas=False)
        assert notice.order_no == ""
        assert notice.ticker == ""
        assert notice.filled_qty == 0
        assert notice.filled_price == 0.0

    def test_empty_fields_overseas(self):
        """빈 필드 리스트 해외 — 예외 없이 파싱"""
        notice = RealtimeExecutionNotice([], is_overseas=True)
        assert notice.order_no == ""
        assert notice.ticker == ""
        assert notice.filled_qty == 0

    def test_short_fields_domestic(self):
        """필드 수 부족 시 기본값으로 처리"""
        fields = ["CUST", "ACNT", "ORD001"]  # 3개만
        notice = RealtimeExecutionNotice(fields, is_overseas=False)
        assert notice.order_no == "ORD001"
        assert notice.ticker == ""
        assert notice.filled_qty == 0

    def test_non_numeric_qty(self):
        """숫자가 아닌 체결수량 — 0으로 처리"""
        fields = [""] * 26
        fields[2] = "ORD001"
        fields[8] = "005930"
        fields[9] = "abc"  # 비숫자
        fields[13] = "2"

        notice = RealtimeExecutionNotice(fields, is_overseas=False)
        assert notice.filled_qty == 0


# ══════════════════════════════════════════════════════════════════
# RealtimeManager 체결 통보 Wiring 테스트
# ══════════════════════════════════════════════════════════════════


class TestRealtimeManagerExecNoticeWiring:
    """RealtimeManager.start()에서 체결 통보가 구독되는지 검증"""

    @pytest.mark.asyncio
    async def test_exec_notice_subscribed_on_start(self):
        """start() 호출 시 subscribe_exec_notice()가 호출된다."""
        mock_ws = AsyncMock()
        mock_ws.connect = AsyncMock(return_value=True)
        mock_ws.subscribe_batch = AsyncMock(return_value=1)
        mock_ws.subscribe_exec_notice = AsyncMock()

        with patch(
            "core.data_collector.kis_websocket.KISRealtimeClient",
            return_value=mock_ws,
        ):
            from core.data_collector.realtime_manager import RealtimeManager

            mgr = RealtimeManager()
            result = await mgr.start(["005930"])

        assert result is True
        mock_ws.subscribe_exec_notice.assert_called_once()
        # on_exec_notice 콜백이 등록되었는지 확인
        assert mock_ws.on_exec_notice is not None

    @pytest.mark.asyncio
    async def test_exec_notice_failure_does_not_block_start(self):
        """subscribe_exec_notice() 실패 시에도 start()는 성공한다."""
        mock_ws = AsyncMock()
        mock_ws.connect = AsyncMock(return_value=True)
        mock_ws.subscribe_batch = AsyncMock(return_value=1)
        mock_ws.subscribe_exec_notice = AsyncMock(side_effect=RuntimeError("HTS ID missing"))

        with patch(
            "core.data_collector.kis_websocket.KISRealtimeClient",
            return_value=mock_ws,
        ):
            from core.data_collector.realtime_manager import RealtimeManager

            mgr = RealtimeManager()
            result = await mgr.start(["005930"])

        assert result is True  # 실패해도 시세 수신은 정상
