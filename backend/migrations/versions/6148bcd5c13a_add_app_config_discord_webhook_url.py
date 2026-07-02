"""add app_config.discord_webhook_url

Revision ID: 6148bcd5c13a
Revises: bff8c5e2c810
Create Date: 2026-07-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6148bcd5c13a"
down_revision: str | Sequence[str] | None = "bff8c5e2c810"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "app_config",
        sa.Column(
            "discord_webhook_url",
            sa.String(),
            nullable=False,
            server_default=sa.text("''"),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.drop_column("discord_webhook_url")
