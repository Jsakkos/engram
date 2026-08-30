"""add discord ripped notification settings

Revision ID: a4c81f6b2e39
Revises: c3e17a2b8d40
Create Date: 2026-08-29 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4c81f6b2e39"
down_revision: str | Sequence[str] | None = "c3e17a2b8d40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "app_config",
        sa.Column(
            "discord_template_ripped", sa.String(), nullable=False, server_default=sa.text("''")
        ),
    )
    # server_default 0, unlike the other three notify toggles: this event is
    # opt-in, so an upgraded row must come back OFF.
    op.add_column(
        "app_config",
        sa.Column(
            "discord_notify_ripped", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.drop_column("discord_notify_ripped")
        batch_op.drop_column("discord_template_ripped")
