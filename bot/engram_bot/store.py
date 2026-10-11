"""Small SQLite store: which threads are the bot's, issue drafts, and spend/rate counters.

Conversation text is NOT stored: Discord already holds it, and the bot rebuilds each
turn's history from the thread. What lives here is what Discord can't tell us.
Every call is a few-row query, so plain ``sqlite3`` on the event loop is fine.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    thread_id    INTEGER PRIMARY KEY,
    reporter_id  INTEGER NOT NULL,
    origin       TEXT    NOT NULL,          -- forum | mention | command
    kind         TEXT,                      -- bug | feature, for /bug and /feature threads
    muted        INTEGER NOT NULL DEFAULT 0,
    created_at   REAL    NOT NULL
);
CREATE TABLE IF NOT EXISTS drafts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id     INTEGER NOT NULL,
    reporter_id   INTEGER NOT NULL,
    message_id    INTEGER,
    payload       TEXT    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'pending',  -- pending | filing | filed | cancelled
    issue_number  INTEGER,
    result_url    TEXT,
    created_at    REAL    NOT NULL,
    resolved_at   REAL
);
CREATE INDEX IF NOT EXISTS drafts_thread ON drafts(thread_id);
CREATE INDEX IF NOT EXISTS drafts_message ON drafts(message_id);
CREATE TABLE IF NOT EXISTS usage (
    ts            REAL    NOT NULL,
    user_id       INTEGER,
    thread_id     INTEGER,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read    INTEGER NOT NULL,
    cache_write   INTEGER NOT NULL,
    cost_usd      REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_ts ON usage(ts);
"""

DAY = 86400.0


@dataclass(frozen=True)
class ThreadInfo:
    thread_id: int
    reporter_id: int
    origin: str
    kind: str | None
    muted: bool


@dataclass(frozen=True)
class DraftRow:
    id: int
    thread_id: int
    reporter_id: int
    message_id: int | None
    payload: dict
    status: str
    issue_number: int | None
    result_url: str | None


class Store:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    # -- threads ----------------------------------------------------------------------

    def add_thread(self, thread_id: int, reporter_id: int, origin: str, kind: str | None = None):
        self._db.execute(
            "INSERT OR IGNORE INTO threads(thread_id, reporter_id, origin, kind, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (thread_id, reporter_id, origin, kind, time.time()),
        )

    def thread(self, thread_id: int) -> ThreadInfo | None:
        row = self._db.execute("SELECT * FROM threads WHERE thread_id = ?", (thread_id,)).fetchone()
        if row is None:
            return None
        return ThreadInfo(
            thread_id=row["thread_id"],
            reporter_id=row["reporter_id"],
            origin=row["origin"],
            kind=row["kind"],
            muted=bool(row["muted"]),
        )

    def set_muted(self, thread_id: int, muted: bool) -> None:
        self._db.execute(
            "UPDATE threads SET muted = ? WHERE thread_id = ?", (int(muted), thread_id)
        )

    # -- drafts -----------------------------------------------------------------------

    def add_draft(self, thread_id: int, reporter_id: int, payload: dict) -> int:
        cur = self._db.execute(
            "INSERT INTO drafts(thread_id, reporter_id, payload, created_at) VALUES (?, ?, ?, ?)",
            (thread_id, reporter_id, json.dumps(payload, sort_keys=True), time.time()),
        )
        return int(cur.lastrowid)

    def set_draft_message(self, draft_id: int, message_id: int) -> None:
        self._db.execute("UPDATE drafts SET message_id = ? WHERE id = ?", (message_id, draft_id))

    def draft(self, draft_id: int) -> DraftRow | None:
        row = self._db.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        return self._draft_row(row) if row else None

    def drafts_by_message(self, message_ids: list[int]) -> dict[int, DraftRow]:
        if not message_ids:
            return {}
        marks = ",".join("?" * len(message_ids))
        rows = self._db.execute(
            f"SELECT * FROM drafts WHERE message_id IN ({marks})", message_ids
        ).fetchall()
        return {row["message_id"]: self._draft_row(row) for row in rows}

    def claim_draft(self, draft_id: int) -> bool:
        """Atomically move a pending draft to 'filing' so a double click files once."""
        cur = self._db.execute(
            "UPDATE drafts SET status = 'filing' WHERE id = ? AND status = 'pending'", (draft_id,)
        )
        return cur.rowcount == 1

    def release_draft(self, draft_id: int) -> None:
        """Filing failed: let the user try again."""
        self._db.execute(
            "UPDATE drafts SET status = 'pending' WHERE id = ? AND status = 'filing'", (draft_id,)
        )

    def finish_draft(self, draft_id: int, issue_number: int, url: str) -> None:
        self._db.execute(
            "UPDATE drafts SET status = 'filed', issue_number = ?, result_url = ?, "
            "resolved_at = ? WHERE id = ?",
            (issue_number, url, time.time(), draft_id),
        )

    def cancel_draft(self, draft_id: int) -> bool:
        cur = self._db.execute(
            "UPDATE drafts SET status = 'cancelled', resolved_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (time.time(), draft_id),
        )
        return cur.rowcount == 1

    def supersede_drafts(self, thread_id: int) -> list[DraftRow]:
        """Cancel a thread's pending drafts (a revision replaces them). Returns them."""
        rows = self._db.execute(
            "SELECT * FROM drafts WHERE thread_id = ? AND status = 'pending'", (thread_id,)
        ).fetchall()
        self._db.execute(
            "UPDATE drafts SET status = 'cancelled', resolved_at = ? "
            "WHERE thread_id = ? AND status = 'pending'",
            (time.time(), thread_id),
        )
        return [self._draft_row(r) for r in rows]

    def issues_filed_since(self, user_id: int, since: float) -> int:
        row = self._db.execute(
            "SELECT COUNT(*) FROM drafts WHERE reporter_id = ? AND status = 'filed' "
            "AND resolved_at >= ?",
            (user_id, since),
        ).fetchone()
        return int(row[0])

    @staticmethod
    def _draft_row(row: sqlite3.Row) -> DraftRow:
        return DraftRow(
            id=row["id"],
            thread_id=row["thread_id"],
            reporter_id=row["reporter_id"],
            message_id=row["message_id"],
            payload=json.loads(row["payload"]),
            status=row["status"],
            issue_number=row["issue_number"],
            result_url=row["result_url"],
        )

    # -- usage ------------------------------------------------------------------------

    def record_usage(
        self,
        *,
        user_id: int | None,
        thread_id: int | None,
        input_tokens: int,
        output_tokens: int,
        cache_read: int,
        cache_write: int,
        cost_usd: float,
    ) -> None:
        self._db.execute(
            "INSERT INTO usage VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                time.time(),
                user_id,
                thread_id,
                input_tokens,
                output_tokens,
                cache_read,
                cache_write,
                cost_usd,
            ),
        )

    def spend_since(self, since: float) -> float:
        row = self._db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM usage WHERE ts >= ?", (since,)
        ).fetchone()
        return float(row[0])

    def turns_since(self, user_id: int, since: float) -> int:
        row = self._db.execute(
            "SELECT COUNT(*) FROM usage WHERE user_id = ? AND ts >= ?", (user_id, since)
        ).fetchone()
        return int(row[0])
