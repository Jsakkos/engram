"""fingerprint contribution show_title

Adds a nullable human-readable show name on fingerprint_contributions for local
display/diagnostics (logs, future queue UI). Not part of the upload identity —
the server keys off tmdb_id+season+episode. Mirrors the database.py reconciler
path used by frozen builds (which skip Alembic) — the two must stay in agreement.

Revision ID: b7f4c2e9a318
Revises: e1f2a3b4c5d6
Create Date: 2026-05-29 17:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7f4c2e9a318"
down_revision: str | Sequence[str] | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("fingerprint_contributions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("show_title", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("fingerprint_contributions", schema=None) as batch_op:
        batch_op.drop_column("show_title")
