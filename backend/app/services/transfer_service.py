import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.metrics import TRANSFERS
from app.db.models import Transfer, User
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


def _replay(db: Session, sender_id: uuid.UUID, key: str | None, fp: str) -> TransferResult | None:
    if not key:
        return None
    t = db.scalar(
        select(Transfer).where(Transfer.sender_id == sender_id, Transfer.idempotency_key == key)
    )
    if t is None:
        return None
    if t.request_hash != fp:
        raise Conflict(
            "Idempotency-Key was already used with a different request",
            code="IDEMPOTENCY_KEY_REUSED",
        )
    return TransferResult(t, replayed=True)


def create_transfer(
    db: Session,
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
        return prior

    recipient = user_service.get_by_email(db, recipient_email)
    if recipient is None:
        raise NotFound("Recipient not found", code="RECIPIENT_NOT_FOUND")
    if recipient.id == sender.id:
        raise ValidationFailed("You cannot transfer to yourself", code="SELF_TRANSFER")
    target_currency = recipient.default_currency

    try:
        source_wallet = wallet_service.find_wallet(db, sender.id, source_currency)
        if source_wallet is None:
            raise NotFound(f"You have no {source_currency} wallet", code="WALLET_NOT_FOUND")
        rates = fx_service.rates_if_needed(db, currency, source_currency, target_currency)
        debit_conv = fx_service.convert(amount, currency, source_currency, rates)
        credit_conv = fx_service.convert(amount, currency, target_currency, rates)
        target_wallet = wallet_service.get_or_create_wallet(db, recipient.id, target_currency)
        locked = wallet_service.lock_wallets(db, source_wallet.id, target_wallet.id)

        transfer = Transfer(
            id=uuid.uuid4(),
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
        db.add(transfer)
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
            locked[source_wallet.id],
            TransactionType.TRANSFER_OUT,
            out_conv,
            amount=debit_conv.target_amount,
            reference_id=transfer.id,
            counterparty_id=recipient.id,
            description=note,
        )
        wallet_service.post_entry(
            db,
            locked[target_wallet.id],
            TransactionType.TRANSFER_IN,
            credit_conv,
            amount=credit_conv.target_amount,
            reference_id=transfer.id,
            counterparty_id=sender.id,
            description=note,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        if (prior := _replay(db, sender.id, idempotency_key, fp)) is not None:
            return prior
        TRANSFERS.labels(outcome="error", cross_currency="n/a").inc()
        raise
    except Exception as exc:
        db.rollback()
        TRANSFERS.labels(outcome=getattr(exc, "code", "error").lower(), cross_currency="n/a").inc()
        raise

    cross = str(len({currency, source_currency, target_currency}) > 1).lower()
    TRANSFERS.labels(outcome="success", cross_currency=cross).inc()
    log.info(
        "transfer_completed",
        extra={
            "transfer_id": str(transfer.id),
            "recipient_id": str(recipient.id),
            "cross_currency": cross,
        },
    )
    return TransferResult(transfer)


def get_transfer(db: Session, user_id: uuid.UUID, transfer_id: uuid.UUID) -> Transfer:
    t = db.scalar(
        select(Transfer).where(
            Transfer.id == transfer_id,
            or_(Transfer.sender_id == user_id, Transfer.recipient_id == user_id),
        )
    )
    if t is None:
        raise NotFound("Transfer not found")
    return t
