"""
Stage 5 테스트: 자본금 & 재조정 (Capital & Reconciliation)

9개의 보호 계층 검증:
  1. CapitalBudget - 전략별 자본금 할당
  2. AssetClassLimiter - 자산 클래스별 한도
  3. ReconciliationEngine - 브로커 재조정
  4. DailyOrderLimiter - 일일 주문 건수
  5. StaleQuoteBlocker - 지연 호가 차단
  6. AIDelayFallback - AI 신선도 검증
  7. APIFailureSafeMode - API 실패 안전 모드
  8. CashFloorGuard - 현금 플로어 가드
  9. 통합 시나리오 테스트
"""

from datetime import datetime, timedelta
import pytest

from core.capital_budget import CapitalBudget, AssetClassLimiter
from core.reconciliation import ReconciliationEngine, ReconciliationResult
from core.capital_protection import (
    DailyOrderLimiter,
    StaleQuoteBlocker,
    AIDelayFallback,
    APIFailureSafeMode,
    CashFloorGuard,
)


# ══════════════════════════════════════════════════════════════
# CapitalBudget 테스트
# ══════════════════════════════════════════════════════════════

class TestCapitalBudgetBasics:
    """CapitalBudget 기본 기능 테스트"""

    def test_creation_default_allocations(self):
        """기본 할당으로 생성"""
        budget = CapitalBudget(total_capital=1_000_000)
        assert budget.total_capital == 1_000_000
        assert len(budget.strategy_allocations) == 4
        # 균등 할당 확인
        for alloc in budget.strategy_allocations.values():
            assert abs(alloc - 0.25) < 0.001

    def test_creation_custom_allocations(self):
        """사용자 정의 할당으로 생성"""
        allocations = {"TREND": 0.30, "MEAN_REV": 0.20, "FACTOR": 0.25, "RISK_PARITY": 0.25}
        budget = CapitalBudget(total_capital=1_000_000, strategy_allocations=allocations)
        assert budget.strategy_allocations == allocations

    def test_creation_zero_capital_fails(self):
        """자본금 0은 실패"""
        with pytest.raises(ValueError):
            CapitalBudget(total_capital=0)

    def test_creation_negative_capital_fails(self):
        """음수 자본금은 실패"""
        with pytest.raises(ValueError):
            CapitalBudget(total_capital=-1000)

    def test_creation_allocation_sum_not_one_fails(self):
        """할당 합이 1.0이 아니면 실패"""
        allocations = {"TREND": 0.3, "MEAN_REV": 0.3, "FACTOR": 0.3}  # sum = 0.9
        with pytest.raises(ValueError):
            CapitalBudget(total_capital=1_000_000, strategy_allocations=allocations)


class TestCapitalBudgetAllocation:
    """자본금 할당 테스트"""

    @pytest.fixture
    def budget(self):
        allocations = {"TREND": 0.50, "MEAN_REV": 0.30, "FACTOR": 0.20}
        return CapitalBudget(
            total_capital=1_000_000,
            strategy_allocations=allocations
        )

    def test_get_budget_trend(self, budget):
        """TREND 할당 확인"""
        assert budget.get_budget("TREND") == 500_000

    def test_get_budget_mean_rev(self, budget):
        """MEAN_REV 할당 확인"""
        assert budget.get_budget("MEAN_REV") == 300_000

    def test_get_budget_factor(self, budget):
        """FACTOR 할당 확인"""
        assert budget.get_budget("FACTOR") == 200_000

    def test_get_budget_unknown_strategy(self, budget):
        """알려지지 않은 전략은 오류"""
        with pytest.raises(KeyError):
            budget.get_budget("UNKNOWN")


