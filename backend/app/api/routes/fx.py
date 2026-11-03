import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import DB, CurrentUser
from app.api.schemas import CurrencyCode, FxSnapshotDetail, QuoteOut
from app.core.config import get_settings
from app.domain import money
from app.services import fx_service

router = APIRouter(prefix="/fx", tags=["fx"])


@router.get("/currencies", response_model=list[str])
def currencies(db: DB) -> list[str]:
    return fx_service.supported_currencies(db)


@router.get("/rates", response_model=FxSnapshotDetail)
def latest_rates(db: DB, _: CurrentUser) -> FxSnapshotDetail:
    return FxSnapshotDetail.model_validate(fx_service.current_rates(db).snapshot)


@router.get("/snapshots/{snapshot_id}", response_model=FxSnapshotDetail)
def snapshot(snapshot_id: uuid.UUID, db: DB, _: CurrentUser) -> FxSnapshotDetail:
    """Audit endpoint: the exact rates a historical conversion used."""
    return FxSnapshotDetail.model_validate(fx_service.get_snapshot(db, snapshot_id))


@router.get("/quote", response_model=QuoteOut)
def quote(
    db: DB,
    _: CurrentUser,
    amount: Annotated[Decimal, Query(gt=0)],
    from_currency: Annotated[CurrencyCode, Query(alias="from")],
    to_currency: Annotated[CurrencyCode, Query(alias="to")],
) -> QuoteOut:
    src = money.validate_currency(from_currency)
    tgt = money.validate_currency(to_currency)
    amount = money.parse_amount(amount, src, get_settings().max_transaction_amount)
    rates = fx_service.rates_if_needed(db, src, tgt)
    conv = fx_service.convert(amount, src, tgt, rates)
    return QuoteOut(
        amount=amount,
        from_currency=src,
        to_currency=tgt,
        rate=conv.rate,
        converted_amount=conv.target_amount,
        snapshot_id=conv.snapshot_id,
        rates_as_of=rates.snapshot.fetched_at if rates else None,
    )
