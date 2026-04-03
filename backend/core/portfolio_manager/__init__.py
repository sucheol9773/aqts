"""
포트폴리오 관리 모듈 (Phase 4 - F-05)

사용자 투자 프로필 관리, 포트폴리오 구성 최적화,
정기/비상 리밸런싱, 유니버스 관리, 환율 데이터 관리를 담당합니다.

모듈 구성:
- profile: 사용자 투자 프로필 관리 (F-05-01)
- construction: 포트폴리오 구성 엔진 (F-05-02)
- rebalancing: 리밸런싱 엔진 (F-05-03, F-05-04)
- universe: 유니버스 관리 (F-05-06)
- exchange_rate: 환율 데이터 관리 (F-05-05)
"""

from core.portfolio_manager.profile import (
    InvestorProfile,
    InvestorProfileManager,
)
from core.portfolio_manager.construction import (
    TargetAllocation,
    TargetPortfolio,
    PortfolioConstructionEngine,
)
from core.portfolio_manager.rebalancing import (
    RebalancingOrder,
    RebalancingResult,
    RebalancingEngine,
)
from core.portfolio_manager.universe import (
    UniverseItem,
    UniverseManager,
)
from core.portfolio_manager.exchange_rate import (
    ExchangeRate,
    ExchangeRateManager,
)

__all__ = [
    # Profile
    "InvestorProfile",
    "InvestorProfileManager",
    # Construction
    "TargetAllocation",
    "TargetPortfolio",
    "PortfolioConstructionEngine",
    # Rebalancing
    "RebalancingOrder",
    "RebalancingResult",
    "RebalancingEngine",
    # Universe
    "UniverseItem",
    "UniverseManager",
    # Exchange Rate
    "ExchangeRate",
    "ExchangeRateManager",
]
