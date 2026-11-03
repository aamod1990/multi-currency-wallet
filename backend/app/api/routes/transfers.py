import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import DB, CurrentUser, IdempotencyKey
from app.api.schemas import TransferOut, TransferRequest
from app.services import transfer_service

router = APIRouter(prefix="/transfers", tags=["transfers"])


@router.post("", response_model=TransferOut, status_code=status.HTTP_201_CREATED)
def create_transfer(
    body: TransferRequest, user: CurrentUser, db: DB, key: IdempotencyKey, response: Response
) -> TransferOut:
    result = transfer_service.create_transfer(
        db,
        user,
        recipient_email=body.recipient_email,
        amount=body.amount,
        currency=body.currency,
        source_currency=body.source_currency,
        note=body.note,
        idempotency_key=key,
    )
    if result.replayed:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
    return TransferOut.model_validate(result.transfer)


@router.get("/{transfer_id}", response_model=TransferOut)
def get_transfer(transfer_id: uuid.UUID, user: CurrentUser, db: DB) -> TransferOut:
    return TransferOut.model_validate(transfer_service.get_transfer(db, user.id, transfer_id))
