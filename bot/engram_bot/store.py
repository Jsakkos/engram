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
-- Issues the bot filed or commented on, and the thread to report back to.
CREATE TABLE IF NOT EXISTS follows (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_number  INTEGER NOT NULL,
    thread_id     INTEGER NOT NULL,
    reporter_id   INTEGER NOT NULL,
    kind          TEXT    NOT NULL,          -- issue | comment
    filed_at      REAL    NOT NULL,
    state         TEXT    NOT NULL,          -- open | closed, as last seen
    state_reason  TEXT,
    closed_at     TEXT,                      -- ISO 8601 from GitHub
    release_tag   TEXT,                      -- release already announced in the thread
    UNIQUE(issue_number, thread_id)
);
CREATE INDEX IF NOT EXISTS follows_issue ON follows(issue_number);
-- Comments the bot itself posted (never relayed back to Discord).
CREATE TABLE IF NOT EXISTS own_comments (comment_id INTEGER PRIMARY KEY);
-- Maintainer comments relayed into a thread; the Reply button refers to these ids.
CREATE TABLE IF NOT EXISTS relays (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    comment_id    INTEGER NOT NULL,
    issue_number  INTEGER NOT NULL,
    thread_id     INTEGER NOT NULL,
    reporter_id   INTEGER NOT NULL,
    author        TEXT    NOT NULL,
    url           TEXT    NOT NULL,
    relayed_at    REAL    NOT NULL,
    UNIQUE(comment_id, thread_id)
);
-- Bot replies, so reactions on them can be recorded as answer feedback.
CREATE TABLE IF NOT EXISTS bot_messages (
    message_id    INTEGER PRIMARY KEY,
    thread_id     INTEGER,
    model         TEXT,
    created_at    REAL    NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
    message_id    INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    value         INTEGER NOT NULL,          -- +1 | -1
    created_at    REAL    NOT NULL,
    PRIMARY KEY (message_id, user_id)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
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

    # -- follow-through -----------------------------------------------------------------

    def add_follow(
        self,
        issue_number: int,
        thread_id: int,
        reporter_id: int,
        kind: str,
        state: str = "open",
        state_reason: str | None = None,
    ) -> None:
        # Joining an issue that's already closed means "it still happens for me": no
        # release should claim to fix it. Mark the announcement as done up front.
        release_tag = "(already closed)" if state == "closed" else None
        self._db.execute(
            "INSERT OR IGNORE INTO follows(issue_number, thread_id, reporter_id, kind, filed_at, "
            "state, state_reason, release_tag) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                issue_number,
                thread_id,
                reporter_id,
                kind,
                time.time(),
                state,
                state_reason,
                release_tag,
            ),
        )

    def follows_for(self, issue_number: int) -> list[Follow]:
        rows = self._db.execute(
            "SELECT * FROM follows WHERE issue_number = ? ORDER BY id", (issue_number,)
        ).fetchall()
        return [Follow(**dict(r)) for r in rows]

    def followed_issues(self) -> set[int]:
        return {r[0] for r in self._db.execute("SELECT DISTINCT issue_number FROM follows")}

    def follows_awaiting_release(self) -> list[Follow]:
        rows = self._db.execute(
            "SELECT * FROM follows WHERE state = 'closed' AND state_reason = 'completed' "
            "AND release_tag IS NULL ORDER BY id"
        ).fetchall()
        return [Follow(**dict(r)) for r in rows]

    def set_follow_state(
        self, follow_id: int, state: str, state_reason: str | None, closed_at: str | None
    ) -> None:
        # A reopen starts a new fix cycle, so the next release may announce it again.
        self._db.execute(
            "UPDATE follows SET state = ?, state_reason = ?, closed_at = ?, "
            "release_tag = CASE WHEN ? = 'open' THEN NULL ELSE release_tag END WHERE id = ?",
            (state, state_reason, closed_at, state, follow_id),
        )

    def set_follow_release(self, follow_id: int, tag: str) -> None:
        self._db.execute("UPDATE follows SET release_tag = ? WHERE id = ?", (tag, follow_id))

    def add_own_comment(self, comment_id: int) -> None:
        self._db.execute("INSERT OR IGNORE INTO own_comments VALUES (?)", (comment_id,))

    def is_own_comment(self, comment_id: int) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM own_comments WHERE comment_id = ?", (comment_id,)
        ).fetchone()
        return row is not None

    def add_relay(
        self,
        comment_id: int,
        issue_number: int,
        thread_id: int,
        reporter_id: int,
        author: str,
        url: str,
    ) -> int | None:
        """Record a relay. Returns its id, or None if this comment already went to the thread."""
        cur = self._db.execute(
            "INSERT OR IGNORE INTO relays(comment_id, issue_number, thread_id, reporter_id, "
            "author, url, relayed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (comment_id, issue_number, thread_id, reporter_id, author, url, time.time()),
        )
        return int(cur.lastrowid) if cur.rowcount == 1 else None

    def remove_relay(self, relay_id: int) -> None:
        """Posting to Discord failed: forget it so the next poll tries again."""
        self._db.execute("DELETE FROM relays WHERE id = ?", (relay_id,))

    def relay(self, relay_id: int) -> Relay | None:
        row = self._db.execute("SELECT * FROM relays WHERE id = ?", (relay_id,)).fetchone()
        return Relay(**dict(row)) if row else None

    # -- feedback ---------------------------------------------------------------------

    def add_bot_message(self, message_id: int, thread_id: int | None, model: str) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO bot_messages VALUES (?, ?, ?, ?)",
            (message_id, thread_id, model, time.time()),
        )

    def is_bot_message(self, message_id: int) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM bot_messages WHERE message_id = ?", (message_id,)
        ).fetchone()
        return row is not None

    def set_feedback(self, message_id: int, user_id: int, value: int | None) -> None:
        if value is None:
            self._db.execute(
                "DELETE FROM feedback WHERE message_id = ? AND user_id = ?", (message_id, user_id)
            )
            return
        self._db.execute(
            "INSERT INTO feedback VALUES (?, ?, ?, ?) ON CONFLICT(message_id, user_id) "
            "DO UPDATE SET value = excluded.value, created_at = excluded.created_at",
            (message_id, user_id, value, time.time()),
        )

    def thread_downvoted(self, thread_id: int) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM feedback f JOIN bot_messages b ON b.message_id = f.message_id "
            "WHERE b.thread_id = ? AND f.value < 0 LIMIT 1",
            (thread_id,),
        ).fetchone()
        return row is not None

    # -- digest -----------------------------------------------------------------------

    def digest(self, since: float) -> Digest:
        def one(sql: str, *args) -> float:
            return self._db.execute(sql, args).fetchone()[0] or 0

        filed = self._db.execute(
            "SELECT issue_number, payload, result_url FROM drafts WHERE status = 'filed' "
            "AND resolved_at >= ? ORDER BY resolved_at",
            (since,),
        ).fetchall()
        downvoted = self._db.execute(
            "SELECT b.thread_id, b.message_id, SUM(f.value) AS score FROM feedback f "
            "JOIN bot_messages b ON b.message_id = f.message_id WHERE f.created_at >= ? "
            "GROUP BY b.message_id HAVING score < 0 ORDER BY score LIMIT 15",
            (since,),
        ).fetchall()
        return Digest(
            threads=int(one("SELECT COUNT(*) FROM threads WHERE created_at >= ?", since)),
            turns=int(one("SELECT COUNT(*) FROM usage WHERE ts >= ?", since)),
            spend_usd=float(one("SELECT SUM(cost_usd) FROM usage WHERE ts >= ?", since)),
            drafts=int(one("SELECT COUNT(*) FROM drafts WHERE created_at >= ?", since)),
            cancelled=int(
                one(
                    "SELECT COUNT(*) FROM drafts WHERE status = 'cancelled' AND resolved_at >= ?",
                    since,
                )
            ),
            filed=[(r["issue_number"], json.loads(r["payload"]), r["result_url"]) for r in filed],
            thumbs_up=int(
                one("SELECT COUNT(*) FROM feedback WHERE value > 0 AND created_at >= ?", since)
            ),
            thumbs_down=int(
                one("SELECT COUNT(*) FROM feedback WHERE value < 0 AND created_at >= ?", since)
            ),
            downvoted=[(r["thread_id"], r["message_id"]) for r in downvoted],
            relays=int(one("SELECT COUNT(*) FROM relays WHERE relayed_at >= ?", since)),
        )

    # -- meta -------------------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


@dataclass(frozen=True)
class Follow:
    id: int
    issue_number: int
    thread_id: int
    reporter_id: int
    kind: str
    filed_at: float
    state: str
    state_reason: str | None
    closed_at: str | None
    release_tag: str | None


@dataclass(frozen=True)
class Relay:
    id: int
    comment_id: int
    issue_number: int
    thread_id: int
    reporter_id: int
    author: str
    url: str
    relayed_at: float


@dataclass(frozen=True)
class Digest:
    threads: int
    turns: int
    spend_usd: float
    drafts: int
    cancelled: int
    filed: list[tuple[int, dict, str]]
    thumbs_up: int
    thumbs_down: int
    downvoted: list[tuple[int | None, int]]
    relays: int
