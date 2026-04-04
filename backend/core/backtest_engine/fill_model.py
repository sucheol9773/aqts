"""
부분 체결 시뮬레이션 모델 (Fill Model)

Stage 3-A: Minimum Realism (편향 제거)

주요 기능:
- Partial fill 시뮬레이션: 대량 주문의 일부 체결 표현
- ADV cap 적용: 일일 거래량 대비 주문량 제한
- Order splitting: 대량 주문을 여러 소주문으로 분할
"""

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class FillResult:
    """체결 결과"""

    filled_quantity: float  # 체결된 수량
    unfilled_quantity: float  # 미체결된 수량
    fill_ratio: float  # 체결률 (0.0 ~ 1.0)


class FillModel:
    """부분 체결 시뮬레이션 모델"""

    # ADV 대비 주문량 임계값
    LIGHT_ADV_PCT = 0.10  # 10% ADV 이하: 전량 체결
    MEDIUM_ADV_PCT = 0.30  # 10-30% ADV: 부분 체결
    HEAVY_ADV_PCT = 1.0  # 30% ADV 초과: 상당 부분 미체결

    def __init__(self):
        """FillModel 초기화"""
        pass

    def simulate_fill(
        self,
        order_quantity: float,
        adv: float,
        price: float,
    ) -> FillResult:
        """
        부분 체결 시뮬레이션

        주문량이 일일 거래량(ADV)에 비해 크면 부분 체결이 발생합니다.
        - quantity <= 10% ADV: 전량 체결 (fill_ratio = 1.0)
        - 10% < quantity <= 30% ADV: 선형 감소 (fill_ratio = 1.0 ~ 0.5)
        - quantity > 30% ADV: 비선형 감소 (fill_ratio < 0.5)

        Args:
            order_quantity: 주문 수량
            adv: Average Daily Volume (일 평균 거래량)
            price: 현재 주가 (미사용, 호환성 유지)

        Returns:
            FillResult (체결 수량, 미체결 수량, 체결률)
        """
        if adv <= 0:
            # ADV가 없으면 주문량의 50%만 체결
            return FillResult(
                filled_quantity=order_quantity * 0.5,
                unfilled_quantity=order_quantity * 0.5,
                fill_ratio=0.5,
            )

        adv_pct = order_quantity / adv

        if adv_pct <= self.LIGHT_ADV_PCT:
            # 10% 이하: 전량 체결
            fill_ratio = 1.0
        elif adv_pct <= self.MEDIUM_ADV_PCT:
            # 10-30%: 선형 감소
            # 10%에서 1.0, 30%에서 0.5로 선형 보간
            progress = (adv_pct - self.LIGHT_ADV_PCT) / (self.MEDIUM_ADV_PCT - self.LIGHT_ADV_PCT)
            fill_ratio = 1.0 - (progress * 0.5)  # 1.0 ~ 0.5
        else:
            # 30% 초과: 비선형 감소 (제곱 함수)
            # 30%에서 0.5, 100%에서 거의 0
            excess = adv_pct - self.MEDIUM_ADV_PCT
            fill_ratio = 0.5 * (1.0 - (excess / (1.0 - self.MEDIUM_ADV_PCT)) ** 1.5)
            fill_ratio = max(fill_ratio, 0.1)  # 최소 10% 체결

        filled_quantity = order_quantity * fill_ratio
        unfilled_quantity = order_quantity * (1.0 - fill_ratio)

        return FillResult(
            filled_quantity=filled_quantity,
            unfilled_quantity=unfilled_quantity,
            fill_ratio=fill_ratio,
        )

    def apply_adv_cap(
        self,
        order_quantity: float,
        adv: float,
        max_adv_pct: float = 0.05,
    ) -> float:
        """
        ADV cap 적용

        일일 거래량의 max_adv_pct% 이상을 한 번에 거래할 수 없습니다.
        과도한 주문량은 제한됩니다.

        Args:
            order_quantity: 주문 수량
            adv: Average Daily Volume
            max_adv_pct: 최대 ADV 비율 (기본값: 0.05 = 5%)

        Returns:
            제한된 주문 수량
        """
        if adv <= 0:
            return order_quantity

        max_quantity = adv * max_adv_pct
        capped_quantity = min(order_quantity, max_quantity)

        return capped_quantity

    def split_large_order(
        self,
        order_quantity: float,
        adv: float,
        max_adv_pct: float = 0.05,
    ) -> List[float]:
        """
        대량 주문 분할

        ADV cap을 초과하는 주문을 여러 소주문으로 분할합니다.
        각 소주문은 최대 max_adv_pct * ADV 수량을 갖습니다.

        Args:
            order_quantity: 원래 주문 수량
            adv: Average Daily Volume
            max_adv_pct: 최대 ADV 비율 (기본값: 0.05 = 5%)

        Returns:
            소주문 수량 리스트
        """
        if adv <= 0:
            # ADV가 없으면 3개의 동일한 부분으로 분할
            sub_quantity = order_quantity / 3.0
            return [sub_quantity, sub_quantity, sub_quantity]

        max_sub_quantity = adv * max_adv_pct

        if max_sub_quantity <= 0:
            # 비정상적인 경우 주문량의 절반씩 2개로 분할
            return [order_quantity / 2.0, order_quantity / 2.0]

        if order_quantity <= max_sub_quantity:
            # 분할 불필요
            return [order_quantity]

        # 주문량을 max_sub_quantity씩 분할
        sub_orders = []
        remaining = order_quantity

        while remaining > 0:
            if remaining <= max_sub_quantity:
                sub_orders.append(remaining)
                remaining = 0
            else:
                sub_orders.append(max_sub_quantity)
                remaining -= max_sub_quantity

        return sub_orders

    def calculate_fill_cost(
        self,
        order_quantity: float,
        adv: float,
        base_price: float,
        price_impact_per_adv_pct: float = 0.001,
    ) -> Dict:
        """
        체결 비용 계산 (advanced)

        부분 체결 시 추가 비용이 발생할 수 있습니다.
        이는 주문을 완료하기 위해 더 긴 시간이 필요하고,
        그 동안 가격이 불리하게 변할 수 있음을 반영합니다.

        Args:
            order_quantity: 주문 수량
            adv: Average Daily Volume
            base_price: 기본 가격
            price_impact_per_adv_pct: ADV% 당 가격 영향 (기본값: 0.1%)

        Returns:
            {
                'fill_result': FillResult,
                'avg_fill_price': float,  # 평균 체결가
                'total_cost': float,       # 총 거래 비용
                'cost_pct': float,         # 비용률 (%)
            }
        """
        fill_result = self.simulate_fill(order_quantity, adv, base_price)

        adv_pct = order_quantity / adv if adv > 0 else 0

        # Price impact: ADV% 비율만큼 가격이 영향을 받음
        price_impact_ratio = adv_pct * price_impact_per_adv_pct
        price_impact = base_price * price_impact_ratio

        # 부분 체결로 인한 추가 슬리피지
        # 체결률이 낮을수록 추가 비용 발생 (더 오래 시간이 걸림)
        partial_fill_cost = base_price * (1.0 - fill_result.fill_ratio) * 0.002

        avg_fill_price = base_price + price_impact + partial_fill_cost
        total_cost = (avg_fill_price - base_price) * fill_result.filled_quantity
        cost_pct = ((avg_fill_price - base_price) / base_price) * 100

        return {
            "fill_result": fill_result,
            "avg_fill_price": avg_fill_price,
            "total_cost": total_cost,
            "cost_pct": cost_pct,
        }
