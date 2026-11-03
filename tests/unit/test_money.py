from decimal import Decimal

import pytest

from app.core.errors import UnsupportedCurrency, ValidationFailed
from app.domain import money


class TestValidateCurrency:
    def test_normalises_case_and_whitespace(self):
        assert money.validate_currency(" usd ") == "USD"

    @pytest.mark.parametrize("code", ["", "XXX", "BTC", "US"])
    def test_rejects_unsupported(self, code):
        with pytest.raises(UnsupportedCurrency):
            money.validate_currency(code)


class TestParseAmount:
    def test_accepts_valid_amount(self):
        assert money.parse_amount(Decimal("10.50"), "USD", 1000) == Decimal("10.50")

    @pytest.mark.parametrize("amount", ["0", "-1", "NaN", "Infinity"])
    def test_rejects_non_positive_or_non_finite(self, amount):
        with pytest.raises(ValidationFailed):
            money.parse_amount(Decimal(amount), "USD", 1000)

    def test_rejects_excess_precision_instead_of_rounding(self):
        with pytest.raises(ValidationFailed, match="decimal places"):
            money.parse_amount(Decimal("1.005"), "USD", 1000)

    def test_respects_zero_decimal_currencies(self):
        with pytest.raises(ValidationFailed):
            money.parse_amount(Decimal("100.5"), "JPY", 10_000)
        assert money.parse_amount(Decimal("100"), "JPY", 10_000) == Decimal("100")

    def test_three_decimal_currency(self):
        assert money.parse_amount(Decimal("1.234"), "KWD", 10) == Decimal("1.234")

    def test_rejects_above_limit(self):
        with pytest.raises(ValidationFailed, match="maximum"):
            money.parse_amount(Decimal("1001"), "USD", 1000)


class TestConversion:
    RATES = {"USD": Decimal("1"), "EUR": Decimal("0.92"), "JPY": Decimal("151.4")}

    def test_identity_rate(self):
        assert money.cross_rate(self.RATES, "EUR", "EUR") == Decimal(1)

    def test_cross_rate_via_base(self):
        assert money.cross_rate(self.RATES, "EUR", "JPY") == Decimal("164.5652173913")

    def test_missing_rate(self):
        with pytest.raises(UnsupportedCurrency):
            money.cross_rate(self.RATES, "USD", "GBP")

    def test_convert_rounds_to_target_minor_units(self):
        rate = money.cross_rate(self.RATES, "USD", "JPY")
        assert money.convert(Decimal("10.01"), rate, "JPY") == Decimal("1516")

    def test_bankers_rounding(self):
        assert money.quantize(Decimal("0.125"), "USD") == Decimal("0.12")
        assert money.quantize(Decimal("0.135"), "USD") == Decimal("0.14")

    def test_round_trip_drift_is_bounded(self):
        fwd = money.cross_rate(self.RATES, "USD", "EUR")
        back = money.cross_rate(self.RATES, "EUR", "USD")
        eur = money.convert(Decimal("100.00"), fwd, "EUR")
        usd = money.convert(eur, back, "USD")
        assert abs(usd - Decimal("100.00")) <= Decimal("0.01")
