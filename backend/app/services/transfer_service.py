import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal

from pymongo.client_session import ClientSession
from pymongo.errors import DuplicateKeyError

from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.metrics import TRANSFERS
from app.db.models import Transfer, User
from app.db.mongo import TRANSFERS as TRANSFERS_COLL
from app.db.mongo import Db, run_in_transaction
from app.db.repository import hydrate_transfer
from app.domain import money
from app.domain.enums import TransactionType
from app.services import fx_service, user_service, wallet_service
from app.services.fx_service import Conversion
from app.services.idempotency import request_fingerprint

log = logging.getLogger(__name__)


@dataclass
class TransferResult:
    transfer: Transfer
    replayed: bool = False


def _replay(
    db: Db, sender_id: uuid.UUID, key: str | None, fp: str, session: ClientSession | None = None
) -> TransferResult | None:
    if not key:
        return None
    doc = db[TRANSFERS_COLL].find_one(
        {"sender_id": sender_id, "idempotency_key": key}, session=session
    )
    if doc is None:
        return None
    if doc.get("request_hash") != fp:
        raise Conflict(
            "Idempotency-Key was already used with a different request",
            code="IDEMPOTENCY_KEY_REUSED",
        )
    return TransferResult(Transfer.from_doc(doc), replayed=True)


def create_transfer(
    db: Db,
    sender: User,
    *,
    recipient_email: str,
    amount: Decimal,
    currency: str,
    source_currency: str | None = None,
    note: str | None = None,
    idempotency_key: str | None = None,
) -> TransferResult:
    """Send `amount` `currency` to another user.

    - Sender is debited from their `source_currency` wallet (defaults to `currency`).
    - Recipient is credited in their default currency.
    - Both conversions use one FX snapshot, so the transfer is internally consistent.
    - Debit, credit, transfer record and both ledger legs commit in one transaction.
    """
    currency = money.validate_currency(currency)
    source_currency = money.validate_currency(source_currency or currency)
    amount = money.parse_amount(amount, currency, get_settings().max_transaction_amount)
    recipient_email = user_service.normalize_email(recipient_email)
    fp = request_fingerprint(
        {
            "op": "transfer",
            "to": recipient_email,
            "amount": amount,
            "currency": currency,
            "source_currency": source_currency,
            "note": note,
        }
    )
    if (prior := _replay(db, sender.id, idempotency_key, fp)) is not None:
        TRANSFERS.labels(outcome="replayed", cross_currency="n/a").inc()
        return TransferResult(hydrate_transfer(db, prior.transfer), replayed=True)

    recipient = user_service.get_by_email(db, recipient_email)
    if recipient is None:
        raise NotFound("Recipient not found", code="RECIPIENT_NOT_FOUND")
    if recipient.id == sender.id:
        raise ValidationFailed("You cannot transfer to yourself", code="SELF_TRANSFER")
    target_currency = recipient.default_currency

    def txn(session: ClientSession) -> TransferResult:
        if (prior := _replay(db, sender.id, idempotency_key, fp, session)) is not None:
            return prior
        source_wallet = wallet_service.find_wallet(db, sender.id, source_currency, session)
        if source_wallet is None:
            raise NotFound(f"You have no {source_currency} wallet", code="WALLET_NOT_FOUND")
        rates = fx_service.rates_if_needed(db, currency, source_currency, target_currency)
        debit_conv = fx_service.convert(amount, currency, source_currency, rates)
        credit_conv = fx_service.convert(amount, currency, target_currency, rates)
        target_wallet = wallet_service.get_or_create_wallet(
            db, recipient.id, target_currency, session
        )
        transfer = Transfer(
            sender_id=sender.id,
            recipient_id=recipient.id,
            amount=amount,
            currency=currency,
            source_amount=debit_conv.target_amount,
            source_currency=source_currency,
            source_rate=debit_conv.rate,
            target_amount=credit_conv.target_amount,
            target_currency=target_currency,
            target_rate=credit_conv.rate,
            fx_snapshot_id=rates.snapshot.id if rates else None,
            note=note,
            idempotency_key=idempotency_key,
            request_hash=fp,
        )
        db[TRANSFERS_COLL].insert_one(transfer.to_doc(), session=session)
        out_conv = Conversion(
            amount,
            currency,
            debit_conv.target_amount,
            source_currency,
            debit_conv.rate,
            debit_conv.snapshot_id,
        )
        wallet_service.post_entry(
            db,
            session,
            source_wallet,
            TransactionType.TRANSFER_OUT,
            out_conv,
            amount=debit_conv.target_amount,
            reference_id=transfer.id,
            counterparty_id=recipient.id,
            description=note,
        )
        wallet_service.post_entry(
            db,
            session,
            target_wallet,
            TransactionType.TRANSFER_IN,
            credit_conv,
            amount=credit_conv.target_amount,
            reference_id=transfer.id,
            counterparty_id=sender.id,
            description=note,
        )
        return TransferResult(transfer)

    try:
        result = run_in_transaction(db, txn)
    except DuplicateKeyError:
        if (prior := _replay(db, sender.id, idempotency_key, fp)) is not None:
            TRANSFERS.labels(outcome="replayed", cross_currency="n/a").inc()
            return TransferResult(hydrate_transfer(db, prior.transfer), replayed=True)
        TRANSFERS.labels(outcome="error", cross_currency="n/a").inc()
        raise
    except Exception as exc:
        outcome = str(getattr(exc, "code", "error")).lower()
        TRANSFERS.labels(outcome=outcome, cross_currency="n/a").inc()
        raise

    hydrate_transfer(db, result.transfer)
    if result.replayed:
        TRANSFERS.labels(outcome="replayed", cross_currency="n/a").inc()
        return result
    cross = str(len({currency, source_currency, target_currency}) > 1).lower()
    TRANSFERS.labels(outcome="success", cross_currency=cross).inc()
    log.info(
        "transfer_completed",
        extra={
            "transfer_id": str(result.transfer.id),
            "recipient_id": str(recipient.id),
            "cross_currency": cross,
        },
    )
    return result


def get_transfer(db: Db, user_id: uuid.UUID, transfer_id: uuid.UUID) -> Transfer:
    doc = db[TRANSFERS_COLL].find_one(
        {"_id": transfer_id, "$or": [{"sender_id": user_id}, {"recipient_id": user_id}]}
    )
    if doc is None:
        raise NotFound("Transfer not found")
    return hydrate_transfer(db, Transfer.from_doc(doc))