class TestCapitalBudgetChecking:
    """예산 체크 테스트"""

    @pytest.fixture
    def budget(self):
        allocations = {"TREND": 0.50, "MEAN_REV": 0.50}
        return CapitalBudget(
            total_capital=1_000_000,
            strategy_allocations=allocations
        )

    def test_check_budget_within_limit(self, budget):
        """예산 내 요청은 허용"""
        assert budget.check_budget("TREND", 100_000) is True

    def test_check_budget_exact_limit(self, budget):
        """정확히 할당량과 같으면 허용"""
        assert budget.check_budget("TREND", 500_000) is True

    def test_check_budget_exceeds_limit(self, budget):
        """예산 초과는 거부"""
        assert budget.check_budget("TREND", 600_000) is False

    def test_check_budget_zero(self, budget):
        """0은 항상 허용"""
        assert budget.check_budget("TREND", 0) is True

    def test_check_budget_negative_fails(self, budget):
        """음수는 오류"""
        with pytest.raises(ValueError):
            budget.check_budget("TREND", -1000)

    def test_check_budget_unknown_strategy(self, budget):
        """알려지지 않은 전략은 오류"""
        with pytest.raises(KeyError):
            budget.check_budget("UNKNOWN", 1000)


class TestCapitalBudgetRecordUsage:
    """사용 기록 테스트"""

    @pytest.fixture
    def budget(self):
        allocations = {"TREND": 0.50, "MEAN_REV": 0.50}
        return CapitalBudget(
            total_capital=1_000_000,
            strategy_allocations=allocations
        )

    def test_record_usage_within_budget(self, budget):
        """예산 내 사용 기록"""
        remaining = budget.record_usage("TREND", 100_000)
        assert remaining == 400_000

    def test_record_usage_multiple(self, budget):
        """여러 번 사용 기록"""
        budget.record_usage("TREND", 100_000)
        budget.record_usage("TREND", 150_000)
        remaining = budget.record_usage("TREND", 50_000)
        assert remaining == 200_000

    def test_record_usage_exceeds_budget_fails(self, budget):
        """예산 초과 기록은 실패"""
        budget.record_usage("TREND", 400_000)
        with pytest.raises(ValueError):
            budget.record_usage("TREND", 150_000)

    def test_record_usage_negative_fails(self, budget):
        """음수는 오류"""
        with pytest.raises(ValueError):
            budget.record_usage("TREND", -1000)

    def test_record_usage_unknown_strategy(self, budget):
        """알려지지 않은 전략은 오류"""
        with pytest.raises(KeyError):
            budget.record_usage("UNKNOWN", 1000)


class TestCapitalBudgetRemaining:
    """남은 예산 테스트"""

    @pytest.fixture
    def budget(self):
        allocations = {"TREND": 0.50, "MEAN_REV": 0.50}
        return CapitalBudget(
            total_capital=1_000_000,
            strategy_allocations=allocations
        )

    def test_get_remaining_full(self, budget):
        """사용 전 남은 예산 = 할당"""
        assert budget.get_remaining("TREND") == 500_000

    def test_get_remaining_after_usage(self, budget):
        """사용 후 남은 예산"""
        budget.record_usage("TREND", 300_000)
        assert budget.get_remaining("TREND") == 200_000

    def test_get_remaining_zero(self, budget):
        """모두 사용했으면 0"""
        budget.record_usage("TREND", 500_000)
        assert budget.get_remaining("TREND") == 0


class TestCapitalBudgetReset:
    """리셋 테스트"""

    @pytest.fixture
    def budget(self):
        allocations = {"TREND": 0.50, "MEAN_REV": 0.50}
        return CapitalBudget(
            total_capital=1_000_000,
            strategy_allocations=allocations
        )

    def test_reset_daily(self, budget):
        """일일 리셋"""
        budget.record_usage("TREND", 300_000)
        assert budget.get_remaining("TREND") == 200_000

        budget.reset_daily()
        assert budget.get_remaining("TREND") == 500_000

    def test_reset_daily_all_strategies(self, budget):
        """모든 전략 리셋"""
        budget.record_usage("TREND", 300_000)
        budget.record_usage("MEAN_REV", 400_000)

        budget.reset_daily()
        assert budget.get_remaining("TREND") == 500_000
        assert budget.get_remaining("MEAN_REV") == 500_000


# ══════════════════════════════════════════════════════════════
# AssetClassLimiter 테스트
# ══════════════════════════════════════════════════════════════

