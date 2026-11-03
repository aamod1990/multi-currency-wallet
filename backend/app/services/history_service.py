import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.core.errors import NotFound, ValidationFailed
from app.db.models import LedgerEntry
from app.db.mongo import LEDGER, Db
from app.db.repository import hydrate_entries
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
    db: Db, user_id: uuid.UUID, f: HistoryFilter, page: int, page_size: int
) -> tuple[list[LedgerEntry], int]:
    if f.date_from and f.date_to and f.date_from > f.date_to:
        raise ValidationFailed("date_from must be before date_to")
    query: dict[str, Any] = {"user_id": user_id}
    type_conds: list[set[str]] = []
    if f.types:
        type_conds.append({t.value for t in f.types})
    if f.direction == "in":
        type_conds.append({t.value for t in IN_TYPES})
    elif f.direction == "out":
        type_conds.append({t.value for t in OUT_TYPES})
    if type_conds:
        query["type"] = {"$in": sorted(set.intersection(*type_conds))}
    if f.currency:
        query["currency"] = f.currency
    created: dict[str, Any] = {}
    if f.date_from:
        created["$gte"] = f.date_from
    if f.date_to:
        created["$lte"] = f.date_to
    if created:
        query["created_at"] = created
    amount: dict[str, Any] = {}
    if f.min_amount is not None:
        amount["$gte"] = f.min_amount
    if f.max_amount is not None:
        amount["$lte"] = f.max_amount
    if amount:
        query["amount"] = amount

    total = db[LEDGER].count_documents(query)
    cursor = (
        db[LEDGER]
        .find(query)
        .sort([("created_at", -1), ("seq", -1)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return hydrate_entries(db, [LedgerEntry.from_doc(d) for d in cursor]), total


def get_entry(db: Db, user_id: uuid.UUID, entry_id: uuid.UUID) -> LedgerEntry:
    doc = db[LEDGER].find_one({"_id": entry_id, "user_id": user_id})
    if doc is None:
        raise NotFound("Transaction not found")
    return hydrate_entries(db, [LedgerEntry.from_doc(doc)], with_snapshot=True)[0]
