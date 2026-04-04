"""FinancialData 계약 테스트 (Contract 2)."""

from datetime import date

import pytest
from pydantic import ValidationError

from contracts.financial_data import FinancialData


def _valid_fin(**overrides):
    defaults = dict(
        ticker="005930",
        period_end=date(2024, 12, 31),
        filing_date=date(2025, 3, 15),
        eps=5000.0,
        per=12.5,
        pbr=1.2,
        roe=15.0,
    )
    defaults.update(overrides)
    return defaults


@pytest.mark.smoke
class TestFinancialDataValid:
    def test_basic_creation(self):
        f = FinancialData(**_valid_fin())
        assert f.ticker == "005930"
        assert f.eps == 5000.0

    def test_optional_fields_none(self):
        f = FinancialData(**_valid_fin(eps=None, per=None, pbr=None, roe=None))
        assert f.eps is None

    def test_filing_same_as_period_end(self):
        f = FinancialData(**_valid_fin(period_end=date(2024, 12, 31), filing_date=date(2024, 12, 31)))
        assert f.filing_date == f.period_end

    def test_revenue_and_income(self):
        f = FinancialData(**_valid_fin(revenue=50000.0, operating_income=8000.0, net_income=6000.0))
        assert f.revenue == 50000.0

    def test_debt_ratio(self):
        f = FinancialData(**_valid_fin(debt_ratio=150.0))
        assert f.debt_ratio == 150.0


@pytest.mark.smoke
class TestFinancialDataInvalid:
    def test_filing_before_period(self):
        with pytest.raises(ValidationError, match="look-ahead bias"):
            FinancialData(**_valid_fin(period_end=date(2024, 12, 31), filing_date=date(2024, 11, 1)))

    def test_per_zero(self):
        with pytest.raises(ValidationError, match="PER 이상치"):
            FinancialData(**_valid_fin(per=0.0))

    def test_per_negative(self):
        with pytest.raises(ValidationError, match="PER 이상치"):
            FinancialData(**_valid_fin(per=-5.0))

    def test_per_extreme(self):
        with pytest.raises(ValidationError, match="PER 이상치"):
            FinancialData(**_valid_fin(per=1001.0))

    def test_negative_revenue(self):
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            FinancialData(**_valid_fin(revenue=-100.0))

    def test_negative_debt_ratio(self):
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            FinancialData(**_valid_fin(debt_ratio=-10.0))

    def test_empty_ticker(self):
        with pytest.raises(ValidationError):
            FinancialData(**_valid_fin(ticker=""))

    def test_extra_field(self):
        with pytest.raises(ValidationError, match="Extra inputs"):
            FinancialData(**_valid_fin(sector="IT"))

    def test_immutable(self):
        f = FinancialData(**_valid_fin())
        with pytest.raises(ValidationError):
            f.eps = 9999.0

    def test_per_at_boundary_1000(self):
        # PER = 1000 should be valid (≤ 1000)
        f = FinancialData(**_valid_fin(per=1000.0))
        assert f.per == 1000.0
