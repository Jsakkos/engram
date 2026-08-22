"""add always_show_episode_span

Revision ID: d5b28c31f907
Revises: c3e17a2b8d40
Create Date: 2026-08-22 17:41:18.902355

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5b28c31f907"
down_revision: str | Sequence[str] | None = "c3e17a2b8d40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "app_config",
        sa.Column(
            "always_show_episode_span", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("app_config", "always_show_episode_span")
