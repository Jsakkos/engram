"""Safety copy of the SQLite database, taken before startup migrates it (#730).

engram.db holds credentials users must re-enter by hand (MakeMKV key, TMDB token,
AI and OpenSubtitles keys) plus the whole job history, and init_db() rewrites its
schema in place on every startup: create_all, the column reconcilers (which DROP
columns on frozen builds, where Alembic never runs), then Alembic. A copy is taken
first whenever that could change anything:

- the app version differs from the last version that finished opening this
  database (the issue's trigger; needed because frozen builds skip Alembic), or
- the live schema has drifted from the models, or an Alembic revision is pending
  (catches dev databases, where the version string does not move).

Backups sit next to the database as ``<name>.bak-<previous-version>-<timestamp>``
and only the newest few are kept. The copy goes through SQLite's online backup API
rather than a file copy, so writes still in the ``-wal`` file are included.

Nothing here may block startup: every failure is logged as a warning and the
migration proceeds without a backup.
"""

import glob
import json
import logging
import os
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import MetaData
from sqlalchemy.engine import make_url

logger = logging.getLogger(__name__)

BACKUP_KEEP = 3
_BACKUP_INFIX = ".bak-"
_PARTIAL_SUFFIX = ".partial"
_VERSION_MARKER_SUFFIX = ".version"
_UNKNOWN_VERSION = "unknown"
# Microseconds keep two backups in one second distinct and sortable by name.
_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%S.%fZ"


def sqlite_file_path(database_url: str) -> Path | None:
    """The on-disk file behind a SQLite URL, or None for memory/non-SQLite URLs."""
    try:
        url = make_url(database_url)
    except Exception:
        return None
    if not url.get_backend_name().startswith("sqlite"):
        return None
    database = url.database
    if not database or database == ":memory:":
        return None
    if database.startswith("file:") or url.query.get("mode") == "memory":
        # URI-style names (shared-cache memory DBs, etc.) are not plain files.
        return None
    return Path(database)


def _marker_path(db_path: Path) -> Path:
    return db_path.with_name(db_path.name + _VERSION_MARKER_SUFFIX)


