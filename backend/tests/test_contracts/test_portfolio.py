"""Portfolio 계약 테스트 (Contract 6)."""

import pytest
from pydantic import ValidationError

from config.constants import Market
from contracts.portfolio import PortfolioTarget, PositionTarget


def _pos(ticker="005930", market=Market.KRX, target_weight=0.2, **kw):
    return PositionTarget(ticker=ticker, market=market, target_weight=target_weight, **kw)


@pytest.mark.smoke
class TestPositionTargetValid:
    def test_basic(self):
        p = _pos()
        assert p.ticker == "005930"
        assert p.target_weight == 0.2

    def test_zero_weight(self):
        p = _pos(target_weight=0.0)
        assert p.target_weight == 0.0

    def test_full_weight(self):
        p = _pos(target_weight=1.0)
        assert p.target_weight == 1.0

    def test_with_reason(self):
        p = _pos(reason="가치 팩터 상위")
        assert p.reason == "가치 팩터 상위"


@pytest.mark.smoke
class TestPortfolioTargetValid:
    def test_balanced_portfolio(self):
        positions = [_pos(ticker=f"00{i}930", target_weight=0.15) for i in range(5)]
        pt = PortfolioTarget(positions=positions, cash_weight=0.25)
        assert len(pt.positions) == 5

    def test_all_cash(self):
        pt = PortfolioTarget(positions=[], cash_weight=1.0)
        assert pt.cash_weight == 1.0

    def test_single_position(self):
        pt = PortfolioTarget(
            positions=[_pos(target_weight=0.9)],
            cash_weight=0.1,
        )
        assert len(pt.positions) == 1

    def test_tolerance_within_001(self):
        # 0.199 * 5 + 0.005 = 1.0
        positions = [_pos(ticker=f"T{i}", target_weight=0.199) for i in range(5)]
        pt = PortfolioTarget(positions=positions, cash_weight=0.005)
        assert pt is not None

    def test_with_reason(self):
        pt = PortfolioTarget(
            positions=[_pos(target_weight=0.8)],
            cash_weight=0.2,
            rebalance_reason="월간 리밸런싱",
        )
        assert pt.rebalance_reason == "월간 리밸런싱"


@pytest.mark.smoke
class TestPortfolioTargetInvalid:
    def test_weight_sum_exceeds(self):
        with pytest.raises(ValidationError, match="비중 합계 불일치"):
            PortfolioTarget(
                positions=[_pos(target_weight=0.8)],
                cash_weight=0.5,
            )

    def test_weight_sum_too_low(self):
        with pytest.raises(ValidationError, match="비중 합계 불일치"):
            PortfolioTarget(
                positions=[_pos(target_weight=0.3)],
                cash_weight=0.3,
            )

    def test_duplicate_tickers(self):
        with pytest.raises(ValidationError, match="중복 ticker"):
            PortfolioTarget(
                positions=[
                    _pos(ticker="005930", target_weight=0.3),
                    _pos(ticker="005930", target_weight=0.3),
                ],
                cash_weight=0.4,
            )

    def test_weight_above_1(self):
        with pytest.raises(ValidationError, match="less than or equal to 1"):
            _pos(target_weight=1.5)

    def test_weight_below_0(self):
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            _pos(target_weight=-0.1)

    def test_cash_weight_negative(self):
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            PortfolioTarget(
                positions=[_pos(target_weight=0.5)],
                cash_weight=-0.1,
            )

    def test_extra_field(self):
        with pytest.raises(ValidationError, match="Extra inputs"):
            PortfolioTarget(positions=[], cash_weight=1.0, benchmark="KOSPI")

    def test_immutable(self):
        pt = PortfolioTarget(positions=[], cash_weight=1.0)
        with pytest.raises(ValidationError):
            pt.cash_weight = 0.5
