"""Small read helpers shared by services (hydrating references for responses)."""

import uuid

from pymongo.client_session import ClientSession

from app.db.models import FxRateSnapshot, LedgerEntry, Transfer, User
from app.db.mongo import FX_SNAPSHOTS, USERS, Db


def get_user(db: Db, user_id: uuid.UUID, session: ClientSession | None = None) -> User | None:
    doc = db[USERS].find_one({"_id": user_id}, session=session)
    return User.from_doc(doc) if doc else None


def users_by_id(db: Db, ids: set[uuid.UUID]) -> dict[uuid.UUID, User]:
    if not ids:
        return {}
    return {d["_id"]: User.from_doc(d) for d in db[USERS].find({"_id": {"$in": list(ids)}})}


def hydrate_entries(
    db: Db, entries: list[LedgerEntry], with_snapshot: bool = False
) -> list[LedgerEntry]:
    users = users_by_id(db, {e.counterparty_id for e in entries if e.counterparty_id})
    for e in entries:
        e.counterparty = users.get(e.counterparty_id) if e.counterparty_id else None
        if with_snapshot and e.fx_snapshot_id:
            doc = db[FX_SNAPSHOTS].find_one({"_id": e.fx_snapshot_id})
            e.fx_snapshot = FxRateSnapshot.from_doc(doc) if doc else None
    return entries


def hydrate_transfer(db: Db, t: Transfer) -> Transfer:
    users = users_by_id(db, {t.sender_id, t.recipient_id})
    t.sender, t.recipient = users.get(t.sender_id), users.get(t.recipient_id)
    return t
