"""add backup columns

Revision ID: 02946b05fe8d
Revises: a4c81f6b2e39
Create Date: 2026-09-05 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "02946b05fe8d"
down_revision: str | Sequence[str] | None = "a4c81f6b2e39"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("disc_jobs", sa.Column("source_spec", sa.String(), nullable=True))
    op.add_column("disc_jobs", sa.Column("backup_path", sa.String(), nullable=True))
    op.add_column("disc_jobs", sa.Column("backup_status", sa.String(), nullable=True))
    op.add_column(
        "app_config",
        sa.Column("backup_path", sa.String(), nullable=False, server_default=sa.text("''")),
    )
    # server_default 0: this is opt-in, so an upgraded row must come back OFF.
    op.add_column(
        "app_config",
        sa.Column("backup_before_rip", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "app_config",
        sa.Column(
            "timeout_backing_up_seconds",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("7200"),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.drop_column("timeout_backing_up_seconds")
        batch_op.drop_column("backup_before_rip")
        batch_op.drop_column("backup_path")
    with op.batch_alter_table("disc_jobs", schema=None) as batch_op:
        batch_op.drop_column("backup_status")
        batch_op.drop_column("backup_path")
        batch_op.drop_column("source_spec")
