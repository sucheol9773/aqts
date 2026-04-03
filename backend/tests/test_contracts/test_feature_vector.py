"""FeatureVector 계약 테스트 (Contract 4)."""

import pytest
from datetime import datetime
from pydantic import ValidationError

from contracts.feature_vector import FeatureVector


def _valid_fv(**overrides):
    defaults = dict(
        ticker="005930", as_of=datetime(2024, 6, 1, 9, 0),
        factor_value=0.5, factor_momentum=0.3,
    )
    defaults.update(overrides)
    return defaults


@pytest.mark.smoke
class TestFeatureVectorValid:
    def test_basic_creation(self):
        fv = FeatureVector(**_valid_fv())
        assert fv.factor_value == 0.5

    def test_all_factors(self):
        fv = FeatureVector(**_valid_fv(
            factor_quality=0.2, factor_low_vol=-0.1, factor_size=-0.5
        ))
        assert fv.factor_size == -0.5

    def test_only_technical(self):
        fv = FeatureVector(
            ticker="AAPL", as_of=datetime(2024, 6, 1),
            tech_rsi=65.0,
        )
        assert fv.tech_rsi == 65.0

    def test_only_sentiment(self):
        fv = FeatureVector(
            ticker="005930", as_of=datetime(2024, 6, 1),
            sentiment=0.7,
        )
        assert fv.sentiment == 0.7

    def test_boundary_values(self):
        fv = FeatureVector(**_valid_fv(
            factor_value=-1.0, factor_momentum=1.0, sentiment=-1.0
        ))
        assert fv.factor_value == -1.0
        assert fv.factor_momentum == 1.0

    def test_rsi_boundaries(self):
        fv = FeatureVector(**_valid_fv(tech_rsi=0.0))
        assert fv.tech_rsi == 0.0
        fv2 = FeatureVector(**_valid_fv(tech_rsi=100.0))
        assert fv2.tech_rsi == 100.0


@pytest.mark.smoke
class TestFeatureVectorInvalid:
    def test_no_features_at_all(self):
        with pytest.raises(ValidationError, match="최소 1개"):
            FeatureVector(ticker="005930", as_of=datetime(2024, 6, 1))

    def test_factor_out_of_range_high(self):
        with pytest.raises(ValidationError, match="less than or equal to 1"):
            FeatureVector(**_valid_fv(factor_value=1.5))

    def test_factor_out_of_range_low(self):
        with pytest.raises(ValidationError, match="greater than or equal to -1"):
            FeatureVector(**_valid_fv(factor_momentum=-1.5))

    def test_rsi_above_100(self):
        with pytest.raises(ValidationError, match="less than or equal to 100"):
            FeatureVector(**_valid_fv(tech_rsi=101.0))

    def test_rsi_below_0(self):
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            FeatureVector(**_valid_fv(tech_rsi=-1.0))

    def test_sentiment_out_of_range(self):
        with pytest.raises(ValidationError):
            FeatureVector(**_valid_fv(sentiment=2.0))

    def test_extra_field(self):
        with pytest.raises(ValidationError, match="Extra inputs"):
            FeatureVector(**_valid_fv(custom_factor=0.5))

    def test_empty_ticker(self):
        with pytest.raises(ValidationError):
            FeatureVector(**_valid_fv(ticker=""))

    def test_immutable(self):
        fv = FeatureVector(**_valid_fv())
        with pytest.raises(ValidationError):
            fv.factor_value = 0.9

    def test_all_none_features_explicitly(self):
        with pytest.raises(ValidationError, match="최소 1개"):
            FeatureVector(
                ticker="005930", as_of=datetime(2024, 6, 1),
                factor_value=None, factor_momentum=None,
                factor_quality=None, factor_low_vol=None, factor_size=None,
                tech_rsi=None, tech_macd_signal=None, tech_bollinger_pctb=None,
                sentiment=None,
            )
