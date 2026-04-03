"""
Stage 5.1-5.2: 자본금 예산 관리 (Capital Budget Management)

다전략 포트폴리오의 자본금을 전략별로 배분하고 관리합니다.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict


@dataclass
class CapitalBudget:
    """
    전략별 자본금 할당 및 사용 추적

    각 전략에 할당된 자본금 내에서만 주문을 실행하도록 제한합니다.
    """

    total_capital: float
    strategy_allocations: Dict[str, float] = field(default_factory=dict)
    _daily_usage: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        """초기화 검증"""
        if self.total_capital <= 0:
            raise ValueError(f"total_capital must be > 0, got {self.total_capital}")

        # 기본 할당 (미지정 시 균등 배분)
        if not self.strategy_allocations:
            strategies = ["TREND", "MEAN_REV", "FACTOR", "RISK_PARITY"]
            self.strategy_allocations = {s: 1.0 / len(strategies) for s in strategies}

        # 할당 비율 합이 1.0 확인
        total_allocation = sum(self.strategy_allocations.values())
        if abs(total_allocation - 1.0) > 0.001:
            raise ValueError(
                f"strategy_allocations must sum to 1.0, got {total_allocation}"
            )

        # 일일 사용 초기화
        for strategy in self.strategy_allocations:
            self._daily_usage[strategy] = 0.0

    def get_budget(self, strategy_id: str) -> float:
        """전략에 할당된 자본금 반환"""
        if strategy_id not in self.strategy_allocations:
            raise KeyError(f"Unknown strategy: {strategy_id}")

        return self.total_capital * self.strategy_allocations[strategy_id]

    def check_budget(self, strategy_id: str, amount: float) -> bool:
        """
        요청 금액이 전략의 예산 범위 내인지 확인

        Args:
            strategy_id: 전략 식별자
            amount: 요청 금액 (양수)

        Returns:
            True if within budget, False otherwise
        """
        if strategy_id not in self.strategy_allocations:
            raise KeyError(f"Unknown strategy: {strategy_id}")

        if amount < 0:
            raise ValueError(f"amount must be >= 0, got {amount}")

        allocated = self.get_budget(strategy_id)
        used = self._daily_usage.get(strategy_id, 0.0)
        remaining = allocated - used

        return amount <= remaining

    def record_usage(self, strategy_id: str, amount: float) -> float:
        """
        주문 실행 후 자본금 사용 기록

        Args:
            strategy_id: 전략 식별자
            amount: 사용한 금액 (양수)

        Returns:
            남은 예산 금액

        Raises:
            ValueError: 초과 사용 시도 시
            KeyError: 알려지지 않은 전략
        """
        if strategy_id not in self.strategy_allocations:
            raise KeyError(f"Unknown strategy: {strategy_id}")

        if amount < 0:
            raise ValueError(f"amount must be >= 0, got {amount}")

        # 예산 초과 확인
        if not self.check_budget(strategy_id, amount):
            allocated = self.get_budget(strategy_id)
            used = self._daily_usage.get(strategy_id, 0.0)
            raise ValueError(
                f"Budget exceeded for {strategy_id}: "
                f"allocated={allocated}, used={used}, requested={amount}"
            )

        self._daily_usage[strategy_id] = self._daily_usage.get(strategy_id, 0.0) + amount
        return self.get_remaining(strategy_id)

    def get_remaining(self, strategy_id: str) -> float:
        """전략의 남은 예산 반환"""
        if strategy_id not in self.strategy_allocations:
            raise KeyError(f"Unknown strategy: {strategy_id}")

        allocated = self.get_budget(strategy_id)
        used = self._daily_usage.get(strategy_id, 0.0)
        return allocated - used

    def reset_daily(self) -> None:
        """일일 사용 통계 초기화 (매일 자정 호출)"""
        for strategy in self.strategy_allocations:
            self._daily_usage[strategy] = 0.0


@dataclass
class AssetClassLimiter:
    """
    자산 클래스별 최대 비중 제한

    예: KR 주식 최대 60%, US 주식 최대 40%
    """

    limits: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        """초기화 검증"""
        if not self.limits:
            self.limits = {
                "KR_EQUITY": 0.6,
                "US_EQUITY": 0.4,
            }

        # 각 한도가 0~1 범위 확인
        for asset_class, limit in self.limits.items():
            if not (0 <= limit <= 1):
                raise ValueError(
                    f"Limit for {asset_class} must be in [0, 1], got {limit}"
                )

    def check_limit(
        self,
        asset_class: str,
        current_weight: float,
        additional_weight: float
    ) -> bool:
        """
        추가 비중이 한도를 초과하지 않는지 확인

        Args:
            asset_class: 자산 클래스 (e.g., "KR_EQUITY")
            current_weight: 현재 포트폴리오 비중 (0~1)
            additional_weight: 추가 비중 (0~1)

        Returns:
            True if within limit, False otherwise
        """
        if asset_class not in self.limits:
            raise KeyError(f"Unknown asset class: {asset_class}")

        if not (0 <= current_weight <= 1):
            raise ValueError(f"current_weight must be in [0, 1], got {current_weight}")

        if not (0 <= additional_weight <= 1):
            raise ValueError(
                f"additional_weight must be in [0, 1], got {additional_weight}"
            )

        total_weight = current_weight + additional_weight
        limit = self.limits[asset_class]

        return total_weight <= limit

    def get_available(self, asset_class: str, current_weight: float) -> float:
        """
        자산 클래스의 추가 가능 비중 반환

        Args:
            asset_class: 자산 클래스
            current_weight: 현재 포트폴리오 비중

        Returns:
            추가 가능한 최대 비중
        """
        if asset_class not in self.limits:
            raise KeyError(f"Unknown asset class: {asset_class}")

        if not (0 <= current_weight <= 1):
            raise ValueError(f"current_weight must be in [0, 1], got {current_weight}")

        limit = self.limits[asset_class]
        return max(0.0, limit - current_weight)
