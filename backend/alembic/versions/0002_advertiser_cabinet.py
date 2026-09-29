"""advertiser cabinet: owner of advertiser, dialog states

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-28 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('advertisers', sa.Column('owner_user_id', sa.BigInteger(), nullable=True))
    op.create_unique_constraint('uq_advertisers_owner_user_id', 'advertisers', ['owner_user_id'])
    op.create_table('dialog_states',
    sa.Column('user_id', sa.BigInteger(), autoincrement=False, nullable=False),
    sa.Column('flow', sa.String(length=32), nullable=False),
    sa.Column('step', sa.String(length=32), nullable=False),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('user_id')
    )


def downgrade() -> None:
    op.drop_table('dialog_states')
    op.drop_constraint('uq_advertisers_owner_user_id', 'advertisers', type_='unique')
    op.drop_column('advertisers', 'owner_user_id')
