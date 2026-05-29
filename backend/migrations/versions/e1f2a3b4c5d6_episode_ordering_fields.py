"""episode ordering fields (#200)

Adds the global default ordering on app_config, the per-show override table,
and the audit fields on disc_titles. Mirrors the database.py reconciler path
used by frozen builds (which skip Alembic) — the two must stay in agreement.

Revision ID: e1f2a3b4c5d6
Revises: a1b2c3d4e5f6
Create Date: 2026-05-28 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "show_ordering_preferences",
        sa.Column("tmdb_id", sa.Integer(), primary_key=True),
        sa.Column("ordering", sa.String(), nullable=False, server_default="aired"),
        sa.Column("episode_group_id", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(datetime('now'))"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(datetime('now'))"),
        ),
    )

    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "episode_ordering_preference",
                sa.String(),
                nullable=False,
                server_default=sa.text("'aired'"),
            )
        )

    with op.batch_alter_table("disc_titles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("episode_ordering", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("episode_group_id", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("disc_titles", schema=None) as batch_op:
        batch_op.drop_column("episode_group_id")
        batch_op.drop_column("episode_ordering")

    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.drop_column("episode_ordering_preference")

    op.drop_table("show_ordering_preferences")
