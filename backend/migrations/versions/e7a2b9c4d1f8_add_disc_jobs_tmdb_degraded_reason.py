"""add disc_jobs.tmdb_degraded_reason

Human-readable cause recorded when classification ran without TMDB because the
API key was absent or rejected (#243 P3). Mirrors the database.py reconciler
path used by frozen builds (which skip Alembic) — the two must stay in agreement.

Revision ID: e7a2b9c4d1f8
Revises: d4e5f6a7b8c9
Create Date: 2026-06-09 22:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7a2b9c4d1f8"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("disc_jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("tmdb_degraded_reason", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("disc_jobs", schema=None) as batch_op:
        batch_op.drop_column("tmdb_degraded_reason")
