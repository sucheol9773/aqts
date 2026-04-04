"""
백테스트 무결성 모듈 유닛테스트

Stage 3-A: Minimum Realism (편향 제거)

테스트 범위:
1. BiasChecker: 30+ 테스트
   - Point-in-time 컴플라이언스 (pass/fail)
   - Look-ahead bias 탐지 (empty/violations)
   - Survivorship bias 확인 (pass/missing tickers)

2. SlippageModel: spread cost, market impact, slippage application
   - BUY/SELL별 슬리피지 적용
   - Zero values 처리

3. FillModel: partial fill simulation, ADV cap, order splitting
   - Full fill (<10% ADV)
   - Partial fill (10-30% ADV)
   - Heavy partial (>30% ADV)
   - ADV cap 적용
   - Order splitting
"""

from datetime import datetime

import pandas as pd
import pytest

from config.constants import Country, Market, OrderSide
from core.backtest_engine.bias_checker import BiasChecker, BiasViolation
from core.backtest_engine.fill_model import FillModel
from core.order_executor.slippage import SlippageModel

# ══════════════════════════════════════════════════════════════════════════════
# BiasChecker Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestBiasCheckerInit:
    """BiasChecker 초기화 테스트"""

    def test_init(self):
        """BiasChecker 초기화"""
        checker = BiasChecker()
        assert checker.violations == []
        assert not checker.has_violations()

    def test_get_violations_empty(self):
        """초기 상태에서 violations 비어있음"""
        checker = BiasChecker()
        assert checker.get_violations() == []

    def test_get_violation_summary_empty(self):
        """초기 상태에서 summary"""
        checker = BiasChecker()
        summary = checker.get_violation_summary()
        assert summary["total_violations"] == 0
        assert summary["high_severity"] == 0


class TestPointInTimeCompliance:
    """Point-in-time 컴플라이언스 테스트"""

    def test_data_after_filing(self):
        """공시일 이후 데이터 사용 (컴플라이언스 O)"""
        checker = BiasChecker()
        data_date = datetime(2024, 1, 15)
        filing_date = datetime(2024, 1, 10)
        assert checker.check_point_in_time(data_date, filing_date) is True

    def test_data_same_filing_date(self):
        """공시일과 동일한 날짜 (컴플라이언스 O)"""
        checker = BiasChecker()
        date = datetime(2024, 1, 10)
        assert checker.check_point_in_time(date, date) is True

    def test_data_before_filing(self):
        """공시일 이전 데이터 사용 (컴플라이언스 X)"""
        checker = BiasChecker()
        data_date = datetime(2024, 1, 5)
        filing_date = datetime(2024, 1, 10)
        assert checker.check_point_in_time(data_date, filing_date) is False

    def test_data_far_before_filing(self):
        """공시일보다 훨씬 이전 데이터 (컴플라이언스 X)"""
        checker = BiasChecker()
        data_date = datetime(2024, 1, 1)
        filing_date = datetime(2024, 1, 31)
        assert checker.check_point_in_time(data_date, filing_date) is False

    def test_data_far_after_filing(self):
        """공시일보다 훨씬 이후 데이터 (컴플라이언스 O)"""
        checker = BiasChecker()
        data_date = datetime(2024, 2, 28)
        filing_date = datetime(2024, 1, 1)
        assert checker.check_point_in_time(data_date, filing_date) is True