class TestAssetClassLimiter:
    """자산 클래스 한도 테스트"""

    @pytest.fixture
    def limiter(self):
        limits = {"KR_EQUITY": 0.60, "US_EQUITY": 0.40}
        return AssetClassLimiter(limits=limits)

    def test_creation_custom_limits(self, limiter):
        """사용자 정의 한도"""
        assert limiter.limits["KR_EQUITY"] == 0.60
        assert limiter.limits["US_EQUITY"] == 0.40

    def test_creation_default_limits(self):
        """기본 한도"""
        limiter = AssetClassLimiter()
        assert "KR_EQUITY" in limiter.limits
        assert "US_EQUITY" in limiter.limits

    def test_creation_invalid_limit_fails(self):
        """0~1 범위 벗어나면 실패"""
        with pytest.raises(ValueError):
            AssetClassLimiter(limits={"KR_EQUITY": 1.5})

    def test_check_limit_within(self, limiter):
        """한도 내"""
        assert limiter.check_limit("KR_EQUITY", 0.35, 0.20) is True

    def test_check_limit_exact(self, limiter):
        """정확히 한도와 같음"""
        assert limiter.check_limit("KR_EQUITY", 0.30, 0.30) is True

    def test_check_limit_exceeds(self, limiter):
        """한도 초과"""
        assert limiter.check_limit("KR_EQUITY", 0.50, 0.20) is False

    def test_check_limit_negative_current_fails(self, limiter):
        """음수 비중은 오류"""
        with pytest.raises(ValueError):
            limiter.check_limit("KR_EQUITY", -0.10, 0.20)

    def test_get_available_capacity(self, limiter):
        """가용 비중"""
        available = limiter.get_available("KR_EQUITY", 0.40)
        assert abs(available - 0.20) < 0.001  # floating point tolerance

    def test_get_available_full(self, limiter):
        """비중 0일 때 전체 가능"""
        available = limiter.get_available("KR_EQUITY", 0.0)
        assert available == 0.60

    def test_get_available_exhausted(self, limiter):
        """비중이 한도와 같을 때"""
        available = limiter.get_available("KR_EQUITY", 0.60)
        assert available == 0.0


# ══════════════════════════════════════════════════════════════
# ReconciliationEngine 테스트
# ══════════════════════════════════════════════════════════════

class TestReconciliationEngine:
    """재조정 엔진 테스트"""

    @pytest.fixture
    def engine(self):
        return ReconciliationEngine()

    def test_reconcile_matched(self, engine):
        """포지션 일치"""
        broker = {"005930": 100.0, "000660": 50.0}
        internal = {"005930": 100.0, "000660": 50.0}

        result = engine.reconcile(broker, internal)

        assert result.matched is True
        assert len(result.mismatches) == 0
        assert result.broker_total == 150.0
        assert result.internal_total == 150.0

    def test_reconcile_mismatch_qty(self, engine):
        """수량 불일치"""
        broker = {"005930": 100.0, "000660": 50.0}
        internal = {"005930": 90.0, "000660": 50.0}

        result = engine.reconcile(broker, internal)

        assert result.matched is False
        assert len(result.mismatches) == 1
        assert result.mismatches[0]["ticker"] == "005930"
        assert result.mismatches[0]["broker_qty"] == 100.0
        assert result.mismatches[0]["internal_qty"] == 90.0
        assert result.mismatches[0]["difference"] == 10.0

    def test_reconcile_broker_has_extra(self, engine):
        """브로커에 추가 종목"""
        broker = {"005930": 100.0, "000660": 50.0}
        internal = {"005930": 100.0}

        result = engine.reconcile(broker, internal)

        assert result.matched is False
        assert len(result.mismatches) == 1
        assert result.mismatches[0]["ticker"] == "000660"

    def test_reconcile_internal_has_extra(self, engine):
        """내부에 추가 종목"""
        broker = {"005930": 100.0}
        internal = {"005930": 100.0, "000660": 50.0}

        result = engine.reconcile(broker, internal)

        assert result.matched is False
        assert len(result.mismatches) == 1
        assert result.mismatches[0]["ticker"] == "000660"

    def test_reconcile_empty_positions(self, engine):
        """빈 포지션"""
        result = engine.reconcile({}, {})

        assert result.matched is True
        assert len(result.mismatches) == 0
        assert result.broker_total == 0.0
        assert result.internal_total == 0.0

    def test_reconcile_invalid_broker_type(self, engine):
        """브로커 포지션이 dict가 아님"""
        with pytest.raises(TypeError):
            engine.reconcile("not_a_dict", {})

    def test_reconcile_invalid_internal_type(self, engine):
        """내부 포지션이 dict가 아님"""
        with pytest.raises(TypeError):
            engine.reconcile({}, "not_a_dict")

    def test_reconcile_balance_matched(self, engine):
        """잔액 일치"""
        assert engine.reconcile_balance(1000.0, 1000.0) is True

    def test_reconcile_balance_within_tolerance(self, engine):
        """잔액 허용 오차 내"""
        assert engine.reconcile_balance(1000.0, 1000.005, tolerance=0.01) is True

    def test_reconcile_balance_exceeds_tolerance(self, engine):
        """잔액 허용 오차 초과"""
        assert engine.reconcile_balance(1000.0, 1000.02, tolerance=0.01) is False

    def test_reconcile_balance_negative_fails(self, engine):
        """음수 잔액은 오류"""
        with pytest.raises(ValueError):
            engine.reconcile_balance(-1000.0, 1000.0)

    def test_get_mismatches_no_result(self, engine):
        """결과 없음"""
        assert engine.get_mismatches() == []

    def test_get_mismatches_after_reconcile(self, engine):
        """재조정 후 불일치 조회"""
        broker = {"005930": 100.0}
        internal = {"005930": 90.0}

        engine.reconcile(broker, internal)
        mismatches = engine.get_mismatches()

        assert len(mismatches) == 1
        assert mismatches[0]["difference"] == 10.0


