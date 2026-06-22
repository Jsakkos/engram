"""add fingerprint_retractions queue

Revision ID: 01f4f5567376
Revises: a4f1c8d20e93
Create Date: 2026-06-21 21:33:07.286268

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "01f4f5567376"
down_revision: str | Sequence[str] | None = "a4f1c8d20e93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fingerprint_retractions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "queued_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(datetime('now'))"),
        ),
        sa.Column("pseudonym", sa.String(), nullable=False),
        sa.Column("tmdb_id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.Column("episode", sa.Integer(), nullable=True),
        sa.Column("fingerprint_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), nullable=True),
        sa.Column("upload_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("upload_status", sa.String(), nullable=True),
        sa.Column("upload_error_msg", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("fingerprint_retractions")
