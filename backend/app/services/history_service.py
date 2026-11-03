import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import NotFound, ValidationFailed
from app.db.models import LedgerEntry
from app.domain.enums import TransactionType


@dataclass
class HistoryFilter:
    types: list[TransactionType] | None = None
    currency: str | None = None
    direction: str | None = None  # "in" | "out"
    date_from: datetime | None = None
    date_to: datetime | None = None
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None


OUT_TYPES = [t for t in TransactionType if t.is_outflow]
IN_TYPES = [t for t in TransactionType if not t.is_outflow]


def list_entries(
    db: Session, user_id: uuid.UUID, f: HistoryFilter, page: int, page_size: int
) -> tuple[list[LedgerEntry], int]:
    if f.date_from and f.date_to and f.date_from > f.date_to:
        raise ValidationFailed("date_from must be before date_to")
    conds = [LedgerEntry.user_id == user_id]
    if f.types:
        conds.append(LedgerEntry.type.in_(f.types))
    if f.direction == "in":
        conds.append(LedgerEntry.type.in_(IN_TYPES))
    elif f.direction == "out":
        conds.append(LedgerEntry.type.in_(OUT_TYPES))
    if f.currency:
        conds.append(LedgerEntry.currency == f.currency)
    if f.date_from:
        conds.append(LedgerEntry.created_at >= f.date_from)
    if f.date_to:
        conds.append(LedgerEntry.created_at <= f.date_to)
    if f.min_amount is not None:
        conds.append(LedgerEntry.amount >= f.min_amount)
    if f.max_amount is not None:
        conds.append(LedgerEntry.amount <= f.max_amount)

    total = db.scalar(select(func.count()).select_from(LedgerEntry).where(*conds)) or 0
    items = db.scalars(
        select(LedgerEntry)
        .where(*conds)
        .order_by(LedgerEntry.created_at.desc(), LedgerEntry.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return list(items), total


def get_entry(db: Session, user_id: uuid.UUID, entry_id: uuid.UUID) -> LedgerEntry:
    entry = db.scalar(
        select(LedgerEntry)
        .options(selectinload(LedgerEntry.fx_snapshot))
        .where(LedgerEntry.id == entry_id, LedgerEntry.user_id == user_id)
    )
    if entry is None:
        raise NotFound("Transaction not found")
    return entry
