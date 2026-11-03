# Architecture

## 1. Overview

```
             ┌──────────────────────── Docker image ────────────────────────┐
 Browser ──► │ FastAPI (uvicorn)                                            │
  (SPA)      │  ├─ /            React SPA (static, built by Vite)           │
             │  ├─ /api/*       routers → services → domain / db            │──► PostgreSQL 16
             │  ├─ /health/*    liveness / readiness                        │
             │  ├─ /metrics     Prometheus                                  │◄── Prometheus ─► Grafana / Alertmanager
             │  └─ FxRefresher  background thread (advisory-locked)         │──► open.er-api.com → Frankfurter (fallback)
             └──────────────────────────────────────────────────────────────┘
```

A modular monolith: one process, one database, clear internal layers. This is deliberate — every operation that matters (transfers, conversions) needs a single ACID transaction across wallets of different users, which a monolith with one Postgres gives for free. Splitting into services would turn that into a distributed-transaction problem with no benefit at this scale.

## 2. Layers (backend)

| Layer | Package | Responsibility | Depends on |
|---|---|---|---|
| HTTP | `app/api` | routing, request/response schemas (Pydantic), auth dependency, idempotency header, mapping errors to HTTP | services |
| Application | `app/services` | use cases: `wallet_service`, `transfer_service`, `fx_service`, `history_service`, `user_service`; transaction boundaries and locking | domain, db, fx |
| Domain | `app/domain` | money rules: currency whitelist, minor units, quantization, cross rates, conversion; transaction types | nothing |
| Infrastructure | `app/db`, `app/fx`, `app/core` | SQLAlchemy models/session, FX provider adapters, config, security, logging, metrics, middleware | — |

Rules: routers contain no business logic; services raise typed `AppError`s (`InsufficientFunds`, `Conflict`, `FxUnavailable`, …) that one handler turns into the error envelope; the domain layer is pure and unit-tested without a DB. FX providers implement a small `FxProvider` protocol, so the static provider (tests, offline) and the live chain are interchangeable.

## 3. Data model

```
users ─1:1─ user_photos
  │
  └─1:N─ wallets (user_id, currency) UNIQUE, balance NUMERIC(20,4) CHECK >= 0
            │
            └─1:N─ ledger_entries ──N:1── fx_rate_snapshots (provider, base, rates JSONB, fetched_at)
                     │  type, amount, balance_after, original_amount/currency, fx_rate,
                     │  reference_id ──► transfers.id | exchange pair id
                     │  counterparty_user_id, idempotency_key (+request_hash)
                     │
transfers ───────────┘  sender/recipient, source_amount/currency, target_amount/currency,
                        rates, fx_snapshot_id, idempotency_key UNIQUE(sender_id, key), CHECK sender≠recipient
```

- **Ledger + materialized balance.** `ledger_entries` is append-only and is the audit trail; `wallets.balance` is a cache updated in the *same* transaction. Each entry stores `balance_after`, so any wallet's history can be verified by replaying entries.
- **Money as `NUMERIC`/`Decimal` end-to-end.** No floats anywhere — DB columns are `NUMERIC(20,4)` (rates `NUMERIC(24,10)`), Python uses `Decimal`, and the API serializes amounts as strings. Amounts are quantized to the currency's minor unit with banker's rounding.
- **Double-sided movements share a `reference_id`.** A transfer creates `TRANSFER_OUT` + `TRANSFER_IN`; an exchange creates `CONVERSION_OUT` + `CONVERSION_IN`. Both legs point to the same FX snapshot.
- **Indexes** follow the read paths: `(user_id, created_at DESC)` for history, `(wallet_id, created_at)`, `reference_id`.

## 4. Consistency controls

Concurrency is the core risk of a wallet. Controls, outermost to innermost:

1. **Idempotency keys** (`Idempotency-Key` header, optional) on credit/debit/exchange/transfer. Stored with a SHA-256 fingerprint of the request body under a unique constraint `(user, key)`. Same key + same body → the original result is replayed (`200` instead of `201`); same key + different body → `409 IDEMPOTENCY_KEY_REUSED`. Two concurrent requests with the same key race on the unique constraint; the loser rolls back and replays the winner. The frontend generates one key per logical submission, so double-clicks and retries are safe.
2. **One DB transaction per operation**, with wallets locked via `SELECT … FOR UPDATE` **in a stable order (by wallet id)**. Two opposite transfers A→B and B→A therefore lock in the same order and cannot deadlock (covered by a concurrency test).
3. **Check-then-write under lock**: the balance check happens after the lock is held, so no lost updates or overdrafts.
4. **Database constraints as the last line of defence**: `CHECK (balance >= 0)`, positive amounts, `sender_id <> recipient_id`, unique `(user_id, currency)` wallets.
5. **Single FX snapshot per operation**: both legs of a cross-currency transfer use one snapshot, so the debit and credit are mutually consistent and reproducible.

Integration tests hammer these paths with threads against real Postgres: 20 concurrent debits that would overdraw, bidirectional transfer storms (asserting no deadlock and conservation of money), and concurrent reuse of an idempotency key.

