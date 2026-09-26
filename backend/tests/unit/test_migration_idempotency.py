"""Alembic revisions must be idempotent against init_db()'s out-of-band writers.

init_db() runs create_all(), _add_missing_columns() and _drop_extra_columns()
before Alembic, so a pending revision routinely finds its work already done.
f3a9c1e7b204 (drop disc_jobs.is_transcoding_enabled) used to raise KeyError on
such a DB, which froze alembic_version at 9b793042b934 and blocked every later
revision on every startup (seen nightly on the subtitle-cache harvest server).
"""

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

import app.models  # noqa: F401  (register every table on SQLModel.metadata)

DROP_REV = "f3a9c1e7b204"
BEFORE_DROP_REV = "9b793042b934"
BASELINE_REV = "d9cbecac097c"
COLUMN = "is_transcoding_enabled"


@pytest.fixture
def alembic_db(tmp_path):
    """A temp SQLite DB with the full current schema, wired into Alembic.

    migrations/env.py reads settings.database_url directly, so it must point at
    the temp DB before any Alembic command runs.
    """
    from alembic.config import Config

    import app.database as db_mod

    db_path = tmp_path / "migrate.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    original_url = db_mod.settings.database_url
    db_mod.settings.database_url = f"sqlite+aiosqlite:///{db_path}"
    try:
        SQLModel.metadata.create_all(sync_engine)
        yield Config(str(db_mod._ALEMBIC_INI)), sync_engine
    finally:
        db_mod.settings.database_url = original_url
        sync_engine.dispose()


def _columns(engine, table: str) -> set[str]:
    with engine.connect() as conn:
        return {col["name"] for col in inspect(conn).get_columns(table)}


def _current_rev(engine) -> str | None:
    from alembic.runtime.migration import MigrationContext

    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def _head(cfg) -> str:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(cfg).get_current_head()


def _add_legacy_column(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE disc_jobs ADD COLUMN {COLUMN} BOOLEAN"))


@pytest.mark.unit
class TestDropTranscodingColumnMigration:
    def test_upgrade_skips_drop_when_column_already_gone(self, alembic_db):
        """The server case: _drop_extra_columns() already removed the column."""
        from alembic import command

        cfg, engine = alembic_db
        assert COLUMN not in _columns(engine, "disc_jobs")
        command.stamp(cfg, BEFORE_DROP_REV)

        command.upgrade(cfg, DROP_REV)

        assert _current_rev(engine) == DROP_REV
        assert COLUMN not in _columns(engine, "disc_jobs")

    def test_upgrade_drops_column_when_present(self, alembic_db):
        from alembic import command

        cfg, engine = alembic_db
        _add_legacy_column(engine)
        command.stamp(cfg, BEFORE_DROP_REV)

        command.upgrade(cfg, DROP_REV)

        assert _current_rev(engine) == DROP_REV
        assert COLUMN not in _columns(engine, "disc_jobs")

    def test_downgrade_adds_column_when_absent(self, alembic_db):
        from alembic import command

        cfg, engine = alembic_db
        command.stamp(cfg, DROP_REV)

        command.downgrade(cfg, BEFORE_DROP_REV)

        assert _current_rev(engine) == BEFORE_DROP_REV
        assert COLUMN in _columns(engine, "disc_jobs")

    def test_downgrade_skips_add_when_column_present(self, alembic_db):
        from alembic import command

        cfg, engine = alembic_db
        _add_legacy_column(engine)
        command.stamp(cfg, DROP_REV)

        command.downgrade(cfg, BEFORE_DROP_REV)

        assert _current_rev(engine) == BEFORE_DROP_REV
        assert COLUMN in _columns(engine, "disc_jobs")


@pytest.mark.unit
class TestWholeChainIdempotency:
    def test_every_revision_replays_cleanly_over_current_schema(self, alembic_db):
        """Replay the whole chain, WITHOUT the self-heal, over a DB that
        create_all() has already brought to the current schema.

        This is the state init_db() leaves for any pending revision, so a new
        revision that re-adds a column, re-creates a table or index, or drops an
        already-dropped column fails here instead of freezing alembic_version
        on users' databases.
        """
        from alembic import command

        cfg, engine = alembic_db
        command.stamp(cfg, BASELINE_REV)

        command.upgrade(cfg, "head")

        assert _current_rev(engine) == _head(cfg)

    def test_stuck_database_reaches_head_without_warnings(self, alembic_db, monkeypatch):
        """End to end through _run_alembic_upgrade(), as init_db() calls it, for
        a DB stamped at the revision the harvest server was stuck on: no
        failure is logged and no revision needs the duplicate-column self-heal.
        """
        from alembic import command

        import app.database as db_mod

        cfg, engine = alembic_db
        command.stamp(cfg, BEFORE_DROP_REV)

        logged: list[str] = []
        monkeypatch.setattr(db_mod.logger, "warning", lambda msg, *a, **k: logged.append(msg))
        monkeypatch.setattr(db_mod.logger, "error", lambda msg, *a, **k: logged.append(msg))

        db_mod._run_alembic_upgrade()

        assert logged == []
        assert _current_rev(engine) == _head(cfg)
        assert COLUMN not in _columns(engine, "disc_jobs")


@pytest.mark.unit
class TestSelfHealFailureReporting:
    def test_unhealable_failure_logs_stuck_revision_and_reraises(self, alembic_db, monkeypatch):
        """A non-duplicate-column failure must still propagate (stamping past
        it would silently skip its work), but the log has to say that the stamp
        is stuck and how many revisions are blocked behind it.
        """
        from alembic import command

        import app.database as db_mod

        cfg, engine = alembic_db
        command.stamp(cfg, BEFORE_DROP_REV)

        def fake_upgrade(_cfg, _rev):
            raise KeyError(COLUMN)

        errors: list[str] = []
        monkeypatch.setattr(command, "upgrade", fake_upgrade)
        monkeypatch.setattr(db_mod.logger, "error", lambda msg, *a, **k: errors.append(msg))

        with pytest.raises(KeyError):
            db_mod._upgrade_to_head_self_healing(cfg, engine)

        assert _current_rev(engine) == BEFORE_DROP_REV
        assert len(errors) == 1
        assert f"revision {DROP_REV} failed" in errors[0]
        assert f"stays at {BEFORE_DROP_REV}" in errors[0]
        assert "24 pending revision(s)" in errors[0]
