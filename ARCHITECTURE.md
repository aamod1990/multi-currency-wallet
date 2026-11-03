# Architecture

## 1. Overview

```
             ┌──────────────────────── Docker image ────────────────────────┐
 Browser ──► │ FastAPI (uvicorn)                                            │
  (SPA)      │  ├─ /            React SPA (static, built by Vite)           │
             │  ├─ /api/*       routers → services → domain / db            │──► MongoDB 7 (replica set)
             │  ├─ /health/*    liveness / readiness                        │
             │  ├─ /metrics     Prometheus                                  │◄── Prometheus ─► Grafana / Alertmanager
             │  └─ FxRefresher  background thread (lease-locked)            │──► open.er-api.com → Frankfurter (fallback)
             └──────────────────────────────────────────────────────────────┘
```

A modular monolith: one process, one database, clear internal layers. This is deliberate — every operation that matters (transfers, conversions) needs a single ACID transaction across wallets of different users, which a monolith with one MongoDB replica set gives via multi-document transactions. Splitting into services would turn that into a distributed-transaction problem with no benefit at this scale.

## 2. Layers (backend)

| Layer | Package | Responsibility | Depends on |
|---|---|---|---|
| HTTP | `app/api` | routing, request/response schemas (Pydantic), auth dependency, idempotency header, mapping errors to HTTP | services |
| Application | `app/services` | use cases: `wallet_service`, `transfer_service`, `fx_service`, `history_service`, `user_service`; transaction boundaries and locking | domain, db, fx |
| Domain | `app/domain` | money rules: currency whitelist, minor units, quantization, cross rates, conversion; transaction types | nothing |
| Infrastructure | `app/db`, `app/fx`, `app/core` | MongoDB client/codecs/schema, document dataclasses, FX provider adapters, config, security, logging, metrics, middleware | — |

Rules: routers contain no business logic; services raise typed `AppError`s (`InsufficientFunds`, `Conflict`, `FxUnavailable`, …) that one handler turns into the error envelope; the domain layer is pure and unit-tested without a DB. FX providers implement a small `FxProvider` protocol, so the static provider (tests, offline) and the live chain are interchangeable.

## 3. Data model (MongoDB)

```
users            { _id: UUID, email (unique), password_hash, full_name, default_currency, ... }
user_photos      { _id: user UUID, content_type, data: Binary, etag }
wallets          { _id, user_id, currency, balance: Decimal128, version }   unique(user_id, currency)
                   validator: balance >= 0
fx_rate_snapshots{ _id, provider, base_currency, rates: {CCY: "rate"}, provider_updated_at, fetched_at }
ledger_entries   { _id, user_id, wallet_id, type, amount, currency, balance_after,
                   original_amount/currency, fx_rate, fx_snapshot_id, reference_id,
                   counterparty_id, description, idempotency_key, request_hash, created_at, seq }
                   validator: amount > 0, balance_after >= 0
                   unique(user_id, idempotency_key) partial; idx(user_id, created_at, seq); idx(reference_id)
transfers        { _id, sender_id, recipient_id, amount/currency, source_*, target_*, fx_snapshot_id,
                   status, note, idempotency_key, request_hash, created_at }
                   validator: amount > 0, sender_id != recipient_id; unique(sender_id, idempotency_key) partial
locks            { _id: "fx-refresh", owner, expires_at }   (refresher lease)
```

