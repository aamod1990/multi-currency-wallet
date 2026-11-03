from decimal import Decimal

import httpx
import pytest
import respx

from app.fx.providers import (
    ChainedProvider,
    FrankfurterProvider,
    FxProviderError,
    OpenErApiProvider,
    StaticProvider,
)

OPEN_ER = "https://open.er-api.com/v6/latest/USD"
FRANKFURTER = "https://api.frankfurter.app/latest"


@pytest.fixture
def client():
    with httpx.Client() as c:
        yield c


@respx.mock
def test_open_er_api_parses_and_filters_unknown_currencies(client):
    respx.get(OPEN_ER).respond(
        json={
            "result": "success",
            "time_last_update_unix": 1_700_000_000,
            "rates": {"USD": 1, "EUR": 0.92, "BTC": 0.00001, "INR": "83.1", "GBP": -1},
        }
    )
    quote = OpenErApiProvider(client).fetch("USD")
    assert quote.rates == {"USD": Decimal(1), "EUR": Decimal("0.92"), "INR": Decimal("83.1")}
    assert quote.provider_updated_at is not None


@respx.mock
def test_open_er_api_error_result(client):
    respx.get(OPEN_ER).respond(json={"result": "error", "error-type": "unsupported-code"})
    with pytest.raises(FxProviderError, match="unsupported-code"):
        OpenErApiProvider(client).fetch("USD")


@respx.mock
def test_open_er_api_http_failure(client):
    respx.get(OPEN_ER).respond(status_code=503)
    with pytest.raises(FxProviderError):
        OpenErApiProvider(client).fetch("USD")


@respx.mock
def test_chained_provider_falls_back(client):
    respx.get(OPEN_ER).mock(side_effect=httpx.ConnectTimeout("boom"))
    respx.get(FRANKFURTER).respond(
        json={"base": "USD", "date": "2024-05-01", "rates": {"EUR": 0.93}}
    )
    chain = ChainedProvider([OpenErApiProvider(client), FrankfurterProvider(client)])
    quote = chain.fetch("USD")
    assert quote.provider == "frankfurter.app"
    assert quote.rates["EUR"] == Decimal("0.93")


@respx.mock
def test_chained_provider_all_fail(client):
    respx.get(OPEN_ER).respond(status_code=500)
    respx.get(FRANKFURTER).respond(status_code=500)
    chain = ChainedProvider([OpenErApiProvider(client), FrankfurterProvider(client)])
    with pytest.raises(FxProviderError):
        chain.fetch("USD")


def test_static_provider_rebases():
    quote = StaticProvider({"USD": "1", "EUR": "0.5"}).fetch("EUR")
    assert quote.rates["USD"] == Decimal(2)
    assert quote.rates["EUR"] == Decimal(1)
