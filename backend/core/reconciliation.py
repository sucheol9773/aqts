"""
Stage 5.3: 브로커 재조정 (Broker Reconciliation)

내부 포지션과 브로커의 실제 포지션을 자동으로 비교하고
불일치를 감지하면 즉시 알림을 발행합니다.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ReconciliationResult(BaseModel):
    """
    재조정 결과를 담는 데이터 모델

    matched: 모든 포지션이 일치했는지 여부
    mismatches: 불일치한 포지션 목록
    timestamp: 재조정 수행 시간
    broker_total: 브로커 측 총 포지션 가치
    internal_total: 내부 기록 포지션 가치
    """

    matched: bool
    mismatches: List[Dict] = Field(default_factory=list)
    timestamp: datetime
    broker_total: float
    internal_total: float

    class Config:
        from_attributes = True


@dataclass
class ReconciliationEngine:
    """
    브로커 재조정 엔진

    매거래 후 내부 기록과 브로커 API 응답을 비교하여
    불일치를 감지하고 기록합니다.
    """

    _last_result: Optional[ReconciliationResult] = None

    def reconcile(
        self, broker_positions: Dict[str, float], internal_positions: Dict[str, float]
    ) -> ReconciliationResult:
        """
        브로커 포지션과 내부 포지션 비교

        Args:
            broker_positions: 브로커의 포지션 {"005930": 100, "000660": 50}
            internal_positions: 내부 기록 {"005930": 100, "000660": 50}

        Returns:
            ReconciliationResult 객체
        """
        if not isinstance(broker_positions, dict):
            raise TypeError("broker_positions must be dict")
        if not isinstance(internal_positions, dict):
            raise TypeError("internal_positions must be dict")

        mismatches = []
        matched = True

        # 브로커에 있는 모든 종목 확인
        all_tickers = set(broker_positions.keys()) | set(internal_positions.keys())

        for ticker in all_tickers:
            broker_qty = broker_positions.get(ticker, 0.0)
            internal_qty = internal_positions.get(ticker, 0.0)

            if abs(broker_qty - internal_qty) > 1e-6:  # 부동소수점 오차 허용
                matched = False
                mismatches.append(
                    {
                        "ticker": ticker,
                        "broker_qty": broker_qty,
                        "internal_qty": internal_qty,
                        "difference": broker_qty - internal_qty,
                    }
                )

        broker_total = sum(broker_positions.values())
        internal_total = sum(internal_positions.values())

        result = ReconciliationResult(
            matched=matched,
            mismatches=mismatches,
            timestamp=datetime.utcnow(),
            broker_total=broker_total,
            internal_total=internal_total,
        )

        self._last_result = result
        return result

    def reconcile_balance(self, broker_balance: float, internal_balance: float, tolerance: float = 0.01) -> bool:
        """
        계좌 잔액(현금) 확인

        Args:
            broker_balance: 브로커의 현금 잔액
            internal_balance: 내부 기록 현금 잔액
            tolerance: 허용 오차 (절대값, 기본 0.01)

        Returns:
            True if balance matches within tolerance, False otherwise
        """
        if broker_balance < 0 or internal_balance < 0:
            raise ValueError("Balances must be non-negative")

        difference = abs(broker_balance - internal_balance)
        return difference <= tolerance

    def get_mismatches(self) -> List[Dict]:
        """마지막 재조정의 불일치 목록 반환"""
        if self._last_result is None:
            return []
        return self._last_result.mismatches
