"""
슬리피지 및 매매 마진 모델 (Slippage Model)

Stage 3-A: Minimum Realism (편향 제거)

주요 기능:
- Bid-ask spread 기반 비용 계산
- Market impact 계산 (Square-root model)
- BUY/SELL별 슬리피지 적용
"""

from math import sqrt
from typing import Dict, Optional

from config.constants import Country, Market, OrderSide


class SlippageModel:
    """슬리피지 및 매매 마진 모델"""

    # 기본 스프레드 (bps: basis points, 0.01% = 1 bp)
    DEFAULT_SPREADS = {
        Country.KR: {
            "large_cap": 1.0,  # KOSPI 대형주: ~1 bp (0.01%)
            "mid_cap": 2.0,  # KOSPI 중형주: ~2 bp
            "small_cap": 5.0,  # KOSPI 소형주: ~5 bp
            "kosdaq": 3.0,  # KOSDAQ: ~3 bp
        },
        Country.US: {
            "large_cap": 0.5,  # NYSE large-cap: ~0.5 bp
            "mid_cap": 1.0,  # NYSE mid-cap: ~1 bp
            "small_cap": 2.0,  # NYSE small-cap: ~2 bp
            "nasdaq": 1.0,  # NASDAQ: ~1 bp
        },
    }

    def __init__(self, country: Country = Country.KR):
        """
        SlippageModel 초기화

        Args:
            country: 국가 (Country.KR 또는 Country.US)
        """
        self.country = country
        self.spreads = self.DEFAULT_SPREADS.get(country, self.DEFAULT_SPREADS[Country.KR])

    def calculate_spread_cost(
        self,
        ticker: str,
        market: Market,
        avg_spread: Optional[float] = None,
    ) -> float:
        """
        Half-spread 비용 계산

        market maker 입장에서 spread의 절반을 비용으로 지불합니다.
        (매수: 높은 ask price, 매도: 낮은 bid price)

        Args:
            ticker: 종목 코드
            market: 시장 (Market.KRX, Market.NASDAQ 등)
            avg_spread: 평균 spread (bps, 제공 시 이 값 사용)

        Returns:
            Half-spread 비용 (% 형태, 예: 0.005 = 0.5 bps = 0.005%)
        """
        if avg_spread is not None:
            # 명시적으로 spread 제공된 경우
            half_spread_bps = avg_spread / 2.0
            return half_spread_bps / 10000.0  # bps를 % 형태로 변환

        # 시장별 기본 spread 사용
        spread_bps = self._get_default_spread(market)
        half_spread_bps = spread_bps / 2.0

        return half_spread_bps / 10000.0  # % 형태로 반환

    def calculate_market_impact(
        self,
        order_quantity: float,
        adv: float,
        price: float,
    ) -> float:
        """
        Market impact 계산 (Square-root model)

        큰 주문은 시장에 영향을 미쳐 매도 시 가격이 하락하고
        매수 시 가격이 상승합니다.

        Formula: impact = price * sigma * sqrt(order_quantity / adv)

        sigma는 일반적으로 일별 수익률 변동성입니다.
        여기서는 통상적인 값 0.02 (2% 일별 변동성)를 사용합니다.

        Args:
            order_quantity: 주문 수량
            adv: Average Daily Volume (일 평균 거래량)
            price: 현재 주가

        Returns:
            Market impact (가격 단위, 절댓값)
        """
        if adv <= 0 or order_quantity <= 0 or price <= 0:
            return 0.0

        sigma = 0.02  # 일별 수익률 변동성 (2%)
        impact_ratio = sigma * sqrt(order_quantity / adv)
        impact_cost = price * impact_ratio

        return impact_cost

    def apply_slippage(
        self,
        price: float,
        side: OrderSide,
        spread_cost: float,
        impact_cost: float,
    ) -> float:
        """
        슬리피지 적용

        BUY 주문: 가격 상승 (spread_cost + impact_cost 추가)
        SELL 주문: 가격 하락 (spread_cost + impact_cost 차감)

        Args:
            price: 기본 가격
            side: 주문 방향 (OrderSide.BUY 또는 OrderSide.SELL)
            spread_cost: Half-spread 비용 (% 형태, 예: 0.00005 = 0.5 bps)
            impact_cost: Market impact 비용 (가격 단위)

        Returns:
            슬리피지 적용 후 가격
        """
        spread_price = price * spread_cost
        total_cost = spread_price + impact_cost

        if side == OrderSide.BUY:
            # BUY: 높은 가격에 매수 (spread + impact 상승)
            return price + total_cost
        else:  # OrderSide.SELL
            # SELL: 낮은 가격에 매도 (spread + impact 하락)
            return price - total_cost

    def _get_default_spread(self, market: Market) -> float:
        """
        시장별 기본 spread (bps) 반환

        Args:
            market: 시장

        Returns:
            Spread (bps)
        """
        if market == Market.KRX:
            return self.spreads.get("large_cap", 1.0)
        elif market == Market.NASDAQ:
            return self.spreads.get("nasdaq", 1.0)
        elif market == Market.NYSE:
            return self.spreads.get("large_cap", 1.0)
        elif market == Market.AMEX:
            return self.spreads.get("small_cap", 2.0)
        else:
            # 기본값
            return self.spreads.get("large_cap", 1.0)

    def get_spread_config(self) -> Dict:
        """현재 설정된 spread config 반환"""
        return {
            "country": self.country.value,
            "spreads": self.spreads,
        }
