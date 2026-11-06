"""add makemkv_min_title_length (MakeMKV --minlength)

Revision ID: 5c2d8e1f4a7b
Revises: b7d1e4a9c2f3
Create Date: 2026-10-20 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.migration_guards import add_column_if_missing

# revision identifiers, used by Alembic.
revision: str = "5c2d8e1f4a7b"
down_revision: str | Sequence[str] | None = "b7d1e4a9c2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    add_column_if_missing(
        "app_config",
        sa.Column(
            "makemkv_min_title_length",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("120"),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("app_config", "makemkv_min_title_length")