def _read_marker(db_path: Path) -> dict:
    try:
        data = json.loads(_marker_path(db_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_marker(db_path: Path, data: dict) -> None:
    marker = _marker_path(db_path)
    tmp = marker.with_name(marker.name + _PARTIAL_SUFFIX)
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, marker)


def last_opened_version(db_path: Path) -> str | None:
    """The app version that last finished opening this database, if recorded."""
    version = _read_marker(db_path).get("version")
    return version if isinstance(version, str) else None


def record_opened_version(db_path: Path, version: str) -> None:
    """Record that ``version`` finished migrating this database.

    Also clears any pending-backup note, so the next upgrade backs up afresh.
    """
    _write_marker(db_path, {"version": version})


def mark_startup_complete(database_url: str, version: str) -> None:
    """record_opened_version for a URL, never raising (called at the end of init_db)."""
    db_path = sqlite_file_path(database_url)
    if db_path is None or not db_path.exists():
        return
    try:
        record_opened_version(db_path, version)
    except OSError as e:
        logger.warning(f"Could not record database version marker: {e}", exc_info=True)


def list_backups(db_path: Path) -> list[Path]:
    """This database's completed backups, oldest first."""
    prefix = db_path.name + _BACKUP_INFIX
    backups = [
        p
        for p in db_path.parent.glob(f"{glob.escape(prefix)}*")
        if p.is_file() and not p.name.endswith(_PARTIAL_SUFFIX)
    ]
    # The timestamp is the last "-"-separated part (version tags never contain "-").
    return sorted(backups, key=lambda p: p.name.rsplit("-", 1)[-1])


def pending_schema_changes(
    db_path: Path, metadata: MetaData, alembic_ini: Path | None = None
) -> list[str]:
    """Describe what init_db() would change in this database's schema.

    Mirrors the reconcilers' view (create_all, _add_missing_columns,
    _drop_extra_columns, _migrate_app_config all key on table and column
    names) plus a pending Alembic revision when Alembic is available.
    """
    changes: list[str] = []
    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for name, table in metadata.tables.items():
            if name not in tables:
                changes.append(f"create table {name}")
                continue
            actual = {row[1] for row in conn.execute(f"PRAGMA table_info('{name}')")}
            expected = {col.name for col in table.columns}
            changes += [f"add column {name}.{c}" for c in sorted(expected - actual)]
            changes += [f"drop column {name}.{c}" for c in sorted(actual - expected)]

        if alembic_ini is not None and alembic_ini.exists() and "alembic_version" in tables:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            current = row[0] if row else None
            head = _alembic_head(alembic_ini)
            if head is not None and current != head:
                changes.append(f"alembic upgrade {current or 'base'} -> {head}")
    finally:
        conn.close()
    return changes


def _alembic_head(alembic_ini: Path) -> str | None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(Config(str(alembic_ini))).get_current_head()


def _version_tag(version: str | None) -> str:
    # "-" separates the tag from the timestamp, so it cannot appear in the tag.
    return re.sub(r"[^A-Za-z0-9._+]", "_", version or _UNKNOWN_VERSION)


def _copy_database(src: Path, dest: Path) -> None:
    """Consistent copy of a (possibly live, WAL-mode) database via the backup API."""
    source = sqlite3.connect(src)
    try:
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def _prune(db_path: Path, keep: int) -> None:
    for old in list_backups(db_path)[:-keep] if keep > 0 else []:
        try:
            old.unlink()
            logger.info(f"Removed old database backup {old}")
        except OSError as e:
            logger.warning(f"Could not remove old database backup {old}: {e}")


def backup_before_migrate(
    database_url: str,
    metadata: MetaData,
    app_version: str,
    alembic_ini: Path | None = None,
    keep: int = BACKUP_KEEP,
) -> Path | None:
    """Back up the database if startup is about to change it.

    Returns the backup that protects this startup (new, or kept from an
    interrupted earlier attempt), or None when none was needed or it failed.
    Never raises.
    """
    try:
        db_path = sqlite_file_path(database_url)
        if db_path is None or not db_path.is_file() or db_path.stat().st_size == 0:
            return None

        marker = _read_marker(db_path)
        previous = marker.get("version") if isinstance(marker.get("version"), str) else None

        # A startup that died mid-migration never records the new version, so
        # every restart sees the upgrade as pending again. Reuse the backup taken
        # by that first attempt instead of copying the half-migrated database
        # and, after `keep` restarts, pruning away the only good copy.
        pending = marker.get("pending_backup")
        if isinstance(pending, str):
            pending_path = db_path.with_name(pending)
            if pending_path.is_file():
                logger.info(
                    f"Keeping database backup {pending_path} from an earlier startup "
                    "that did not finish migrating"
                )
                return pending_path

        changes = pending_schema_changes(db_path, metadata, alembic_ini)
        if previous == app_version and not changes:
            return None

        stamp = datetime.now(UTC).strftime(_TIMESTAMP_FORMAT)
        backup = db_path.with_name(f"{db_path.name}{_BACKUP_INFIX}{_version_tag(previous)}-{stamp}")
        partial = backup.with_name(backup.name + _PARTIAL_SUFFIX)
        try:
            _copy_database(db_path, partial)
            os.replace(partial, backup)
        finally:
            partial.unlink(missing_ok=True)

        _write_marker(db_path, {**marker, "pending_backup": backup.name})
        reason = (
            f"upgrading from {previous or 'an unrecorded version'} to {app_version}"
            if previous != app_version
            else "schema changes pending"
        )
        detail = f" ({', '.join(changes)})" if changes else ""
        logger.info(f"Backed up database to {backup} before migrating: {reason}{detail}")
        _prune(db_path, keep)
        return backup
    except Exception as e:
        logger.warning(
            f"Could not back up the database before migrating; continuing without a backup: {e}",
            exc_info=True,
        )
        return None