# ══════════════════════════════════════════════════════════════
# DailyOrderLimiter 테스트
# ══════════════════════════════════════════════════════════════

class TestDailyOrderLimiter:
    """일일 주문 제한 테스트"""

    def test_creation_default(self):
        """기본 제한 생성"""
        limiter = DailyOrderLimiter()
        assert limiter.max_orders == 50

    def test_creation_custom(self):
        """사용자 정의 제한"""
        limiter = DailyOrderLimiter(max_orders=100)
        assert limiter.max_orders == 100

    def test_creation_zero_fails(self):
        """0 제한은 실패"""
        with pytest.raises(ValueError):
            DailyOrderLimiter(max_orders=0)

    def test_can_place_order_initially(self):
        """초기에는 주문 가능"""
        limiter = DailyOrderLimiter(max_orders=50)
        assert limiter.can_place_order() is True

    def test_record_order(self):
        """주문 기록"""
        limiter = DailyOrderLimiter(max_orders=50)
        remaining = limiter.record_order()
        assert remaining == 49
        assert limiter.get_count() == 1

    def test_record_order_multiple(self):
        """여러 주문 기록"""
        limiter = DailyOrderLimiter(max_orders=50)
        for _ in range(5):
            limiter.record_order()
        assert limiter.get_count() == 5

    def test_record_order_at_limit(self):
        """한도 도달"""
        limiter = DailyOrderLimiter(max_orders=3)
        limiter.record_order()
        limiter.record_order()
        limiter.record_order()

        assert limiter.can_place_order() is False
        with pytest.raises(RuntimeError):
            limiter.record_order()

    def test_reset_daily(self):
        """일일 리셋"""
        limiter = DailyOrderLimiter(max_orders=50)
        limiter.record_order()
        limiter.record_order()
        assert limiter.get_count() == 2

        limiter.reset_daily()
        assert limiter.get_count() == 0
        assert limiter.can_place_order() is True


# ══════════════════════════════════════════════════════════════
# StaleQuoteBlocker 테스트
# ══════════════════════════════════════════════════════════════

