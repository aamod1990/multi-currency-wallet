from fastapi import APIRouter, Request, status

from app.api.deps import DB
from app.api.schemas import LoginRequest, SignupRequest, TokenResponse, UserOut
from app.core.config import get_settings
from app.core.rate_limit import SlidingWindowLimiter
from app.core.security import create_access_token
from app.services import user_service

router = APIRouter(prefix="/auth", tags=["auth"])
login_limiter = SlidingWindowLimiter(get_settings().login_rate_limit_per_minute)


def _token_response(user, has_photo: bool = False) -> TokenResponse:
    token, ttl = create_access_token(user.id)
    out = UserOut.model_validate(user).model_copy(update={"has_photo": has_photo})
    return TokenResponse(access_token=token, expires_in=ttl, user=out)


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def signup(body: SignupRequest, request: Request, db: DB) -> TokenResponse:
    login_limiter.hit(f"signup:{request.client.host if request.client else '-'}")
    user = user_service.signup(
        db,
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        default_currency=body.default_currency,
    )
    return _token_response(user)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, db: DB) -> TokenResponse:
    ip = request.client.host if request.client else "-"
    login_limiter.hit(f"login:{ip}:{body.email.lower()}")
    user = user_service.authenticate(db, body.email, body.password)
    return _token_response(user, user_service.has_photo(db, user.id))
