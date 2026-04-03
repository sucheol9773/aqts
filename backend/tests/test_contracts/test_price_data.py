"""PriceData 계약 테스트 (Contract 1)."""

import pytest
from datetime import date, datetime
from pydantic import ValidationError

from contracts.price_data import PriceData
from config.constants import Market


def _valid_price(**overrides):
    """유효한 PriceData kwargs."""
    defaults = dict(
        ticker="005930", market=Market.KRX, trade_date=date(2024, 6, 1),
        open=70000, high=72000, low=69000, close=71000, volume=1_000_000,
    )
    defaults.update(overrides)
    return defaults


class TestPriceDataValid:
    def test_basic_creation(self):
        p = PriceData(**_valid_price())
        assert p.ticker == "005930"
        assert p.close == 71000

    def test_adjusted_close(self):
        p = PriceData(**_valid_price(adjusted_close=70500.0))
        assert p.adjusted_close == 70500.0

    def test_zero_volume_allowed(self):
        p = PriceData(**_valid_price(volume=0))
        assert p.volume == 0

    def test_us_market(self):
        p = PriceData(**_valid_price(ticker="AAPL", market=Market.NYSE))
        assert p.market == Market.NYSE

    def test_ticker_with_dot(self):
        p = PriceData(**_valid_price(ticker="BRK.B"))
        assert p.ticker == "BRK.B"

    def test_immutable(self):
        p = PriceData(**_valid_price())
        with pytest.raises(ValidationError):
            p.close = 99999


class TestPriceDataInvalid:
    def test_negative_open(self):
        with pytest.raises(ValidationError, match="greater than 0"):
            PriceData(**_valid_price(open=-1))

    def test_negative_volume(self):
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            PriceData(**_valid_price(volume=-100))

    def test_high_less_than_low(self):
        with pytest.raises(ValidationError, match="OHLC 관계 위반"):
            PriceData(**_valid_price(high=69000, low=72000))

    def test_high_less_than_close(self):
        with pytest.raises(ValidationError, match="high.*close"):
            PriceData(**_valid_price(high=70000, close=71000))

    def test_low_greater_than_open(self):
        with pytest.raises(ValidationError, match="low.*open"):
            PriceData(**_valid_price(low=71000, open=70000, close=70500, high=71500))

    def test_empty_ticker(self):
        with pytest.raises(ValidationError):
            PriceData(**_valid_price(ticker=""))

    def test_special_chars_ticker(self):
        with pytest.raises(ValidationError, match="허용되지 않는 문자"):
            PriceData(**_valid_price(ticker="AB@#"))

    def test_extra_field_forbidden(self):
        with pytest.raises(ValidationError, match="Extra inputs"):
            PriceData(**_valid_price(extra_field="bad"))

    def test_zero_price(self):
        with pytest.raises(ValidationError, match="greater than 0"):
            PriceData(**_valid_price(close=0))

    def test_adjusted_close_negative(self):
        with pytest.raises(ValidationError, match="greater than 0"):
            PriceData(**_valid_price(adjusted_close=-100.0))
