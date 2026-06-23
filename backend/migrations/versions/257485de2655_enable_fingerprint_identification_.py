"""enable fingerprint identification default on

Revision ID: 257485de2655
Revises: 01f4f5567376
Create Date: 2026-06-22 21:35:55.933591

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '257485de2655'
down_revision: Union[str, Sequence[str], None] = '01f4f5567376'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE app_config SET enable_fingerprint_identification = 1")


def downgrade() -> None:
    op.execute("UPDATE app_config SET enable_fingerprint_identification = 0")
