"""add disc_titles.output_index

Records the disc-native "_tNN" number MakeMKV embeds in a title's suggested
output filename (TINFO attr 27), captured at scan time. Some discs number
titles starting at 1 (no "t00") or with gaps, so this can differ from
title_index (the 0-based scan-order position) — see issue #517. Mirrors the
database.py reconciler path used by frozen builds (which skip Alembic) — the
two must stay in agreement.

Revision ID: f1a2b3c4d5e6
Revises: 33568e53d94d
Create Date: 2026-07-19 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "33568e53d94d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("disc_titles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("output_index", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("disc_titles", schema=None) as batch_op:
        batch_op.drop_column("output_index")
