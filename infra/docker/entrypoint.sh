#!/bin/sh
# Apply migrations (idempotent; Alembic takes a lock on the version table), then serve.
set -eu
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  alembic upgrade head
fi
exec uvicorn app.asgi:app --host 0.0.0.0 --port "${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-1}" --proxy-headers --forwarded-allow-ips='*' --no-access-log
