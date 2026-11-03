import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import auth, fx, health, transactions, transfers, users, wallets
from app.core.config import Settings, get_settings
from app.core.error_monitor import ErrorRateMonitor, MonitorConfig
from app.core.errors import AppError
from app.core.logging import configure_logging, request_id_ctx
from app.core.middleware import install_observability
from app.db.session import session_factory
from app.fx.factory import build_provider
from app.services.fx_refresher import FxRefresher

log = logging.getLogger("app")


def _error(status: int, code: str, message: str, details=None) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "request_id": request_id_ctx.get()}}
    if details is not None:
        body["error"]["details"] = jsonable_encoder(details)
    return JSONResponse(status_code=status, content=body)


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError):
        return _error(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        details = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]}
            for e in exc.errors()
        ]
        return _error(422, "VALIDATION_ERROR", "Request validation failed", details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return _error(exc.status_code, code, str(exc.detail))


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    """Serve the built SPA from the same origin (no CORS, one deployable)."""
    if not (dist / "index.html").exists():
        return
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    index = dist / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith(("api/", "health/", "metrics")):
            return _error(404, "NOT_FOUND", "Not Found")
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def create_app(settings: Settings | None = None, *, start_background: bool = True) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        refresher = None
        if start_background and settings.fx_refresh_enabled:
            refresher = FxRefresher(
                build_provider(settings), session_factory(), settings.fx_refresh_interval_seconds
            )
            refresher.start()
        log.info(
            "startup", extra={"environment": settings.environment, "version": settings.version}
        )
        yield
        if refresher:
            refresher.stop()
        log.info("shutdown")

    app = FastAPI(
        title="Multi-Currency Wallet API",
        version=settings.version,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    monitor = ErrorRateMonitor(
        MonitorConfig(
            window_seconds=settings.error_monitor_window_seconds,
            min_requests=settings.error_monitor_min_requests,
            threshold_5xx=settings.error_monitor_5xx_threshold,
            threshold_4xx=settings.error_monitor_4xx_threshold,
            cooldown_seconds=settings.error_monitor_cooldown_seconds,
            webhook_url=settings.alert_webhook_url,
        )
    )
    app.state.error_monitor = monitor
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Idempotent-Replayed"],
    )
    install_observability(app, monitor)
    _install_error_handlers(app)

    api = APIRouter(prefix="/api")
    for r in (auth, users, wallets, transfers, transactions, fx):
        api.include_router(r.router)
    app.include_router(api)
    app.include_router(health.router)

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request):
        expected = f"Bearer {settings.metrics_token}"
        supplied = request.headers.get("authorization", "")
        if settings.metrics_token and not hmac.compare_digest(supplied, expected):
            return _error(401, "UNAUTHORIZED", "Metrics token required")
        return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    _mount_frontend(app, Path(settings.frontend_dist))
    return app
