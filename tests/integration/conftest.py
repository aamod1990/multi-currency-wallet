"""Integration tests run the real FastAPI app against a real MongoDB replica set
(multi-document transactions, write conflicts and unique indexes can't be faked).

Set TEST_MONGODB_URL, e.g. mongodb://localhost:27017/?directConnection=true
"""

import os
import uuid

import pytest

TEST_DB = os.environ.get("TEST_MONGODB_URL")
if not TEST_DB:
    pytest.skip(
        "TEST_MONGODB_URL not set; skipping integration tests", allow_module_level=True
    )

os.environ.update(
    MONGODB_URL=TEST_DB,
    MONGODB_DB="wallet_test",
    ENVIRONMENT="test",
    FX_PROVIDER="static",
    FX_REFRESH_ENABLED="false",
    FRONTEND_DIST="/nonexistent",
    LOG_JSON="false",
    LOG_LEVEL="WARNING",
)

from fastapi.testclient import TestClient  # noqa: E402

from app.api.routes.auth import login_limiter  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.mongo import ensure_schema, get_client, get_database  # noqa: E402
from app.fx.providers import StaticProvider  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services import fx_service  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def fresh_schema():
    get_client().drop_database(get_settings().mongodb_db)
    ensure_schema(get_database())
    yield


@pytest.fixture(autouse=True)
def clean_db():
    db = get_database()
    for name in db.list_collection_names():
        db[name].delete_many({})
    login_limiter.reset()
    fx_service.refresh_rates(db, StaticProvider())
    yield


@pytest.fixture
def db():
    return get_database()


@pytest.fixture(scope="session")
def app():
    return create_app(get_settings(), start_background=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


class ApiUser:
    def __init__(self, client: TestClient, email: str, currency: str, payload: dict):
        self.client = client
        self.email = email
        self.currency = currency
        self.id = payload["user"]["id"]
        self.headers = {"Authorization": f"Bearer {payload['access_token']}"}

    def get(self, url, **kw):
        return self.client.get(
            url, headers={**self.headers, **kw.pop("headers", {})}, **kw
        )

    def post(self, url, json=None, idem: str | None = None, **kw):
        headers = {**self.headers, **kw.pop("headers", {})}
        if idem:
            headers["Idempotency-Key"] = idem
        return self.client.post(url, json=json, headers=headers, **kw)

    def patch(self, url, json=None):
        return self.client.patch(url, json=json, headers=self.headers)

    def put(self, url, **kw):
        return self.client.put(url, headers=self.headers, **kw)

    def delete(self, url):
        return self.client.delete(url, headers=self.headers)

    def credit(self, amount, currency=None, wallet_currency=None, **kw):
        body = {"amount": str(amount), "currency": currency or self.currency}
        if wallet_currency:
            body["wallet_currency"] = wallet_currency
        r = self.post("/api/wallets/credit", body, **kw)
        assert r.status_code in (200, 201), r.text
        return r.json()

    def balances(self) -> dict[str, str]:
        return {
            w["currency"]: w["balance"]
            for w in self.get("/api/wallets").json()["wallets"]
        }


@pytest.fixture
def make_user(client):
    def _make(currency: str = "USD", email: str | None = None) -> ApiUser:
        email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
        r = client.post(
            "/api/auth/signup",
            json={
                "email": email,
                "password": "Passw0rd!",
                "full_name": "Test User",
                "default_currency": currency,
            },
        )
        assert r.status_code == 201, r.text
        return ApiUser(client, email, currency, r.json())

    return _make
