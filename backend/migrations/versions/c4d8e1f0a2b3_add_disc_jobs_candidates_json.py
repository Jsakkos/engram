"""add disc_jobs.candidates_json

Persists same-name TMDB collision candidates on a job (JSON list of
{tmdb_id, name, year, popularity}). Recorded at identify time whenever >=2
same-name shows exist (e.g. Frasier 1993 #3452 vs the 2023 revival #195241) so
the downstream wrong-show detector can suggest the right twin without
re-querying TMDB. Mirrors the database.py reconciler path used by frozen builds
(which skip Alembic) — the two must stay in agreement.

Revision ID: c4d8e1f0a2b3
Revises: b7f4c2e9a318
Create Date: 2026-06-01 18:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d8e1f0a2b3"
down_revision: str | Sequence[str] | None = "b7f4c2e9a318"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("disc_jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("candidates_json", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("disc_jobs", schema=None) as batch_op:
        batch_op.drop_column("candidates_json")
