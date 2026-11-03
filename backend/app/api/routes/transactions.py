import math
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api.deps import DB, CurrentUser
from app.api.schemas import Page, TransactionDetail, TransactionOut
from app.domain.enums import TransactionType
from app.domain.money import validate_currency
from app.services import history_service
from app.services.history_service import HistoryFilter

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.get("", response_model=Page[TransactionOut])
def list_transactions(
    user: CurrentUser,
    db: DB,
    type: Annotated[list[TransactionType] | None, Query()] = None,
    currency: Annotated[str | None, Query(min_length=3, max_length=3)] = None,
    direction: Literal["in", "out"] | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    min_amount: Annotated[Decimal | None, Query(ge=0)] = None,
    max_amount: Annotated[Decimal | None, Query(ge=0)] = None,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[TransactionOut]:
    f = HistoryFilter(
        types=type,
        currency=validate_currency(currency) if currency else None,
        direction=direction,
        date_from=date_from,
        date_to=date_to,
        min_amount=min_amount,
        max_amount=max_amount,
    )
    items, total = history_service.list_entries(db, user.id, f, page, page_size)
    return Page[TransactionOut](
        items=[TransactionOut.model_validate(e) for e in items],
        total=total,
        page=page,
        page_size=page_size,
        pages=max(1, math.ceil(total / page_size)),
    )


@router.get("/{entry_id}", response_model=TransactionDetail)
def get_transaction(entry_id: uuid.UUID, user: CurrentUser, db: DB) -> TransactionDetail:
    return TransactionDetail.model_validate(history_service.get_entry(db, user.id, entry_id))
