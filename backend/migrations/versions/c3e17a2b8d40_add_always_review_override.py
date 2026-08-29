"""add always_review manual-review override

Revision ID: c3e17a2b8d40
Revises: b7d3f9a1c204
Create Date: 2026-08-22 10:12:04.118722

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3e17a2b8d40"
down_revision: str | Sequence[str] | None = "b7d3f9a1c204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "app_config",
        sa.Column("always_review", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("app_config", "always_review")
