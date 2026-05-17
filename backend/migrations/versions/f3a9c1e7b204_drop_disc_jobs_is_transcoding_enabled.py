"""drop disc_jobs.is_transcoding_enabled

Revision ID: f3a9c1e7b204
Revises: 9b793042b934
Create Date: 2026-05-16 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a9c1e7b204"
down_revision: str | Sequence[str] | None = "9b793042b934"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("disc_jobs", schema=None) as batch_op:
        batch_op.drop_column("is_transcoding_enabled")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("disc_jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("is_transcoding_enabled", sa.Boolean(), nullable=True))
