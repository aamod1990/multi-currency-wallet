from typing import Any


class AppError(Exception):
    """Base for expected, client-visible errors. Mapped to a JSON error envelope."""

    status_code = 400
    code = "BAD_REQUEST"

    def __init__(self, message: str, *, details: Any = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.details = details
        if code:
            self.code = code


class ValidationFailed(AppError):
    status_code = 422
    code = "VALIDATION_ERROR"


class Unauthorized(AppError):
    status_code = 401
    code = "UNAUTHORIZED"


class Forbidden(AppError):
    status_code = 403
    code = "FORBIDDEN"


class NotFound(AppError):
    status_code = 404
    code = "NOT_FOUND"


class Conflict(AppError):
    status_code = 409
    code = "CONFLICT"


class InsufficientFunds(AppError):
    status_code = 422
    code = "INSUFFICIENT_FUNDS"


class UnsupportedCurrency(AppError):
    status_code = 422
    code = "UNSUPPORTED_CURRENCY"


class RateLimited(AppError):
    status_code = 429
    code = "RATE_LIMITED"


class FxUnavailable(AppError):
    status_code = 503
    code = "FX_RATES_UNAVAILABLE"
