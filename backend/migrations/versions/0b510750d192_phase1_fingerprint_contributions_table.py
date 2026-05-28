"""phase1 fingerprint_contributions table

Revision ID: 0b510750d192
Revises: 53b4ddc7751e
Create Date: 2026-05-27 17:47:19.622258

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0b510750d192"
down_revision: str | Sequence[str] | None = "53b4ddc7751e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fingerprint_contributions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("queued_at", sa.DateTime(), nullable=False),
        sa.Column(
            "title_id", sa.Integer(), sa.ForeignKey("disc_titles.id"), nullable=True, index=True
        ),
        sa.Column("chromaprint_blob", sa.LargeBinary(), nullable=False),
        sa.Column("tmdb_id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.Column("episode", sa.Integer(), nullable=True),
        sa.Column("match_confidence", sa.Float(), nullable=False),
        sa.Column("match_source", sa.String(), nullable=False),
        sa.Column("disc_content_hash", sa.LargeBinary(), nullable=True),
        sa.Column("pseudonym", sa.String(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), nullable=True),
        sa.Column("upload_attempts", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("fingerprint_contributions")
