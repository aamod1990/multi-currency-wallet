# Multi-Currency Wallet Platform

Hold, convert and send money across 32 currencies. FastAPI + MongoDB backend, React + TypeScript frontend, shipped as **one Docker image**.

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
backend/          FastAPI app (app/), pyproject
  app/api/        HTTP layer: routers, schemas (validation), dependencies
  app/services/   Use cases: wallet, transfer, fx, history, user
  app/domain/     Pure domain: money/currency rules, enums
  app/db/         MongoDB client, codecs, schema (validators + indexes), document models
  app/fx/         Third-party FX providers (+ fallback chain, static provider)
  app/core/       Config, security, logging, metrics, error monitor, middleware
frontend/         React + Vite + TanStack Query SPA (src/, vitest tests in src/test)
tests/unit        Pure unit tests (money, FX parsing, security, error monitor, config)
tests/integration API tests against a real MongoDB replica set (incl. concurrency)
infra/            Docker entrypoint, Prometheus config + alerts, Grafana, smoke test
render.yaml       Render Blueprint (web service; MongoDB on Atlas)
.github/workflows CI/CD pipeline
```

## Setup

Prerequisites: Docker 24+ (that's all you need to run it). Without Docker: Python 3.12, Node 20 and MongoDB 7 running as a single-node replica set (see [Run without Docker](#run-without-docker)).

```bash
git clone https://github.com/aamod1990/multi-currency-wallet.git
cd multi-currency-wallet
```

Configuration is environment-driven (`backend/app/core/config.py`). Most relevant variables:

| Variable | Default | Purpose |
|---|---|---|
| `MONGODB_URL` | `mongodb://localhost:27017/?directConnection=true` | must point to a replica set (transactions); Atlas `mongodb+srv://` URLs work as-is |
| `MONGODB_DB` | `wallet` | database name |
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
docker compose up --build                     # app + MongoDB → http://localhost:8000
docker compose --profile monitoring up --build  # + Prometheus :9090, Grafana :3000
./infra/scripts/smoke_test.sh http://localhost:8000   # end-to-end API smoke test
```

On start the app idempotently creates collections, validators and indexes, then serves the API (`/api/*`), the SPA (`/`), health (`/health/*`) and metrics (`/metrics`). OpenAPI docs: http://localhost:8000/docs. Set `FX_PROVIDER=static` to run fully offline. If port 27017 is taken, `MONGO_PORT=27018 docker compose up`. The compose `mongo` service initiates its single-node replica set automatically.

### Run without Docker

1. **Install MongoDB 7** (Community Server): Windows/macOS/Linux installers at mongodb.com/try/download/community, or `brew tap mongodb/brew && brew install mongodb-community@7.0` on macOS. Also install `mongosh`.
2. **Start it as a single-node replica set** (required for transactions) — stop any MongoDB service already running on 27017 first:
   ```bash
   mkdir -p ~/mongo-data                      # Windows: mkdir C:\mongo-data
   mongod --replSet rs0 --dbpath ~/mongo-data --bind_ip localhost --port 27017
   ```
3. **Initiate the replica set once** (in another terminal):
   ```bash
   mongosh --eval "rs.initiate({_id: 'rs0', members: [{_id: 0, host: 'localhost:27017'}]})"
   ```
   (Alternatively skip 1–3 and use a free MongoDB Atlas cluster: set `MONGODB_URL` to its `mongodb+srv://…` string in `backend/.env`.)
4. **Backend** (http://localhost:8000):
   ```bash
   python3.12 -m venv .venv && source .venv/bin/activate     # Windows: py -3.12 -m venv .venv ; .venv\Scripts\activate
   pip install -e "backend[dev]"
   cd backend && uvicorn app.asgi:app --reload --port 8000
   ```
   Check http://localhost:8000/health/ready → `"status":"ok"`. Set `FX_PROVIDER=static` in `backend/.env` if the FX APIs are unreachable.
5. **Frontend** (http://localhost:5173, proxies `/api` to :8000):
   ```bash
   cd frontend && npm install && npm run dev
   ```

### Local development with Docker for the database only

```bash
docker compose up -d mongo
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e "backend[dev]"
cd backend && uvicorn app.asgi:app --reload

# frontend (http://localhost:5173, proxies /api to :8000)
cd frontend && npm ci && npm run dev
```

### Tests & checks

```bash
# backend: lint, types, unit + integration (integration needs a MongoDB replica set;
# tests use and wipe the `wallet_test` database)
export TEST_MONGODB_URL="mongodb://localhost:27017/?directConnection=true"
ruff check backend tests && (cd backend && mypy app) && pytest --cov=app

# frontend
cd frontend && npm run lint && npm test && npm run build
```

Integration tests skip automatically when `TEST_MONGODB_URL` is unset. They use a real MongoDB replica set because multi-document transactions, write-conflict retries, validators and unique indexes are the point and can't be mocked meaningfully.

## Deployment

**Target: Render** (free Docker web service) + **MongoDB Atlas** (free M0 cluster, which is a replica set so transactions work). The web service is defined as code in [`render.yaml`](render.yaml).

1. Create an Atlas M0 cluster, a database user, and allow network access from Render (or `0.0.0.0/0` for a demo); copy the `mongodb+srv://` connection string.
2. Render dashboard → **New → Blueprint** → select this repo and paste the Atlas string into `MONGODB_URL`. Render builds the `Dockerfile`, generates `JWT_SECRET` / `METRICS_TOKEN`, and uses `/health/ready` as the health check (failed readiness blocks a bad deploy from taking traffic).
3. Copy the service's **Deploy Hook URL** into the GitHub secret `RENDER_DEPLOY_HOOK_URL`, and set the repo variable `PUBLIC_URL` (e.g. `https://multi-currency-wallet.onrender.com`).

**CI/CD** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) on every PR and push to `main`:

| Job | What |
|---|---|
| `backend` | ruff lint + format check, mypy, pytest (unit + integration against a MongoDB single-node replica set), coverage artifact |
| `frontend` | oxlint, vitest, `tsc` + Vite production build |
| `docker` | builds the production image, `docker compose up --wait` (health-gated), runs `infra/scripts/smoke_test.sh` |
| `deploy` | `main` only, after all of the above: triggers the Render deploy hook, then polls `$PUBLIC_URL/health/ready` until healthy |

`autoDeploy` is off in `render.yaml` so only commits that passed CI reach production.

Any Docker host works too: the image needs only `MONGODB_URL` and `JWT_SECRET`, listens on `$PORT`, and creates its indexes on boot.

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
| MongoDB multi-document transactions + conditional `$inc` (`balance >= amount` in the filter) | atomic, overdraft-proof balance updates; write conflicts abort the loser, which `with_transaction` retries; collection validator `balance >= 0` as a backstop | requires a replica set (even locally); contended wallets cause retries rather than queueing; 60 s default transaction limit |
| Document model with references (not embedding ledger in wallets) | unbounded ledger can't live in a 16 MB document; history queries need their own indexes | no foreign keys — referential integrity is enforced in services, inside transactions |
| Ledger + materialized balance | O(1) balance reads, full audit trail with `balance_after` per entry | balance and ledger must be written in the same transaction (they are) |
| Synchronous transfers in one DB transaction | strong consistency, no saga/compensation needed – both users share a DB | does not generalize to cross-service or external transfers (would need an outbox/saga) |
| Immutable FX snapshots referenced by entries | complete conversion traceability and reproducibility | one row per refresh (tiny: ~32 rates/hour) |
| Background refresh thread + MongoDB lease document (TTL-style `expires_at`) | no extra scheduler infrastructure; only one replica refreshes | coupled to API process lifecycle; a dedicated worker/cron would be cleaner at scale |
| JWT access tokens in `localStorage` | stateless, simple SPA auth | no server-side revocation; XSS could read the token (mitigated by strict CSP and React escaping). Next step: short-lived access + httpOnly refresh cookie |
| Photos stored in MongoDB (binary field in a separate `user_photos` collection, ≤ 2 MB after re-encode) | no object storage dependency, transactional with the user | DB bloat at scale; S3 + CDN would be the production path |
| In-process rate limiter & error monitor | zero dependencies, works on the free tier | per-replica state; Redis / Prometheus would be the multi-replica answer |
| Sync PyMongo in FastAPI's threadpool | mature driver with the convenient `with_transaction` retry API | lower peak concurrency than an async driver (Motor/PyMongo async) |
| Decimal128 for all money | exact decimal arithmetic in the DB (incl. `$inc`), no floats | Decimal128 needs a codec in Python (`DecimalCodec`) |

## Known limitations

- **Public deployment** depends on connecting the Render Blueprint (one-time, needs a Render account); the free tier sleeps after inactivity (first request ~30–50 s) and the Atlas M0 tier has storage/throughput limits.
- No refresh tokens, logout-everywhere, email verification, password reset or MFA.
- Rate limiting and the in-app error monitor are per-process (not shared across replicas).
- No fees, spreads, limits per day, or AML checks; amounts are capped by `MAX_TRANSACTION_AMOUNT`.
- FX providers are free tiers without SLA; if both fail beyond `FX_MAX_AGE_SECONDS`, cross-currency operations are rejected until rates recover.
- Transfers are only between users of this platform; no reversals/refunds flow (ledger is append-only – a reversal would be a new compensating entry).
- Frontend tests cover the API client, validation and auth flows; the remaining pages are verified manually/E2E rather than with component tests.
- No end-to-end browser test suite in CI (smoke test covers the API path through the production image).
