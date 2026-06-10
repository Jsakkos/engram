"""add disc_titles.rerip_attempts

Tracks how many times a rip-failed title has been re-ripped (Feature C —
single-track re-rip after clean & reinsert). Mirrors the database.py reconciler
path used by frozen builds (which skip Alembic) — the two must stay in agreement.

Revision ID: d4e5f6a7b8c9
Revises: c5e9a1b3d7f2
Create Date: 2026-06-09 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c5e9a1b3d7f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("disc_titles", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("rerip_attempts", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("disc_titles", schema=None) as batch_op:
        batch_op.drop_column("rerip_attempts")
