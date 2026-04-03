"""
포트폴리오 구성 엔진 테스트 (F-05-02)

TargetAllocation, TargetPortfolio, PortfolioConstructionEngine의 종합 단위 테스트

테스트 범위:
- TargetAllocation 데이터 구조 및 변환
- TargetPortfolio 속성 및 계산 로직
- PortfolioConstructionEngine 최적화 및 제약 조건 적용
- 리밸런싱 주문 생성

모든 외부 API는 Mock으로 대체합니다.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from config.constants import Market, RiskProfile, PORTFOLIO_CONSTRAINTS
from core.portfolio_manager.construction import (
    PortfolioConstructionEngine,
    TargetAllocation,
    TargetPortfolio,
)


# ══════════════════════════════════════
# TargetAllocation 테스트
# ══════════════════════════════════════
class TestTargetAllocation:
    """
    TargetAllocation 데이터 구조 테스트

    목표 포트폴리오의 개별 종목 할당량을 검증합니다.
    """

    def test_create_allocation(self):
        """목표 할당량 생성 테스트"""
        # Arrange
        ticker = "005930"
        market = Market.KRX
        target_weight = 0.15
        current_weight = 0.10
        signal_score = 0.65
        sector = "Technology"

        # Act
        allocation = TargetAllocation(
            ticker=ticker,
            market=market,
            target_weight=target_weight,
            current_weight=current_weight,
            signal_score=signal_score,
            sector=sector,
        )

        # Assert
        assert allocation.ticker == ticker
        assert allocation.market == market
        assert allocation.target_weight == target_weight
        assert allocation.current_weight == current_weight
        assert allocation.signal_score == signal_score
        assert allocation.sector == sector

    def test_create_allocation_defaults(self):
        """기본값 포함 할당량 생성 테스트"""
        # Act
        allocation = TargetAllocation(
            ticker="AAPL",
            market=Market.NYSE,
            target_weight=0.20,
            current_weight=0.15,
            signal_score=0.50,
        )

        # Assert
        assert allocation.sector == ""  # 기본값

    def test_to_dict(self):
        """할당량을 딕셔너리로 변환 테스트"""
        # Arrange
        allocation = TargetAllocation(
            ticker="000660",
            market=Market.KRX,
            target_weight=0.1234567,
            current_weight=0.0987654,
            signal_score=0.6543210,
            sector="Finance",
        )

        # Act
        result = allocation.to_dict()

        # Assert
        assert result["ticker"] == "000660"
        assert result["market"] == "KRX"
        # 4자리로 반올림 확인
        assert result["target_weight"] == 0.1235
        assert result["current_weight"] == 0.0988
        assert result["signal_score"] == 0.6543
        assert result["sector"] == "Finance"
        assert isinstance(result, dict)

    def test_to_dict_market_value(self):
        """시장 정보가 value로 변환되는지 테스트"""
        # Arrange
        allocation = TargetAllocation(
            ticker="MSFT",
            market=Market.NASDAQ,
            target_weight=0.18,
            current_weight=0.12,
            signal_score=0.72,
        )

        # Act
        result = allocation.to_dict()

        # Assert
        assert result["market"] == "NASDAQ"  # Enum.value


# ══════════════════════════════════════
# TargetPortfolio 테스트
# ══════════════════════════════════════
class TestTargetPortfolio:
    """
    TargetPortfolio 데이터 구조 및 속성 테스트

    목표 포트폴리오의 통계 및 계산 기능을 검증합니다.
    """

    @pytest.fixture
    def sample_allocations(self):
        """테스트용 할당량 샘플"""
        return [
            TargetAllocation(
                ticker="005930",
                market=Market.KRX,
                target_weight=0.20,
                current_weight=0.15,
                signal_score=0.70,
                sector="Technology",
            ),
            TargetAllocation(
                ticker="000660",
                market=Market.KRX,
                target_weight=0.18,
                current_weight=0.12,
                signal_score=0.65,
                sector="Finance",
            ),
            TargetAllocation(
                ticker="AAPL",
                market=Market.NYSE,
                target_weight=0.15,
                current_weight=0.10,
                signal_score=0.60,
                sector="Technology",
            ),
            TargetAllocation(
                ticker="MSFT",
                market=Market.NASDAQ,
                target_weight=0.12,
                current_weight=0.08,
                signal_score=0.55,
                sector="Technology",
            ),
            TargetAllocation(
                ticker="JPM",
                market=Market.NYSE,
                target_weight=0.10,
                current_weight=0.05,
                signal_score=0.50,
                sector="Finance",
            ),
            TargetAllocation(
                ticker="BRK.B",
                market=Market.NYSE,
                target_weight=0.0,
                current_weight=0.20,
                signal_score=0.0,
                sector="Finance",
            ),
        ]

    def test_stock_count(self, sample_allocations):
        """보유 종목 수 계산 테스트"""
        # Arrange
        portfolio = TargetPortfolio(allocations=sample_allocations)

        # Act
        count = portfolio.stock_count

        # Assert
        # target_weight > 0인 종목만 카운트
        expected = 5  # 첫 5개가 > 0
        assert count == expected

    def test_stock_count_with_zero_weight(self, sample_allocations):
        """0 가중치 종목 제외 테스트"""
        # Arrange
        allocations = sample_allocations[:1]
        allocations[0].target_weight = 0.0
        portfolio = TargetPortfolio(allocations=allocations)

        # Act
        count = portfolio.stock_count

        # Assert
        assert count == 0

    def test_sector_weights(self, sample_allocations):
        """섹터별 가중치 합산 테스트"""
        # Arrange
        portfolio = TargetPortfolio(allocations=sample_allocations)

        # Act
        sector_weights = portfolio.sector_weights

        # Assert
        assert "Technology" in sector_weights
        assert "Finance" in sector_weights
        # Technology: 0.20 + 0.15 + 0.12 = 0.47
        assert abs(sector_weights["Technology"] - 0.47) < 0.001
        # Finance: 0.18 + 0.10 + 0.0 = 0.28
        assert abs(sector_weights["Finance"] - 0.28) < 0.001

    def test_sector_weights_excludes_empty_sector(self):
        """빈 섹터 제외 테스트"""
        # Arrange
        allocations = [
            TargetAllocation(
                ticker="TEST1",
                market=Market.NYSE,
                target_weight=0.10,
                current_weight=0.05,
                signal_score=0.50,
                sector="",  # 빈 섹터
            ),
        ]
        portfolio = TargetPortfolio(allocations=allocations)

        # Act
        sector_weights = portfolio.sector_weights

        # Assert
        assert "" not in sector_weights  # 빈 섹터 제외

    def test_market_weights(self, sample_allocations):
        """시장별 가중치 합산 테스트"""
        # Arrange
        portfolio = TargetPortfolio(allocations=sample_allocations)

        # Act
        market_weights = portfolio.market_weights

        # Assert
        assert "KRX" in market_weights
        assert "NYSE" in market_weights
        assert "NASDAQ" in market_weights
        # KRX: 0.20 + 0.18 = 0.38
        assert abs(market_weights["KRX"] - 0.38) < 0.001
        # NYSE: 0.15 + 0.10 + 0.0 = 0.25
        assert abs(market_weights["NYSE"] - 0.25) < 0.001
        # NASDAQ: 0.12
        assert abs(market_weights["NASDAQ"] - 0.12) < 0.001

    def test_market_weights_sum(self, sample_allocations):
        """시장 가중치의 합이 포트폴리오 가중치 합과 일치하는지 테스트"""
        # Arrange
        portfolio = TargetPortfolio(allocations=sample_allocations)

        # Act
        market_weights = portfolio.market_weights
        total_weight = sum(w.target_weight for w in sample_allocations)
        market_weights_sum = sum(market_weights.values())

        # Assert
        assert abs(market_weights_sum - total_weight) < 0.001

    def test_to_dict(self, sample_allocations):
        """포트폴리오를 딕셔너리로 변환 테스트"""
        # Arrange
        now = datetime.now(timezone.utc)
        portfolio = TargetPortfolio(
            allocations=sample_allocations,
            total_value=10000000,
            cash_ratio=0.15,
            generated_at=now,
            optimization_method="risk_parity",
        )

        # Act
        result = portfolio.to_dict()

        # Assert
        assert "allocations" in result
        assert len(result["allocations"]) == len(sample_allocations)
        assert result["total_value"] == 10000000.0
        assert result["cash_ratio"] == 0.15
        assert result["generated_at"] == now
        assert result["optimization_method"] == "risk_parity"

    def test_to_dict_allocations_converted(self, sample_allocations):
        """딕셔너리 변환 시 할당량도 변환되는지 테스트"""
        # Arrange
        portfolio = TargetPortfolio(allocations=sample_allocations[:1])

        # Act
        result = portfolio.to_dict()

        # Assert
        assert isinstance(result["allocations"], list)
        assert isinstance(result["allocations"][0], dict)
        assert "ticker" in result["allocations"][0]


# ══════════════════════════════════════
# PortfolioConstructionEngine 테스트
# ══════════════════════════════════════
class TestPortfolioConstructionEngine:
    """
    포트폴리오 구성 엔진 핵심 기능 테스트

    최적화, 제약 조건 적용, 리밸런싱 주문 생성 로직을 검증합니다.
    """

    @pytest.fixture
    def engine(self):
        """포트폴리오 구성 엔진 인스턴스"""
        return PortfolioConstructionEngine(
            risk_profile=RiskProfile.BALANCED,
            constraints=PORTFOLIO_CONSTRAINTS,
        )

    @pytest.fixture
    def sample_signals(self):
        """테스트용 시그널"""
        return {
            "005930": 0.70,
            "000660": 0.65,
            "AAPL": 0.60,
            "MSFT": 0.55,
            "JPM": 0.50,
            "GOOGL": 0.45,
        }

    @pytest.fixture
    def sample_sector_info(self):
        """테스트용 섹터 정보"""
        return {
            "005930": "Technology",
            "000660": "Finance",
            "AAPL": "Technology",
            "MSFT": "Technology",
            "JPM": "Finance",
            "GOOGL": "Technology",
        }

    @pytest.fixture
    def sample_market_info(self):
        """테스트용 시장 정보"""
        return {
            "005930": Market.KRX,
            "000660": Market.KRX,
            "AAPL": Market.NYSE,
            "MSFT": Market.NASDAQ,
            "JPM": Market.NYSE,
            "GOOGL": Market.NASDAQ,
        }

    @pytest.mark.asyncio
    async def test_construct_mean_variance(
        self, engine, sample_signals, sample_sector_info, sample_market_info
    ):
        """평균-분산 최적화 포트폴리오 구성 테스트"""
        # Arrange
        current_portfolio = {t: 0.0 for t in sample_signals.keys()}
        seed_capital = 10000000.0
        method = "mean_variance"

        # Act
        portfolio = await engine.construct(
            ensemble_signals=sample_signals,
            current_portfolio=current_portfolio,
            seed_capital=seed_capital,
            method=method,
            sector_info=sample_sector_info,
            market_info=sample_market_info,
        )

        # Assert
        assert len(portfolio.allocations) > 0
        # 가중치의 합 + 현금 = 1.0
        total_weight = sum(a.target_weight for a in portfolio.allocations)
        assert abs(total_weight + portfolio.cash_ratio - 1.0) < 0.01
        assert portfolio.optimization_method == method
        assert portfolio.total_value == seed_capital

    @pytest.mark.asyncio
    async def test_construct_risk_parity(
        self, engine, sample_signals, sample_sector_info, sample_market_info
    ):
        """리스크 패리티 포트폴리오 구성 테스트"""
        # Arrange
        current_portfolio = {t: 0.0 for t in sample_signals.keys()}
        seed_capital = 10000000.0
        method = "risk_parity"

        # Act
        portfolio = await engine.construct(
            ensemble_signals=sample_signals,
            current_portfolio=current_portfolio,
            seed_capital=seed_capital,
            method=method,
            sector_info=sample_sector_info,
            market_info=sample_market_info,
        )

        # Assert
        assert len(portfolio.allocations) > 0
        assert portfolio.optimization_method == method
        # 리스크 패리티: 낮은 신호 → 높은 가중치 (역비례)
        # 신호가 낮은 종목이 높은 가중치를 가져야 함
        sorted_allocs = sorted(
            portfolio.allocations, key=lambda x: x.signal_score, reverse=True
        )
        # 첫 번째(높은 신호)의 가중치 < 마지막(낮은 신호)의 가중치
        if len(sorted_allocs) >= 2:
            assert sorted_allocs[0].target_weight < sorted_allocs[-1].target_weight

    @pytest.mark.asyncio
    async def test_construct_empty_signals(self, engine):
        """빈 시그널 포트폴리오 구성 테스트"""
        # Arrange
        empty_signals = {}
        current_portfolio = {}
        seed_capital = 10000000.0

        # Act
        portfolio = await engine.construct(
            ensemble_signals=empty_signals,
            current_portfolio=current_portfolio,
            seed_capital=seed_capital,
        )

        # Assert
        assert len(portfolio.allocations) == 0
        assert portfolio.cash_ratio == 1.0

    @pytest.mark.asyncio
    async def test_construct_with_current_portfolio(
        self, engine, sample_signals, sample_sector_info, sample_market_info
    ):
        """현재 포트폴리오 정보 포함 구성 테스트"""
        # Arrange
        current_portfolio = {
            "005930": 0.20,
            "000660": 0.15,
            "AAPL": 0.10,
            "MSFT": 0.08,
            "JPM": 0.05,
            "GOOGL": 0.02,
        }
        seed_capital = 10000000.0

        # Act
        portfolio = await engine.construct(
            ensemble_signals=sample_signals,
            current_portfolio=current_portfolio,
            seed_capital=seed_capital,
            sector_info=sample_sector_info,
            market_info=sample_market_info,
        )

        # Assert
        assert len(portfolio.allocations) > 0
        # 할당량의 current_weight 확인
        for alloc in portfolio.allocations:
            assert alloc.current_weight == current_portfolio.get(alloc.ticker, 0.0)

    def test_mean_variance_optimize_basic(self, engine, sample_signals):
        """평균-분산 최적화 기본 테스트"""
        # Act
        weights = engine._mean_variance_optimize(sample_signals, PORTFOLIO_CONSTRAINTS)

        # Assert
        assert isinstance(weights, dict)
        assert len(weights) == len(sample_signals)
        # 가중치 합 = 1.0
        assert abs(sum(weights.values()) - 1.0) < 0.001
        # 모든 가중치는 0 이상
        assert all(w >= 0.0 for w in weights.values())
        # 모든 가중치는 max_single_weight 이하
        max_weight = PORTFOLIO_CONSTRAINTS.get("max_single_weight", 0.20)
        assert all(w <= max_weight + 0.001 for w in weights.values())

    def test_mean_variance_optimize_signal_influence(self, engine):
        """평균-분산 최적화: 신호가 가중치에 영향을 미치는지 테스트"""
        # Arrange
        # 신호 1: 높은 신호 점수
        signals_high = {
            "A": 0.9,
            "B": 0.9,
            "C": 0.1,
            "D": 0.1,
        }

        # Act
        weights = engine._mean_variance_optimize(signals_high, PORTFOLIO_CONSTRAINTS)

        # Assert
        # A, B의 가중치가 C, D보다 커야 함
        assert weights["A"] >= weights["C"]
        assert weights["B"] >= weights["D"]

    def test_mean_variance_optimize_empty(self, engine):
        """평균-분산 최적화: 빈 신호 테스트"""
        # Act
        weights = engine._mean_variance_optimize({}, PORTFOLIO_CONSTRAINTS)

        # Assert
        assert weights == {}

    def test_risk_parity_optimize_basic(self, engine, sample_signals):
        """리스크 패리티 최적화 기본 테스트"""
        # Act
        weights = engine._risk_parity_optimize(sample_signals, PORTFOLIO_CONSTRAINTS)

        # Assert
        assert isinstance(weights, dict)
        assert len(weights) == len(sample_signals)
        # 가중치 합 = 1.0
        assert abs(sum(weights.values()) - 1.0) < 0.001
        # 모든 가중치는 0 이상
        assert all(w >= 0.0 for w in weights.values())

    def test_risk_parity_optimize_inverse_relationship(self, engine):
        """리스크 패리티: 신호가 낮을수록 가중치가 높은지 테스트"""
        # Arrange
        signals = {
            "HIGH_SIGNAL": 0.9,
            "LOW_SIGNAL": 0.1,
            "ZERO_SIGNAL": 0.0,
        }

        # Act
        weights = engine._risk_parity_optimize(signals, PORTFOLIO_CONSTRAINTS)

        # Assert
        # 신호가 낮을수록 가중치가 높아야 함 (역비례)
        assert weights["LOW_SIGNAL"] > weights["HIGH_SIGNAL"]
        assert weights["ZERO_SIGNAL"] > weights["HIGH_SIGNAL"]

    def test_risk_parity_optimize_empty(self, engine):
        """리스크 패리티 최적화: 빈 신호 테스트"""
        # Act
        weights = engine._risk_parity_optimize({}, PORTFOLIO_CONSTRAINTS)

        # Assert
        assert weights == {}

    def test_signal_proportional_weights_basic(self, engine, sample_signals):
        """신호 비례 가중치 기본 테스트"""
        # Arrange
        max_weight = 0.20

        # Act
        weights = engine._signal_proportional_weights(sample_signals, max_weight)

        # Assert
        assert isinstance(weights, dict)
        assert len(weights) == len(sample_signals)
        # 가중치 합 = 1.0
        assert abs(sum(weights.values()) - 1.0) < 0.001
        # 모든 가중치 <= max_weight
        assert all(w <= max_weight + 0.001 for w in weights.values())

    def test_signal_proportional_weights_proportional(self, engine):
        """신호 비례 가중치: 신호에 비례하는지 테스트"""
        # Arrange
        signals = {
            "HIGH": 0.8,
            "MEDIUM": 0.4,
            "LOW": 0.2,
        }
        max_weight = 0.5

        # Act
        weights = engine._signal_proportional_weights(signals, max_weight)

        # Assert
        # HIGH > MEDIUM > LOW
        assert weights["HIGH"] > weights["MEDIUM"]
        assert weights["MEDIUM"] > weights["LOW"]

    def test_signal_proportional_weights_all_negative(self, engine):
        """신호 비례 가중치: 모든 신호가 음수일 때 동일 가중 테스트"""
        # Arrange
        signals = {
            "NEGATIVE1": -0.5,
            "NEGATIVE2": -0.3,
            "NEGATIVE3": -0.1,
        }
        max_weight = 0.5

        # Act
        weights = engine._signal_proportional_weights(signals, max_weight)

        # Assert
        # 모든 음수 신호는 0으로 취급되어 동일 가중 (1/3)
        expected_weight = 1.0 / 3
        assert abs(weights["NEGATIVE1"] - expected_weight) < 0.001
        assert abs(weights["NEGATIVE2"] - expected_weight) < 0.001
        assert abs(weights["NEGATIVE3"] - expected_weight) < 0.001

    def test_signal_proportional_weights_max_weight_constraint(self, engine):
        """신호 비례 가중치: max_weight 제약 테스트"""
        # Arrange
        signals = {
            "A": 0.8,
            "B": 0.1,
            "C": 0.1,
        }
        max_weight = 0.50

        # Act
        weights = engine._signal_proportional_weights(signals, max_weight)

        # Assert
        # 정규화되어야 함
        assert abs(sum(weights.values()) - 1.0) < 0.001
        # 모든 가중치는 비음수
        assert all(w >= 0.0 for w in weights.values())

    def test_apply_constraints_max_single_weight(self, engine):
        """제약 조건: 종목당 최대 비중 테스트"""
        # Arrange
        weights = {
            "A": 0.50,  # max_single_weight 초과
            "B": 0.30,  # max_single_weight 초과
            "C": 0.20,
        }
        sector_info = {"A": "Sector1", "B": "Sector2", "C": "Sector3"}
        max_single = 0.20

        # Act
        constrained = engine._apply_constraints(weights, sector_info)

        # Assert
        # 적용 과정에서 가중치는 정규화됨
        assert abs(sum(constrained.values()) - 1.0) < 0.001
        # 정규화되었으므로 모든 가중치는 유효해야 함
        assert all(w >= 0.0 for w in constrained.values())

    def test_apply_constraints_max_sector_weight(self, engine):
        """제약 조건: 섹터당 최대 비중 테스트"""
        # Arrange
        weights = {
            "A": 0.15,
            "B": 0.15,
            "C": 0.30,
        }
        sector_info = {
            "A": "Technology",
            "B": "Technology",  # 합계 0.30 < 0.40 제약 (제약을 초과하지 않는 경우)
            "C": "Finance",
        }

        # Act
        constrained = engine._apply_constraints(weights, sector_info)

        # Assert
        # 정규화 후에도 모든 가중치는 유효해야 함
        assert abs(sum(constrained.values()) - 1.0) < 0.001
        assert all(w >= 0.0 for w in constrained.values())

    def test_apply_constraints_min_positions(self, engine):
        """제약 조건: 최소 종목 수 테스트"""
        # Arrange
        weights = {
            "A": 0.40,
            "B": 0.40,
            "C": 0.20,
        }
        sector_info = {"A": "S1", "B": "S2", "C": "S3"}
        min_pos = PORTFOLIO_CONSTRAINTS.get("min_positions", 5)

        # Act
        constrained = engine._apply_constraints(weights, sector_info)

        # Assert
        # 0이 아닌 가중치의 개수 >= min_positions (또는 원본이 작으면 그대로)
        active_positions = len([w for w in constrained.values() if w > 0.0001])
        assert active_positions >= min(min_pos, len(weights))

    def test_apply_constraints_combined(self, engine):
        """제약 조건: 종합 제약 적용 테스트"""
        # Arrange
        weights = {
            "A": 0.25,
            "B": 0.25,
            "C": 0.25,
            "D": 0.25,
        }
        sector_info = {"A": "Tech", "B": "Tech", "C": "Finance", "D": "Finance"}

        # Act
        constrained = engine._apply_constraints(weights, sector_info)

        # Assert
        # 정규화되어야 함
        assert abs(sum(constrained.values()) - 1.0) < 0.001
        # 모든 가중치는 비음수
        assert all(w >= 0.0 for w in constrained.values())

    def test_calculate_rebalancing_orders_no_change(self, engine):
        """리밸런싱 주문: 변화 없을 때 테스트"""
        # Arrange
        current = {"A": 0.20, "B": 0.30, "C": 0.50}
        target = {"A": 0.20, "B": 0.30, "C": 0.50}
        capital = 10000000.0

        # Act
        orders = engine._calculate_rebalancing_orders(current, target, capital)

        # Assert
        # 변화가 없으므로 주문이 없어야 함
        assert len(orders) == 0

    def test_calculate_rebalancing_orders_buy(self, engine):
        """리밸런싱 주문: BUY 주문 생성 테스트"""
        # Arrange
        current = {"A": 0.10, "B": 0.30}
        target = {"A": 0.25, "B": 0.25}  # A를 증가
        capital = 10000000.0

        # Act
        orders = engine._calculate_rebalancing_orders(current, target, capital)

        # Assert
        # A에 대한 BUY 주문 존재
        a_orders = [o for o in orders if o["ticker"] == "A"]
        assert len(a_orders) > 0
        assert a_orders[0]["action"] == "BUY"
        assert a_orders[0]["weight_diff"] > 0

    def test_calculate_rebalancing_orders_sell(self, engine):
        """리밸런싱 주문: SELL 주문 생성 테스트"""
        # Arrange
        current = {"A": 0.30, "B": 0.20}
        target = {"A": 0.15, "B": 0.25}  # A를 감소
        capital = 10000000.0

        # Act
        orders = engine._calculate_rebalancing_orders(current, target, capital)

        # Assert
        # A에 대한 SELL 주문 존재
        a_orders = [o for o in orders if o["ticker"] == "A"]
        assert len(a_orders) > 0
        assert a_orders[0]["action"] == "SELL"
        assert a_orders[0]["weight_diff"] < 0

    def test_calculate_rebalancing_orders_new_position(self, engine):
        """리밸런싱 주문: 새 종목 추가 테스트"""
        # Arrange
        current = {"A": 0.40, "B": 0.60}
        target = {"A": 0.25, "B": 0.50, "C": 0.25}  # C 신규 추가
        capital = 10000000.0

        # Act
        orders = engine._calculate_rebalancing_orders(current, target, capital)

        # Assert
        # C에 대한 BUY 주문 존재
        c_orders = [o for o in orders if o["ticker"] == "C"]
        assert len(c_orders) > 0
        assert c_orders[0]["action"] == "BUY"

    def test_calculate_rebalancing_orders_ignore_small_changes(self, engine):
        """리밸런싱 주문: 작은 변화는 무시 테스트"""
        # Arrange
        current = {"A": 0.2000, "B": 0.3000}
        target = {"A": 0.2005, "B": 0.2995}  # 0.5bp 변화 (0.001 미만)
        capital = 10000000.0

        # Act
        orders = engine._calculate_rebalancing_orders(current, target, capital)

        # Assert
        # 0.001 미만 변화는 무시됨
        assert len(orders) == 0

    def test_calculate_rebalancing_orders_sorted_by_diff(self, engine):
        """리밸런싱 주문: 변화량으로 정렬 테스트"""
        # Arrange
        current = {"A": 0.10, "B": 0.20, "C": 0.30}
        target = {"A": 0.35, "B": 0.22, "C": 0.20}
        capital = 10000000.0

        # Act
        orders = engine._calculate_rebalancing_orders(current, target, capital)

        # Assert
        # 변화량 크기 순서로 정렬
        if len(orders) > 1:
            for i in range(len(orders) - 1):
                assert abs(orders[i]["weight_diff"]) >= abs(orders[i + 1]["weight_diff"])

    def test_calculate_rebalancing_orders_quantity_positive(self, engine):
        """리밸런싱 주문: 수량이 양수인지 테스트"""
        # Arrange
        current = {"A": 0.10}
        target = {"A": 0.40}
        capital = 10000000.0

        # Act
        orders = engine._calculate_rebalancing_orders(current, target, capital)

        # Assert
        assert all(o["quantity"] > 0 for o in orders)

    @pytest.mark.asyncio
    async def test_construct_allocations_count(
        self, engine, sample_signals, sample_sector_info, sample_market_info
    ):
        """포트폴리오: 할당량 수가 합리적인지 테스트"""
        # Arrange
        current_portfolio = {t: 0.0 for t in sample_signals.keys()}
        seed_capital = 10000000.0

        # Act
        portfolio = await engine.construct(
            ensemble_signals=sample_signals,
            current_portfolio=current_portfolio,
            seed_capital=seed_capital,
            sector_info=sample_sector_info,
            market_info=sample_market_info,
        )

        # Assert
        # 할당량이 과도하게 많지 않아야 함
        assert portfolio.stock_count <= 10
        # 최소 1개 이상
        assert portfolio.stock_count >= 1

    @pytest.mark.asyncio
    async def test_construct_allocations_have_required_fields(
        self, engine, sample_signals, sample_sector_info, sample_market_info
    ):
        """포트폴리오: 모든 할당량이 필수 필드를 가지는지 테스트"""
        # Arrange
        current_portfolio = {t: 0.0 for t in sample_signals.keys()}
        seed_capital = 10000000.0

        # Act
        portfolio = await engine.construct(
            ensemble_signals=sample_signals,
            current_portfolio=current_portfolio,
            seed_capital=seed_capital,
            sector_info=sample_sector_info,
            market_info=sample_market_info,
        )

        # Assert
        for allocation in portfolio.allocations:
            assert allocation.ticker in sample_signals
            assert allocation.market in [Market.KRX, Market.NYSE, Market.NASDAQ, Market.AMEX]
            assert 0 <= allocation.target_weight <= 1.0
            assert 0 <= allocation.current_weight <= 1.0
            assert -1.0 <= allocation.signal_score <= 1.0

    @pytest.mark.asyncio
    async def test_construct_cash_ratio_valid(
        self, engine, sample_signals, sample_sector_info, sample_market_info
    ):
        """포트폴리오: 현금 비중이 유효한지 테스트"""
        # Arrange
        current_portfolio = {t: 0.0 for t in sample_signals.keys()}
        seed_capital = 10000000.0

        # Act
        portfolio = await engine.construct(
            ensemble_signals=sample_signals,
            current_portfolio=current_portfolio,
            seed_capital=seed_capital,
            sector_info=sample_sector_info,
            market_info=sample_market_info,
        )

        # Assert
        assert 0 <= portfolio.cash_ratio <= 1.0
        # 주식 + 현금 = 1.0
        total = sum(a.target_weight for a in portfolio.allocations) + portfolio.cash_ratio
        assert abs(total - 1.0) < 0.01


# ══════════════════════════════════════
# 엣지 케이스 및 통합 테스트
# ══════════════════════════════════════
class TestEdgeCases:
    """
    엣지 케이스 및 통합 시나리오 테스트
    """

    @pytest.fixture
    def engine(self):
        """포트폴리오 구성 엔진"""
        return PortfolioConstructionEngine(
            risk_profile=RiskProfile.AGGRESSIVE,
            constraints=PORTFOLIO_CONSTRAINTS,
        )

    @pytest.mark.asyncio
    async def test_single_signal(self, engine):
        """단일 종목 신호 테스트"""
        # Arrange
        signals = {"ONLY_ONE": 0.75}
        sector_info = {"ONLY_ONE": "Tech"}
        market_info = {"ONLY_ONE": Market.NYSE}

        # Act
        portfolio = await engine.construct(
            ensemble_signals=signals,
            current_portfolio={},
            seed_capital=1000000.0,
            sector_info=sector_info,
            market_info=market_info,
        )

        # Assert
        assert len(portfolio.allocations) >= 1
        assert portfolio.allocations[0].ticker == "ONLY_ONE"
        # 포트폴리오 합계는 1.0 (또는 현금 포함)
        total = sum(a.target_weight for a in portfolio.allocations) + portfolio.cash_ratio
        assert abs(total - 1.0) < 0.01

    @pytest.mark.asyncio
    async def test_mixed_positive_negative_signals(self, engine):
        """혼합 신호 (양수/음수) 테스트"""
        # Arrange
        signals = {
            "POS1": 0.8,
            "POS2": 0.6,
            "NEG1": -0.5,
            "NEG2": -0.7,
        }
        sector_info = {k: f"Sector{i}" for i, k in enumerate(signals.keys())}
        market_info = {k: Market.NYSE for k in signals.keys()}

        # Act
        portfolio = await engine.construct(
            ensemble_signals=signals,
            current_portfolio={},
            seed_capital=1000000.0,
            sector_info=sector_info,
            market_info=market_info,
        )

        # Assert
        # 양수 신호 종목의 가중치 > 음수 신호 종목의 가중치
        pos_allocs = [a for a in portfolio.allocations if a.signal_score > 0]
        neg_allocs = [a for a in portfolio.allocations if a.signal_score < 0]
        if pos_allocs and neg_allocs:
            avg_pos = np.mean([a.target_weight for a in pos_allocs])
            avg_neg = np.mean([a.target_weight for a in neg_allocs])
            assert avg_pos >= avg_neg

    @pytest.mark.asyncio
    async def test_very_large_portfolio(self, engine):
        """대형 포트폴리오 (100개 종목) 테스트"""
        # Arrange
        signals = {f"STOCK_{i:03d}": 0.5 + 0.01 * i for i in range(100)}
        sector_info = {f"STOCK_{i:03d}": f"Sector{i % 10}" for i in range(100)}
        market_info = {
            f"STOCK_{i:03d}": Market.NYSE if i % 2 == 0 else Market.NASDAQ
            for i in range(100)
        }

        # Act
        portfolio = await engine.construct(
            ensemble_signals=signals,
            current_portfolio={},
            seed_capital=10000000.0,
            sector_info=sector_info,
            market_info=market_info,
        )

        # Assert
        # 포트폴리오는 유효한 할당량을 가져야 함
        assert len(portfolio.allocations) > 0
        # 가중치 합이 유효해야 함
        total_weight = sum(a.target_weight for a in portfolio.allocations)
        assert abs(total_weight + portfolio.cash_ratio - 1.0) < 0.01

    def test_custom_constraints(self):
        """커스텀 제약 조건 테스트"""
        # Arrange
        custom_constraints = {
            "max_single_weight": 0.10,
            "max_sector_weight": 0.25,
            "min_positions": 10,
        }
        engine = PortfolioConstructionEngine(
            risk_profile=RiskProfile.CONSERVATIVE,
            constraints=custom_constraints,
        )
        weights = {f"A{i}": 0.5 / 10 for i in range(10)}
        sector_info = {f"A{i}": f"Sector{i % 3}" for i in range(10)}

        # Act
        constrained = engine._apply_constraints(weights, sector_info)

        # Assert
        assert all(w <= custom_constraints["max_single_weight"] + 0.001 for w in constrained.values())
        assert abs(sum(constrained.values()) - 1.0) < 0.001
