from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, sourced from environment variables (12-factor)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "multi-currency-wallet"
    environment: Literal["local", "test", "staging", "production"] = "local"
    version: str = "1.0.0"
    log_level: str = "INFO"
    log_json: bool = True

    database_url: str = "postgresql+psycopg://wallet:wallet@localhost:5432/wallet"
    db_pool_size: int = 5
    db_max_overflow: int = 10

    jwt_secret: str = Field(default="change-me-in-production-please-32b+", min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # FX
    fx_provider: Literal["live", "static"] = "live"
    fx_refresh_enabled: bool = True
    fx_refresh_interval_seconds: int = 3600
    fx_max_age_seconds: int = 6 * 3600
    fx_http_timeout_seconds: float = 5.0
    fx_base_currency: str = "USD"

    # Limits
    max_transaction_amount: int = 1_000_000_000
    max_photo_bytes: int = 2 * 1024 * 1024
    login_rate_limit_per_minute: int = 10

    # Observability
    metrics_token: str | None = None
    error_monitor_window_seconds: int = 300
    error_monitor_min_requests: int = 20
    error_monitor_5xx_threshold: float = 0.05
    error_monitor_4xx_threshold: float = 0.30
    error_monitor_cooldown_seconds: int = 600
    alert_webhook_url: str | None = None

    frontend_dist: str = "/app/frontend_dist"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str) and not v.startswith("["):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        # Managed providers (Render, Heroku) hand out postgres:// URLs.
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix) :]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
