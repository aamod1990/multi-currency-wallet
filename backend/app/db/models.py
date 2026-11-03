"""Domain records persisted as MongoDB documents.

Plain dataclasses keep services free of driver details; `to_doc`/`from_doc` map
`id` <-> `_id`. Fields marked `transient` are hydrated for responses, never stored.
"""

import threading
import time
import uuid
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, ClassVar, Self

from app.domain.enums import TransactionType, TransferStatus

TRANSIENT = {"transient": True}


def now() -> datetime:
    # Mongo stores milliseconds; truncate up front so in-memory == persisted.
    t = datetime.now(UTC)
    return t.replace(microsecond=t.microsecond // 1000 * 1000)


_seq_lock = threading.Lock()
_last_ns = 0


def next_seq() -> int:
    """Monotonic tiebreaker for entries created within the same millisecond."""
    global _last_ns
    with _seq_lock:
        _last_ns = max(time.time_ns(), _last_ns + 1)
        return _last_ns


class Document:
    collection: ClassVar[str]
    enums: ClassVar[dict[str, type]] = {}

    def to_doc(self) -> dict[str, Any]:
        doc: dict[str, Any] = {}
        for f in fields(self):  # type: ignore[arg-type]
            if f.metadata.get("transient"):
                continue
            value = getattr(self, f.name)
            if f.name in self.enums and value is not None:
                value = value.value
            doc["_id" if f.name == "id" else f.name] = value
        return doc

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> Self:
        names = {f.name for f in fields(cls) if not f.metadata.get("transient")}  # type: ignore[arg-type]
        kwargs = {("id" if k == "_id" else k): v for k, v in doc.items()}
        kwargs = {k: v for k, v in kwargs.items() if k in names}
        for name, enum in cls.enums.items():
            if kwargs.get(name) is not None:
                kwargs[name] = enum(kwargs[name])
        return cls(**kwargs)


@dataclass
class User(Document):
    collection: ClassVar[str] = "users"

    email: str
    password_hash: str
    full_name: str
    default_currency: str
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=now)
    updated_at: datetime = field(default_factory=now)


@dataclass
class UserPhoto(Document):
    """Separate collection so profile reads never load blobs. `id` == user id."""

    collection: ClassVar[str] = "user_photos"

    id: uuid.UUID
    content_type: str
    data: bytes
    etag: str
    updated_at: datetime = field(default_factory=now)


@dataclass
class Wallet(Document):
    collection: ClassVar[str] = "wallets"

    user_id: uuid.UUID
    currency: str
    balance: Decimal = Decimal(0)
    version: int = 0
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=now)
    updated_at: datetime = field(default_factory=now)


@dataclass
class FxRateSnapshot(Document):
    """Immutable record of rates fetched from a provider. Every conversion references
    the snapshot it used, so any historical amount can be re-derived and audited."""

    collection: ClassVar[str] = "fx_rate_snapshots"

    provider: str
    base_currency: str
    rates: dict[str, str]
    provider_updated_at: datetime | None = None
    fetched_at: datetime = field(default_factory=now)
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class Transfer(Document):
    collection: ClassVar[str] = "transfers"
    enums: ClassVar[dict[str, type]] = {"status": TransferStatus}

    sender_id: uuid.UUID
    recipient_id: uuid.UUID
    amount: Decimal
    currency: str
    source_amount: Decimal
    source_currency: str
    source_rate: Decimal
    target_amount: Decimal
    target_currency: str
    target_rate: Decimal
    fx_snapshot_id: uuid.UUID | None = None
    status: TransferStatus = TransferStatus.COMPLETED
    note: str | None = None
    idempotency_key: str | None = None
    request_hash: str | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=now)

    sender: User | None = field(default=None, metadata=TRANSIENT)
    recipient: User | None = field(default=None, metadata=TRANSIENT)


@dataclass
class LedgerEntry(Document):
    """Append-only, per-wallet ledger. A wallet's balance always equals the signed sum
    of its entries; `balance_after` makes every entry independently verifiable."""

    collection: ClassVar[str] = "ledger_entries"
    enums: ClassVar[dict[str, type]] = {"type": TransactionType}

    user_id: uuid.UUID
    wallet_id: uuid.UUID
    type: TransactionType
    amount: Decimal
    currency: str
    balance_after: Decimal
    # What the user asked for, before conversion into the wallet currency.
    original_amount: Decimal
    original_currency: str
    fx_rate: Decimal = Decimal(1)
    fx_snapshot_id: uuid.UUID | None = None
    # Groups the legs of a transfer/conversion.
    reference_id: uuid.UUID | None = None
    counterparty_id: uuid.UUID | None = None
    description: str | None = None
    idempotency_key: str | None = None
    request_hash: str | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=now)
    seq: int = field(default_factory=next_seq)

    counterparty: User | None = field(default=None, metadata=TRANSIENT)
    fx_snapshot: FxRateSnapshot | None = field(default=None, metadata=TRANSIENT)
