"""MongoDB client, codecs, schema (validators + indexes) and transaction helper."""

from collections.abc import Callable, Iterator
from datetime import UTC
from decimal import Decimal
from typing import Any, TypeVar

from bson.codec_options import TypeCodec, TypeRegistry
from bson.decimal128 import Decimal128
from pymongo import ASCENDING, DESCENDING, IndexModel, MongoClient, ReadPreference
from pymongo.client_session import ClientSession
from pymongo.database import Database
from pymongo.errors import CollectionInvalid
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from app.core.config import get_settings

T = TypeVar("T")
Doc = dict[str, Any]
Db = Database[Doc]

USERS = "users"
USER_PHOTOS = "user_photos"
WALLETS = "wallets"
FX_SNAPSHOTS = "fx_rate_snapshots"
TRANSFERS = "transfers"
LEDGER = "ledger_entries"
LOCKS = "locks"


class DecimalCodec(TypeCodec):
    """Money never touches binary floats: Python Decimal <-> BSON Decimal128."""

    python_type = Decimal
    bson_type = Decimal128

    def transform_python(self, value: Decimal) -> Decimal128:
        return Decimal128(value)

    def transform_bson(self, value: Decimal128) -> Decimal:
        return value.to_decimal()


_client: MongoClient[Doc] | None = None


def get_client() -> MongoClient[Doc]:
    global _client
    if _client is None:
        s = get_settings()
        _client = MongoClient(
            s.mongodb_url,
            uuidRepresentation="standard",
            tz_aware=True,
            tzinfo=UTC,
            type_registry=TypeRegistry([DecimalCodec()]),
            serverSelectionTimeoutMS=s.mongodb_timeout_ms,
            maxPoolSize=s.mongodb_max_pool_size,
            retryWrites=True,
        )
    return _client


def get_database() -> Db:
    return get_client()[get_settings().mongodb_db]


def get_db() -> Iterator[Db]:
    yield get_database()


def reset_client() -> None:
    global _client
    if _client is not None:
        _client.close()
    _client = None


def run_in_transaction(db: Db, fn: Callable[[ClientSession], T]) -> T:
    """Run `fn` in a snapshot-isolated, majority-committed transaction.

    `with_transaction` retries the whole callback on TransientTransactionError
    (e.g. a WriteConflict when two requests touch the same wallet), so `fn` must
    only have database side effects.
    """
    with db.client.start_session() as session:
        return session.with_transaction(
            fn,
            read_concern=ReadConcern("snapshot"),
            write_concern=WriteConcern("majority"),
            read_preference=ReadPreference.PRIMARY,
        )


# Server-side validators: the database's last line of defence, mirroring the
# CHECK constraints a relational schema would have.
VALIDATORS: dict[str, Doc] = {
    WALLETS: {"balance": {"$gte": Decimal128("0")}},
    LEDGER: {
        "amount": {"$gt": Decimal128("0")},
        "balance_after": {"$gte": Decimal128("0")},
    },
    TRANSFERS: {
        "amount": {"$gt": Decimal128("0")},
        "$expr": {"$ne": ["$sender_id", "$recipient_id"]},
    },
}

_HAS_KEY = {"idempotency_key": {"$type": "string"}}
INDEXES: dict[str, list[IndexModel]] = {
    USERS: [IndexModel([("email", ASCENDING)], unique=True, name="uq_users_email")],
    WALLETS: [
        IndexModel(
            [("user_id", ASCENDING), ("currency", ASCENDING)],
            unique=True,
            name="uq_wallets_user_currency",
        )
    ],
    FX_SNAPSHOTS: [IndexModel([("fetched_at", DESCENDING)], name="ix_fx_fetched_at")],
    TRANSFERS: [
        IndexModel(
            [("sender_id", ASCENDING), ("idempotency_key", ASCENDING)],
            unique=True,
            partialFilterExpression=_HAS_KEY,
            name="uq_transfers_sender_idem",
        ),
        IndexModel([("recipient_id", ASCENDING)], name="ix_transfers_recipient"),
    ],
    LEDGER: [
        IndexModel(
            [("user_id", ASCENDING), ("idempotency_key", ASCENDING)],
            unique=True,
            partialFilterExpression=_HAS_KEY,
            name="uq_ledger_user_idem",
        ),
        IndexModel(
            [("user_id", ASCENDING), ("created_at", DESCENDING), ("seq", DESCENDING)],
            name="ix_ledger_user_created",
        ),
        IndexModel([("wallet_id", ASCENDING), ("seq", ASCENDING)], name="ix_ledger_wallet"),
        IndexModel([("reference_id", ASCENDING)], name="ix_ledger_reference"),
    ],
}


def ensure_schema(db: Db) -> None:
    """Idempotent: create collections (transactions can't create them implicitly
    on older servers), apply validators and build indexes. Runs at startup."""
    existing = set(db.list_collection_names())
    for name in (USERS, USER_PHOTOS, WALLETS, FX_SNAPSHOTS, TRANSFERS, LEDGER, LOCKS):
        validator = VALIDATORS.get(name)
        if name not in existing:
            try:
                if validator:
                    db.create_collection(name, validator=validator)
                else:
                    db.create_collection(name)
                continue
            except CollectionInvalid:
                pass  # created concurrently by another replica
        if validator:
            db.command("collMod", name, validator=validator)
    for name, indexes in INDEXES.items():
        db[name].create_indexes(indexes)