class TestLookaheadBiasDetection:
    """Look-ahead bias 탐지 테스트"""

    def test_no_lookahead(self):
        """미래 데이터 없음"""
        checker = BiasChecker()
        reference_date = datetime(2024, 1, 15)
        records = [
            {"date": datetime(2024, 1, 10)},
            {"date": datetime(2024, 1, 12)},
            {"date": datetime(2024, 1, 15)},
        ]
        violations = checker.detect_lookahead(records, reference_date)
        assert len(violations) == 0

    def test_lookahead_one_record(self):
        """한 개의 미래 데이터"""
        checker = BiasChecker()
        reference_date = datetime(2024, 1, 15)
        records = [
            {"date": datetime(2024, 1, 10)},
            {"date": datetime(2024, 1, 20)},
            {"date": datetime(2024, 1, 12)},
        ]
        violations = checker.detect_lookahead(records, reference_date)
        assert len(violations) == 1
        assert violations[0].violation_type == "lookahead"

    def test_lookahead_multiple_records(self):
        """여러 개의 미래 데이터"""
        checker = BiasChecker()
        reference_date = datetime(2024, 1, 15)
        records = [
            {"date": datetime(2024, 1, 20)},
            {"date": datetime(2024, 1, 25)},
            {"date": datetime(2024, 1, 10)},
        ]
        violations = checker.detect_lookahead(records, reference_date)
        assert len(violations) == 2

    def test_lookahead_string_dates(self):
        """문자열 형식의 날짜"""
        checker = BiasChecker()
        reference_date = datetime(2024, 1, 15)
        records = [
            {"date": "2024-01-20"},
            {"date": "2024-01-10"},
        ]
        violations = checker.detect_lookahead(records, reference_date)
        assert len(violations) == 1

    def test_lookahead_no_date_field(self):
        """'date' 필드 없음"""
        checker = BiasChecker()
        reference_date = datetime(2024, 1, 15)
        records = [
            {"value": 100},
            {"value": 200},
        ]
        violations = checker.detect_lookahead(records, reference_date)
        assert len(violations) == 0

    def test_lookahead_mixed_date_formats(self):
        """혼합된 날짜 형식"""
        checker = BiasChecker()
        reference_date = datetime(2024, 1, 15)
        records = [
            {"date": "2024-01-20"},
            {"date": datetime(2024, 1, 25)},
            {"date": "2024-01-10"},
        ]
        violations = checker.detect_lookahead(records, reference_date)
        assert len(violations) == 2

    def test_lookahead_invalid_date(self):
        """유효하지 않은 날짜 형식"""
        checker = BiasChecker()
        reference_date = datetime(2024, 1, 15)
        records = [
            {"date": "invalid_date"},
            {"date": datetime(2024, 1, 20)},
        ]
        violations = checker.detect_lookahead(records, reference_date)
        assert len(violations) == 1

    def test_lookahead_violation_details(self):
        """Violation 상세 정보"""
        checker = BiasChecker()
        reference_date = datetime(2024, 1, 15)
        records = [
            {"date": datetime(2024, 1, 20)},
        ]
        violations = checker.detect_lookahead(records, reference_date)
        assert violations[0].severity == "high"
        assert "Future data" in violations[0].description


class TestSurvivorshipBiasCheck:
    """Survivorship bias 확인 테스트"""

    def test_all_delisted_tickers_present(self):
        """모든 상장폐지 종목이 universe에 존재"""
        checker = BiasChecker()
        universe_dates = ["A", "B", "C", "DELISTED1", "DELISTED2"]
        delisted_tickers = ["DELISTED1", "DELISTED2"]
        missing = checker.check_survivorship(universe_dates, delisted_tickers)
        assert missing == []

    def test_some_delisted_tickers_missing(self):
        """일부 상장폐지 종목이 universe에 없음"""
        checker = BiasChecker()
        universe_dates = ["A", "B", "C", "DELISTED1"]
        delisted_tickers = ["DELISTED1", "DELISTED2", "DELISTED3"]
        missing = checker.check_survivorship(universe_dates, delisted_tickers)
        assert set(missing) == {"DELISTED2", "DELISTED3"}

    def test_all_delisted_tickers_missing(self):
        """모든 상장폐지 종목이 universe에 없음"""
        checker = BiasChecker()
        universe_dates = ["A", "B", "C"]
        delisted_tickers = ["DELISTED1", "DELISTED2"]
        missing = checker.check_survivorship(universe_dates, delisted_tickers)
        assert set(missing) == {"DELISTED1", "DELISTED2"}

    def test_survivorship_with_series(self):
        """Series 형식의 universe"""
        checker = BiasChecker()
        universe_dates = pd.Series(["A", "B", "DELISTED1", "C"])
        delisted_tickers = ["DELISTED1", "DELISTED2"]
        missing = checker.check_survivorship(universe_dates, delisted_tickers)
        assert missing == ["DELISTED2"]

    def test_survivorship_with_dict(self):
        """Dict 형식의 universe"""
        checker = BiasChecker()
        universe_dates = {
            "2024-01-01": ["A", "B", "C"],
            "2024-01-02": ["A", "DELISTED1", "C"],
        }
        delisted_tickers = ["DELISTED1", "DELISTED2"]
        missing = checker.check_survivorship(universe_dates, delisted_tickers)
        assert missing == ["DELISTED2"]

    def test_survivorship_empty_delisted(self):
        """상장폐지 종목이 없음"""
        checker = BiasChecker()
        universe_dates = ["A", "B", "C"]
        delisted_tickers = []
        missing = checker.check_survivorship(universe_dates, delisted_tickers)
        assert missing == []

    def test_survivorship_empty_universe(self):
        """Universe가 비어있음"""
        checker = BiasChecker()
        universe_dates = []
        delisted_tickers = ["DELISTED1"]
        missing = checker.check_survivorship(universe_dates, delisted_tickers)
        assert missing == ["DELISTED1"]


