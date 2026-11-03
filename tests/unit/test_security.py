import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import get_settings
from app.core.errors import RateLimited, Unauthorized
from app.core.rate_limit import SlidingWindowLimiter
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_hash_round_trip():
    h = hash_password("s3cretpass")
    assert h != "s3cretpass"
    assert verify_password("s3cretpass", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "not-a-hash")


def test_token_round_trip():
    uid = uuid.uuid4()
    token, ttl = create_access_token(uid)
    assert ttl > 0
    assert decode_access_token(token) == uid


def test_expired_token_rejected():
    s = get_settings()
    past = datetime.now(UTC) - timedelta(hours=2)
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "iat": past,
            "exp": past + timedelta(minutes=1),
            "typ": "access",
        },
        s.jwt_secret,
        algorithm=s.jwt_algorithm,
    )
    with pytest.raises(Unauthorized) as exc:
        decode_access_token(token)
    assert exc.value.code == "TOKEN_EXPIRED"


@pytest.mark.parametrize(
    "token",
    [
        "garbage",
        jwt.encode(
            {"sub": str(uuid.uuid4()), "exp": 9999999999, "iat": 0, "typ": "access"},
            "wrong-secret-wrong-secret-1234567",
            algorithm="HS256",
        ),
        jwt.encode(
            {"sub": str(uuid.uuid4()), "exp": 9999999999, "iat": 0, "typ": "access"},
            key=None,
            algorithm="none",
        ),
    ],
    ids=["garbage", "wrong-secret", "alg-none"],
)
def test_invalid_tokens_rejected(token):
    with pytest.raises(Unauthorized):
        decode_access_token(token)


def test_rate_limiter_blocks_after_limit():
    limiter = SlidingWindowLimiter(limit=2, window_seconds=60)
    limiter.hit("k")
    limiter.hit("k")
    with pytest.raises(RateLimited):
        limiter.hit("k")
    limiter.hit("other")
