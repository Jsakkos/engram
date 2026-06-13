"""phaseC disc_contributions table

Revision ID: a4f1c8d20e93
Revises: 5ea422081173
Create Date: 2026-06-12 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4f1c8d20e93"
down_revision: str | Sequence[str] | None = "5ea422081173"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "disc_contributions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "queued_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(datetime('now'))"),
        ),
        sa.Column("disc_content_hash", sa.LargeBinary(), nullable=False),
        sa.Column("tmdb_id", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.Column("titles_json", sa.String(), nullable=False),
        sa.Column("pseudonym", sa.String(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), nullable=True),
        sa.Column("upload_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("upload_status", sa.String(), nullable=True),
        sa.Column("upload_error_msg", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("disc_contributions")
