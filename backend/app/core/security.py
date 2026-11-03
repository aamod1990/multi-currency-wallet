import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core.config import get_settings
from app.core.errors import Unauthorized


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


# Used to keep login timing constant when the user does not exist.
DUMMY_HASH = hash_password("timing-attack-mitigation")


def create_access_token(user_id: uuid.UUID) -> tuple[str, int]:
    s = get_settings()
    now = datetime.now(UTC)
    ttl = timedelta(minutes=s.access_token_ttl_minutes)
    claims = {
        "sub": str(user_id),
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
        "typ": "access",
    }
    return jwt.encode(claims, s.jwt_secret, algorithm=s.jwt_algorithm), int(ttl.total_seconds())


def decode_access_token(token: str) -> uuid.UUID:
    s = get_settings()
    try:
        claims = jwt.decode(
            token,
            s.jwt_secret,
            algorithms=[s.jwt_algorithm],
            options={"require": ["exp", "sub", "iat"]},
        )
        if claims.get("typ") != "access":
            raise Unauthorized("Invalid token type")
        return uuid.UUID(claims["sub"])
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Token expired", code="TOKEN_EXPIRED") from exc
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise Unauthorized("Invalid token") from exc
