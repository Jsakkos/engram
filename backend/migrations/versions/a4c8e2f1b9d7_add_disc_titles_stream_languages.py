"""add disc_titles.audio_languages / subtitle_languages

Disc stream languages from the MakeMKV scan (SINFO attr 3), stored as JSON
lists of normalized codes (issue #241). Mirrors the database.py reconciler path
used by frozen builds (which skip Alembic); the two must stay in agreement.

Revision ID: a4c8e2f1b9d7
Revises: 5c2d8e1f4a7b
Create Date: 2026-10-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.migration_guards import add_column_if_missing

# revision identifiers, used by Alembic.
revision: str = "a4c8e2f1b9d7"
down_revision: str | Sequence[str] | None = "5c2d8e1f4a7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    add_column_if_missing("disc_titles", sa.Column("audio_languages", sa.String(), nullable=True))
    add_column_if_missing(
        "disc_titles", sa.Column("subtitle_languages", sa.String(), nullable=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("disc_titles", schema=None) as batch_op:
        batch_op.drop_column("subtitle_languages")
        batch_op.drop_column("audio_languages")
