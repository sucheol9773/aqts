"""
Contract Converters: 내부 dataclass ↔ Pydantic 계약 변환

내부 모듈의 가벼운 dataclass와 외부 경계의 Pydantic 계약 사이를
변환합니다. 계약 변환 시 Pydantic validation이 실행되므로
데이터 무결성이 강제됩니다.
"""

from typing import Optional

from config.constants import (
    Market,
    SignalDirection,
)
from contracts.order import OrderIntent
from contracts.signal import Signal as SignalContract


def signal_value_to_direction(value: float, threshold: float = 0.1) -> SignalDirection:
    """시그널 value (-1.0~+1.0)를 SignalDirection으로 변환합니다.

    Args:
        value: -1.0(강한 매도) ~ +1.0(강한 매수)
        threshold: HOLD 판정 기준 (abs(value) ≤ threshold → HOLD)
    """
    if value > threshold:
        return SignalDirection.BUY
    elif value < -threshold:
        return SignalDirection.SELL
    return SignalDirection.HOLD


def internal_signal_to_contract(
    internal_signal,
    *,
    market: Market = Market.KRX,
) -> SignalContract:
    """core.quant_engine.signal_generator.Signal → contracts.Signal 변환.

    Pydantic validation이 적용되어 confidence 범위, ticker 형식 등이 검증됩니다.

    Args:
        internal_signal: Signal dataclass (ticker, strategy, value, confidence, reason)
        market: 거래 시장 (기본값: KRX)

    Returns:
        contracts.Signal (Pydantic 모델)

    Raises:
        pydantic.ValidationError: 계약 위반 시
    """
    direction = signal_value_to_direction(internal_signal.value)

    # HOLD 시그널의 confidence가 0이면 계약을 통과시키고,
    # BUY/SELL 시그널의 confidence가 0이면 ValidationError 발생
    confidence = internal_signal.confidence
    if direction == SignalDirection.HOLD and confidence == 0.0:
        confidence = 0.0  # 계약상 허용

    return SignalContract(
        ticker=internal_signal.ticker,
        market=market,
        direction=direction,
        confidence=confidence,
        strategy_id=internal_signal.strategy,
        reason=internal_signal.reason,
    )


def order_request_to_contract(
    order_request,
    *,
    strategy_id: Optional[str] = None,
    decision_id: Optional[str] = None,
) -> OrderIntent:
    """core.order_executor.OrderRequest → contracts.OrderIntent 변환.

    Pydantic validation이 적용되어 LIMIT 가격 필수 등이 검증됩니다.

    Args:
        order_request: OrderRequest dataclass
        strategy_id: 전략 식별자 (optional)
        decision_id: 감사 체인 연결 ID (optional)

    Returns:
        contracts.OrderIntent (Pydantic 모델)

    Raises:
        pydantic.ValidationError: 계약 위반 시
    """
    return OrderIntent(
        ticker=order_request.ticker,
        market=order_request.market,
        side=order_request.side,
        order_type=order_request.order_type,
        quantity=order_request.quantity,
        limit_price=order_request.limit_price,
        reason=order_request.reason,
        strategy_id=strategy_id,
        decision_id=decision_id,
    )
