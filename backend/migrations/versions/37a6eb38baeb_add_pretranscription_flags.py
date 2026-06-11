"""add_pretranscription_flags

Revision ID: 37a6eb38baeb
Revises: e7a2b9c4d1f8
Create Date: 2026-06-11 14:22:44.061656

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "37a6eb38baeb"
down_revision: str | Sequence[str] | None = "e7a2b9c4d1f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "enable_background_pretranscription",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "pretranscribe_full_file",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.drop_column("pretranscribe_full_file")
        batch_op.drop_column("enable_background_pretranscription")
