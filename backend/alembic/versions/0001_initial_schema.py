"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-03
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fx_rate_snapshots",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("rates", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provider_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fx_rate_snapshots")),
    )
    op.create_index(
        "ix_fx_rate_snapshots_fetched_at", "fx_rate_snapshots", ["fetched_at"], unique=False
    )
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=100), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("default_currency", sa.String(length=3), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "transfers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("sender_id", sa.UUID(), nullable=False),
        sa.Column("recipient_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("source_amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("source_currency", sa.String(length=3), nullable=False),
        sa.Column("source_rate", sa.Numeric(precision=28, scale=10), nullable=False),
        sa.Column("target_amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("target_currency", sa.String(length=3), nullable=False),
        sa.Column("target_rate", sa.Numeric(precision=28, scale=10), nullable=False),
        sa.Column("fx_snapshot_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.Enum("COMPLETED", name="transfer_status"), nullable=False),
        sa.Column("note", sa.String(length=140), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_transfers_amount_positive")),
        sa.CheckConstraint("sender_id <> recipient_id", name=op.f("ck_transfers_no_self_transfer")),
        sa.ForeignKeyConstraint(
            ["fx_snapshot_id"],
            ["fx_rate_snapshots.id"],
            name=op.f("fk_transfers_fx_snapshot_id_fx_rate_snapshots"),
        ),
        sa.ForeignKeyConstraint(
            ["recipient_id"], ["users.id"], name=op.f("fk_transfers_recipient_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["sender_id"], ["users.id"], name=op.f("fk_transfers_sender_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transfers")),
        sa.UniqueConstraint("sender_id", "idempotency_key", name="uq_transfers_sender_idem"),
    )
    op.create_table(
        "user_photos",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("content_type", sa.String(length=50), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("etag", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_photos_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_photos")),
    )
    op.create_table(
        "wallets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("balance", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("balance >= 0", name=op.f("ck_wallets_balance_non_negative")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_wallets_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wallets")),
        sa.UniqueConstraint("user_id", "currency", name="uq_wallets_user_currency"),
    )
    op.create_index(op.f("ix_wallets_user_id"), "wallets", ["user_id"], unique=False)
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("wallet_id", sa.UUID(), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "CREDIT",
                "DEBIT",
                "TRANSFER_OUT",
                "TRANSFER_IN",
                "CONVERSION_OUT",
                "CONVERSION_IN",
                name="transaction_type",
            ),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("balance_after", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("original_amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("original_currency", sa.String(length=3), nullable=False),
        sa.Column("fx_rate", sa.Numeric(precision=28, scale=10), nullable=False),
        sa.Column("fx_snapshot_id", sa.UUID(), nullable=True),
        sa.Column("reference_id", sa.UUID(), nullable=True),
        sa.Column("counterparty_id", sa.UUID(), nullable=True),
        sa.Column("description", sa.String(length=140), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_ledger_entries_amount_positive")),
        sa.CheckConstraint(
            "balance_after >= 0", name=op.f("ck_ledger_entries_balance_after_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["counterparty_id"],
            ["users.id"],
            name=op.f("fk_ledger_entries_counterparty_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["fx_snapshot_id"],
            ["fx_rate_snapshots.id"],
            name=op.f("fk_ledger_entries_fx_snapshot_id_fx_rate_snapshots"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_ledger_entries_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["wallet_id"],
            ["wallets.id"],
            name=op.f("fk_ledger_entries_wallet_id_wallets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_entries")),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_ledger_user_idem"),
    )
    op.create_index("ix_ledger_reference", "ledger_entries", ["reference_id"], unique=False)
    op.create_index(
        "ix_ledger_user_created", "ledger_entries", ["user_id", "created_at"], unique=False
    )
    op.create_index(
        "ix_ledger_wallet_created", "ledger_entries", ["wallet_id", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_ledger_wallet_created", table_name="ledger_entries")
    op.drop_index("ix_ledger_user_created", table_name="ledger_entries")
    op.drop_index("ix_ledger_reference", table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_index(op.f("ix_wallets_user_id"), table_name="wallets")
    op.drop_table("wallets")
    op.drop_table("user_photos")
    op.drop_table("transfers")
    op.drop_table("users")
    op.drop_index("ix_fx_rate_snapshots_fetched_at", table_name="fx_rate_snapshots")
    op.drop_table("fx_rate_snapshots")
    sa.Enum(name="transaction_type").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="transfer_status").drop(op.get_bind(), checkfirst=True)