## 5. Currency exchange

- **Providers**: `OpenErApiProvider` (primary, free, no key, ~160 currencies) → `FrankfurterProvider` (ECB data, fallback) via `ChainedProvider`. Responses are validated, filtered to supported currencies, and parsed as `Decimal`; malformed payloads count as failures.
- **Refresh**: `FxRefresher` thread runs every `FX_REFRESH_INTERVAL_SECONDS` (default 1 h — the providers update daily/hourly). It takes a Postgres **advisory lock** so only one replica fetches, and skips if the latest snapshot is still fresh. Success/failure are logged and counted (`fx_refresh_total`, `fx_rates_last_success_timestamp_seconds`). A first refresh runs at startup.
- **Snapshots are immutable rows**. Every converted ledger entry/transfer stores `fx_snapshot_id` and the effective `fx_rate`; `GET /api/transactions/{id}` and `GET /api/fx/snapshots/{id}` expose provider, fetch time and all rates — this is the *conversion traceability* requirement.
- **Staleness guard**: if the newest snapshot is older than `FX_MAX_AGE_SECONDS`, conversions fail fast with `503 FX_UNAVAILABLE` rather than using unsafe rates. Same-currency operations don't need rates and keep working. Readiness reports `degraded` in this state.

## 6. API design

- Resource-oriented REST under `/api`, OpenAPI at `/docs`. Mutations return the resulting entries *and* updated wallets so the UI needs no extra round-trip.
- Consistent error envelope `{error: {code, message, request_id, details}}` with stable machine codes (`INSUFFICIENT_FUNDS`, `VALIDATION_ERROR`, `FX_UNAVAILABLE`, …); validation errors list per-field messages, which the UI maps onto form fields.
- Status codes: `201` create, `200` idempotent replay, `401/403/404`, `409` conflicts (idempotency reuse), `422` validation & business rule violations (insufficient funds), `429` rate limit, `503` FX unavailable, `500` with no internals leaked.
- History uses offset pagination (`page`, `page_size ≤ 100`, `total`, `pages`) — simple for a UI with page numbers. Keyset pagination would be the upgrade for very deep histories.

## 7. Security

- Passwords: bcrypt (cost 12), strength rule (≥ 8 chars, letter + digit). Login runs a dummy bcrypt verify for unknown emails to avoid user enumeration by timing; errors are generic.
- JWT (HS256) with `exp`, `nbf`, `iat`, `jti`, `typ`, required claims and algorithm pinning; secret from env, enforced ≥ 32 chars.
- Login rate limit per (IP, email) sliding window → `429`.
- Authorization at the service layer: every query is scoped by the authenticated user; transfers/transactions of others return `404` (no existence leak).
- Input validation everywhere via Pydantic: ISO currency whitelist, amount precision per currency, max amount, note length, email format.
- Uploads: content-type + size limits, decoded and **re-encoded** with Pillow (drops EXIF/GPS and polyglot payloads), resized to 512 px; decompression-bomb protection.
- HTTP headers: strict CSP (`script-src 'self'`), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`. Container runs as non-root. `/metrics` can be token-protected.

## 8. Reliability & error handling

- Every unhandled exception becomes a `500` JSON envelope with a `request_id`, counted in `app_unhandled_exceptions_total` and logged with stack trace — never an HTML page or stack trace to the client.
- External calls (FX) have timeouts and a fallback provider; failure degrades only cross-currency features.
- DB pool with `pool_pre_ping`; readiness fails (503) when the DB is unreachable so the platform stops routing traffic.
- Migrations run on boot via Alembic (idempotent); the frontend retries only transient errors (network/5xx), never 4xx, and never auto-retries mutations (idempotency keys make manual retries safe).

## 9. Observability

See README → *Observability & 4XX/5XX detection*. Summary: structured JSON logs correlated by `request_id`; RED metrics per route template; business counters; liveness vs. readiness probes; two-layer error-rate detection (Prometheus ratio + baseline alerts, and an in-app rolling-window monitor with webhook for environments without Prometheus).

## 10. Frontend

React 19 + TypeScript + Vite; TanStack Query for server state (cache invalidation after mutations), React Router for pages (Wallets, Transfer, History, Profile, Login/Signup). A typed API client centralises auth headers, idempotency keys, error-envelope parsing and 401 → logout. Client-side validation mirrors server rules (currency precision, password strength) for fast feedback, but the server remains the authority. Live FX quotes (debounced) show the converted amount before submitting; transfers require an explicit review/confirm step.

## 11. Scaling path

| Pressure | Next step |
|---|---|
| More API replicas | move rate limiter & error monitor to Redis/Prometheus; FX refresh already replica-safe |
| Hot wallets / high write rate | per-wallet queueing or partitioning; keep ledger append-only |
| History volume | keyset pagination, partition `ledger_entries` by month |
| External payment rails | outbox pattern + saga with compensating ledger entries |
| Photos | object storage + CDN, signed URLs |
| Auth | refresh tokens in httpOnly cookies, token revocation list, MFA |
