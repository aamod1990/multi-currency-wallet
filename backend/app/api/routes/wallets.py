from fastapi import APIRouter, Response, status

from app.api.deps import DB, CurrentUser, IdempotencyKey
from app.api.schemas import (
    CreateWalletRequest,
    ExchangeRequest,
    MoneyMovementRequest,
    OperationResponse,
    TransactionOut,
    WalletList,
    WalletOut,
)
from app.core.errors import AppError
from app.services import fx_service, wallet_service
from app.services.wallet_service import OperationResult

router = APIRouter(prefix="/wallets", tags=["wallets"])


def _op_response(db, result: OperationResult, response: Response) -> OperationResponse:
    if result.replayed:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
    wallet_ids = {e.wallet_id for e in result.entries}
    wallets = [
        w for w in wallet_service.list_wallets(db, result.entries[0].user_id) if w.id in wallet_ids
    ]
    return OperationResponse(
        transactions=[TransactionOut.model_validate(e) for e in result.entries],
        wallets=[WalletOut.model_validate(w) for w in wallets],
    )


@router.get("", response_model=WalletList)
def list_wallets(user: CurrentUser, db: DB) -> WalletList:
    wallets = wallet_service.list_wallets(db, user.id)
    try:
        rates = fx_service.current_rates(db)
    except AppError:
        rates = None
    return WalletList(
        wallets=[WalletOut.model_validate(w) for w in wallets],
        total=wallet_service.total_in_currency(wallets, user.default_currency, rates),
        total_currency=user.default_currency,
        rates_as_of=rates.snapshot.fetched_at if rates else None,
    )


@router.post("", response_model=WalletOut, status_code=status.HTTP_201_CREATED)
def create_wallet(body: CreateWalletRequest, user: CurrentUser, db: DB) -> WalletOut:
    wallet = wallet_service.get_or_create_wallet(db, user.id, body.currency)
    db.commit()
    return WalletOut.model_validate(wallet)


@router.post("/credit", response_model=OperationResponse, status_code=status.HTTP_201_CREATED)
def credit(
    body: MoneyMovementRequest,
    user: CurrentUser,
    db: DB,
    key: IdempotencyKey,
    response: Response,
) -> OperationResponse:
    result = wallet_service.credit(
        db,
        user,
        amount=body.amount,
        currency=body.currency,
        wallet_currency=body.wallet_currency,
        description=body.description,
        idempotency_key=key,
    )
    return _op_response(db, result, response)


@router.post("/debit", response_model=OperationResponse, status_code=status.HTTP_201_CREATED)
def debit(
    body: MoneyMovementRequest,
    user: CurrentUser,
    db: DB,
    key: IdempotencyKey,
    response: Response,
) -> OperationResponse:
    result = wallet_service.debit(
        db,
        user,
        amount=body.amount,
        currency=body.currency,
        wallet_currency=body.wallet_currency,
        description=body.description,
        idempotency_key=key,
    )
    return _op_response(db, result, response)


@router.post("/exchange", response_model=OperationResponse, status_code=status.HTTP_201_CREATED)
def exchange(
    body: ExchangeRequest, user: CurrentUser, db: DB, key: IdempotencyKey, response: Response
) -> OperationResponse:
    result = wallet_service.exchange(
        db,
        user,
        amount=body.amount,
        from_currency=body.from_currency,
        to_currency=body.to_currency,
        idempotency_key=key,
    )
    return _op_response(db, result, response)
