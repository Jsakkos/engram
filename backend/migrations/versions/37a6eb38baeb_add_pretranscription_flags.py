"""Add app_config pre-transcription flags

Adds two boolean columns to app_config: enable_background_pretranscription
(master switch, default ON — transcribe unresolved tracks while a job waits in
review so re-matching is near-instant) and pretranscribe_full_file (default OFF —
also transcribe each track end-to-end, not just short scan-point samples; useful
when full-file fallbacks are common but expensive). Frozen builds skip Alembic
entirely and converge via database.py::_add_missing_columns(), which honours the
same server defaults — the two paths must stay in agreement.

Revision ID: 37a6eb38baeb
Revises: e7a2b9c4d1f8
Create Date: 2026-06-11 14:22:44.061656

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "37a6eb38baeb"
down_revision: str | Sequence[str] | None = "e7a2b9c4d1f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "enable_background_pretranscription",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "pretranscribe_full_file",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("app_config", schema=None) as batch_op:
        batch_op.drop_column("pretranscribe_full_file")
        batch_op.drop_column("enable_background_pretranscription")
