"""
AQTS API 라우터 모듈

Phase 5: 인증, 포트폴리오, 주문, 프로필, 시장, 알림, 시스템 라우터
"""

from api.routes import alerts, auth, market, orders, portfolio, profile, system

__all__ = [
    "auth",
    "portfolio",
    "orders",
    "profile",
    "market",
    "alerts",
    "system",
]