class TestBiasCheckerViolationManagement:
    """Bias violation 관리 테스트"""

    def test_add_violation(self):
        """Violation 추가"""
        checker = BiasChecker()
        violation = BiasViolation(
            violation_type="lookahead",
            date=datetime(2024, 1, 1),
            description="Test violation",
        )
        checker.add_violation(violation)
        assert len(checker.violations) == 1

    def test_has_violations_true(self):
        """Violation 있음"""
        checker = BiasChecker()
        violation = BiasViolation(
            violation_type="lookahead",
            date=datetime(2024, 1, 1),
            description="Test",
        )
        checker.add_violation(violation)
        assert checker.has_violations()

    def test_has_violations_false(self):
        """Violation 없음"""
        checker = BiasChecker()
        assert not checker.has_violations()

    def test_clear_violations(self):
        """Violation 초기화"""
        checker = BiasChecker()
        violation = BiasViolation(
            violation_type="lookahead",
            date=datetime(2024, 1, 1),
            description="Test",
        )
        checker.add_violation(violation)
        checker.clear_violations()
        assert len(checker.violations) == 0

    def test_violation_summary_multiple(self):
        """여러 violation의 summary"""
        checker = BiasChecker()
        checker.add_violation(BiasViolation("lookahead", datetime(2024, 1, 1), "Test1"))
        checker.add_violation(BiasViolation("lookahead", datetime(2024, 1, 2), "Test2"))
        checker.add_violation(BiasViolation("survivorship", datetime(2024, 1, 3), "Test3"))

        summary = checker.get_violation_summary()
        assert summary["total_violations"] == 3
        assert summary["by_type"]["lookahead"] == 2
        assert summary["by_type"]["survivorship"] == 1


# ══════════════════════════════════════════════════════════════════════════════
# SlippageModel Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestSlippageModelInit:
    """SlippageModel 초기화 테스트"""

    def test_init_default_country(self):
        """기본 국가 설정"""
        model = SlippageModel()
        assert model.country == Country.KR

    def test_init_us_country(self):
        """미국 설정"""
        model = SlippageModel(country=Country.US)
        assert model.country == Country.US


class TestSpreadCostCalculation:
    """Spread cost 계산 테스트"""

    def test_spread_cost_default(self):
        """기본 spread cost"""
        model = SlippageModel(country=Country.KR)
        cost = model.calculate_spread_cost("005930", Market.KRX)
        # KRX large_cap: 1 bp → 0.5 bp half-spread → 0.00005
        assert 0.00004 < cost < 0.00006

    def test_spread_cost_custom(self):
        """커스텀 spread cost"""
        model = SlippageModel()
        cost = model.calculate_spread_cost("005930", Market.KRX, avg_spread=2.0)
        # 2.0 bp / 2 = 1.0 bp = 0.0001
        assert abs(cost - 0.0001) < 0.00001

    def test_spread_cost_zero(self):
        """Zero spread cost"""
        model = SlippageModel()
        cost = model.calculate_spread_cost("005930", Market.KRX, avg_spread=0.0)
        assert cost == 0.0

    def test_spread_cost_us_market(self):
        """미국 시장 spread cost"""
        model = SlippageModel(country=Country.US)
        cost = model.calculate_spread_cost("AAPL", Market.NASDAQ)
        # NASDAQ: 1 bp → 0.5 bp half-spread → 0.00005
        assert 0.00004 < cost < 0.00006


