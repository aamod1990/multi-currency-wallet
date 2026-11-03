import uuid
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import Unauthorized, ValidationFailed
from app.core.logging import user_id_ctx
from app.core.security import decode_access_token
from app.db.models import User
from app.db.session import get_db

bearer = HTTPBearer(auto_error=False)
DB = Annotated[Session, Depends(get_db)]


def get_current_user(
    request: Request,
    db: DB,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise Unauthorized("Not authenticated")
    user_id: uuid.UUID = decode_access_token(creds.credentials)
    user = db.get(User, user_id)
    if user is None:
        raise Unauthorized("User no longer exists")
    user_id_ctx.set(str(user.id))
    request.state.user_id = str(user.id)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def idempotency_key(
    key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str | None:
    if key is None:
        return None
    key = key.strip()
    if not (8 <= len(key) <= 100) or not all(c.isalnum() or c in "-_:" for c in key):
        raise ValidationFailed("Idempotency-Key must be 8-100 chars of [A-Za-z0-9-_:]")
    return key


IdempotencyKey = Annotated[str | None, Depends(idempotency_key)]
