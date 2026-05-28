"""phase2_disclosure_fields

Revision ID: a1b2c3d4e5f6
Revises: 7d7a3fc6a743
Create Date: 2026-05-28 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "7d7a3fc6a743"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "fingerprint_disclosure_accepted",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.add_column(
            sa.Column("fingerprint_disclosure_accepted_at", sa.DateTime(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.drop_column("fingerprint_disclosure_accepted_at")
        batch_op.drop_column("fingerprint_disclosure_accepted")
