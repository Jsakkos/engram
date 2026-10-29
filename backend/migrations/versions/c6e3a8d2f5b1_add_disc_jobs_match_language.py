"""add disc_jobs.content_language / match_language

The show's TMDB original language and the language its episodes are matched in
(issue #241, phase 2), as normalized codes. Mirrors the database.py reconciler
path used by frozen builds (which skip Alembic); the two must stay in agreement.

Revision ID: c6e3a8d2f5b1
Revises: a4c8e2f1b9d7
Create Date: 2026-10-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.migration_guards import add_column_if_missing

# revision identifiers, used by Alembic.
revision: str = "c6e3a8d2f5b1"
down_revision: str | Sequence[str] | None = "a4c8e2f1b9d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    add_column_if_missing("disc_jobs", sa.Column("content_language", sa.String(), nullable=True))
    add_column_if_missing("disc_jobs", sa.Column("match_language", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("disc_jobs", schema=None) as batch_op:
        batch_op.drop_column("match_language")
        batch_op.drop_column("content_language")
