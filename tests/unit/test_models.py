import uuid
from decimal import Decimal

from app.db.models import LedgerEntry, Transfer, User, next_seq
from app.domain.enums import TransactionType, TransferStatus


def _entry(**kw):
    base = dict(
        user_id=uuid.uuid4(),
        wallet_id=uuid.uuid4(),
        type=TransactionType.CREDIT,
        amount=Decimal("10.00"),
        currency="USD",
        balance_after=Decimal("10.00"),
        original_amount=Decimal("10.00"),
        original_currency="USD",
    )
    return LedgerEntry(**{**base, **kw})


def test_document_round_trip_maps_id_and_enums():
    e = _entry(type=TransactionType.TRANSFER_OUT)
    doc = e.to_doc()
    assert doc["_id"] == e.id and "id" not in doc
    assert doc["type"] == "TRANSFER_OUT" and type(doc["type"]) is str
    back = LedgerEntry.from_doc(doc)
    assert back.type is TransactionType.TRANSFER_OUT and back == e


def test_transient_relations_are_never_persisted():
    u = User(
        email="a@example.com", password_hash="x", full_name="A", default_currency="USD"
    )
    e = _entry(counterparty=u)
    assert "counterparty" not in e.to_doc() and "fx_snapshot" not in e.to_doc()
    t = Transfer(
        sender_id=u.id,
        recipient_id=uuid.uuid4(),
        amount=Decimal(1),
        currency="USD",
        source_amount=Decimal(1),
        source_currency="USD",
        source_rate=Decimal(1),
        target_amount=Decimal(1),
        target_currency="USD",
        target_rate=Decimal(1),
        sender=u,
    )
    doc = t.to_doc()
    assert "sender" not in doc and doc["status"] == "COMPLETED"
    assert Transfer.from_doc({**doc, "unknown": 1}).status is TransferStatus.COMPLETED


def test_seq_is_strictly_increasing():
    seqs = [next_seq() for _ in range(1000)]
    assert seqs == sorted(seqs) and len(set(seqs)) == 1000
