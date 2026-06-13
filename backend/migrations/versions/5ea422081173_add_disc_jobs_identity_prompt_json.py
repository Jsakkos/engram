"""add disc_jobs.identity_prompt_json

Non-blocking identity CTA for jobs that rip first and ask questions later
(walk-away Phase B). Stores a JSON envelope {"kind": "name"|"season"|"reidentify",
"reason": "<human-readable text>"} set by IdentificationCoordinator when a disc
ships to RIPPING with an open identity question. Cleared when the user answers
(Phase B5) or converted to review_reason at rip-end if blocking review is still
needed (Phase B4). Nullable — absence means no pending prompt.

Frozen builds skip Alembic entirely and converge via database.py::_add_missing_columns(),
which honours nullable → DEFAULT NULL — the two paths must stay in agreement.

Revision ID: 5ea422081173
Revises: 37a6eb38baeb
Create Date: 2026-06-12 19:39:37.690159

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5ea422081173"
down_revision: str | Sequence[str] | None = "37a6eb38baeb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("disc_jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("identity_prompt_json", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("disc_jobs", schema=None) as batch_op:
        batch_op.drop_column("identity_prompt_json")
