import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from app.core.config import get_settings
from app.core.errors import FxUnavailable, NotFound
from app.core.metrics import FX_LAST_SUCCESS, FX_REFRESH
from app.db.models import FxRateSnapshot, now
from app.db.mongo import FX_SNAPSHOTS, Db
from app.domain import money
from app.fx.providers import FxProvider, FxProviderError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Rates:
    snapshot: FxRateSnapshot
    rates: dict[str, Decimal]

    @property
    def age_seconds(self) -> float:
        return (datetime.now(UTC) - self.snapshot.fetched_at).total_seconds()


@dataclass(frozen=True)
class Conversion:
    source_amount: Decimal
    source_currency: str
    target_amount: Decimal
    target_currency: str
    rate: Decimal
    snapshot_id: uuid.UUID | None

    @property
    def is_cross_currency(self) -> bool:
        return self.source_currency != self.target_currency


def refresh_rates(db: Db, provider: FxProvider) -> FxRateSnapshot:
    base = get_settings().fx_base_currency
    try:
        quote = provider.fetch(base)
    except FxProviderError:
        FX_REFRESH.labels(outcome="failure").inc()
        log.error("fx_refresh_failed", exc_info=True)
        raise
    snap = FxRateSnapshot(
        provider=quote.provider,
        base_currency=quote.base,
        rates={k: str(v) for k, v in sorted(quote.rates.items())},
        provider_updated_at=quote.provider_updated_at,
        fetched_at=now(),
    )
    db[FX_SNAPSHOTS].insert_one(snap.to_doc())
    FX_REFRESH.labels(outcome="success").inc()
    FX_LAST_SUCCESS.set(snap.fetched_at.timestamp())
    log.info(
        "fx_refreshed",
        extra={"provider": snap.provider, "currencies": len(snap.rates), "snapshot": str(snap.id)},
    )
    return snap


def latest_snapshot(db: Db) -> FxRateSnapshot | None:
    doc = db[FX_SNAPSHOTS].find_one(sort=[("fetched_at", -1)])
    return FxRateSnapshot.from_doc(doc) if doc else None


def get_snapshot(db: Db, snapshot_id: uuid.UUID) -> FxRateSnapshot:
    doc = db[FX_SNAPSHOTS].find_one({"_id": snapshot_id})
    if doc is None:
        raise NotFound("Rate snapshot not found")
    return FxRateSnapshot.from_doc(doc)


def current_rates(db: Db) -> Rates:
    """Latest rates, refusing to convert on data older than FX_MAX_AGE_SECONDS."""
    snap = latest_snapshot(db)
    if snap is None:
        raise FxUnavailable("Exchange rates are not available yet")
    rates = Rates(snap, {k: Decimal(v) for k, v in snap.rates.items()})
    if rates.age_seconds > get_settings().fx_max_age_seconds:
        raise FxUnavailable(
            "Exchange rates are stale; conversions are temporarily disabled",
            details={"fetched_at": snap.fetched_at.isoformat()},
        )
    return rates


def supported_currencies(db: Db) -> list[str]:
    snap = latest_snapshot(db)
    quoted = set(snap.rates) if snap else set()
    return sorted(c for c in money.CURRENCY_EXPONENTS if not quoted or c in quoted)


def convert(amount: Decimal, source: str, target: str, rates: Rates | None) -> Conversion:
    """Convert using an explicit Rates object (so multi-leg operations share one snapshot)."""
    if source == target:
        return Conversion(amount, source, amount, target, Decimal(1), None)
    if rates is None:
        raise FxUnavailable("Exchange rates are not available")
    rate = money.cross_rate(rates.rates, source, target)
    return Conversion(
        source_amount=amount,
        source_currency=source,
        target_amount=money.convert(amount, rate, target),
        target_currency=target,
        rate=rate,
        snapshot_id=rates.snapshot.id,
    )


def rates_if_needed(db: Db, *currencies: str) -> Rates | None:
    return current_rates(db) if len(set(currencies)) > 1 else None
