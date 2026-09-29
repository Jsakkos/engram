"""add discord notification templates

Revision ID: 33568e53d94d
Revises: 6148bcd5c13a
Create Date: 2026-07-13 20:04:50.800238

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.migration_guards import add_column_if_missing

# revision identifiers, used by Alembic.
revision: str = "33568e53d94d"
down_revision: str | Sequence[str] | None = "6148bcd5c13a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    add_column_if_missing(
        "app_config",
        sa.Column(
            "discord_template_completed",
            sa.String(),
            nullable=False,
            server_default=sa.text("''"),
        ),
    )
    add_column_if_missing(
        "app_config",
        sa.Column(
            "discord_template_failed",
            sa.String(),
            nullable=False,
            server_default=sa.text("''"),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.drop_column("discord_template_failed")
        batch_op.drop_column("discord_template_completed")
