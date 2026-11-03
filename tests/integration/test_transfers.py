import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from fastapi.testclient import TestClient


def test_same_currency_transfer(make_user):
    alice, bob = make_user("USD"), make_user("USD")
    alice.credit("100")
    r = alice.post(
        "/api/transfers",
        {
            "recipient_email": bob.email,
            "amount": "40",
            "currency": "USD",
            "note": "lunch",
        },
    )
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["source_amount"] == "40" and t["target_amount"] == "40"
    assert t["fx_snapshot_id"] is None
    assert alice.balances()["USD"] == "60"
    assert bob.balances()["USD"] == "40"
    bob_tx = bob.get("/api/transactions").json()["items"][0]
    assert bob_tx["type"] == "TRANSFER_IN"
    assert bob_tx["counterparty"]["email"] == alice.email
    assert bob_tx["reference_id"] == t["id"]


def test_cross_currency_transfer_credits_recipient_default_currency(make_user):
    alice, bob = make_user("USD"), make_user("INR")
    alice.credit("100")
    r = alice.post(
        "/api/transfers",
        {"recipient_email": bob.email, "amount": "10", "currency": "USD"},
    )
    assert r.status_code == 201
    t = r.json()
    assert t["target_currency"] == "INR"
    assert t["target_amount"] == "832.5"
    assert t["fx_snapshot_id"]
    assert bob.balances()["INR"] == "832.5"


def test_transfer_denominated_in_third_currency(make_user):
    alice, bob = make_user("USD"), make_user("GBP")
    alice.credit("200")
    r = alice.post(
        "/api/transfers",
        {
            "recipient_email": bob.email,
            "amount": "92",
            "currency": "EUR",
            "source_currency": "USD",
        },
    )
    t = r.json()
    assert r.status_code == 201, r.text
    assert t["source_amount"] == "100"  # 92 EUR -> 100 USD
    assert t["target_amount"] == "79"  # 92 EUR -> 79 GBP
    assert alice.balances()["USD"] == "100"


def test_transfer_errors(make_user):
    alice, bob = make_user("USD"), make_user("USD")
    alice.credit("10")
    cases = [
        (
            {"recipient_email": "ghost@example.com", "amount": "1", "currency": "USD"},
            404,
            "RECIPIENT_NOT_FOUND",
        ),
        (
            {"recipient_email": alice.email, "amount": "1", "currency": "USD"},
            422,
            "SELF_TRANSFER",
        ),
        (
            {"recipient_email": bob.email, "amount": "11", "currency": "USD"},
            422,
            "INSUFFICIENT_FUNDS",
        ),
        (
            {"recipient_email": bob.email, "amount": "1", "currency": "EUR"},
            404,
            "WALLET_NOT_FOUND",
        ),
    ]
    for body, status, code in cases:
        r = alice.post("/api/transfers", body)
        assert r.status_code == status, (body, r.text)
        assert r.json()["error"]["code"] == code
    assert alice.balances()["USD"] == "10"
    assert bob.balances()["USD"] == "0"


def test_transfer_idempotency(make_user):
    alice, bob = make_user("USD"), make_user("USD")
    alice.credit("100")
    body = {"recipient_email": bob.email, "amount": "25", "currency": "USD"}
    r1 = alice.post("/api/transfers", body, idem="transfer-abc-1")
    r2 = alice.post("/api/transfers", body, idem="transfer-abc-1")
    assert r1.status_code == 201 and r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
    assert alice.balances()["USD"] == "75"


def test_transfer_visible_to_both_parties_only(make_user):
    alice, bob, eve = make_user(), make_user(), make_user()
    alice.credit("10")
    tid = alice.post(
        "/api/transfers",
        {"recipient_email": bob.email, "amount": "1", "currency": "USD"},
    ).json()["id"]
    assert alice.get(f"/api/transfers/{tid}").status_code == 200
    assert bob.get(f"/api/transfers/{tid}").status_code == 200
    assert eve.get(f"/api/transfers/{tid}").status_code == 404


def test_concurrent_transfers_never_overdraw(app, make_user):
    """20 parallel transfers of 10 from a balance of 100: exactly 10 succeed and the
    total amount of money in the system is conserved."""
    alice, bob = make_user("USD"), make_user("USD")
    alice.credit("100")
    barrier = threading.Barrier(20)

    def send(_):
        with TestClient(app) as c:
            barrier.wait()
            return c.post(
                "/api/transfers",
                headers=alice.headers,
                json={"recipient_email": bob.email, "amount": "10", "currency": "USD"},
            ).status_code

    with ThreadPoolExecutor(max_workers=20) as pool:
        codes = list(pool.map(send, range(20)))
    assert codes.count(201) == 10
    assert codes.count(422) == 10
    assert alice.balances()["USD"] == "0"
    assert bob.balances()["USD"] == "100"


def test_concurrent_bidirectional_transfers_do_not_deadlock(app, make_user):
    alice, bob = make_user("USD"), make_user("USD")
    alice.credit("500")
    bob.credit("500")

    def send(i):
        a, b = (alice, bob) if i % 2 else (bob, alice)
        with TestClient(app) as c:
            return c.post(
                "/api/transfers",
                headers=a.headers,
                json={"recipient_email": b.email, "amount": "5", "currency": "USD"},
            ).status_code

    with ThreadPoolExecutor(max_workers=10) as pool:
        codes = list(pool.map(send, range(40)))
    assert codes == [201] * 40
    total = Decimal(alice.balances()["USD"]) + Decimal(bob.balances()["USD"])
    assert total == Decimal(1000)


def test_concurrent_same_idempotency_key_posts_once(app, make_user):
    alice, bob = make_user("USD"), make_user("USD")
    alice.credit("100")
    key = f"race-{uuid.uuid4().hex[:12]}"
    barrier = threading.Barrier(8)

    def send(_):
        with TestClient(app) as c:
            barrier.wait()
            r = c.post(
                "/api/transfers",
                headers={**alice.headers, "Idempotency-Key": key},
                json={"recipient_email": bob.email, "amount": "10", "currency": "USD"},
            )
            return r.status_code, r.json().get("id")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(send, range(8)))
    assert {code for code, _ in results} <= {200, 201}
    assert len({tid for _, tid in results}) == 1
    assert alice.balances()["USD"] == "90"
