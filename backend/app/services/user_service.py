import hashlib
import io
import logging

from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, Unauthorized, ValidationFailed
from app.core.metrics import AUTH_EVENTS
from app.core.security import DUMMY_HASH, hash_password, verify_password
from app.db.models import User, UserPhoto
from app.domain.money import validate_currency
from app.services import wallet_service

log = logging.getLogger(__name__)

Image.MAX_IMAGE_PIXELS = 25_000_000  # decompression-bomb guard
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}
PHOTO_MAX_SIDE = 512


def normalize_email(email: str) -> str:
    return email.strip().lower()


def get_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(func.lower(User.email) == normalize_email(email)))


def signup(
    db: Session, *, email: str, password: str, full_name: str, default_currency: str
) -> User:
    currency = validate_currency(default_currency)
    user = User(
        email=normalize_email(email),
        password_hash=hash_password(password),
        full_name=full_name.strip(),
        default_currency=currency,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        AUTH_EVENTS.labels(event="signup", outcome="duplicate").inc()
        raise Conflict("An account with this email already exists", code="EMAIL_TAKEN") from exc
    wallet_service.get_or_create_wallet(db, user.id, currency)
    db.commit()
    AUTH_EVENTS.labels(event="signup", outcome="success").inc()
    log.info("user_signed_up", extra={"new_user_id": str(user.id)})
    return user


def authenticate(db: Session, email: str, password: str) -> User:
    user = get_by_email(db, email)
    # Always run bcrypt so response time doesn't reveal whether the email exists.
    ok = verify_password(password, user.password_hash if user else DUMMY_HASH)
    if not user or not ok:
        AUTH_EVENTS.labels(event="login", outcome="failure").inc()
        raise Unauthorized("Invalid email or password", code="INVALID_CREDENTIALS")
    AUTH_EVENTS.labels(event="login", outcome="success").inc()
    return user


def update_profile(
    db: Session, user: User, *, full_name: str | None, default_currency: str | None
) -> User:
    if full_name is not None:
        user.full_name = full_name.strip()
    if default_currency is not None:
        user.default_currency = validate_currency(default_currency)
        wallet_service.get_or_create_wallet(db, user.id, user.default_currency)
    db.commit()
    db.refresh(user)
    return user


def set_photo(db: Session, user: User, data: bytes) -> UserPhoto:
    """Validate by decoding (never trust Content-Type), then re-encode: this strips
    EXIF/GPS metadata and neutralises polyglot files."""
    if not data:
        raise ValidationFailed("Empty file")
    if len(data) > get_settings().max_photo_bytes:
        raise ValidationFailed("Photo exceeds the 2 MB limit", code="FILE_TOO_LARGE")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = probe.format
            probe.verify()
        if fmt not in ALLOWED_IMAGE_FORMATS:
            raise ValidationFailed("Only JPEG, PNG or WEBP images are allowed")
        with Image.open(io.BytesIO(data)) as img:
            img.thumbnail((PHOTO_MAX_SIDE, PHOTO_MAX_SIDE))
            has_alpha = img.mode in ("RGBA", "LA", "P")
            out = io.BytesIO()
            if has_alpha:
                img.convert("RGBA").save(out, format="PNG", optimize=True)
                content_type = "image/png"
            else:
                img.convert("RGB").save(out, format="JPEG", quality=85)
                content_type = "image/jpeg"
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError) as exc:
        raise ValidationFailed("File is not a valid image") from exc

    encoded = out.getvalue()
    photo = db.get(UserPhoto, user.id) or UserPhoto(user_id=user.id)
    photo.data = encoded
    photo.content_type = content_type
    photo.etag = hashlib.sha256(encoded).hexdigest()[:32]
    db.add(photo)
    db.commit()
    return photo


def get_photo(db: Session, user_id) -> UserPhoto:
    photo = db.get(UserPhoto, user_id)
    if photo is None:
        raise NotFound("No profile photo")
    return photo


def delete_photo(db: Session, user: User) -> None:
    photo = db.get(UserPhoto, user.id)
    if photo:
        db.delete(photo)
        db.commit()


def has_photo(db: Session, user_id) -> bool:
    return (db.scalar(select(func.count()).where(UserPhoto.user_id == user_id)) or 0) > 0
