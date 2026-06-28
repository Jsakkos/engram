"""add disc_jobs import_manifest_json

Revision ID: bff8c5e2c810
Revises: 257485de2655
Create Date: 2026-06-27 22:15:17.363107

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bff8c5e2c810"
down_revision: str | Sequence[str] | None = "257485de2655"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("disc_jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("import_manifest_json", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("disc_jobs", schema=None) as batch_op:
        batch_op.drop_column("import_manifest_json")
