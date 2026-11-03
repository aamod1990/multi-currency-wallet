# Multi-Currency Wallet Platform

Hold, convert and send money across 32 currencies. FastAPI + PostgreSQL backend, React + TypeScript frontend, shipped as **one Docker image**.

- **Auth & profile** – signup / login (JWT), editable name, default currency and profile photo.
- **Wallets** – credit / debit *any* currency into *any* wallet with automatic conversion; exchange between own wallets.
- **FX** – live rates from [open.er-api.com](https://open.er-api.com) with [Frankfurter](https://frankfurter.app) fallback, refreshed on a schedule; every converted transaction links to the immutable rate snapshot it used.
- **Transfers** – user-to-user, cross-currency (sender picks the paying wallet, recipient is credited in their default currency), atomic and idempotent.
- **History** – filter by type / currency / direction / date / amount, paginated, with a detail view showing the FX audit trail.
- **Ops** – JSON logs with request IDs, Prometheus metrics, liveness/readiness probes, in-app 4XX/5XX spike detector + Prometheus alert rules, Grafana dashboard.

Design rationale: [ARCHITECTURE.md](ARCHITECTURE.md). How AI was used: [AI_USAGE.md](AI_USAGE.md).

**Public URL:** _see the PR / repo description – populated once the Render blueprint is deployed (see [Deployment](#deployment))._

---

## Repository layout

```
backend/          FastAPI app (app/), Alembic migrations, pyproject
  app/api/        HTTP layer: routers, schemas (validation), dependencies
  app/services/   Use cases: wallet, transfer, fx, history, user
  app/domain/     Pure domain: money/currency rules, enums
  app/db/         SQLAlchemy models & session
  app/fx/         Third-party FX providers (+ fallback chain, static provider)
  app/core/       Config, security, logging, metrics, error monitor, middleware
frontend/         React + Vite + TanStack Query SPA (src/, vitest tests in src/test)
tests/unit        Pure unit tests (money, FX parsing, security, error monitor, config)
tests/integration API tests against real PostgreSQL (incl. concurrency)
infra/            Docker entrypoint, Prometheus config + alerts, Grafana, smoke test
render.yaml       Render Blueprint (web service + managed Postgres)
.github/workflows CI/CD pipeline
```

## Setup

Prerequisites: Docker 24+ (that's all you need to run it). For local development: Python 3.12, Node 20, PostgreSQL 16 (or the compose `db` service).

```bash
git clone https://github.com/aamod1990/multi-currency-wallet.git
cd multi-currency-wallet
```

Configuration is environment-driven (`backend/app/core/config.py`). Most relevant variables:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://wallet:wallet@localhost:5432/wallet` | `postgres://` URLs are normalised automatically |
| `JWT_SECRET` | dev placeholder | **must** be set (≥32 chars) in production |
| `ACCESS_TOKEN_TTL_MINUTES` | `60` | JWT lifetime |
| `FX_PROVIDER` | `live` | `live` (open.er-api → Frankfurter) or `static` (offline/tests) |
| `FX_REFRESH_INTERVAL_SECONDS` | `3600` | background refresh cadence |
| `FX_MAX_AGE_SECONDS` | `21600` | conversions are refused if the newest snapshot is older |
| `CORS_ORIGINS` | `http://localhost:5173` | comma-separated; not needed for same-origin deployment |
| `METRICS_TOKEN` | unset | if set, `/metrics` requires `Authorization: Bearer <token>` |
| `ERROR_MONITOR_5XX_THRESHOLD` / `_4XX_THRESHOLD` | `0.05` / `0.30` | in-app spike detector |
| `ALERT_WEBHOOK_URL` | unset | Slack-compatible webhook for error-rate alerts |

## Run

### Everything in Docker (recommended)

```bash
docker compose up --build                     # app + Postgres → http://localhost:8000
docker compose --profile monitoring up --build  # + Prometheus :9090, Grafana :3000
./infra/scripts/smoke_test.sh http://localhost:8000   # end-to-end API smoke test
```

The container runs `alembic upgrade head` on start, then serves the API (`/api/*`), the SPA (`/`), health (`/health/*`) and metrics (`/metrics`). OpenAPI docs: http://localhost:8000/docs. Set `FX_PROVIDER=static` to run fully offline. If port 5432 is taken, `DB_PORT=5433 docker compose up`.

### Local development

```bash
# database
docker compose up -d db

# backend (http://localhost:8000)
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e "backend[dev]"
cd backend && alembic upgrade head && uvicorn app.asgi:app --reload

# frontend (http://localhost:5173, proxies /api to :8000)
cd frontend && npm ci && npm run dev
```

### Tests & checks

```bash
# backend: lint, types, unit + integration (integration needs a Postgres DB)
docker exec -it multi-currency-wallet-db-1 createdb -U wallet wallet_test
export TEST_DATABASE_URL=postgresql+psycopg://wallet:wallet@localhost:5432/wallet_test
ruff check backend tests && (cd backend && mypy app) && pytest --cov=app

# frontend
cd frontend && npm run lint && npm test && npm run build
```

Integration tests skip automatically when `TEST_DATABASE_URL` is unset. They use a real PostgreSQL because row locks, CHECK constraints and `ON CONFLICT` semantics are the point and SQLite can't emulate them.

## Deployment

**Target: Render** (free Docker web service + managed PostgreSQL), defined as code in [`render.yaml`](render.yaml).

1. Render dashboard → **New → Blueprint** → select this repo. Render provisions `wallet-db`, builds the `Dockerfile`, generates `JWT_SECRET` / `METRICS_TOKEN`, and uses `/health/ready` as the health check (failed readiness blocks a bad deploy from taking traffic).
2. Copy the service's **Deploy Hook URL** into the GitHub secret `RENDER_DEPLOY_HOOK_URL`, and set the repo variable `PUBLIC_URL` (e.g. `https://multi-currency-wallet.onrender.com`).

**CI/CD** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) on every PR and push to `main`:

| Job | What |
|---|---|
| `backend` | ruff lint + format check, mypy, pytest (unit + integration against a Postgres service), coverage artifact |
| `frontend` | oxlint, vitest, `tsc` + Vite production build |
| `docker` | builds the production image, `docker compose up --wait` (health-gated), runs `infra/scripts/smoke_test.sh` |
| `deploy` | `main` only, after all of the above: triggers the Render deploy hook, then polls `$PUBLIC_URL/health/ready` until healthy |

`autoDeploy` is off in `render.yaml` so only commits that passed CI reach production.

Any Docker host works too: the image needs only `DATABASE_URL` and `JWT_SECRET`, listens on `$PORT`, and migrates on boot.

## Observability & 4XX/5XX detection

- **Logs** – one JSON object per line (`ts`, `level`, `msg`, `request_id`, `user_id`, route, status, latency). `X-Request-ID` is accepted/echoed and included in every error body, so a user-reported error maps straight to log lines.
- **Health** – `/health/live` (process up; for restarts) and `/health/ready` (DB reachable → else 503; FX snapshot freshness and current error ratios reported as `degraded`).
- **Metrics** – `/metrics` (Prometheus): `http_requests_total{route,status,status_class}`, latency histogram, in-flight gauge, unhandled exceptions, wallet ops / transfers / auth events by outcome, FX refresh outcomes and last-success timestamp. Route labels use templates (`/api/transfers/{transfer_id}`) to keep cardinality bounded.
- **Detecting increased 4XX/5XX – two layers:**
  1. **Prometheus rules** ([`infra/prometheus/alerts.yml`](infra/prometheus/alerts.yml)): 5XX ratio > 5% for 5 min (page); 4XX ratio > 30% for 10 min (ticket); 4XX ratio 3× above the same time yesterday (catches regressions below the absolute threshold); login-failure bursts; FX staleness; p95 latency; scrape down. All ratio-based with minimum-traffic guards.
  2. **In-app rolling-window monitor** (`app/core/error_monitor.py`) for environments without a Prometheus server (e.g. Render free tier): tracks 4XX/5XX ratios over the last 5 min, logs a structured `http_error_rate_alert` and POSTs to `ALERT_WEBHOOK_URL` when thresholds are crossed (with cooldown), and exposes the ratios in `/health/ready` and as `app_http_error_ratio`.
- **Dashboard** – `infra/grafana/dashboards/wallet-overview.json` (traffic by status class, error ratios vs. thresholds, latency percentiles, top failing routes, business ops, FX age).

## API overview

All endpoints under `/api`, JSON, `Authorization: Bearer <jwt>` except signup/login. Errors use one envelope: `{"error": {"code", "message", "request_id", "details"}}`. Amounts are **decimal strings** (never JSON floats). Mutations accept an optional `Idempotency-Key` header.

| Method & path | Description |
|---|---|
| `POST /auth/signup`, `POST /auth/login` | returns `{access_token, user}`; login is rate-limited per IP+email |
| `GET/PATCH /users/me` | profile (name, default currency) |
| `PUT/GET/DELETE /users/me/photo` | multipart upload (JPEG/PNG/WEBP ≤ 2 MB, re-encoded, EXIF stripped) |
| `GET /wallets`, `POST /wallets` | list (with total in default currency) / open a wallet |
| `POST /wallets/credit`, `/debit` | `{amount, currency, wallet_currency?}` – converts if they differ |
| `POST /wallets/exchange` | `{amount, from_currency, to_currency}` between own wallets |
| `POST /transfers`, `GET /transfers/{id}` | `{recipient_email, amount, currency, source_currency?, note?}` |
| `GET /transactions` | filters `type` (repeatable), `currency`, `direction=in\|out`, `date_from`, `date_to`, `min_amount`, `max_amount`; `page`, `page_size` |
| `GET /transactions/{id}` | entry + FX snapshot used |
| `GET /fx/currencies`, `/fx/rates`, `/fx/quote`, `/fx/snapshots/{id}` | rates, quotes, audit |

## Assumptions

- Credit/debit simulate external money movement (top-up / withdrawal); there is no real payment rail, KYC or card processing.
- A user has at most one wallet per currency; the default-currency wallet is created at signup, others on first credit, exchange or incoming transfer.
- Incoming transfers land in the **recipient's default currency** (the sender chooses the amount, its currency and which of their wallets pays).
- Rates are mid-market from the provider; no spread or fees are charged. All conversions go through a USD-based snapshot (cross rates computed as `rate[target]/rate[source]`).
- Amounts are rounded to the currency's minor unit (ISO 4217: JPY 0, KWD/BHD 3, others 2) with banker's rounding; rates are stored to 10 decimal places.
- Converting with rates older than `FX_MAX_AGE_SECONDS` (6 h) is unsafe, so such operations fail with `503 FX_UNAVAILABLE`; same-currency operations keep working.
- Email is the user identifier for transfers (case-insensitive).

## Trade-offs

| Decision | Why | Cost |
|---|---|---|
| Single deployable (FastAPI serves the SPA) | one image, same-origin (no CORS, simpler CSP), one Render service | frontend and backend release together; no CDN for static assets |
| PostgreSQL row locks (`SELECT … FOR UPDATE`, stable lock order) | simplest correct concurrency for balances; DB `CHECK (balance >= 0)` as a backstop | hot wallets serialize; fine at this scale, would need sharding/queueing at very high write rates |
| Ledger + materialized balance | O(1) balance reads, full audit trail with `balance_after` per entry | balance and ledger must be written in the same transaction (they are) |
| Synchronous transfers in one DB transaction | strong consistency, no saga/compensation needed – both users share a DB | does not generalize to cross-service or external transfers (would need an outbox/saga) |
| Immutable FX snapshots referenced by entries | complete conversion traceability and reproducibility | one row per refresh (tiny: ~32 rates/hour) |
| Background refresh thread + Postgres advisory lock | no extra scheduler infrastructure; only one replica refreshes | coupled to API process lifecycle; a dedicated worker/cron would be cleaner at scale |
| JWT access tokens in `localStorage` | stateless, simple SPA auth | no server-side revocation; XSS could read the token (mitigated by strict CSP and React escaping). Next step: short-lived access + httpOnly refresh cookie |
| Photos stored in Postgres (bytea, ≤ 2 MB after re-encode) | no object storage dependency, transactional with the user | DB bloat at scale; S3 + CDN would be the production path |
| In-process rate limiter & error monitor | zero dependencies, works on the free tier | per-replica state; Redis / Prometheus would be the multi-replica answer |
| Sync SQLAlchemy in threadpool | simpler, mature psycopg3 driver, lock semantics are explicit | lower peak concurrency than full async |

## Known limitations

- **Public deployment** depends on connecting the Render Blueprint (one-time, needs a Render account); the free tier sleeps after inactivity (first request ~30–50 s) and free Postgres instances expire after 30 days.
- No refresh tokens, logout-everywhere, email verification, password reset or MFA.
- Rate limiting and the in-app error monitor are per-process (not shared across replicas).
- No fees, spreads, limits per day, or AML checks; amounts are capped by `MAX_TRANSACTION_AMOUNT`.
- FX providers are free tiers without SLA; if both fail beyond `FX_MAX_AGE_SECONDS`, cross-currency operations are rejected until rates recover.
- Transfers are only between users of this platform; no reversals/refunds flow (ledger is append-only – a reversal would be a new compensating entry).
- Frontend tests cover the API client, validation and auth flows; the remaining pages are verified manually/E2E rather than with component tests.
- No end-to-end browser test suite in CI (smoke test covers the API path through the production image).
