"""phase2_uploader_fields

Revision ID: 7d7a3fc6a743
Revises: 0b510750d192
Create Date: 2026-05-28 09:26:48.064320

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7d7a3fc6a743"
down_revision: str | Sequence[str] | None = "0b510750d192"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("fingerprint_contributions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("upload_status", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("upload_error_msg", sa.String(), nullable=True))
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.add_column(sa.Column("fingerprint_server_url", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.drop_column("fingerprint_server_url")
    with op.batch_alter_table("fingerprint_contributions", schema=None) as batch_op:
        batch_op.drop_column("upload_error_msg")
        batch_op.drop_column("upload_status")
