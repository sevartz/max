"""publish delay after approval: 10 → 1 minute

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-28 12:50:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column('channel_settings', 'publish_delay_min', server_default='1')
    # Каналы со старым значением по умолчанию переводим на новое; явно изменённые не трогаем.
    op.execute("UPDATE channel_settings SET publish_delay_min = 1 WHERE publish_delay_min = 10")


def downgrade() -> None:
    op.alter_column('channel_settings', 'publish_delay_min', server_default='10')
    op.execute("UPDATE channel_settings SET publish_delay_min = 10 WHERE publish_delay_min = 1")
