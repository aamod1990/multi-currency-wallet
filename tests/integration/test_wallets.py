from decimal import Decimal

from sqlalchemy import func, select

from app.db.models import LedgerEntry, Wallet
from app.domain.enums import TransactionType


def test_credit_same_currency(make_user):
    u = make_user("USD")
    body = u.credit("100.50")
    tx = body["transactions"][0]
    assert tx["type"] == "CREDIT"
    assert tx["amount"] == "100.5"
    assert tx["fx_rate"] == "1"
    assert tx["fx_snapshot_id"] is None
    assert u.balances() == {"USD": "100.5"}


def test_credit_with_conversion_records_rate_and_snapshot(make_user):
    u = make_user("USD")
    body = u.credit("92", currency="EUR", wallet_currency="USD")
    tx = body["transactions"][0]
    # static rates: 1 USD = 0.92 EUR  ->  92 EUR = 100 USD
    assert tx["amount"] == "100"
    assert tx["original_amount"] == "92"
    assert tx["original_currency"] == "EUR"
    assert Decimal(tx["fx_rate"]) == Decimal("1.0869565217")
    assert tx["fx_snapshot_id"]
    snap = u.get(f"/api/fx/snapshots/{tx['fx_snapshot_id']}").json()
    assert snap["provider"] == "static"
    assert snap["rates"]["EUR"]


def test_credit_into_new_currency_creates_wallet(make_user):
    u = make_user("USD")
    u.credit("5000", currency="JPY")
    assert u.balances() == {"USD": "0", "JPY": "5000"}


def test_debit_and_insufficient_funds(make_user):
    u = make_user("USD")
    u.credit("50")
    r = u.post("/api/wallets/debit", {"amount": "20", "currency": "USD"})
    assert r.status_code == 201
    assert r.json()["wallets"][0]["balance"] == "30"
    r = u.post("/api/wallets/debit", {"amount": "30.01", "currency": "USD"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INSUFFICIENT_FUNDS"
    assert u.balances()["USD"] == "30"


def test_debit_in_other_currency_converts(make_user):
    u = make_user("USD")
    u.credit("100")
    r = u.post(
        "/api/wallets/debit",
        {"amount": "46", "currency": "EUR", "wallet_currency": "USD"},
    )
    assert r.status_code == 201
    assert r.json()["transactions"][0]["amount"] == "50"
    assert u.balances()["USD"] == "50"


def test_debit_missing_wallet(make_user):
    u = make_user("USD")
    r = u.post("/api/wallets/debit", {"amount": "1", "currency": "GBP"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "WALLET_NOT_FOUND"


def test_amount_validation(make_user):
    u = make_user("USD")
    for amount in ("0", "-5", "1.001", "abc", "1e30"):
        r = u.post("/api/wallets/credit", {"amount": amount, "currency": "USD"})
        assert r.status_code == 422, amount
    r = u.post("/api/wallets/credit", {"amount": "10.5", "currency": "JPY"})
    assert r.status_code == 422


def test_idempotent_credit_replays_without_double_posting(make_user, db):
    u = make_user("USD")
    first = u.post(
        "/api/wallets/credit", {"amount": "10", "currency": "USD"}, idem="key-12345"
    )
    second = u.post(
        "/api/wallets/credit", {"amount": "10", "currency": "USD"}, idem="key-12345"
    )
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers["Idempotent-Replayed"] == "true"
    assert (
        first.json()["transactions"][0]["id"] == second.json()["transactions"][0]["id"]
    )
    assert u.balances()["USD"] == "10"
    reuse = u.post(
        "/api/wallets/credit", {"amount": "11", "currency": "USD"}, idem="key-12345"
    )
    assert reuse.status_code == 409
    assert reuse.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_exchange_between_own_wallets(make_user):
    u = make_user("USD")
    u.credit("100")
    r = u.post(
        "/api/wallets/exchange",
        {"amount": "50", "from_currency": "USD", "to_currency": "EUR"},
    )
    assert r.status_code == 201, r.text
    legs = {t["type"]: t for t in r.json()["transactions"]}
    assert legs["CONVERSION_OUT"]["amount"] == "50"
    assert legs["CONVERSION_IN"]["amount"] == "46"
    assert (
        legs["CONVERSION_OUT"]["reference_id"] == legs["CONVERSION_IN"]["reference_id"]
    )
    assert u.balances() == {"USD": "50", "EUR": "46"}


def test_stale_rates_block_conversion_but_not_same_currency(make_user, db, monkeypatch):
    from app.core.config import get_settings

    u = make_user("USD")
    monkeypatch.setattr(get_settings(), "fx_max_age_seconds", -1)
    r = u.post(
        "/api/wallets/credit",
        {"amount": "10", "currency": "EUR", "wallet_currency": "USD"},
    )
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "FX_RATES_UNAVAILABLE"
    u.credit("10")  # same-currency still works
    wallets = u.get("/api/wallets").json()
    assert wallets["wallets"][0]["balance"] == "10"


def test_wallet_list_total_in_default_currency(make_user):
    u = make_user("USD")
    u.credit("100")
    u.credit("92", currency="EUR")
    body = u.get("/api/wallets").json()
    assert body["total_currency"] == "USD"
    assert body["total"] == "200"


def test_ledger_balance_invariant(make_user, db):
    u = make_user("USD")
    u.credit("100")
    u.post("/api/wallets/debit", {"amount": "30", "currency": "USD"})
    u.post(
        "/api/wallets/exchange",
        {"amount": "20", "from_currency": "USD", "to_currency": "GBP"},
    )
    for wallet in db.scalars(select(Wallet)).all():
        entries = db.scalars(
            select(LedgerEntry).where(LedgerEntry.wallet_id == wallet.id)
        ).all()
        signed = sum(
            (-e.amount if TransactionType(e.type).is_outflow else e.amount)
            for e in entries
        )
        assert signed == wallet.balance
    assert db.scalar(select(func.count()).select_from(LedgerEntry)) == 4
