"""baseline schema

Revision ID: d9cbecac097c
Revises:
Create Date: 2026-04-05 15:02:53.901009

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "d9cbecac097c"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Baseline migration — tables are created by SQLModel.metadata.create_all().

    This migration exists solely to establish the Alembic version tracking
    baseline. Existing databases should run `alembic stamp head` to mark
    themselves as current without executing any SQL.
    """
    pass


def downgrade() -> None:
    """No downgrade for baseline."""
    pass
