"""drop disc_jobs.is_transcoding_enabled

Revision ID: f3a9c1e7b204
Revises: 9b793042b934
Create Date: 2026-05-16 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from app.migration_guards import add_column_if_missing, drop_column_if_exists

# revision identifiers, used by Alembic.
revision: str = "f3a9c1e7b204"
down_revision: str | Sequence[str] | None = "9b793042b934"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema.

    _drop_extra_columns() in init_db() runs before Alembic and has usually
    dropped this column already; an unguarded batch drop_column then raised
    KeyError and froze alembic_version here, blocking every later revision.
    """
    drop_column_if_exists("disc_jobs", "is_transcoding_enabled")


def downgrade() -> None:
    """Downgrade schema."""
    add_column_if_missing(
        "disc_jobs", sa.Column("is_transcoding_enabled", sa.Boolean(), nullable=True)
    )
