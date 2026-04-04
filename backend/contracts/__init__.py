"""
AQTS Data Contracts (Stage 2-A)
================================
9개 표준 데이터 계약 스키마: 파이프라인 모듈 경계에서 데이터 무결성을 강제합니다.

Contracts:
    1. PriceData        — OHLCV 시세 데이터
    2. FinancialData     — 재무제표 지표
    3. NewsData          — 뉴스/공시 데이터
    4. FeatureVector     — 팩터 스코어 + 기술적 지표 + 감성 점수
    5. Signal            — 매매 시그널
    6. Portfolio         — 포트폴리오 목표 포지션
    7. Order             — 주문 의도
    8. Execution         — 체결 결과
    9. RiskCheck         — 리스크 점검 결과
"""

from contracts.execution import ExecutionResult
from contracts.feature_vector import FeatureVector
from contracts.financial_data import FinancialData
from contracts.news_data import NewsData
from contracts.order import OrderIntent
from contracts.portfolio import PortfolioTarget, PositionTarget
from contracts.price_data import PriceData
from contracts.risk_check import RiskCheckItem, RiskCheckResult
from contracts.signal import Signal

__all__ = [
    "PriceData",
    "FinancialData",
    "NewsData",
    "FeatureVector",
    "Signal",
    "PortfolioTarget",
    "PositionTarget",
    "OrderIntent",
    "ExecutionResult",
    "RiskCheckResult",
    "RiskCheckItem",
]
