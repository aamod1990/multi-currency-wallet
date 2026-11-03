import io

from PIL import Image


def _png(size=(800, 600), mode="RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color=(10, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


def test_signup_returns_token_and_creates_default_wallet(make_user):
    u = make_user("EUR")
    assert u.balances() == {"EUR": "0"}
    me = u.get("/api/users/me").json()
    assert me["default_currency"] == "EUR"
    assert me["has_photo"] is False
    assert "password_hash" not in me


def test_signup_duplicate_email_case_insensitive(client, make_user):
    make_user(email="dup@example.com")
    r = client.post("/api/auth/signup", json={
        "email": "DUP@example.com", "password": "Passw0rd!", "full_name": "X"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "EMAIL_TAKEN"


def test_signup_validation(client):
    r = client.post("/api/auth/signup", json={
        "email": "not-an-email", "password": "short", "full_name": "", "default_currency": "ZZZ"})
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    fields = {d["field"] for d in body["details"]}
    assert {"email", "password", "full_name"} <= fields


def test_signup_unsupported_currency(client):
    r = client.post("/api/auth/signup", json={
        "email": "c@example.com", "password": "Passw0rd!", "full_name": "C",
        "default_currency": "XYZ"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "UNSUPPORTED_CURRENCY"


def test_login_success_and_failure(client, make_user):
    u = make_user()
    ok = client.post("/api/auth/login", json={"email": u.email.upper(), "password": "Passw0rd!"})
    assert ok.status_code == 200
    assert ok.json()["access_token"]
    bad = client.post("/api/auth/login", json={"email": u.email, "password": "nope"})
    assert bad.status_code == 401
    unknown = client.post("/api/auth/login", json={"email": "x@y.io", "password": "nope"})
    assert unknown.status_code == 401
    assert bad.json()["error"]["message"] == unknown.json()["error"]["message"]


def test_login_rate_limited(client, make_user):
    u = make_user()
    codes = [
        client.post("/api/auth/login", json={"email": u.email, "password": "bad"}).status_code
        for _ in range(12)
    ]
    assert codes[-1] == 429


def test_protected_routes_require_auth(client):
    for path in ("/api/users/me", "/api/wallets", "/api/transactions"):
        r = client.get(path)
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "UNAUTHORIZED"
    r = client.get("/api/users/me", headers={"Authorization": "Bearer junk"})
    assert r.status_code == 401


def test_update_profile_creates_wallet_for_new_default_currency(make_user):
    u = make_user("USD")
    r = u.patch("/api/users/me", {"full_name": "Renamed", "default_currency": "gbp"})
    assert r.status_code == 200
    assert r.json()["full_name"] == "Renamed"
    assert r.json()["default_currency"] == "GBP"
    assert set(u.balances()) == {"USD", "GBP"}


def test_photo_upload_resizes_and_strips(make_user):
    u = make_user()
    r = u.put("/api/users/me/photo", files={"file": ("me.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["has_photo"] is True
    photo = u.get("/api/users/me/photo")
    assert photo.status_code == 200
    assert photo.headers["content-type"] == "image/jpeg"
    img = Image.open(io.BytesIO(photo.content))
    assert max(img.size) <= 512
    etag = photo.headers["etag"]
    assert u.get("/api/users/me/photo", headers={"If-None-Match": etag}).status_code == 304
    assert u.delete("/api/users/me/photo").status_code == 204
    assert u.get("/api/users/me/photo").status_code == 404


def test_photo_rejects_non_images_and_large_files(make_user):
    u = make_user()
    fake = u.put("/api/users/me/photo", files={"file": ("x.png", b"<?php evil", "image/png")})
    assert fake.status_code == 422
    big = u.put("/api/users/me/photo",
                files={"file": ("x.png", b"0" * (2 * 1024 * 1024 + 10), "image/png")})
    assert big.status_code == 422
    assert big.json()["error"]["code"] == "FILE_TOO_LARGE"
