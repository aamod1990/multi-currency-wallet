"""Integration tests run the real FastAPI app against a real PostgreSQL database
(row locks, CHECK constraints and ON CONFLICT semantics can't be faked with SQLite).

Set TEST_DATABASE_URL, e.g. postgresql+psycopg://wallet:wallet@localhost:5432/wallet_test
"""

import os
import uuid
from pathlib import Path

import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL")
if not TEST_DB:
    pytest.skip("TEST_DATABASE_URL not set; skipping integration tests", allow_module_level=True)

os.environ.update(
    DATABASE_URL=TEST_DB,
    ENVIRONMENT="test",
    FX_PROVIDER="static",
    FX_REFRESH_ENABLED="false",
    FRONTEND_DIST="/nonexistent",
    LOG_JSON="false",
    LOG_LEVEL="WARNING",
)

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.api.routes.auth import login_limiter  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.session import get_engine, session_factory  # noqa: E402
from app.fx.providers import StaticProvider  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services import fx_service  # noqa: E402

BACKEND = Path(__file__).resolve().parents[2] / "backend"
TABLES = "ledger_entries, transfers, user_photos, wallets, fx_rate_snapshots, users"


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", TEST_DB)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def clean_db():
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} CASCADE"))
    login_limiter.reset()
    with session_factory()() as db:
        fx_service.refresh_rates(db, StaticProvider())
    yield


@pytest.fixture
def db():
    with session_factory()() as s:
        yield s


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
        return self.client.get(url, headers={**self.headers, **kw.pop("headers", {})}, **kw)

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
        return {w["currency"]: w["balance"] for w in self.get("/api/wallets").json()["wallets"]}


@pytest.fixture
def make_user(client):
    def _make(currency: str = "USD", email: str | None = None) -> ApiUser:
        email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
        r = client.post(
            "/api/auth/signup",
            json={"email": email, "password": "Passw0rd!", "full_name": "Test User",
                  "default_currency": currency},
        )
        assert r.status_code == 201, r.text
        return ApiUser(client, email, currency, r.json())

    return _make
