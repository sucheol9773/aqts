"""P1-정합성: ReconciliationRunner 의 운영용 PositionProvider 구현체.

본 모듈은 ``ReconciliationRunner`` 가 비교하는 두 진실원천을 운영 환경의
실제 데이터로 채우는 단일 진입점이다. 이전까지 ``StaticPositionProvider``
만 존재하여 reconcile 가 수치적 통제로 작동하지 못했다 ("정의 ≠ 적용" 의
정합성 도메인 사례).

KISBrokerPositionProvider
    KIS Open API 의 ``inquire-balance`` 응답을 PositionMap 으로 정규화한다.
    응답 구조가 KIS 가이드와 어긋나거나 수치 변환이 불가능하면 raw dict 를
    외부로 노출하지 않고 ``BrokerPositionParseError`` 로 fail-closed 한다 —
    ReconciliationRunner 의 try/except 에 의해 ``aqts_reconciliation_runs_total
    {result="error"}`` 가 증가하고 사이클은 즉시 중단된다.

LedgerPositionProvider
    프로세스 내부 ``PortfolioLedger`` 싱글톤의 snapshot 을 반환한다. 본
    provider 는 ledger 자체를 mutate 하지 않으며, OrderExecutor 의 체결
    시점 record_fill 만이 ledger 의 단일 mutator 이다.

설계 근거: provider 는 데이터 계층에 대한 결합을 모두 흡수하여
ReconciliationEngine 과 ReconciliationRunner 가 어떤 영속화 정책에도
의존하지 않도록 한다. 후속 P1 항목에서 PortfolioLedger 가 DB 영속화로
교체되어도 본 모듈만 수정하면 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from config.logging import logger
from core.data_collector.kis_client import KISClient
from core.portfolio_ledger import PortfolioLedger, get_portfolio_ledger
from core.reconciliation_runner import PositionMap


class BrokerPositionParseError(RuntimeError):
    """KIS 잔고 응답을 PositionMap 으로 정규화할 수 없을 때 raise."""


@dataclass
class KISBrokerPositionProvider:
    """KIS ``get_kr_balance()`` 응답 → PositionMap.

    KIS 잔고 응답 (``output1``) 의 각 항목 형태:
        ``{"pdno": "005930", "hldg_qty": "100", ...}``

    Parameters
    ----------
    kis_client:
        KIS API client. 의존성 주입으로 테스트에서는 fake 를 사용.
    quantity_field:
        보유수량 필드명 (기본 ``hldg_qty``).
    ticker_field:
        종목코드 필드명 (기본 ``pdno``).

    Notes
    -----
    * 본 provider 는 일단 KR 시장만 다룬다 (`get_kr_balance`). US 잔고는
      통화 환산 + 환율 의존성이 있어 후속 항목으로 분리한다.
    * 0주 종목은 결과에서 제외한다 — ``PortfolioLedger.get_positions()`` 와
      동일 정책 (불필요한 mismatch 방지).
    """

    kis_client: KISClient
    quantity_field: str = "hldg_qty"
    ticker_field: str = "pdno"

    async def get_positions(self) -> PositionMap:
        try:
            response = await self.kis_client.get_kr_balance()
        except Exception as exc:
            logger.error("KIS get_kr_balance 실패: %s", exc)
            raise BrokerPositionParseError(f"KIS upstream error: {exc}") from exc
        return self._parse(response)

    def _parse(self, response: Any) -> PositionMap:
        if not isinstance(response, Mapping):
            raise BrokerPositionParseError(f"unexpected response type: {type(response).__name__}")
        rows = response.get("output1")
        if rows is None:
            # 잔고가 비어 있는 정상 응답일 수 있음 — 빈 dict 로 처리.
            return {}
        if not isinstance(rows, list):
            raise BrokerPositionParseError(f"output1 must be list, got {type(rows).__name__}")

        positions: PositionMap = {}
        for idx, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise BrokerPositionParseError(f"output1[{idx}] is not a dict: {type(row).__name__}")
            ticker = row.get(self.ticker_field)
            qty_raw = row.get(self.quantity_field)
            if ticker is None or qty_raw is None:
                raise BrokerPositionParseError(
                    f"output1[{idx}] missing required field " f"({self.ticker_field}/{self.quantity_field})"
                )
            try:
                qty = float(qty_raw)
            except (TypeError, ValueError) as exc:
                raise BrokerPositionParseError(f"output1[{idx}] non-numeric quantity: {qty_raw!r}") from exc
            if qty < 0:
                raise BrokerPositionParseError(f"output1[{idx}] negative quantity {qty} for {ticker}")
            if qty == 0:
                continue
            positions[str(ticker).strip()] = qty
        return positions


@dataclass
class LedgerPositionProvider:
    """``PortfolioLedger`` 의 snapshot 을 PositionMap 으로 반환.

    ledger 인자가 None 이면 프로세스 전역 싱글톤을 사용한다.
    """

    ledger: PortfolioLedger | None = None

    def __post_init__(self) -> None:
        if self.ledger is None:
            self.ledger = get_portfolio_ledger()

    async def get_positions(self) -> PositionMap:
        assert self.ledger is not None
        return self.ledger.get_positions()


__all__ = [
    "BrokerPositionParseError",
    "KISBrokerPositionProvider",
    "LedgerPositionProvider",
]
