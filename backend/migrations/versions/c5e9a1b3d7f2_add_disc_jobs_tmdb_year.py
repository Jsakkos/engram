"""add disc_jobs.tmdb_year

Persists the resolved show's first-air year on a job so the organizer can build
a disambiguated library folder (e.g. Frasier 1993 #3452 vs the 2023 revival
#195241) deterministically and offline. Resolved at identify time from the
same-name candidates (no extra network) or a cached TMDB details lookup. Mirrors
the database.py reconciler path used by frozen builds (which skip Alembic) — the
two must stay in agreement.

Revision ID: c5e9a1b3d7f2
Revises: c4d8e1f0a2b3
Create Date: 2026-06-02 12:50:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5e9a1b3d7f2"
down_revision: str | Sequence[str] | None = "c4d8e1f0a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("disc_jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("tmdb_year", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("disc_jobs", schema=None) as batch_op:
        batch_op.drop_column("tmdb_year")