class TestMarketImpact:
    """Market impact 계산 테스트"""

    def test_market_impact_small_order(self):
        """소규모 주문 (impact 거의 없음)"""
        model = SlippageModel()
        impact = model.calculate_market_impact(100, 1000000, 50000)
        assert impact > 0
        assert impact < 100  # 매우 작은 impact

    def test_market_impact_large_order(self):
        """대규모 주문 (impact 있음)"""
        model = SlippageModel()
        impact = model.calculate_market_impact(50000, 100000, 50000)
        assert impact > 100  # 유의미한 impact

    def test_market_impact_zero_adv(self):
        """ADV가 0"""
        model = SlippageModel()
        impact = model.calculate_market_impact(100, 0, 50000)
        assert impact == 0.0

    def test_market_impact_zero_quantity(self):
        """주문량이 0"""
        model = SlippageModel()
        impact = model.calculate_market_impact(0, 100000, 50000)
        assert impact == 0.0

    def test_market_impact_zero_price(self):
        """가격이 0"""
        model = SlippageModel()
        impact = model.calculate_market_impact(100, 100000, 0)
        assert impact == 0.0

    def test_market_impact_proportional_to_sqrt(self):
        """Market impact는 sqrt(quantity/ADV)에 비례"""
        model = SlippageModel()
        impact1 = model.calculate_market_impact(10000, 100000, 50000)
        impact2 = model.calculate_market_impact(40000, 100000, 50000)
        # impact2/impact1 ≈ sqrt(4) = 2
        assert 1.8 < impact2 / impact1 < 2.2


class TestSlippageApplication:
    """Slippage 적용 테스트"""

    def test_slippage_buy_side(self):
        """BUY: 가격 상승"""
        model = SlippageModel()
        base_price = 50000
        spread_cost = 0.0001  # 0.01%
        impact_cost = 100
        adjusted_price = model.apply_slippage(base_price, OrderSide.BUY, spread_cost, impact_cost)
        # BUY: 가격 + spread + impact
        assert adjusted_price > base_price
        expected = base_price + (base_price * spread_cost) + impact_cost
        assert abs(adjusted_price - expected) < 0.01

    def test_slippage_sell_side(self):
        """SELL: 가격 하락"""
        model = SlippageModel()
        base_price = 50000
        spread_cost = 0.0001
        impact_cost = 100
        adjusted_price = model.apply_slippage(base_price, OrderSide.SELL, spread_cost, impact_cost)
        # SELL: 가격 - spread - impact
        assert adjusted_price < base_price

    def test_slippage_zero_cost(self):
        """비용이 0"""
        model = SlippageModel()
        base_price = 50000
        adjusted_price_buy = model.apply_slippage(base_price, OrderSide.BUY, 0.0, 0.0)
        adjusted_price_sell = model.apply_slippage(base_price, OrderSide.SELL, 0.0, 0.0)
        assert adjusted_price_buy == base_price
        assert adjusted_price_sell == base_price


# ══════════════════════════════════════════════════════════════════════════════
# FillModel Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestFillModelInit:
    """FillModel 초기화 테스트"""

    def test_init(self):
        """FillModel 초기화"""
        model = FillModel()
        assert model.LIGHT_ADV_PCT == 0.10
        assert model.MEDIUM_ADV_PCT == 0.30


class TestFillSimulation:
    """체결 시뮬레이션 테스트"""

    def test_full_fill_light_adv(self):
        """전량 체결 (<10% ADV)"""
        model = FillModel()
        result = model.simulate_fill(5000, 100000, 50000)
        assert result.filled_quantity == 5000
        assert result.unfilled_quantity == 0
        assert result.fill_ratio == 1.0

    def test_full_fill_boundary(self):
        """10% ADV 정확히"""
        model = FillModel()
        result = model.simulate_fill(10000, 100000, 50000)
        assert result.filled_quantity == 10000
        assert result.unfilled_quantity == 0
        assert result.fill_ratio == 1.0

    def test_partial_fill_medium_adv(self):
        """부분 체결 (10-30% ADV)"""
        model = FillModel()
        result = model.simulate_fill(15000, 100000, 50000)  # 15% ADV
        assert 0 < result.fill_ratio < 1.0
        assert result.filled_quantity < 15000
        assert result.unfilled_quantity > 0
        assert result.filled_quantity + result.unfilled_quantity == pytest.approx(15000)

    def test_partial_fill_at_30_pct(self):
        """30% ADV 정확히"""
        model = FillModel()
        result = model.simulate_fill(30000, 100000, 50000)  # 30% ADV
        assert 0.4 < result.fill_ratio < 0.6
        assert result.filled_quantity < 30000

    def test_heavy_partial_fill(self):
        """대량 부분 체결 (>30% ADV)"""
        model = FillModel()
        result = model.simulate_fill(50000, 100000, 50000)  # 50% ADV
        assert result.fill_ratio < 0.5
        assert result.filled_quantity < 50000
        assert result.unfilled_quantity > 0

    def test_very_large_order(self):
        """매우 큰 주문"""
        model = FillModel()
        result = model.simulate_fill(100000, 100000, 50000)  # 100% ADV
        assert result.fill_ratio < 0.3
        assert result.unfilled_quantity > result.filled_quantity

    def test_fill_result_properties(self):
        """FillResult 속성"""
        model = FillModel()
        result = model.simulate_fill(15000, 100000, 50000)
        assert hasattr(result, "filled_quantity")
        assert hasattr(result, "unfilled_quantity")
        assert hasattr(result, "fill_ratio")
        assert result.filled_quantity >= 0
        assert result.unfilled_quantity >= 0

    def test_zero_adv(self):
        """ADV가 0"""
        model = FillModel()
        result = model.simulate_fill(10000, 0, 50000)
        assert result.filled_quantity == 5000  # 50% 체결
        assert result.fill_ratio == 0.5


