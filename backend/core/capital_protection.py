"""
Stage 5.4-5.9: 자본 보호 계층 (Capital Protection Layers)

9개의 다층 보호 메커니즘:
  1. 일일 주문 건수 제한 (DailyOrderLimiter)
  2. 지연 호가 차단 (StaleQuoteBlocker)
  3. AI 데이터 신선도 검증 (AIDelayFallback)
  4. API 실패 안전 모드 (APIFailureSafeMode)
  5. 현금 플로어 가드 (CashFloorGuard)
  ... 추가 4개는 trading_guard.py에서 구현
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class DailyOrderLimiter:
    """
    일일 주문 건수 제한 (Stage 5.1)

    과도한 거래량을 방지하기 위해 하루에 허용되는 주문 건수를 제한합니다.
    """

    max_orders: int = 50
    _order_count: int = field(default=0, init=False)

    def __post_init__(self):
        if self.max_orders <= 0:
            raise ValueError(f"max_orders must be > 0, got {self.max_orders}")

    def can_place_order(self) -> bool:
        """다음 주문을 실행할 수 있는지 확인"""
        return self._order_count < self.max_orders

    def record_order(self) -> int:
        """
        주문을 기록하고 남은 주문 건수 반환

        Raises:
            RuntimeError: 한도를 초과했을 때
        """
        if not self.can_place_order():
            raise RuntimeError(f"Daily order limit exceeded: {self._order_count}/{self.max_orders}")

        self._order_count += 1
        return self.max_orders - self._order_count

    def reset_daily(self) -> None:
        """매일 자정에 카운터 초기화"""
        self._order_count = 0

    def get_count(self) -> int:
        """현재 주문 건수 반환"""
        return self._order_count


@dataclass
class StaleQuoteBlocker:
    """
    지연 호가 차단 (Stage 5.4)

    너무 오래된 호가(quote)를 받으면 주문을 거부합니다.
    config/operational_thresholds.yaml의 stale_quote_max_seconds를 기본값으로 사용합니다.
    """

    max_stale_seconds: int = 30  # operational_thresholds.yaml과 일치

    def __post_init__(self):
        if self.max_stale_seconds <= 0:
            raise ValueError(f"max_stale_seconds must be > 0, got {self.max_stale_seconds}")

    def is_stale(self, quote_timestamp: datetime) -> bool:
        """
        호가가 지연되었는지 확인

        Args:
            quote_timestamp: 호가 생성 시간 (UTC)

        Returns:
            True if stale, False otherwise
        """
        if not isinstance(quote_timestamp, datetime):
            raise TypeError("quote_timestamp must be datetime")

        now = datetime.utcnow()
        age_seconds = (now - quote_timestamp).total_seconds()

        return age_seconds > self.max_stale_seconds

    def validate_quote(self, quote_timestamp: datetime) -> None:
        """
        호가 유효성 검증

        Args:
            quote_timestamp: 호가 생성 시간 (UTC)

        Raises:
            ValueError: 호가가 지연되었을 때
        """
        if self.is_stale(quote_timestamp):
            now = datetime.utcnow()
            age_seconds = (now - quote_timestamp).total_seconds()
            raise ValueError(f"Quote is too stale: age={age_seconds:.1f}s, " f"max_allowed={self.max_stale_seconds}s")


@dataclass
class AIDelayFallback:
    """
    AI 데이터 신선도 검증 (Stage 5.5)

    AI 모델의 분석 결과가 너무 오래되면 가중치를 감소시킵니다.
    """

    max_delay_hours: int = 4  # operational_thresholds.yaml과 유사

    def __post_init__(self):
        if self.max_delay_hours <= 0:
            raise ValueError(f"max_delay_hours must be > 0, got {self.max_delay_hours}")

    def check_freshness(self, ai_timestamp: datetime) -> bool:
        """
        AI 데이터가 신선한지 확인

        Args:
            ai_timestamp: AI 분석 시간 (UTC)

        Returns:
            True if fresh, False if stale
        """
        if not isinstance(ai_timestamp, datetime):
            raise TypeError("ai_timestamp must be datetime")

        now = datetime.utcnow()
        age_hours = (now - ai_timestamp).total_seconds() / 3600

        return age_hours <= self.max_delay_hours

    def get_weight_multiplier(self, ai_timestamp: datetime) -> float:
        """
        데이터 나이에 따른 가중치 배수 계산

        Args:
            ai_timestamp: AI 분석 시간 (UTC)

        Returns:
            1.0 if fresh, linearly decreasing to 0.0 if stale
            Fresh: [0, max_delay_hours] → multiplier = 1.0
            Stale: (max_delay_hours, ∞) → multiplier = 0.0
        """
        if not isinstance(ai_timestamp, datetime):
            raise TypeError("ai_timestamp must be datetime")

        if self.check_freshness(ai_timestamp):
            return 1.0
        else:
            return 0.0


@dataclass
class APIFailureSafeMode:
    """
    API 실패 안전 모드 (Stage 5.6)

    API 호출이 연속으로 실패하면 거래를 중단합니다.
    """

    max_consecutive_failures: int = 3
    _failure_count: int = field(default=0, init=False)

    def __post_init__(self):
        if self.max_consecutive_failures <= 0:
            raise ValueError(f"max_consecutive_failures must be > 0, " f"got {self.max_consecutive_failures}")

    def record_failure(self) -> bool:
        """
        API 실패 기록

        Returns:
            True if safe mode should be activated, False otherwise
        """
        self._failure_count += 1
        return self.is_safe_mode()

    def record_success(self) -> None:
        """API 성공 기록 (실패 카운터 초기화)"""
        self._failure_count = 0

    def is_safe_mode(self) -> bool:
        """안전 모드 활성화 여부"""
        return self._failure_count >= self.max_consecutive_failures

    def get_failure_count(self) -> int:
        """현재 연속 실패 건수"""
        return self._failure_count


@dataclass
class CashFloorGuard:
    """
    현금 플로어 가드 (Stage 5.9)

    포트폴리오의 현금 비중이 최소 한도 아래로 내려가지 않도록 보호합니다.
    """

    min_cash_ratio: float = 0.10  # 최소 10% 현금 유지

    def __post_init__(self):
        if not (0 <= self.min_cash_ratio <= 1):
            raise ValueError(f"min_cash_ratio must be in [0, 1], got {self.min_cash_ratio}")

    def check_floor(self, cash_amount: float, total_portfolio: float) -> bool:
        """
        현금 플로어 확인

        Args:
            cash_amount: 현재 보유 현금
            total_portfolio: 전체 포트폴리오 가치

        Returns:
            True if above floor, False otherwise
        """
        if cash_amount < 0:
            raise ValueError(f"cash_amount must be >= 0, got {cash_amount}")

        if total_portfolio <= 0:
            raise ValueError(f"total_portfolio must be > 0, got {total_portfolio}")

        cash_ratio = cash_amount / total_portfolio
        return cash_ratio >= self.min_cash_ratio

    def max_deployable(self, cash_amount: float, total_portfolio: float) -> float:
        """
        플로어 유지하면서 투자할 수 있는 최대 현금

        Args:
            cash_amount: 현재 보유 현금
            total_portfolio: 전체 포트폴리오 가치

        Returns:
            투자 가능한 최대 현금 (0 이상)
        """
        if cash_amount < 0:
            raise ValueError(f"cash_amount must be >= 0, got {cash_amount}")

        if total_portfolio <= 0:
            raise ValueError(f"total_portfolio must be > 0, got {total_portfolio}")

        # 플로어 유지에 필요한 최소 현금
        min_required = total_portfolio * self.min_cash_ratio

        # 배포 가능한 현금 = 현재 현금 - 최소 필요 현금
        return max(0.0, cash_amount - min_required)
