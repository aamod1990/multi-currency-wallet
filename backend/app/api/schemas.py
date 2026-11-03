import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, PlainSerializer, field_validator

from app.domain.enums import TransactionType

# Amounts travel as strings in JSON so clients never lose precision to floats.
Money = Annotated[Decimal, PlainSerializer(lambda v: format(v.normalize(), "f"), return_type=str)]
CurrencyCode = Annotated[str, Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")]
Note = Annotated[str | None, Field(default=None, max_length=140)]
T = TypeVar("T")


def _upper(v: str | None) -> str | None:
    return v.upper() if isinstance(v, str) else v


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- auth / users ----
class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=120)
    default_currency: CurrencyCode = "USD"

    _u = field_validator("default_currency")(_upper)

    @field_validator("password")
    @classmethod
    def _strength(cls, v: str) -> str:
        if not any(c.isalpha() for c in v) or not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one letter and one digit")
        return v

    @field_validator("full_name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Full name is required")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserOut(ORM):
    id: uuid.UUID
    email: str
    full_name: str
    default_currency: str
    has_photo: bool = False
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    default_currency: CurrencyCode | None = None

    _u = field_validator("default_currency")(_upper)


# ---- wallets ----
class WalletOut(ORM):
    id: uuid.UUID
    currency: str
    balance: Money
    updated_at: datetime


class WalletList(BaseModel):
    wallets: list[WalletOut]
    total: Money | None = Field(description="Sum of balances in the user's default currency")
    total_currency: str
    rates_as_of: datetime | None


class CreateWalletRequest(BaseModel):
    currency: CurrencyCode
    _u = field_validator("currency")(_upper)


class MoneyMovementRequest(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=20, decimal_places=4)
    currency: CurrencyCode = Field(description="Currency the amount is denominated in")
    wallet_currency: CurrencyCode | None = Field(
        default=None, description="Wallet to credit/debit; defaults to `currency`"
    )
    description: Note

    _u = field_validator("currency", "wallet_currency")(_upper)


class ExchangeRequest(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=20, decimal_places=4)
    from_currency: CurrencyCode
    to_currency: CurrencyCode

    _u = field_validator("from_currency", "to_currency")(_upper)


class CounterpartyOut(ORM):
    id: uuid.UUID
    full_name: str
    email: str


class TransactionOut(ORM):
    id: uuid.UUID
    wallet_id: uuid.UUID
    type: TransactionType
    amount: Money
    currency: str
    balance_after: Money
    original_amount: Money
    original_currency: str
    fx_rate: Money
    fx_snapshot_id: uuid.UUID | None
    reference_id: uuid.UUID | None
    counterparty: CounterpartyOut | None
    description: str | None
    created_at: datetime


class FxSnapshotOut(ORM):
    id: uuid.UUID
    provider: str
    base_currency: str
    provider_updated_at: datetime | None
    fetched_at: datetime


class FxSnapshotDetail(FxSnapshotOut):
    rates: dict[str, str]


class TransactionDetail(TransactionOut):
    fx_snapshot: FxSnapshotOut | None


class OperationResponse(BaseModel):
    transactions: list[TransactionOut]
    wallets: list[WalletOut]


# ---- transfers ----
class TransferRequest(BaseModel):
    recipient_email: EmailStr
    amount: Decimal = Field(gt=0, max_digits=20, decimal_places=4)
    currency: CurrencyCode
    source_currency: CurrencyCode | None = Field(
        default=None, description="Sender wallet to debit; defaults to `currency`"
    )
    note: Note

    _u = field_validator("currency", "source_currency")(_upper)


class TransferOut(ORM):
    id: uuid.UUID
    sender: CounterpartyOut
    recipient: CounterpartyOut
    amount: Money
    currency: str
    source_amount: Money
    source_currency: str
    source_rate: Money
    target_amount: Money
    target_currency: str
    target_rate: Money
    fx_snapshot_id: uuid.UUID | None
    status: str
    note: str | None
    created_at: datetime


# ---- fx ----
class QuoteOut(BaseModel):
    amount: Money
    from_currency: str
    to_currency: str
    rate: Money
    converted_amount: Money
    snapshot_id: uuid.UUID | None
    rates_as_of: datetime | None


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int