class TestStaleQuoteBlocker:
    """지연 호가 차단 테스트"""

    def test_creation_default(self):
        """기본 설정"""
        blocker = StaleQuoteBlocker()
        assert blocker.max_stale_seconds == 30

    def test_creation_custom(self):
        """사용자 정의"""
        blocker = StaleQuoteBlocker(max_stale_seconds=60)
        assert blocker.max_stale_seconds == 60

    def test_creation_zero_fails(self):
        """0은 실패"""
        with pytest.raises(ValueError):
            StaleQuoteBlocker(max_stale_seconds=0)

    def test_is_stale_fresh_quote(self):
        """신선한 호가"""
        blocker = StaleQuoteBlocker(max_stale_seconds=30)
        now = datetime.utcnow()
        recent = now - timedelta(seconds=10)

        assert blocker.is_stale(recent) is False

    def test_is_stale_old_quote(self):
        """오래된 호가"""
        blocker = StaleQuoteBlocker(max_stale_seconds=30)
        now = datetime.utcnow()
        old = now - timedelta(seconds=45)

        assert blocker.is_stale(old) is True

    def test_is_stale_boundary(self):
        """경계값"""
        blocker = StaleQuoteBlocker(max_stale_seconds=30)
        now = datetime.utcnow()
        boundary = now - timedelta(seconds=29)

        # 경계보다 최근이면 stale이 아님
        assert blocker.is_stale(boundary) is False

    def test_is_stale_invalid_type(self):
        """datetime이 아니면 오류"""
        blocker = StaleQuoteBlocker()
        with pytest.raises(TypeError):
            blocker.is_stale("2024-01-01")

    def test_validate_quote_fresh(self):
        """신선한 호가 검증 통과"""
        blocker = StaleQuoteBlocker(max_stale_seconds=30)
        now = datetime.utcnow()
        recent = now - timedelta(seconds=10)

        # 예외 발생 안 함
        blocker.validate_quote(recent)

    def test_validate_quote_stale(self):
        """오래된 호가 검증 실패"""
        blocker = StaleQuoteBlocker(max_stale_seconds=30)
        now = datetime.utcnow()
        old = now - timedelta(seconds=45)

        with pytest.raises(ValueError):
            blocker.validate_quote(old)


# ══════════════════════════════════════════════════════════════
# AIDelayFallback 테스트
# ══════════════════════════════════════════════════════════════

class TestAIDelayFallback:
    """AI 데이터 신선도 테스트"""

    def test_creation_default(self):
        """기본 설정"""
        fallback = AIDelayFallback()
        assert fallback.max_delay_hours == 4

    def test_creation_custom(self):
        """사용자 정의"""
        fallback = AIDelayFallback(max_delay_hours=6)
        assert fallback.max_delay_hours == 6

    def test_creation_zero_fails(self):
        """0은 실패"""
        with pytest.raises(ValueError):
            AIDelayFallback(max_delay_hours=0)

    def test_check_freshness_fresh(self):
        """신선한 데이터"""
        fallback = AIDelayFallback(max_delay_hours=4)
        now = datetime.utcnow()
        recent = now - timedelta(hours=2)

        assert fallback.check_freshness(recent) is True

    def test_check_freshness_stale(self):
        """오래된 데이터"""
        fallback = AIDelayFallback(max_delay_hours=4)
        now = datetime.utcnow()
        old = now - timedelta(hours=6)

        assert fallback.check_freshness(old) is False

    def test_check_freshness_boundary(self):
        """경계값"""
        fallback = AIDelayFallback(max_delay_hours=4)
        now = datetime.utcnow()
        boundary = now - timedelta(hours=3)

        # 경계보다 최근이면 fresh
        assert fallback.check_freshness(boundary) is True

    def test_get_weight_multiplier_fresh(self):
        """신선한 데이터의 배수"""
        fallback = AIDelayFallback(max_delay_hours=4)
        now = datetime.utcnow()
        recent = now - timedelta(hours=2)

        assert fallback.get_weight_multiplier(recent) == 1.0

    def test_get_weight_multiplier_stale(self):
        """오래된 데이터의 배수"""
        fallback = AIDelayFallback(max_delay_hours=4)
        now = datetime.utcnow()
        old = now - timedelta(hours=6)

        assert fallback.get_weight_multiplier(old) == 0.0


# ══════════════════════════════════════════════════════════════
# APIFailureSafeMode 테스트
# ══════════════════════════════════════════════════════════════

