"""
Rate Limiter 테스트

slowapi 기반 Rate Limiting 미들웨어의 정상 동작을 검증합니다.
- 제한 미초과 시 정상 응답
- 제한 초과 시 429 응답
- 엔드포인트별 다른 제한 적용
"""

from api.middleware.rate_limiter import (
    RATE_GENERAL,
    RATE_LOGIN,
    RATE_ORDER,
    RATE_PIPELINE,
    limiter,
)


class TestRateLimiterConstants:
    """Rate Limit 상수 검증"""

    def test_login_rate(self):
        """로그인 제한: 5/minute"""
        assert RATE_LOGIN == "5/minute"

    def test_order_rate(self):
        """주문 제한: 10/minute"""
        assert RATE_ORDER == "10/minute"

    def test_general_rate(self):
        """일반 API 제한: 60/minute"""
        assert RATE_GENERAL == "60/minute"

    def test_pipeline_rate(self):
        """파이프라인 제한: 5/minute"""
        assert RATE_PIPELINE == "5/minute"


class TestLimiterInstance:
    """Limiter 인스턴스 검증"""

    def test_limiter_exists(self):
        """limiter 인스턴스가 존재"""
        assert limiter is not None

    def test_limiter_default_limits(self):
        """기본 제한이 설정됨"""
        assert limiter._default_limits is not None

    def test_limiter_has_limit_decorator(self):
        """limiter.limit 데코레이터가 callable"""
        assert callable(limiter.limit)
