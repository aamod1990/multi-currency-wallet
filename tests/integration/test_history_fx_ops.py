from datetime import UTC, datetime, timedelta

from app.fx.providers import FxProviderError, StaticProvider
from app.services.fx_refresher import FxRefresher


def _seed(u, other):
    u.credit("100")
    u.credit("50", currency="EUR")
    u.post("/api/wallets/debit", {"amount": "10", "currency": "USD"})
    u.post(
        "/api/transfers",
        {"recipient_email": other.email, "amount": "5", "currency": "USD"},
    )


def test_history_pagination(make_user):
    u = make_user("USD")
    for i in range(25):
        u.credit(str(i + 1))
    p1 = u.get("/api/transactions", params={"page": 1, "page_size": 10}).json()
    p3 = u.get("/api/transactions", params={"page": 3, "page_size": 10}).json()
    assert p1["total"] == 25 and p1["pages"] == 3
    assert len(p1["items"]) == 10 and len(p3["items"]) == 5
    assert p1["items"][0]["amount"] == "25"  # newest first
    ids = {t["id"] for t in p1["items"]} & {t["id"] for t in p3["items"]}
    assert not ids


def test_history_filters(make_user):
    u, other = make_user("USD"), make_user("USD")
    _seed(u, other)
    q = lambda **p: u.get("/api/transactions", params=p).json()  # noqa: E731
    assert q()["total"] == 4
    assert q(type="CREDIT")["total"] == 2
    assert q(type=["DEBIT", "TRANSFER_OUT"])["total"] == 2
    assert q(currency="EUR")["total"] == 1
    assert q(direction="out")["total"] == 2
    assert q(min_amount="50")["total"] == 2
    assert q(max_amount="9")["total"] == 1
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert q(date_from=future)["total"] == 0
    assert other.get("/api/transactions").json()["total"] == 1  # isolation


def test_history_validation(make_user):
    u = make_user()
    assert u.get("/api/transactions", params={"page_size": 1000}).status_code == 422
    assert u.get("/api/transactions", params={"type": "BOGUS"}).status_code == 422
    r = u.get(
        "/api/transactions",
        params={"date_from": "2025-02-01T00:00:00Z", "date_to": "2025-01-01T00:00:00Z"},
    )
    assert r.status_code == 422


def test_transaction_detail_includes_fx_trace(make_user):
    u = make_user("USD")
    tx = u.credit("10", currency="GBP")["transactions"][0]
    detail = u.get(f"/api/transactions/{tx['id']}").json()
    assert detail["fx_snapshot"] is None  # same currency (GBP wallet)
    tx = u.credit("10", currency="GBP", wallet_currency="USD")["transactions"][0]
    detail = u.get(f"/api/transactions/{tx['id']}").json()
    assert detail["fx_snapshot"]["provider"] == "static"
    other = make_user()
    assert other.get(f"/api/transactions/{tx['id']}").status_code == 404


def test_fx_quote_and_currencies(client, make_user):
    u = make_user()
    q = u.get(
        "/api/fx/quote", params={"amount": "100", "from": "USD", "to": "EUR"}
    ).json()
    assert q["converted_amount"] == "92" and q["snapshot_id"]
    currencies = client.get("/api/fx/currencies").json()
    assert "USD" in currencies and "JPY" in currencies
    assert u.get("/api/fx/rates").json()["base_currency"] == "USD"


def test_refresher_skips_when_fresh_and_respects_force(db):
    from app.db.session import session_factory

    r = FxRefresher(StaticProvider(), session_factory(), interval_seconds=3600)
    assert r.run_once() is False  # snapshot seeded by fixture is fresh
    assert r.run_once(force=True) is True


def test_refresher_propagates_provider_failure(db):
    from app.db.session import session_factory

    class Broken:
        name = "broken"

        def fetch(self, base):
            raise FxProviderError("down")

    r = FxRefresher(Broken(), session_factory(), interval_seconds=3600)
    try:
        r.run_once(force=True)
        raise AssertionError("expected failure")
    except FxProviderError:
        pass


def test_health_endpoints(client):
    assert client.get("/health/live").json() == {"status": "ok"}
    ready = client.get("/health/ready")
    assert ready.status_code == 200
    body = ready.json()
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["fx_rates"]["status"] == "ok"


def test_metrics_and_request_id(client):
    r = client.get("/api/users/me", headers={"X-Request-ID": "trace-123"})
    assert r.headers["X-Request-ID"] == "trace-123"
    assert r.json()["error"]["request_id"] == "trace-123"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    metrics = client.get("/metrics").text
    assert (
        'http_requests_total{method="GET",route="/api/users/me",status="401"' in metrics
    )
    assert "fx_refresh_total" in metrics


def test_unknown_api_route_returns_json_404(client):
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_unhandled_exception_returns_500_envelope(app, client, monkeypatch):
    from app.services import history_service

    def boom(*a, **kw):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(history_service, "list_entries", boom)
    from fastapi.testclient import TestClient

    r = client.post(
        "/api/auth/signup",
        json={"email": "z@example.com", "password": "Passw0rd!", "full_name": "Z"},
    )
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/api/transactions", headers=headers)
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "kaboom" not in resp.text
