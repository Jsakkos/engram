"""add play_all_detection_enabled

Revision ID: e7c94a1d3b28
Revises: d5b28c31f907
Create Date: 2026-08-22 17:58:41.226104

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7c94a1d3b28"
down_revision: str | Sequence[str] | None = "d5b28c31f907"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "app_config",
        sa.Column(
            "play_all_detection_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("app_config", "play_all_detection_enabled")
