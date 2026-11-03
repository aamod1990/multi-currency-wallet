"""Wallet operations. Every balance mutation:

1. runs in one multi-document MongoDB transaction (snapshot reads, majority commit),
2. updates balances with a conditional atomic `$inc` (`balance >= amount` in the
   filter), so an overdraft can never be written even under concurrency,
3. relies on MongoDB write-conflict detection: two transactions touching the same
   wallet cannot both commit; the loser is retried by `with_transaction`,
4. appends ledger entries carrying `balance_after` and the FX snapshot used,
5. is protected by a collection validator (balance >= 0) as a last line of defence,
6. is optionally idempotent via a client-supplied Idempotency-Key.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from pymongo import ReturnDocument
from pymongo.client_session import ClientSession
from pymongo.errors import DuplicateKeyError

from app.core.config import get_settings
from app.core.errors import Conflict, InsufficientFunds, NotFound, ValidationFailed
from app.core.metrics import WALLET_OPERATIONS
from app.db.models import LedgerEntry, User, Wallet, now
from app.db.mongo import LEDGER, WALLETS, Db, run_in_transaction
from app.db.repository import hydrate_entries
from app.domain import money
from app.domain.enums import TransactionType
from app.services import fx_service
from app.services.fx_service import Conversion
from app.services.idempotency import request_fingerprint

log = logging.getLogger(__name__)


@dataclass
class OperationResult:
    entries: list[LedgerEntry]
    replayed: bool = False


def list_wallets(db: Db, user_id: uuid.UUID) -> list[Wallet]:
    return [Wallet.from_doc(d) for d in db[WALLETS].find({"user_id": user_id}).sort("currency")]


def find_wallet(
    db: Db, user_id: uuid.UUID, currency: str, session: ClientSession | None = None
) -> Wallet | None:
    doc = db[WALLETS].find_one({"user_id": user_id, "currency": currency}, session=session)
    return Wallet.from_doc(doc) if doc else None


def get_or_create_wallet(
    db: Db, user_id: uuid.UUID, currency: str, session: ClientSession | None = None
) -> Wallet:
    """Race-safe upsert on the unique (user_id, currency) index."""
    currency = money.validate_currency(currency)
    fresh = Wallet(user_id=user_id, currency=currency).to_doc()
    doc: dict | None
    try:
        doc = db[WALLETS].find_one_and_update(
            {"user_id": user_id, "currency": currency},
            {"$setOnInsert": {k: v for k, v in fresh.items() if k not in ("user_id", "currency")}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
            session=session,
        )
    except DuplicateKeyError:
        if session is not None:
            raise
        doc = db[WALLETS].find_one({"user_id": user_id, "currency": currency})
    assert doc is not None
    return Wallet.from_doc(doc)


def post_entry(
    db: Db,
    session: ClientSession,
    wallet: Wallet,
    tx_type: TransactionType,
    conv: Conversion,
    *,
    amount: Decimal,
    reference_id: uuid.UUID | None = None,
    counterparty_id: uuid.UUID | None = None,
    description: str | None = None,
    idempotency_key: str | None = None,
    request_hash: str | None = None,
) -> LedgerEntry:
    """Atomically apply one signed movement to a wallet and append its ledger entry."""
    if amount <= 0:
        raise ValidationFailed("Amount is too small to convert into the target currency")
    query: dict = {"_id": wallet.id}
    if tx_type.is_outflow:
        query["balance"] = {"$gte": amount}
    updated = db[WALLETS].find_one_and_update(
        query,
        {
            "$inc": {"balance": -amount if tx_type.is_outflow else amount, "version": 1},
            "$set": {"updated_at": now()},
        },
        return_document=ReturnDocument.AFTER,
        session=session,
    )
    if updated is None:
        current = db[WALLETS].find_one({"_id": wallet.id}, session=session)
        if current is None:
            raise NotFound(f"You have no {wallet.currency} wallet", code="WALLET_NOT_FOUND")
        raise InsufficientFunds(
            f"Insufficient funds in {wallet.currency} wallet",
            details={
                "available": format(current["balance"].normalize(), "f"),
                "required": format(amount.normalize(), "f"),
            },
        )
    entry = LedgerEntry(
        user_id=wallet.user_id,
        wallet_id=wallet.id,
        type=tx_type,
        amount=amount,
        currency=wallet.currency,
        balance_after=updated["balance"],
        original_amount=conv.source_amount,
        original_currency=conv.source_currency,
        fx_rate=conv.rate,
        fx_snapshot_id=conv.snapshot_id,
        reference_id=reference_id,
        counterparty_id=counterparty_id,
        description=description,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    db[LEDGER].insert_one(entry.to_doc(), session=session)
    return entry


def _replay(
    db: Db, user_id: uuid.UUID, key: str | None, fp: str, session: ClientSession | None = None
) -> OperationResult | None:
    if not key:
        return None
    doc = db[LEDGER].find_one({"user_id": user_id, "idempotency_key": key}, session=session)
    if doc is None:
        return None
    if doc.get("request_hash") != fp:
        raise Conflict(
            "Idempotency-Key was already used with a different request",
            code="IDEMPOTENCY_KEY_REUSED",
        )
    entries = [LedgerEntry.from_doc(doc)]
    if doc.get("reference_id"):
        cursor = db[LEDGER].find({"reference_id": doc["reference_id"]}, session=session)
        entries = [LedgerEntry.from_doc(d) for d in cursor.sort("seq")]
    return OperationResult(entries, replayed=True)


def _run_idempotent(
    db: Db,
    user_id: uuid.UUID,
    key: str | None,
    fp: str,
    operation: str,
    work: Callable[[ClientSession], list[LedgerEntry]],
) -> OperationResult:
    def txn(session: ClientSession) -> OperationResult:
        # Checked inside the transaction so a retried attempt sees a concurrent winner.
        if (prior := _replay(db, user_id, key, fp, session)) is not None:
            return prior
        return OperationResult(work(session))

    try:
        result = run_in_transaction(db, txn)
    except DuplicateKeyError:
        # A concurrent request with the same key committed first; return its result.
        if (prior := _replay(db, user_id, key, fp)) is not None:
            WALLET_OPERATIONS.labels(operation=operation, outcome="replayed").inc()
            return OperationResult(hydrate_entries(db, prior.entries), replayed=True)
        WALLET_OPERATIONS.labels(operation=operation, outcome="error").inc()
        raise
    except Exception as exc:
        outcome = getattr(exc, "code", "error")
        WALLET_OPERATIONS.labels(operation=operation, outcome=str(outcome).lower()).inc()
        raise
    WALLET_OPERATIONS.labels(
        operation=operation, outcome="replayed" if result.replayed else "success"
    ).inc()
    if not result.replayed:
        log.info(
            "wallet_operation",
            extra={"operation": operation, "entries": [str(e.id) for e in result.entries]},
        )
    hydrate_entries(db, result.entries)
    return result


def credit(
    db: Db,
    user: User,
    *,
    amount: Decimal,
    currency: str,
    wallet_currency: str | None = None,
    description: str | None = None,
    idempotency_key: str | None = None,
) -> OperationResult:
    currency = money.validate_currency(currency)
    wallet_currency = money.validate_currency(wallet_currency or currency)
    amount = money.parse_amount(amount, currency, get_settings().max_transaction_amount)
    fp = request_fingerprint(
        {
            "op": "credit",
            "amount": amount,
            "currency": currency,
            "wallet_currency": wallet_currency,
            "description": description,
        }
    )
    rates = fx_service.rates_if_needed(db, currency, wallet_currency)
    conv = fx_service.convert(amount, currency, wallet_currency, rates)

    def work(session: ClientSession) -> list[LedgerEntry]:
        wallet = get_or_create_wallet(db, user.id, wallet_currency, session)
        return [
            post_entry(
                db,
                session,
                wallet,
                TransactionType.CREDIT,
                conv,
                amount=conv.target_amount,
                description=description,
                idempotency_key=idempotency_key,
                request_hash=fp,
            )
        ]

    return _run_idempotent(db, user.id, idempotency_key, fp, "credit", work)


def debit(
    db: Db,
    user: User,
    *,
    amount: Decimal,
    currency: str,
    wallet_currency: str | None = None,
    description: str | None = None,
    idempotency_key: str | None = None,
) -> OperationResult:
    currency = money.validate_currency(currency)
    wallet_currency = money.validate_currency(wallet_currency or currency)
    amount = money.parse_amount(amount, currency, get_settings().max_transaction_amount)
    fp = request_fingerprint(
        {
            "op": "debit",
            "amount": amount,
            "currency": currency,
            "wallet_currency": wallet_currency,
            "description": description,
        }
    )

    def work(session: ClientSession) -> list[LedgerEntry]:
        wallet = find_wallet(db, user.id, wallet_currency, session)
        if wallet is None:
            raise NotFound(f"You have no {wallet_currency} wallet", code="WALLET_NOT_FOUND")
        rates = fx_service.rates_if_needed(db, currency, wallet_currency)
        conv = fx_service.convert(amount, currency, wallet_currency, rates)
        return [
            post_entry(
                db,
                session,
                wallet,
                TransactionType.DEBIT,
                conv,
                amount=conv.target_amount,
                description=description,
                idempotency_key=idempotency_key,
                request_hash=fp,
            )
        ]

    return _run_idempotent(db, user.id, idempotency_key, fp, "debit", work)


def exchange(
    db: Db,
    user: User,
    *,
    amount: Decimal,
    from_currency: str,
    to_currency: str,
    idempotency_key: str | None = None,
) -> OperationResult:
    """Move value between two of the user's own wallets."""
    from_currency = money.validate_currency(from_currency)
    to_currency = money.validate_currency(to_currency)
    if from_currency == to_currency:
        raise ValidationFailed("Source and target currencies must differ")
    amount = money.parse_amount(amount, from_currency, get_settings().max_transaction_amount)
    fp = request_fingerprint(
        {"op": "exchange", "amount": amount, "from": from_currency, "to": to_currency}
    )

    def work(session: ClientSession) -> list[LedgerEntry]:
        source = find_wallet(db, user.id, from_currency, session)
        if source is None:
            raise NotFound(f"You have no {from_currency} wallet", code="WALLET_NOT_FOUND")
        rates = fx_service.current_rates(db)
        conv = fx_service.convert(amount, from_currency, to_currency, rates)
        target = get_or_create_wallet(db, user.id, to_currency, session)
        ref = uuid.uuid4()
        out_conv = Conversion(
            amount, from_currency, amount, from_currency, conv.rate, conv.snapshot_id
        )
        return [
            post_entry(
                db,
                session,
                source,
                TransactionType.CONVERSION_OUT,
                out_conv,
                amount=amount,
                reference_id=ref,
                description=f"Exchange to {to_currency}",
                idempotency_key=idempotency_key,
                request_hash=fp,
            ),
            post_entry(
                db,
                session,
                target,
                TransactionType.CONVERSION_IN,
                conv,
                amount=conv.target_amount,
                reference_id=ref,
                description=f"Exchange from {from_currency}",
            ),
        ]

    return _run_idempotent(db, user.id, idempotency_key, fp, "exchange", work)


def total_in_currency(
    wallets: list[Wallet], target: str, rates: fx_service.Rates | None
) -> Decimal | None:
    total = Decimal(0)
    for w in wallets:
        if w.currency == target:
            total += w.balance
        elif rates is None:
            return None
        else:
            total += w.balance * money.cross_rate(rates.rates, w.currency, target)
    return money.quantize(total, target)
