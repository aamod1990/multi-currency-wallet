"""Third-party FX rate providers behind a single interface.

Providers return rates quoted against a base currency. Two independent free providers
are chained so a single upstream outage does not stop conversions.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol

import httpx

from app.domain.money import CURRENCY_EXPONENTS

log = logging.getLogger(__name__)


class FxProviderError(Exception):
    pass


@dataclass(frozen=True)
class RateQuote:
    provider: str
    base: str
    rates: dict[str, Decimal]
    provider_updated_at: datetime | None


class FxProvider(Protocol):
    name: str

    def fetch(self, base: str) -> RateQuote: ...


def _clean_rates(raw: dict[str, object], base: str) -> dict[str, Decimal]:
    rates: dict[str, Decimal] = {base: Decimal(1)}
    for code, value in raw.items():
        if code not in CURRENCY_EXPONENTS:
            continue
        try:
            dec = Decimal(str(value))
        except InvalidOperation:
            continue
        if dec.is_finite() and dec > 0:
            rates[code] = dec
    if len(rates) < 2:
        raise FxProviderError("Provider returned no usable rates")
    return rates


class OpenErApiProvider:
    """https://open.er-api.com — free, keyless, ~160 currencies, daily updates."""

    name = "open.er-api.com"

    def __init__(self, client: httpx.Client, url: str = "https://open.er-api.com/v6/latest"):
        self.client, self.url = client, url

    def fetch(self, base: str) -> RateQuote:
        try:
            resp = self.client.get(f"{self.url}/{base}")
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise FxProviderError(f"{self.name}: {exc}") from exc
        if data.get("result") != "success":
            raise FxProviderError(f"{self.name}: {data.get('error-type', 'unknown error')}")
        ts = data.get("time_last_update_unix")
        return RateQuote(
            provider=self.name,
            base=base,
            rates=_clean_rates(data.get("rates", {}), base),
            provider_updated_at=datetime.fromtimestamp(ts, UTC) if ts else None,
        )


class FrankfurterProvider:
    """https://frankfurter.app — ECB reference rates, ~30 currencies (fallback)."""

    name = "frankfurter.app"

    def __init__(self, client: httpx.Client, url: str = "https://api.frankfurter.app/latest"):
        self.client, self.url = client, url

    def fetch(self, base: str) -> RateQuote:
        try:
            resp = self.client.get(self.url, params={"from": base})
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise FxProviderError(f"{self.name}: {exc}") from exc
        updated = None
        if d := data.get("date"):
            try:
                updated = datetime.fromisoformat(d).replace(tzinfo=UTC)
            except ValueError:
                updated = None
        return RateQuote(
            provider=self.name,
            base=base,
            rates=_clean_rates(data.get("rates", {}), base),
            provider_updated_at=updated,
        )


STATIC_USD_RATES: dict[str, str] = {
    "USD": "1", "EUR": "0.92", "GBP": "0.79", "INR": "83.25", "JPY": "151.40",
    "CHF": "0.90", "CAD": "1.36", "AUD": "1.52", "NZD": "1.66", "CNY": "7.24",
    "HKD": "7.82", "SGD": "1.35", "SEK": "10.70", "NOK": "10.80", "DKK": "6.87",
    "PLN": "3.98", "CZK": "23.30", "HUF": "362.0", "ZAR": "18.60", "BRL": "5.05",
    "MXN": "16.90", "AED": "3.6725", "SAR": "3.75", "KRW": "1365.0", "THB": "36.50",
    "MYR": "4.72", "IDR": "15900", "PHP": "56.80", "TRY": "32.30", "ILS": "3.70",
    "KWD": "0.3075", "BHD": "0.376",
}  # fmt: skip


class StaticProvider:
    """Deterministic rates for offline development and tests."""

    name = "static"

    def __init__(self, usd_rates: dict[str, str] | None = None):
        self.usd_rates = {k: Decimal(v) for k, v in (usd_rates or STATIC_USD_RATES).items()}

    def fetch(self, base: str) -> RateQuote:
        if base not in self.usd_rates:
            raise FxProviderError(f"static: unknown base {base}")
        b = self.usd_rates[base]
        return RateQuote(
            provider=self.name,
            base=base,
            rates={k: v / b for k, v in self.usd_rates.items()},
            provider_updated_at=None,
        )


class ChainedProvider:
    """Try providers in order; first success wins."""

    def __init__(self, providers: list[FxProvider]):
        self.providers = providers
        self.name = "+".join(p.name for p in providers)

    def fetch(self, base: str) -> RateQuote:
        errors: list[str] = []
        for p in self.providers:
            try:
                return p.fetch(base)
            except FxProviderError as exc:
                log.warning("fx_provider_failed", extra={"provider": p.name, "error": str(exc)})
                errors.append(str(exc))
        raise FxProviderError("; ".join(errors))
