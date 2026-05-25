"""Persistent record of subtitle-harvest coverage per (show, season).

The build script's per-season coverage threshold (``--min-episodes-ratio``,
default 0.6) historically only gated which seasons make it into the
precomputed tarball — every daily re-run re-attempted the same dead
seasons, burning the OpenSubtitles VIP quota and Addic7ed/TVsubtitles
rate-limit budget on shows that simply don't have subtitles.

This module persists "(show, season) → (attempted_at, coverage_ratio)"
in the same SQLite file as the TMDB cache, so the build script can skip
seasons whose prior attempt fell below threshold within the configured
window. ``--retry-low-coverage`` bypasses the skip when the user wants
to re-attempt after adding a provider or after VIP quota refills.
"""

from __future__ import annotations

import time
from typing import Any

from app.matcher import tmdb_persistent_cache


def _fresh_record(
    tmdb_id: int,
    season: int,
    window_days: int,
) -> dict[str, Any] | None:
    """Return the coverage row for ``(tmdb_id, season)`` iff it was recorded
    within ``window_days``, else None (no row, or stale).

    Shared by ``should_skip`` (low-coverage branch) and ``is_done``
    (at/above-threshold branch) so both agree on what "recent enough to
    trust" means.
    """
    conn = tmdb_persistent_cache.get_conn()
    row = conn.execute(
        "SELECT attempted_at, total_episodes, covered_episodes, coverage_ratio "
        "FROM subtitle_coverage WHERE tmdb_id = ? AND season = ?",
        (tmdb_id, season),
    ).fetchone()
    if row is None:
        return None

    attempted_at, total, covered, ratio = row
    if time.time() - attempted_at > window_days * 86400:
        return None

    return {
        "attempted_at": attempted_at,
        "total_episodes": total,
        "covered_episodes": covered,
        "coverage_ratio": ratio,
    }


def should_skip(
    tmdb_id: int,
    season: int,
    min_ratio: float,
    skip_window_days: int = 30,
) -> tuple[bool, dict[str, Any] | None]:
    """Return ``(skip, prior_row)``.

    ``skip`` is True iff a prior attempt was recorded within
    ``skip_window_days`` AND its coverage_ratio was below ``min_ratio``.
    The caller logs ``prior_row`` so the user sees why a season was
    skipped without having to inspect the DB by hand.
    """
    row = _fresh_record(tmdb_id, season, skip_window_days)
    if row is None or row["coverage_ratio"] >= min_ratio:
        return False, None
    return True, row


def is_done(
    tmdb_id: int,
    season: int,
    min_ratio: float,
    window_days: int = 30,
) -> tuple[bool, dict[str, Any] | None]:
    """Return ``(done, prior_row)``.

    ``done`` is True iff a prior attempt was recorded within ``window_days``
    AND its coverage_ratio was at or above ``min_ratio`` — i.e. the season
    already reached the coverage threshold and can be shipped from the SRTs
    already on disk without re-hitting TMDB/OpenSubtitles/scrapers. The
    symmetric complement of ``should_skip``: a fresh record is either
    below-threshold (skip), at/above-threshold (done), or absent/stale
    (neither — harvest normally).
    """
    row = _fresh_record(tmdb_id, season, window_days)
    if row is None or row["coverage_ratio"] < min_ratio:
        return False, None
    return True, row


def record(tmdb_id: int, season: int, total: int, covered: int) -> None:
    """Insert or replace the coverage row for ``(tmdb_id, season)``.

    ``total`` may be 0 in pathological cases (TMDB returned an empty
    season). Storing the zero is useful so the skip window kicks in
    even for seasons TMDB couldn't enumerate — re-asking TMDB every
    day for an empty season is the same kind of waste this whole
    workstream is trying to eliminate.
    """
    ratio = (covered / total) if total > 0 else 0.0
    conn = tmdb_persistent_cache.get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO subtitle_coverage "
        "(tmdb_id, season, attempted_at, total_episodes, covered_episodes, coverage_ratio) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (tmdb_id, season, time.time(), total, covered, ratio),
    )
    conn.commit()


def clear(tmdb_id: int | None = None, season: int | None = None) -> None:
    """Delete coverage rows.

    No args: drop everything. ``tmdb_id``: drop every season of that show.
    ``tmdb_id`` + ``season``: drop the single row. ``season`` without
    ``tmdb_id`` raises (no scenario for "every show's season 3").
    """
    if season is not None and tmdb_id is None:
        raise ValueError("clear(season=...) requires tmdb_id")
    if not tmdb_persistent_cache.CACHE_DB_PATH.exists():
        return

    conn = tmdb_persistent_cache.get_conn()
    if tmdb_id is None:
        conn.execute("DELETE FROM subtitle_coverage")
    elif season is None:
        conn.execute("DELETE FROM subtitle_coverage WHERE tmdb_id = ?", (tmdb_id,))
    else:
        conn.execute(
            "DELETE FROM subtitle_coverage WHERE tmdb_id = ? AND season = ?",
            (tmdb_id, season),
        )
    conn.commit()