class TestADVCap:
    """ADV cap 적용 테스트"""

    def test_adv_cap_below_limit(self):
        """제한 이하의 주문"""
        model = FillModel()
        capped = model.apply_adv_cap(2000, 100000, max_adv_pct=0.05)
        assert capped == 2000  # 5000 미만이므로 제한 없음

    def test_adv_cap_at_limit(self):
        """정확히 제한선의 주문"""
        model = FillModel()
        capped = model.apply_adv_cap(5000, 100000, max_adv_pct=0.05)
        assert capped == 5000

    def test_adv_cap_exceeds_limit(self):
        """제한을 초과하는 주문"""
        model = FillModel()
        capped = model.apply_adv_cap(10000, 100000, max_adv_pct=0.05)
        assert capped == 5000

    def test_adv_cap_zero_adv(self):
        """ADV가 0"""
        model = FillModel()
        capped = model.apply_adv_cap(1000, 0, max_adv_pct=0.05)
        assert capped == 1000

    def test_adv_cap_custom_percentage(self):
        """커스텀 ADV 퍼센트"""
        model = FillModel()
        capped = model.apply_adv_cap(15000, 100000, max_adv_pct=0.10)
        assert capped == 10000  # 10% ADV = 10000


class TestOrderSplitting:
    """대량 주문 분할 테스트"""

    def test_split_small_order(self):
        """분할 불필요한 소규모 주문"""
        model = FillModel()
        splits = model.split_large_order(2000, 100000, max_adv_pct=0.05)
        assert len(splits) == 1
        assert splits[0] == 2000

    def test_split_large_order(self):
        """분할 필요한 대규모 주문"""
        model = FillModel()
        splits = model.split_large_order(15000, 100000, max_adv_pct=0.05)
        assert len(splits) > 1
        assert sum(splits) == pytest.approx(15000)

    def test_split_order_respects_cap(self):
        """각 분할 주문이 ADV cap 준수"""
        model = FillModel()
        max_per_order = 100000 * 0.05  # 5000
        splits = model.split_large_order(15000, 100000, max_adv_pct=0.05)
        for split in splits:
            assert split <= max_per_order

    def test_split_very_large_order(self):
        """매우 큰 주문"""
        model = FillModel()
        splits = model.split_large_order(100000, 100000, max_adv_pct=0.05)
        assert len(splits) == 20  # 100000 / 5000 = 20

    def test_split_zero_adv(self):
        """ADV가 0"""
        model = FillModel()
        splits = model.split_large_order(10000, 0, max_adv_pct=0.05)
        assert len(splits) == 3  # 3개로 분할
        assert sum(splits) == pytest.approx(10000)

    def test_split_exact_multiple(self):
        """ADV cap의 정확한 배수"""
        model = FillModel()
        splits = model.split_large_order(10000, 100000, max_adv_pct=0.05)
        assert len(splits) == 2
        assert sum(splits) == pytest.approx(10000)


