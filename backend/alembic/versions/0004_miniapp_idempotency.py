"""Store mini-app mutation results for safe retries.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-28 13:30:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "miniapp_idempotency",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=96), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "key_hash", name="uq_miniapp_idempotency_user_key"),
    )
    op.create_index(
        "ix_miniapp_idempotency_created_at", "miniapp_idempotency", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_miniapp_idempotency_created_at", table_name="miniapp_idempotency")
    op.drop_table("miniapp_idempotency")