class TestAPIFailureSafeMode:
    """API 실패 안전 모드 테스트"""

    def test_creation_default(self):
        """기본 설정"""
        safe_mode = APIFailureSafeMode()
        assert safe_mode.max_consecutive_failures == 3

    def test_creation_custom(self):
        """사용자 정의"""
        safe_mode = APIFailureSafeMode(max_consecutive_failures=5)
        assert safe_mode.max_consecutive_failures == 5

    def test_creation_zero_fails(self):
        """0은 실패"""
        with pytest.raises(ValueError):
            APIFailureSafeMode(max_consecutive_failures=0)

    def test_is_safe_mode_initially_false(self):
        """초기에는 안전 모드 비활성"""
        safe_mode = APIFailureSafeMode(max_consecutive_failures=3)
        assert safe_mode.is_safe_mode() is False

    def test_record_failure_below_threshold(self):
        """한도 이하 실패"""
        safe_mode = APIFailureSafeMode(max_consecutive_failures=3)
        result = safe_mode.record_failure()

        assert result is False
        assert safe_mode.get_failure_count() == 1

    def test_record_failure_at_threshold(self):
        """한도 도달"""
        safe_mode = APIFailureSafeMode(max_consecutive_failures=3)
        safe_mode.record_failure()
        safe_mode.record_failure()
        result = safe_mode.record_failure()

        assert result is True
        assert safe_mode.is_safe_mode() is True

    def test_record_success_resets(self):
        """성공 시 초기화"""
        safe_mode = APIFailureSafeMode(max_consecutive_failures=3)
        safe_mode.record_failure()
        safe_mode.record_failure()

        safe_mode.record_success()

        assert safe_mode.get_failure_count() == 0
        assert safe_mode.is_safe_mode() is False

    def test_failure_success_sequence(self):
        """실패-성공 시퀀스"""
        safe_mode = APIFailureSafeMode(max_consecutive_failures=3)

        # 2개 실패
        safe_mode.record_failure()
        safe_mode.record_failure()
        assert safe_mode.is_safe_mode() is False

        # 1개 성공으로 초기화
        safe_mode.record_success()
        assert safe_mode.get_failure_count() == 0


# ══════════════════════════════════════════════════════════════
# CashFloorGuard 테스트
# ══════════════════════════════════════════════════════════════

class TestCashFloorGuard:
    """현금 플로어 가드 테스트"""

    def test_creation_default(self):
        """기본 설정"""
        guard = CashFloorGuard()
        assert guard.min_cash_ratio == 0.10

    def test_creation_custom(self):
        """사용자 정의"""
        guard = CashFloorGuard(min_cash_ratio=0.15)
        assert guard.min_cash_ratio == 0.15

    def test_creation_boundary_zero(self):
        """0 비중은 허용"""
        guard = CashFloorGuard(min_cash_ratio=0.0)
        assert guard.min_cash_ratio == 0.0

    def test_creation_boundary_one(self):
        """1 비중은 허용"""
        guard = CashFloorGuard(min_cash_ratio=1.0)
        assert guard.min_cash_ratio == 1.0

    def test_creation_out_of_range_fails(self):
        """범위 벗어나면 실패"""
        with pytest.raises(ValueError):
            CashFloorGuard(min_cash_ratio=1.5)

    def test_check_floor_above(self):
        """플로어 위"""
        guard = CashFloorGuard(min_cash_ratio=0.10)
        # 현금 200K, 전체 1M → 20% > 10% ✓
        assert guard.check_floor(200_000, 1_000_000) is True

    def test_check_floor_at(self):
        """플로어 정확히"""
        guard = CashFloorGuard(min_cash_ratio=0.10)
        # 현금 100K, 전체 1M → 10% = 10% ✓
        assert guard.check_floor(100_000, 1_000_000) is True

    def test_check_floor_below(self):
        """플로어 아래"""
        guard = CashFloorGuard(min_cash_ratio=0.10)
        # 현금 50K, 전체 1M → 5% < 10% ✗
        assert guard.check_floor(50_000, 1_000_000) is False

    def test_check_floor_negative_cash_fails(self):
        """음수 현금은 오류"""
        guard = CashFloorGuard()
        with pytest.raises(ValueError):
            guard.check_floor(-10_000, 1_000_000)

    def test_check_floor_zero_portfolio_fails(self):
        """포트폴리오 가치 0은 오류"""
        guard = CashFloorGuard()
        with pytest.raises(ValueError):
            guard.check_floor(10_000, 0)

    def test_max_deployable_above_floor(self):
        """플로어 이상에서 배포 가능"""
        guard = CashFloorGuard(min_cash_ratio=0.10)
        # 현금 200K, 전체 1M, 최소 필요 = 100K
        # 배포 가능 = 200K - 100K = 100K
        deployable = guard.max_deployable(200_000, 1_000_000)
        assert deployable == 100_000

    def test_max_deployable_at_floor(self):
        """플로어에서 배포 불가"""
        guard = CashFloorGuard(min_cash_ratio=0.10)
        # 현금 100K, 전체 1M, 최소 필요 = 100K
        # 배포 가능 = 100K - 100K = 0
        deployable = guard.max_deployable(100_000, 1_000_000)
        assert deployable == 0

    def test_max_deployable_below_floor(self):
        """플로어 아래에서 배포 0"""
        guard = CashFloorGuard(min_cash_ratio=0.10)
        # 현금 50K, 전체 1M, 최소 필요 = 100K
        # 배포 가능 = 50K - 100K = -50K → clamp to 0
        deployable = guard.max_deployable(50_000, 1_000_000)
        assert deployable == 0


