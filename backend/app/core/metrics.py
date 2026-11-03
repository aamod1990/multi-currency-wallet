from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter(
    "http_requests_total", "HTTP requests", ["method", "route", "status", "status_class"]
)
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
HTTP_IN_PROGRESS = Gauge("http_requests_in_progress", "In-flight HTTP requests")
UNHANDLED_EXCEPTIONS = Counter("app_unhandled_exceptions_total", "Unhandled exceptions", ["type"])

WALLET_OPERATIONS = Counter(
    "wallet_operations_total", "Wallet operations", ["operation", "outcome"]
)
TRANSFERS = Counter("transfers_total", "User-to-user transfers", ["outcome", "cross_currency"])
AUTH_EVENTS = Counter("auth_events_total", "Authentication events", ["event", "outcome"])

FX_REFRESH = Counter("fx_refresh_total", "FX refresh attempts", ["outcome"])
FX_LAST_SUCCESS = Gauge(
    "fx_rates_last_success_timestamp_seconds", "Unix time of the latest stored FX snapshot"
)
ERROR_RATE = Gauge(
    "app_http_error_ratio", "Rolling-window HTTP error ratio (in-app monitor)", ["status_class"]
)
