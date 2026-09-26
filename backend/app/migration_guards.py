"""Idempotency guards for Alembic revisions.

init_db() runs three out-of-band schema writers BEFORE Alembic on every startup:
``SQLModel.metadata.create_all`` (tables and their declared indexes),
``_add_missing_columns`` and ``_drop_extra_columns``. By the time a pending
revision runs, the DDL it describes has often already happened, so a revision
that blindly re-applies it fails (``duplicate column name``, ``table ... already
exists``, or a ``KeyError`` from batch ``drop_column``) and leaves
``alembic_version`` stuck behind every later revision.

Revisions therefore describe the schema to converge on, not a delta to apply:
each helper here inspects the live schema and skips work that is already done.
Only valid inside a running migration (they use ``op.get_bind()``).
"""

import sqlalchemy as sa
from alembic import op


def _inspector() -> sa.Inspector:
    # A fresh inspector each call: Inspector caches reflection results, and a
    # revision may change the schema between two checks.
    return sa.inspect(op.get_bind())


def table_exists(table: str) -> bool:
    return table in _inspector().get_table_names()


def column_names(table: str) -> set[str]:
    return {col["name"] for col in _inspector().get_columns(table)}


def index_exists(table: str, index: str) -> bool:
    return any(ix["name"] == index for ix in _inspector().get_indexes(table))


def add_column_if_missing(table: str, column: sa.Column) -> None:
    """``op.add_column`` that is a no-op when the column already exists.

    Plain ``op.add_column`` is fine for SQLite: batch mode emits the same
    ``ALTER TABLE ... ADD COLUMN`` for an add, so no table rebuild is lost.
    """
    if column.name not in column_names(table):
        op.add_column(table, column)


def drop_column_if_exists(table: str, column: str) -> None:
    """Batch-mode ``drop_column`` that is a no-op when the column is gone.

    Batch mode is required for SQLite; its ``drop_column`` raises ``KeyError``
    on a column the reflected table lacks, which is the failure this guards.
    """
    if column not in column_names(table):
        return
    with op.batch_alter_table(table, schema=None) as batch_op:
        batch_op.drop_column(column)