# ══════════════════════════════════════════════════════════════
# 통합 시나리오 테스트
# ══════════════════════════════════════════════════════════════

class TestIntegrationScenarios:
    """다층 보호 메커니즘이 함께 작동하는 시나리오"""

    def test_scenario_capital_budget_and_order_limiter(self):
        """자본 예산 + 주문 제한"""
        budget = CapitalBudget(
            total_capital=1_000_000,
            strategy_allocations={"TREND": 1.0}
        )
        limiter = DailyOrderLimiter(max_orders=50)

        # 첫 주문
        assert limiter.can_place_order() is True
        assert budget.check_budget("TREND", 100_000) is True

        limiter.record_order()
        budget.record_usage("TREND", 100_000)

        # 남은 예산 확인
        assert budget.get_remaining("TREND") == 900_000

    def test_scenario_reconciliation_and_cash_floor(self):
        """재조정 + 현금 플로어"""
        engine = ReconciliationEngine()
        guard = CashFloorGuard(min_cash_ratio=0.10)

        # 포지션 재조정
        result = engine.reconcile(
            {"005930": 100, "000660": 50},
            {"005930": 100, "000660": 50}
        )

        assert result.matched is True

        # 현금 플로어 확인 (포트폴리오 1M)
        assert guard.check_floor(150_000, 1_000_000) is True

    def test_scenario_stale_quote_and_ai_delay(self):
        """지연 호가 + AI 신선도"""
        quote_blocker = StaleQuoteBlocker(max_stale_seconds=30)
        ai_fallback = AIDelayFallback(max_delay_hours=4)

        now = datetime.utcnow()

        # 신선한 호가, 신선한 AI
        fresh_quote = now - timedelta(seconds=10)
        fresh_ai = now - timedelta(hours=1)

        assert quote_blocker.is_stale(fresh_quote) is False
        assert ai_fallback.check_freshness(fresh_ai) is True
        assert ai_fallback.get_weight_multiplier(fresh_ai) == 1.0

    def test_scenario_api_failure_cascade(self):
        """API 실패 연쇄"""
        api_safe = APIFailureSafeMode(max_consecutive_failures=3)
        order_limiter = DailyOrderLimiter(max_orders=50)

        # API 실패
        for _ in range(2):
            api_safe.record_failure()

        # 아직 주문 가능
        assert api_safe.is_safe_mode() is False
        assert order_limiter.can_place_order() is True

        # 1개 더 실패하면 안전 모드 활성화
        api_safe.record_failure()

        assert api_safe.is_safe_mode() is True
        # 주문 제한은 독립적으로 작동
        assert order_limiter.can_place_order() is True
