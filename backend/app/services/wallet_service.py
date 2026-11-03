"""Wallet operations. Every balance mutation:

1. runs in a single DB transaction,
2. takes row locks (SELECT ... FOR UPDATE) on all wallets involved, in a stable
   order (by id) to rule out deadlocks,
3. appends ledger entries carrying `balance_after` and the FX snapshot used,
4. is protected by a DB CHECK (balance >= 0) as a last line of defence,
5. is optionally idempotent via a client-supplied Idempotency-Key.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import Conflict, InsufficientFunds, NotFound, ValidationFailed
from app.core.metrics import WALLET_OPERATIONS
from app.db.models import LedgerEntry, User, Wallet
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


def list_wallets(db: Session, user_id: uuid.UUID) -> list[Wallet]:
    return list(
        db.scalars(select(Wallet).where(Wallet.user_id == user_id).order_by(Wallet.currency))
    )


def find_wallet(db: Session, user_id: uuid.UUID, currency: str) -> Wallet | None:
    return db.scalar(select(Wallet).where(Wallet.user_id == user_id, Wallet.currency == currency))


def get_or_create_wallet(db: Session, user_id: uuid.UUID, currency: str) -> Wallet:
    """Race-safe upsert: concurrent creators converge on the same row."""
    currency = money.validate_currency(currency)
    db.execute(
        pg_insert(Wallet)
        .values(id=uuid.uuid4(), user_id=user_id, currency=currency, balance=0, version=0)
        .on_conflict_do_nothing(constraint="uq_wallets_user_currency")
    )
    wallet = find_wallet(db, user_id, currency)
    assert wallet is not None
    return wallet


def lock_wallets(db: Session, *wallet_ids: uuid.UUID) -> dict[uuid.UUID, Wallet]:
    rows = db.scalars(
        select(Wallet)
        .where(Wallet.id.in_(set(wallet_ids)))
        .order_by(Wallet.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    return {w.id: w for w in rows}


def post_entry(
    db: Session,
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
    """Apply one signed movement to a *locked* wallet and append its ledger entry."""
    if amount <= 0:
        raise ValidationFailed("Amount is too small to convert into the target currency")
    if tx_type.is_outflow:
        if wallet.balance < amount:
            raise InsufficientFunds(
                f"Insufficient funds in {wallet.currency} wallet",
                details={
                    "available": format(wallet.balance.normalize(), "f"),
                    "required": format(amount.normalize(), "f"),
                },
            )
        wallet.balance -= amount
    else:
        wallet.balance += amount
    wallet.version += 1
    entry = LedgerEntry(
        user_id=wallet.user_id,
        wallet_id=wallet.id,
        type=tx_type,
        amount=amount,
        currency=wallet.currency,
        balance_after=wallet.balance,
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
    db.add(entry)
    return entry


def _replay(db: Session, user_id: uuid.UUID, key: str | None, fp: str) -> OperationResult | None:
    if not key:
        return None
    entry = db.scalar(
        select(LedgerEntry).where(
            LedgerEntry.user_id == user_id, LedgerEntry.idempotency_key == key
        )
    )
    if entry is None:
        return None
    if entry.request_hash != fp:
        raise Conflict(
            "Idempotency-Key was already used with a different request",
            code="IDEMPOTENCY_KEY_REUSED",
        )
    entries = [entry]
    if entry.reference_id:
        entries = list(
            db.scalars(
                select(LedgerEntry)
                .where(LedgerEntry.reference_id == entry.reference_id)
                .order_by(LedgerEntry.created_at)
            )
        )
    return OperationResult(entries, replayed=True)


def _run_idempotent(
    db: Session,
    user_id: uuid.UUID,
    key: str | None,
    fp: str,
    operation: str,
    work: Callable[[], list[LedgerEntry]],
) -> OperationResult:
    if (prior := _replay(db, user_id, key, fp)) is not None:
        WALLET_OPERATIONS.labels(operation=operation, outcome="replayed").inc()
        return prior
    try:
        entries = work()
        db.commit()
    except IntegrityError:
        # A concurrent request with the same key won the race; return its result.
        db.rollback()
        if (prior := _replay(db, user_id, key, fp)) is not None:
            WALLET_OPERATIONS.labels(operation=operation, outcome="replayed").inc()
            return prior
        WALLET_OPERATIONS.labels(operation=operation, outcome="error").inc()
        raise
    except Exception as exc:
        db.rollback()
        outcome = getattr(exc, "code", "error").lower()
        WALLET_OPERATIONS.labels(operation=operation, outcome=outcome).inc()
        raise
    WALLET_OPERATIONS.labels(operation=operation, outcome="success").inc()
    log.info(
        "wallet_operation",
        extra={"operation": operation, "entries": [str(e.id) for e in entries]},
    )
    return OperationResult(entries)


def credit(
    db: Session,
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

    def work() -> list[LedgerEntry]:
        rates = fx_service.rates_if_needed(db, currency, wallet_currency)
        conv = fx_service.convert(amount, currency, wallet_currency, rates)
        wallet = get_or_create_wallet(db, user.id, wallet_currency)
        wallet = lock_wallets(db, wallet.id)[wallet.id]
        return [
            post_entry(
                db,
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
    db: Session,
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

    def work() -> list[LedgerEntry]:
        wallet = find_wallet(db, user.id, wallet_currency)
        if wallet is None:
            raise NotFound(f"You have no {wallet_currency} wallet", code="WALLET_NOT_FOUND")
        rates = fx_service.rates_if_needed(db, currency, wallet_currency)
        conv = fx_service.convert(amount, currency, wallet_currency, rates)
        wallet = lock_wallets(db, wallet.id)[wallet.id]
        return [
            post_entry(
                db,
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
    db: Session,
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

    def work() -> list[LedgerEntry]:
        source = find_wallet(db, user.id, from_currency)
        if source is None:
            raise NotFound(f"You have no {from_currency} wallet", code="WALLET_NOT_FOUND")
        rates = fx_service.current_rates(db)
        conv = fx_service.convert(amount, from_currency, to_currency, rates)
        target = get_or_create_wallet(db, user.id, to_currency)
        locked = lock_wallets(db, source.id, target.id)
        ref = uuid.uuid4()
        out_conv = Conversion(
            amount, from_currency, amount, from_currency, conv.rate, conv.snapshot_id
        )
        return [
            post_entry(
                db,
                locked[source.id],
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
                locked[target.id],
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
