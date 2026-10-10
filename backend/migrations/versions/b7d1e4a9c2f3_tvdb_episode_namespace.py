"""TheTVDB episode namespace columns

Per-job episode numbering (tmdb|tvdb), divergence summary, fallback note and
crosswalk; per-show tvdb_id + suggestion dismissal; tvdb_api_key override.
Mirrors database.py's _add_missing_columns reconciler, which is the path
frozen builds take (they skip Alembic). The two must stay in agreement.

Revision ID: b7d1e4a9c2f3
Revises: 02946b05fe8d
Create Date: 2026-10-08 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.migration_guards import add_column_if_missing

revision: str = "b7d1e4a9c2f3"
down_revision: str | Sequence[str] | None = "02946b05fe8d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    add_column_if_missing(
        "disc_jobs",
        sa.Column(
            "episode_namespace", sa.String(), nullable=False, server_default=sa.text("'tmdb'")
        ),
    )
    add_column_if_missing(
        "disc_jobs", sa.Column("tvdb_divergence_json", sa.String(), nullable=True)
    )
    add_column_if_missing(
        "disc_jobs", sa.Column("episode_namespace_note", sa.String(), nullable=True)
    )
    add_column_if_missing(
        "disc_jobs", sa.Column("episode_crosswalk_json", sa.String(), nullable=True)
    )
    add_column_if_missing(
        "show_ordering_preferences", sa.Column("tvdb_id", sa.Integer(), nullable=True)
    )
    add_column_if_missing(
        "show_ordering_preferences",
        sa.Column(
            "tvdb_suggestion_dismissed", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
    )
    add_column_if_missing(
        "app_config",
        sa.Column("tvdb_api_key", sa.String(), nullable=False, server_default=sa.text("''")),
    )


def downgrade() -> None:
    with op.batch_alter_table("disc_jobs", schema=None) as batch_op:
        batch_op.drop_column("episode_crosswalk_json")
        batch_op.drop_column("episode_namespace_note")
        batch_op.drop_column("tvdb_divergence_json")
        batch_op.drop_column("episode_namespace")
    with op.batch_alter_table("show_ordering_preferences", schema=None) as batch_op:
        batch_op.drop_column("tvdb_suggestion_dismissed")
        batch_op.drop_column("tvdb_id")
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.drop_column("tvdb_api_key")