- **References, not embedding.** The ledger is unbounded, so it can't be embedded in a wallet (16 MB document limit) and history needs its own indexes. Documents reference each other by UUID (`uuidRepresentation=standard`); dataclasses in `app/db/models.py` map `id` <-> `_id`, and `app/db/repository.py` hydrates counterparties/snapshots for responses.
- **Ledger + materialized balance.** `ledger_entries` is append-only and is the audit trail; `wallets.balance` is a cache updated in the *same* transaction. Each entry stores `balance_after`, so any wallet's history can be verified by replaying entries (covered by a test).
- **Money as `Decimal128`/`Decimal` end-to-end.** No floats anywhere — a `DecimalCodec` maps Python `Decimal` to BSON `Decimal128` (exact decimal arithmetic, including server-side `$inc`), and the API serializes amounts as strings. Amounts are quantized to the currency's minor unit with banker's rounding.
- **Double-sided movements share a `reference_id`.** A transfer creates `TRANSFER_OUT` + `TRANSFER_IN`; an exchange creates `CONVERSION_OUT` + `CONVERSION_IN`. Both legs point to the same FX snapshot.
- **Ordering.** MongoDB dates have millisecond precision, so entries also carry a monotonic `seq` used as a tiebreaker for stable newest-first pagination.
- **Schema enforcement.** Collections, JSON validators and indexes are created idempotently at startup (`ensure_schema`) — the MongoDB equivalent of migrations for this schema.

## 4. Consistency controls

Concurrency is the core risk of a wallet. Controls, outermost to innermost:

1. **Idempotency keys** (`Idempotency-Key` header, optional) on credit/debit/exchange/transfer. Stored with a SHA-256 fingerprint of the request body under a partial unique index `(user, key)`. Same key + same body → the original result is replayed (`200` instead of `201`); same key + different body → `409 IDEMPOTENCY_KEY_REUSED`. The replay check runs *inside* the transaction, so a retried attempt sees a concurrent winner; a `DuplicateKeyError` after commit also falls back to replay. The frontend generates one key per logical submission, so double-clicks and retries are safe.
2. **One multi-document transaction per operation** (`run_in_transaction`: snapshot read concern, majority write concern). Debit, credit, transfer record and both ledger legs commit or abort together.
3. **Conditional atomic updates**: outflows use `find_one_and_update({_id, balance: {$gte: amount}}, {$inc: {balance: -amount}})`. If the filter doesn't match, the operation fails with `INSUFFICIENT_FUNDS` — an overdraft can never be written.
4. **Write-conflict detection instead of locks**: two transactions that modify the same wallet can't both commit; MongoDB aborts one with a `TransientTransactionError` and `with_transaction` re-runs it against fresh data. There are no lock waits, so opposite transfers A→B / B→A cannot deadlock.
5. **Database validators as the last line of defence**: `balance >= 0`, positive amounts, `sender_id != recipient_id`, unique `(user_id, currency)` wallets.
6. **Single FX snapshot per operation**: both legs of a cross-currency transfer use one snapshot, so the debit and credit are mutually consistent and reproducible.

Integration tests hammer these paths with threads against a real replica set: 20 concurrent debits that would overdraw, bidirectional transfer storms (asserting no lost updates and conservation of money), concurrent reuse of an idempotency key, and a direct write that the validator must reject.

## 5. Currency exchange

- **Providers**: `OpenErApiProvider` (primary, free, no key, ~160 currencies) → `FrankfurterProvider` (ECB data, fallback) via `ChainedProvider`. Responses are validated, filtered to supported currencies, and parsed as `Decimal`; malformed payloads count as failures.
- **Refresh**: `FxRefresher` thread runs every `FX_REFRESH_INTERVAL_SECONDS` (default 1 h — the providers update daily/hourly). It takes a short **lease document** in the `locks` collection (atomic upsert on `_id` with an `expires_at`, so a crashed holder can't block forever) so only one replica fetches, and skips if the latest snapshot is still fresh. Success/failure are logged and counted (`fx_refresh_total`, `fx_rates_last_success_timestamp_seconds`). A first refresh runs at startup.
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
- PyMongo connection pool with server-selection timeout; readiness (`ping`) fails (503) when the DB is unreachable so the platform stops routing traffic.
- Collections, validators and indexes are ensured on boot (idempotent); transient transaction errors are retried automatically; the frontend retries only transient errors (network/5xx), never 4xx, and never auto-retries mutations (idempotency keys make manual retries safe).

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
