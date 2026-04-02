"""
AQTS 상수 정의 모듈

시스템 전반에서 사용되는 상수를 중앙 관리합니다.
"""

from enum import Enum


# ══════════════════════════════════════
# 시장 관련 상수
# ══════════════════════════════════════
class Market(str, Enum):
    """투자 대상 시장"""
    KRX = "KRX"          # 한국거래소 (KOSPI + KOSDAQ)
    NYSE = "NYSE"        # 뉴욕증권거래소
    NASDAQ = "NASDAQ"    # 나스닥
    AMEX = "AMEX"        # 아메리칸증권거래소


class AssetType(str, Enum):
    """투자 자산 유형"""
    STOCK = "STOCK"
    ETF = "ETF"
    BOND = "BOND"
    ETN = "ETN"
    REITS = "REITS"
    CASH = "CASH"


class Country(str, Enum):
    """국가 구분"""
    KR = "KR"
    US = "US"


# ══════════════════════════════════════
# 사용자 프로필 관련 상수
# ══════════════════════════════════════
class RiskProfile(str, Enum):
    """수익률 성향"""
    CONSERVATIVE = "CONSERVATIVE"    # 안정적
    BALANCED = "BALANCED"            # 균형적
    AGGRESSIVE = "AGGRESSIVE"        # 공격적
    DIVIDEND = "DIVIDEND"            # 배당형


class InvestmentStyle(str, Enum):
    """투자 스타일"""
    DISCRETIONARY = "DISCRETIONARY"  # 일임형 (자동매매)
    ADVISORY = "ADVISORY"            # 자문형 (추천만)


class InvestmentGoal(str, Enum):
    """투자 목적"""
    WEALTH_GROWTH = "WEALTH_GROWTH"        # 자산 증식
    RETIREMENT = "RETIREMENT"              # 은퇴 자금
    EDUCATION = "EDUCATION"                # 교육비
    INCOME = "INCOME"                      # 정기 수입


# ══════════════════════════════════════
# 매매 관련 상수
# ══════════════════════════════════════
class OrderSide(str, Enum):
    """주문 방향"""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """주문 유형"""
    MARKET = "MARKET"      # 시장가
    LIMIT = "LIMIT"        # 지정가
    TWAP = "TWAP"          # 시간가중평균
    VWAP = "VWAP"          # 거래량가중평균


class OrderStatus(str, Enum):
    """주문 상태"""
    PENDING = "PENDING"          # 대기
    SUBMITTED = "SUBMITTED"      # 제출됨
    PARTIAL = "PARTIAL"          # 부분 체결
    FILLED = "FILLED"            # 전체 체결
    CANCELLED = "CANCELLED"      # 취소
    FAILED = "FAILED"            # 실패


# ══════════════════════════════════════
# 시그널 관련 상수
# ══════════════════════════════════════
class SignalDirection(str, Enum):
    """시그널 방향"""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class StrategyType(str, Enum):
    """전략 유형"""
    FACTOR = "FACTOR"                  # 팩터 투자
    MEAN_REVERSION = "MEAN_REVERSION"  # 평균회귀
    TREND_FOLLOWING = "TREND_FOLLOWING" # 추세추종
    STAT_ARB = "STAT_ARB"             # 통계적 차익
    RISK_PARITY = "RISK_PARITY"       # 리스크 패리티
    ML_SIGNAL = "ML_SIGNAL"           # 머신러닝


class SentimentMode(str, Enum):
    """AI 감성 분석 모드"""
    SCORE = "SCORE"        # Mode A: 감성 점수 산출
    OPINION = "OPINION"    # Mode B: 투자 의견 생성


# ══════════════════════════════════════
# 리밸런싱 관련 상수
# ══════════════════════════════════════
class RebalancingType(str, Enum):
    """리밸런싱 유형"""
    SCHEDULED = "SCHEDULED"    # 정기
    EMERGENCY = "EMERGENCY"    # 비상
    MANUAL = "MANUAL"          # 수동


class RebalancingFrequency(str, Enum):
    """리밸런싱 주기"""
    MONTHLY = "MONTHLY"          # 매월
    BIMONTHLY = "BIMONTHLY"      # 격월
    QUARTERLY = "QUARTERLY"      # 분기별


# ══════════════════════════════════════
# 알림 관련 상수
# ══════════════════════════════════════
class AlertType(str, Enum):
    """알림 유형"""
    DAILY_REPORT = "DAILY_REPORT"
    WEEKLY_REPORT = "WEEKLY_REPORT"
    MONTHLY_REPORT = "MONTHLY_REPORT"
    EMERGENCY_REBALANCING = "EMERGENCY_REBALANCING"
    SYSTEM_ERROR = "SYSTEM_ERROR"


# ══════════════════════════════════════
# 매매 빈도 매핑 (프로필 → 보유 기간)
# ══════════════════════════════════════
HOLDING_PERIOD_MAP = {
    RiskProfile.CONSERVATIVE: {"min_days": 14, "max_days": 180, "label": "포지션"},
    RiskProfile.BALANCED: {"min_days": 3, "max_days": 21, "label": "스윙"},
    RiskProfile.AGGRESSIVE: {"min_days": 1, "max_days": 7, "label": "단타~스윙"},
    RiskProfile.DIVIDEND: {"min_days": 60, "max_days": 365, "label": "포지션"},
}


# ══════════════════════════════════════
# 거래 비용 상수
# ══════════════════════════════════════
TRANSACTION_COSTS = {
    Country.KR: {
        "commission_rate": 0.00015,  # 0.015%
        "tax_rate": 0.0023,          # 0.23% (매도 시)
        "slippage_rate": 0.001,      # 0.1%
    },
    Country.US: {
        "commission_rate": 0.001,    # 0.1%
        "tax_rate": 0.0,             # 세금은 별도 계산 (양도소득세)
        "slippage_rate": 0.001,      # 0.1%
    },
}


# ══════════════════════════════════════
# WebSocket 갱신 주기 (초)
# ══════════════════════════════════════
WS_REFRESH_INTERVALS = {
    "portfolio": 180,       # 3분 (매매 주기 대비 충분)
    "market_index": 60,     # 1분
    "stock_detail": 30,     # 30초 (수동 매매 화면)
    "alerts": 0,            # 즉시 (이벤트 기반)
    "order_execution": 0,   # 즉시 (이벤트 기반)
}


# ══════════════════════════════════════
# 포트폴리오 제약조건
# ══════════════════════════════════════
PORTFOLIO_CONSTRAINTS = {
    "max_single_weight": 0.20,     # 종목당 최대 20%
    "max_sector_weight": 0.40,     # 섹터당 최대 40%
    "min_positions": 5,            # 최소 종목 수
    "max_us_weight_warning": 0.50, # 미국 자산 50% 초과 시 경고
}


# ══════════════════════════════════════
# 데이터 무결성 상수
# ══════════════════════════════════════
DATA_INTEGRITY = {
    "max_consecutive_missing_days": 3,  # 연속 결측 허용 한도 (영업일)
    "outlier_sigma_threshold": 3.0,     # 이상치 탐지 시그마 기준
    "kr_daily_limit_pct": 0.30,         # 한국 상하한가 (±30%)
    "us_circuit_breaker_l1": 0.07,      # 미국 서킷브레이커 1단계 (7%)
}
