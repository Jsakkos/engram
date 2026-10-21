"""Tests for issue #730: back up engram.db before startup migrates it.

Every test works on throwaway SQLite files under tmp_path; nothing here opens
backend/engram.db or ~/.engram/engram.db.
"""

import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel

from app import database_backup as bk
from app.models.app_config import AppConfig

VERSION = "0.42.0"


def _make_current_db(path):
    """A database whose schema already matches the models."""
    sync = create_engine(f"sqlite:///{path.as_posix()}")
    SQLModel.metadata.create_all(sync)
    sync.dispose()


def _make_drifted_db(path, key="secret-key"):
    """A current schema plus an obsolete app_config column holding real data."""
    _make_current_db(path)
    _insert_config(path, key)
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE app_config ADD COLUMN obsolete_field VARCHAR DEFAULT 'old'")
    conn.commit()
    conn.close()


def _insert_config(path, key):
    """Insert an app_config row through the ORM so every NOT NULL default is filled."""
    sync = create_engine(f"sqlite:///{path.as_posix()}")
    with Session(sync) as session:
        session.add(AppConfig(makemkv_key=key))
        session.commit()
    sync.dispose()


def _columns(path, table):
    conn = sqlite3.connect(path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info('{table}')")}
    finally:
        conn.close()


def _url(path):
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def _backups(path):
    return sorted(p.name for p in bk.list_backups(path))


class TestSqliteFilePath:
    def test_file_url_resolves_to_a_path(self, tmp_path):
        db = tmp_path / "engram.db"
        assert bk.sqlite_file_path(_url(db)) == db

    @pytest.mark.parametrize(
        "url",
        [
            "sqlite+aiosqlite:///:memory:",
            "sqlite+aiosqlite://",
            "sqlite:///file:mem?mode=memory&uri=true",
            "postgresql+asyncpg://user@host/engram",
        ],
    )
    def test_memory_and_non_sqlite_urls_are_skipped(self, url):
        assert bk.sqlite_file_path(url) is None


class TestPendingSchemaChanges:
    def test_current_schema_has_no_pending_changes(self, tmp_path):
        db = tmp_path / "engram.db"
        _make_current_db(db)
        assert bk.pending_schema_changes(db, SQLModel.metadata) == []

    def test_extra_column_is_a_pending_change(self, tmp_path):
        db = tmp_path / "engram.db"
        _make_drifted_db(db)
        changes = bk.pending_schema_changes(db, SQLModel.metadata)
        assert any("app_config.obsolete_field" in c for c in changes)

    def test_missing_column_is_a_pending_change(self, tmp_path):
        db = tmp_path / "engram.db"
        _make_current_db(db)
        conn = sqlite3.connect(db)
        conn.execute("ALTER TABLE disc_jobs DROP COLUMN source_spec")
        conn.close()
        changes = bk.pending_schema_changes(db, SQLModel.metadata)
        assert any("disc_jobs.source_spec" in c for c in changes)

    def test_missing_table_is_a_pending_change(self, tmp_path):
        db = tmp_path / "engram.db"
        _make_current_db(db)
        conn = sqlite3.connect(db)
        conn.execute("DROP TABLE disc_titles")
        conn.close()
        changes = bk.pending_schema_changes(db, SQLModel.metadata)
        assert any("disc_titles" in c for c in changes)


class TestBackupBeforeMigrate:
    def test_drift_takes_a_restorable_backup(self, tmp_path):
        db = tmp_path / "engram.db"
        _make_drifted_db(db, key="my-makemkv-key")
        bk.record_opened_version(db, VERSION)

        backup = bk.backup_before_migrate(_url(db), SQLModel.metadata, VERSION)

        assert backup is not None and backup.exists()
        assert backup.parent == db.parent
        assert backup.name.startswith(f"engram.db.bak-{VERSION}-")
        conn = sqlite3.connect(backup)
        try:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            row = conn.execute("SELECT makemkv_key, obsolete_field FROM app_config").fetchone()
        finally:
            conn.close()
        assert row == ("my-makemkv-key", "old")

    def test_version_change_takes_a_backup_named_for_the_old_version(self, tmp_path):
        db = tmp_path / "engram.db"
        _make_current_db(db)
        bk.record_opened_version(db, "0.41.0")

        backup = bk.backup_before_migrate(_url(db), SQLModel.metadata, VERSION)

        assert backup is not None
        assert backup.name.startswith("engram.db.bak-0.41.0-")

    def test_first_run_with_an_unmarked_existing_db_backs_up(self, tmp_path):
        """An install upgrading to the first version with this feature has no marker."""
        db = tmp_path / "engram.db"
        _make_current_db(db)

        backup = bk.backup_before_migrate(_url(db), SQLModel.metadata, VERSION)

        assert backup is not None
        assert backup.name.startswith("engram.db.bak-unknown-")

    def test_same_version_and_no_drift_skips(self, tmp_path):
        db = tmp_path / "engram.db"
        _make_current_db(db)
        bk.record_opened_version(db, VERSION)

        assert bk.backup_before_migrate(_url(db), SQLModel.metadata, VERSION) is None
        assert _backups(db) == []

    def test_missing_or_empty_db_skips(self, tmp_path):
        missing = tmp_path / "missing.db"
        assert bk.backup_before_migrate(_url(missing), SQLModel.metadata, VERSION) is None
        assert not missing.exists()

        empty = tmp_path / "empty.db"
        empty.touch()
        assert bk.backup_before_migrate(_url(empty), SQLModel.metadata, VERSION) is None
        assert _backups(empty) == []

    def test_memory_url_skips(self):
        url = "sqlite+aiosqlite:///:memory:"
        assert bk.backup_before_migrate(url, SQLModel.metadata, VERSION) is None

    def test_wal_contents_are_included(self, tmp_path):
        """A raw file copy of a live WAL database misses uncheckpointed writes."""
        db = tmp_path / "engram.db"
        _make_current_db(db)
        _insert_config(db, "checkpointed")
        writer = sqlite3.connect(db)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE app_config SET makemkv_key = 'only-in-wal'")
        writer.commit()
        try:
            backup = bk.backup_before_migrate(_url(db), SQLModel.metadata, VERSION)
            assert backup is not None
            conn = sqlite3.connect(backup)
            try:
                keys = [r[0] for r in conn.execute("SELECT makemkv_key FROM app_config")]
            finally:
                conn.close()
            assert keys == ["only-in-wal"]
        finally:
            writer.close()

    def test_only_the_newest_backups_are_kept(self, tmp_path):
        db = tmp_path / "engram.db"
        _make_current_db(db)
        for i in range(5):
            bk.record_opened_version(db, f"0.30.{i}")
            assert bk.backup_before_migrate(_url(db), SQLModel.metadata, VERSION, keep=3)
            bk.record_opened_version(db, VERSION)

        names = _backups(db)
        assert len(names) == 3
        assert {n.split("-")[1] for n in names} == {"0.30.2", "0.30.3", "0.30.4"}

    def test_pruning_leaves_unrelated_files_alone(self, tmp_path):
        db = tmp_path / "engram.db"
        _make_current_db(db)
        other = tmp_path / "other.db.bak-0.1.0-20260101T000000"
        other.write_text("not ours")
        for i in range(4):
            bk.record_opened_version(db, f"0.30.{i}")
            bk.backup_before_migrate(_url(db), SQLModel.metadata, VERSION, keep=1)
        assert other.exists()
        assert len(_backups(db)) == 1

    def test_an_interrupted_upgrade_keeps_the_original_backup(self, tmp_path):
        """A crash loop must not rotate the pre-upgrade copy out of the kept set.

        Startup that dies mid-migration never records the new version, so every
        restart sees the upgrade as pending again. Taking a fresh backup each time
        would back up the half-migrated database and, after `keep` restarts,
        delete the only good one.
        """
        db = tmp_path / "engram.db"
        _make_drifted_db(db)
        bk.record_opened_version(db, "0.41.0")

        first = bk.backup_before_migrate(_url(db), SQLModel.metadata, VERSION, keep=2)
        assert first is not None
        # Startup crashed: no record_opened_version(VERSION).
        for _ in range(3):
            again = bk.backup_before_migrate(_url(db), SQLModel.metadata, VERSION, keep=2)
            assert again == first
        assert _backups(db) == [first.name]

        # Once a startup completes, the next pending change backs up afresh.
        bk.record_opened_version(db, VERSION)
        later = bk.backup_before_migrate(_url(db), SQLModel.metadata, "0.43.0", keep=2)
        assert later is not None and later != first

    def test_backup_failure_never_raises(self, tmp_path, monkeypatch, caplog):
        db = tmp_path / "engram.db"
        _make_drifted_db(db)

        def boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(bk, "_copy_database", boom)
        with caplog.at_level("WARNING", logger="app.database_backup"):
            assert bk.backup_before_migrate(_url(db), SQLModel.metadata, VERSION) is None
        assert "disk full" in caplog.text
        assert _backups(db) == []
        assert not list(tmp_path.glob("*.partial"))


class TestInitDbBacksUpBeforeMigrating:
    """init_db() copies the database before any reconciler or Alembic touches it.

    Isolation follows test_backup_columns.TestSchemaConvergence: patch
    settings.database_url (read by Alembic's env.py and by the backup) and
    app.database.engine/async_session to a scratch file under tmp_path.
    """

    @pytest.mark.asyncio
    async def test_backup_holds_the_pre_migration_schema(self, tmp_path, monkeypatch):
        import app.database as db_mod
        from app.config import settings

        db = tmp_path / "scratch-730.db"
        _make_drifted_db(db, key="pre-upgrade-key")
        url = _url(db)
        monkeypatch.setattr(settings, "database_url", url)

        scratch_engine = create_async_engine(url, connect_args={"check_same_thread": False})
        original_engine = db_mod.engine
        original_session_factory = db_mod.async_session
        db_mod.engine = scratch_engine
        db_mod.async_session = sessionmaker(
            scratch_engine, class_=AsyncSession, expire_on_commit=False
        )
        try:
            await db_mod.init_db()
        finally:
            db_mod.engine = original_engine
            db_mod.async_session = original_session_factory
            await scratch_engine.dispose()

        # The live database was migrated...
        assert "obsolete_field" not in _columns(db, "app_config")
        # ...and the backup still has what it looked like before.
        backups = bk.list_backups(db)
        assert len(backups) == 1
        assert "obsolete_field" in _columns(backups[0], "app_config")
        conn = sqlite3.connect(backups[0])
        try:
            assert conn.execute("SELECT makemkv_key FROM app_config").fetchone() == (
                "pre-upgrade-key",
            )
        finally:
            conn.close()

        # A completed startup records the version, so the next one is a no-op.
        from app import __version__

        assert bk.last_opened_version(db) == __version__
        assert bk.backup_before_migrate(url, SQLModel.metadata, __version__) is None
