import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    LargeBinary,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.domain.enums import TransactionType, TransferStatus

AMOUNT = Numeric(20, 4)
RATE = Numeric(28, 10)


def _now() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    default_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    photo: Mapped["UserPhoto | None"] = relationship(
        back_populates="user", uselist=False, lazy="noload", cascade="all, delete-orphan"
    )


class UserPhoto(Base):
    """Kept separate from users so profile reads never load blobs."""

    __tablename__ = "user_photos"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    content_type: Mapped[str] = mapped_column(String(50), nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    etag: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped[User] = relationship(back_populates="photo")


class Wallet(TimestampMixin, Base):
    __tablename__ = "wallets"
    __table_args__ = (
        UniqueConstraint("user_id", "currency", name="uq_wallets_user_currency"),
        CheckConstraint("balance >= 0", name="balance_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    balance: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False, default=Decimal(0))
    # Optimistic version counter; row locks give the real guarantee, this aids auditing.
    version: Mapped[int] = mapped_column(nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )


class FxRateSnapshot(Base):
    """Immutable record of rates fetched from a provider. Every conversion references
    the snapshot it used, so any historical amount can be re-derived and audited."""

    __tablename__ = "fx_rate_snapshots"
    __table_args__ = (Index("ix_fx_rate_snapshots_fetched_at", "fetched_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rates: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    provider_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Transfer(TimestampMixin, Base):
    __tablename__ = "transfers"
    __table_args__ = (
        UniqueConstraint("sender_id", "idempotency_key", name="uq_transfers_sender_idem"),
        CheckConstraint("sender_id <> recipient_id", name="no_self_transfer"),
        CheckConstraint("amount > 0", name="amount_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sender_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    recipient_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    amount: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    source_amount: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    source_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    source_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    target_amount: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    target_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    target_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    fx_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fx_rate_snapshots.id")
    )
    status: Mapped[TransferStatus] = mapped_column(
        Enum(TransferStatus, name="transfer_status"), default=TransferStatus.COMPLETED
    )
    note: Mapped[str | None] = mapped_column(String(140))
    idempotency_key: Mapped[str | None] = mapped_column(String(100))
    request_hash: Mapped[str | None] = mapped_column(String(64))

    sender: Mapped[User] = relationship(foreign_keys=[sender_id], lazy="joined")
    recipient: Mapped[User] = relationship(foreign_keys=[recipient_id], lazy="joined")


class LedgerEntry(Base):
    """Append-only, per-wallet ledger. A wallet's balance always equals the signed sum
    of its entries; `balance_after` makes every entry independently verifiable."""

    __tablename__ = "ledger_entries"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_ledger_user_idem"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("balance_after >= 0", name="balance_after_non_negative"),
        Index("ix_ledger_user_created", "user_id", "created_at"),
        Index("ix_ledger_wallet_created", "wallet_id", "created_at"),
        Index("ix_ledger_reference", "reference_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wallets.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[TransactionType] = mapped_column(
        Enum(TransactionType, name="transaction_type"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    # What the user asked for, before conversion into the wallet currency.
    original_amount: Mapped[Decimal] = mapped_column(AMOUNT, nullable=False)
    original_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    fx_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False, default=Decimal(1))
    fx_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fx_rate_snapshots.id")
    )
    # Groups the legs of a transfer/conversion.
    reference_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    counterparty_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    description: Mapped[str | None] = mapped_column(String(140))
    idempotency_key: Mapped[str | None] = mapped_column(String(100))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False
    )

    counterparty: Mapped[User | None] = relationship(foreign_keys=[counterparty_id], lazy="joined")
    fx_snapshot: Mapped[FxRateSnapshot | None] = relationship(lazy="noload")
