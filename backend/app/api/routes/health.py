from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import DB
from app.core.config import get_settings
from app.services import fx_service

router = APIRouter(tags=["health"])


@router.get("/health/live")
def live() -> dict[str, str]:
    """Liveness: the process is up and serving. No dependencies checked."""
    return {"status": "ok"}


@router.get("/health/ready")
def ready(request: Request, db: DB) -> JSONResponse:
    """Readiness: DB reachable (hard requirement) + FX freshness (soft, 'degraded')."""
    s = get_settings()
    checks: dict[str, dict[str, object]] = {}
    healthy = True
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = {"status": "ok"}
    except Exception as exc:
        healthy = False
        checks["database"] = {"status": "error", "error": type(exc).__name__}

    degraded = False
    if healthy:
        snap = fx_service.latest_snapshot(db)
        if snap is None:
            degraded = True
            checks["fx_rates"] = {"status": "missing"}
        else:
            age = (datetime.now(UTC) - snap.fetched_at).total_seconds()
            stale = age > s.fx_max_age_seconds
            degraded |= stale
            checks["fx_rates"] = {
                "status": "stale" if stale else "ok",
                "provider": snap.provider,
                "age_seconds": int(age),
            }
    monitor = getattr(request.app.state, "error_monitor", None)
    if monitor:
        checks["http_errors"] = monitor.snapshot()

    overall = "error" if not healthy else ("degraded" if degraded else "ok")
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": overall, "version": s.version, "checks": checks},
    )