class TestFillCostCalculation:
    """체결 비용 계산 테스트 (advanced)"""

    def test_fill_cost_small_order(self):
        """소규모 주문 비용"""
        model = FillModel()
        result = model.calculate_fill_cost(1000, 100000, 50000)
        assert result["fill_result"].fill_ratio == 1.0
        assert result["avg_fill_price"] >= 50000
        assert result["cost_pct"] >= 0

    def test_fill_cost_large_order(self):
        """대규모 주문 비용"""
        model = FillModel()
        result = model.calculate_fill_cost(50000, 100000, 50000)
        assert result["fill_result"].fill_ratio < 1.0
        # 부분 체결이므로 추가 비용 발생
        assert result["cost_pct"] > 0

    def test_fill_cost_return_structure(self):
        """반환 구조"""
        model = FillModel()
        result = model.calculate_fill_cost(10000, 100000, 50000)
        assert "fill_result" in result
        assert "avg_fill_price" in result
        assert "total_cost" in result
        assert "cost_pct" in result

    def test_fill_cost_zero_adv(self):
        """ADV가 0일 때 비용"""
        model = FillModel()
        result = model.calculate_fill_cost(1000, 0, 50000)
        assert result["cost_pct"] >= 0


# ══════════════════════════════════════════════════════════════════════════════
# Integration Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestBiasCheckerIntegration:
    """BiasChecker 통합 테스트"""

    def test_comprehensive_violation_tracking(self):
        """종합적인 violation 추적"""
        checker = BiasChecker()

        # Point-in-time 위반
        assert not checker.check_point_in_time(datetime(2024, 1, 5), datetime(2024, 1, 10))

        # Look-ahead 위반
        records = [{"date": datetime(2024, 1, 20)}]
        violations = checker.detect_lookahead(records, datetime(2024, 1, 15))
        assert len(violations) > 0

        # Survivorship 위반
        missing = checker.check_survivorship(["A", "B"], ["C"])
        assert "C" in missing


class TestSlippageAndFillIntegration:
    """Slippage와 Fill 통합 테스트"""

    def test_complete_order_execution(self):
        """완전한 주문 실행 시뮬레이션"""
        slippage_model = SlippageModel(country=Country.KR)
        fill_model = FillModel()

        # 주문 기본 정보
        base_price = 50000
        order_quantity = 10000
        adv = 100000

        # 1. 체결 시뮬레이션
        fill_result = fill_model.simulate_fill(order_quantity, adv, base_price)
        filled_qty = fill_result.filled_quantity

        # 2. Slippage 계산
        spread_cost = slippage_model.calculate_spread_cost("TEST", Market.KRX)
        impact_cost = slippage_model.calculate_market_impact(filled_qty, adv, base_price)

        # 3. 최종 체결가 계산
        final_price = slippage_model.apply_slippage(base_price, OrderSide.BUY, spread_cost, impact_cost)

        # 검증
        assert filled_qty <= order_quantity
        assert final_price >= base_price  # BUY는 higher price


# ══════════════════════════════════════════════════════════════════════════════
# Edge Cases and Boundary Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestEdgeCasesAndBoundaries:
    """엣지 케이스 및 경계 테스트"""

    def test_bias_checker_extreme_dates(self):
        """극단적인 날짜"""
        checker = BiasChecker()
        old_date = datetime(1900, 1, 1)
        new_date = datetime(2100, 1, 1)
        assert not checker.check_point_in_time(old_date, new_date)
        assert checker.check_point_in_time(new_date, old_date)

    def test_slippage_extreme_prices(self):
        """극단적인 가격"""
        model = SlippageModel()

        # 매우 낮은 가격
        impact_low = model.calculate_market_impact(100, 10000, 0.01)
        assert impact_low >= 0

        # 매우 높은 가격
        impact_high = model.calculate_market_impact(100, 10000, 1000000)
        assert impact_high >= 0

    def test_fill_model_100pct_adv(self):
        """정확히 100% ADV"""
        model = FillModel()
        result = model.simulate_fill(100000, 100000, 50000)
        assert result.fill_ratio > 0
        assert result.fill_ratio < 1.0

    def test_fill_model_999pct_adv(self):
        """999% ADV (매우 큰 주문)"""
        model = FillModel()
        result = model.simulate_fill(999000, 100000, 50000)
        assert result.fill_ratio >= 0.1  # 최소 10%

    def test_surviving_zero_delisted(self):
        """상장폐지 종목이 정말 많을 때"""
        checker = BiasChecker()
        universe = ["A"]
        delisted = ["D" + str(i) for i in range(100)]
        missing = checker.check_survivorship(universe, delisted)
        assert len(missing) == 100
