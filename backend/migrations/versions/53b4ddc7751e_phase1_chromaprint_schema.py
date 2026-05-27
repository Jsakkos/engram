"""phase1 chromaprint schema

Revision ID: 53b4ddc7751e
Revises: f3a9c1e7b204
Create Date: 2026-05-27 16:50:28.761800

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "53b4ddc7751e"
down_revision: str | Sequence[str] | None = "f3a9c1e7b204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("disc_titles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("chromaprint_blob", sa.LargeBinary(), nullable=True))
        batch_op.add_column(sa.Column("chromaprint_extracted_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.add_column(sa.Column("fpcalc_path", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("contribution_pseudonym", sa.String(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "enable_fingerprint_contributions",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.drop_column("enable_fingerprint_contributions")
        batch_op.drop_column("contribution_pseudonym")
        batch_op.drop_column("fpcalc_path")
    with op.batch_alter_table("disc_titles", schema=None) as batch_op:
        batch_op.drop_column("chromaprint_extracted_at")
        batch_op.drop_column("chromaprint_blob")
