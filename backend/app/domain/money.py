"""Money primitives. All arithmetic uses Decimal; floats never touch balances."""

from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from app.core.errors import UnsupportedCurrency, ValidationFailed

# ISO-4217 minor-unit exponents for the currencies the platform supports.
# A currency must be both listed here and quoted by the FX provider to be usable.
CURRENCY_EXPONENTS: dict[str, int] = {
    "USD": 2, "EUR": 2, "GBP": 2, "INR": 2, "JPY": 0, "CHF": 2, "CAD": 2,
    "AUD": 2, "NZD": 2, "CNY": 2, "HKD": 2, "SGD": 2, "SEK": 2, "NOK": 2,
    "DKK": 2, "PLN": 2, "CZK": 2, "HUF": 2, "ZAR": 2, "BRL": 2, "MXN": 2,
    "AED": 2, "SAR": 2, "KRW": 0, "THB": 2, "MYR": 2, "IDR": 2, "PHP": 2,
    "TRY": 2, "ILS": 2, "KWD": 3, "BHD": 3,
}  # fmt: skip

RATE_PRECISION = Decimal("1e-10")


def validate_currency(code: str) -> str:
    code = (code or "").strip().upper()
    if code not in CURRENCY_EXPONENTS:
        raise UnsupportedCurrency(f"Currency '{code}' is not supported")
    return code


def minor_unit(currency: str) -> Decimal:
    return Decimal(1).scaleb(-CURRENCY_EXPONENTS[validate_currency(currency)])


def quantize(amount: Decimal, currency: str) -> Decimal:
    """Round to the currency's minor unit using banker's rounding."""
    return amount.quantize(minor_unit(currency), rounding=ROUND_HALF_EVEN)


def parse_amount(amount: Decimal, currency: str, max_amount: int) -> Decimal:
    """Validate a user-supplied amount: positive, finite, within limits and
    not more precise than the currency allows (we never silently drop cents)."""
    currency = validate_currency(currency)
    if not amount.is_finite() or amount <= 0:
        raise ValidationFailed("Amount must be a positive number")
    if amount > max_amount:
        raise ValidationFailed(f"Amount exceeds the maximum of {max_amount}")
    q = quantize(amount, currency)
    if q != amount:
        raise ValidationFailed(
            f"{currency} supports at most {CURRENCY_EXPONENTS[currency]} decimal places"
        )
    return q


def cross_rate(rates: dict[str, Decimal], source: str, target: str) -> Decimal:
    """Rate to convert 1 unit of `source` into `target`, given base-quoted rates."""
    if source == target:
        return Decimal(1)
    try:
        src, tgt = rates[source], rates[target]
    except KeyError as exc:
        raise UnsupportedCurrency(f"No exchange rate available for {exc.args[0]}") from exc
    with localcontext() as ctx:
        ctx.prec = 28
        return (tgt / src).quantize(RATE_PRECISION, rounding=ROUND_HALF_EVEN)


def convert(amount: Decimal, rate: Decimal, target: str) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 28
        return quantize(amount * rate, target)
