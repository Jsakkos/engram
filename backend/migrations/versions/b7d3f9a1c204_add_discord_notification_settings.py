"""add discord notification settings

Revision ID: b7d3f9a1c204
Revises: f1a2b3c4d5e6
Create Date: 2026-08-15 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d3f9a1c204"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "app_config",
        sa.Column(
            "discord_template_review", sa.String(), nullable=False, server_default=sa.text("''")
        ),
    )
    op.add_column(
        "app_config",
        sa.Column(
            "discord_notify_completed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column(
        "app_config",
        sa.Column(
            "discord_notify_failed", sa.Boolean(), nullable=False, server_default=sa.text("1")
        ),
    )
    op.add_column(
        "app_config",
        sa.Column(
            "discord_notify_review", sa.Boolean(), nullable=False, server_default=sa.text("1")
        ),
    )
    op.add_column(
        "app_config",
        sa.Column(
            "discord_mention_review", sa.String(), nullable=False, server_default=sa.text("''")
        ),
    )
    op.add_column(
        "app_config",
        sa.Column("dashboard_base_url", sa.String(), nullable=False, server_default=sa.text("''")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.drop_column("dashboard_base_url")
        batch_op.drop_column("discord_mention_review")
        batch_op.drop_column("discord_notify_review")
        batch_op.drop_column("discord_notify_failed")
        batch_op.drop_column("discord_notify_completed")
        batch_op.drop_column("discord_template_review")
