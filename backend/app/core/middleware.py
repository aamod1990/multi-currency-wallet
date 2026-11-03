import logging
import re
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.error_monitor import ErrorRateMonitor
from app.core.logging import request_id_ctx, user_id_ctx
from app.core.metrics import HTTP_IN_PROGRESS, HTTP_LATENCY, HTTP_REQUESTS, UNHANDLED_EXCEPTIONS

log = logging.getLogger("app.access")
_REQ_ID_RE = re.compile(r"^[A-Za-z0-9\-_.]{1,64}$")
_SKIP_ACCESS_LOG = {"/health/live", "/health/ready", "/metrics"}

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
    ),
}


def _route_template(request: Request) -> str:
    """Use the route template (e.g. /api/transfers/{transfer_id}) as the metric label
    to keep cardinality bounded."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return path
    return "/api/unmatched" if request.url.path.startswith("/api") else "/static"


def install_observability(app: FastAPI, monitor: ErrorRateMonitor) -> None:
    @app.middleware("http")
    async def observe(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _REQ_ID_RE.match(incoming) else uuid.uuid4().hex
        rid_token = request_id_ctx.set(request_id)
        uid_token = user_id_ctx.set(None)
        start = time.perf_counter()
        HTTP_IN_PROGRESS.inc()
        try:
            try:
                response = await call_next(request)
            except Exception as exc:
                UNHANDLED_EXCEPTIONS.labels(type=type(exc).__name__).inc()
                log.exception("unhandled_exception", extra={"path": request.url.path})
                response = JSONResponse(
                    status_code=500,
                    content={
                        "error": {
                            "code": "INTERNAL_ERROR",
                            "message": "An unexpected error occurred",
                            "request_id": request_id,
                        }
                    },
                )
            duration = time.perf_counter() - start
            route = _route_template(request)
            status = response.status_code
            HTTP_REQUESTS.labels(request.method, route, str(status), f"{status // 100}xx").inc()
            HTTP_LATENCY.labels(request.method, route).observe(duration)
            if route not in _SKIP_ACCESS_LOG and route != "/static":
                monitor.record(status)
                level = logging.WARNING if status >= 500 else logging.INFO
                log.log(
                    level,
                    "request",
                    extra={
                        "method": request.method,
                        "path": request.url.path,
                        "route": route,
                        "status": status,
                        "duration_ms": round(duration * 1000, 2),
                        "client_ip": request.client.host if request.client else None,
                        "user_id": getattr(request.state, "user_id", None),
                    },
                )
            response.headers["X-Request-ID"] = request_id
            for k, v in SECURITY_HEADERS.items():
                response.headers.setdefault(k, v)
            return response
        finally:
            HTTP_IN_PROGRESS.dec()
            request_id_ctx.reset(rid_token)
            user_id_ctx.reset(uid_token)
