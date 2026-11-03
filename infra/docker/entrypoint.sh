#!/bin/sh
# Collections, validators and indexes are ensured idempotently at app startup.
set -eu
exec uvicorn app.asgi:app --host 0.0.0.0 --port "${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-1}" --proxy-headers --forwarded-allow-ips='*' --no-access-log
