"""Manual subtitle bulk-import: preview/commit logic for user-supplied .srt files.

Feeds directly into the existing subtitle cache that ``LocalSubtitleProvider``
scans (``subtitle_provider.py``) — writing a correctly-named file there makes it
available to matching identically to an automated find, so this module owns
parsing/validation/writing only and never touches the matcher.

See docs/superpowers/specs/2026-07-09-manual-subtitle-ingestion-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.matcher.episode_identification import reference_coverage
from app.matcher.subtitle_provider import parse_season_episode
from app.matcher.subtitle_utils import is_valid_srt_content

MIN_SEASON = 0
MAX_SEASON = 50
MIN_EPISODE = 1
MAX_EPISODE = 999
MAX_FILES_PER_BATCH = 60
MAX_CONTENT_BYTES = 2 * 1024 * 1024


@dataclass
class PreviewInputFile:
    filename: str
    content: str


@dataclass
class PreviewFileResult:
    filename: str
    season: int | None
    episode: int | None
    status: str  # "ready" | "already_covered" | "unparseable" | "invalid_content" | "duplicate"
    warning: str | None = None


def _in_range(season: int, episode: int) -> bool:
    return MIN_SEASON <= season <= MAX_SEASON and MIN_EPISODE <= episode <= MAX_EPISODE


def _encoding_warning(content: str) -> str | None:
    return "possible encoding issue" if "�" in content else None


def classify_files(
    cache_dir: Path,
    tmdb_id: int | None,
    show_name: str,
    files: list[PreviewInputFile],
) -> list[PreviewFileResult]:
    """Classify each uploaded file for the preview confirmation table.

    Parses season/episode from the filename (same parser ``LocalSubtitleProvider``
    relies on elsewhere), checks whether a reference already exists via the same
    ``reference_coverage`` function that powers the season-roster's ``has_reference``
    flag, and flags duplicates within the batch (first file wins the slot,
    regardless of its own validity).
    """
    parsed: list[tuple[PreviewInputFile, int | None, int | None]] = []
    seasons_needed: dict[int, list[int]] = {}
    for f in files:
        info = parse_season_episode(f.filename)
        season = info.season if info else None
        episode = info.episode if info else None
        if season is not None and episode is not None and _in_range(season, episode):
            seasons_needed.setdefault(season, []).append(episode)
        else:
            season = episode = None
        parsed.append((f, season, episode))

    coverage_by_season: dict[int, dict[str, str]] = {
        season: reference_coverage(cache_dir, tmdb_id, show_name, season, episodes)
        for season, episodes in seasons_needed.items()
    }

    results: list[PreviewFileResult] = []
    seen: set[tuple[int, int]] = set()
    for f, season, episode in parsed:
        if season is None or episode is None:
            results.append(PreviewFileResult(f.filename, None, None, "unparseable"))
            continue

        key = (season, episode)
        if key in seen:
            results.append(
                PreviewFileResult(
                    f.filename,
                    season,
                    episode,
                    "duplicate",
                    warning="same episode as an earlier file in this batch",
                )
            )
            continue
        seen.add(key)

        if len(f.content.encode("utf-8")) > MAX_CONTENT_BYTES:
            results.append(
                PreviewFileResult(
                    f.filename, season, episode, "invalid_content", warning="file too large"
                )
            )
            continue
        if not is_valid_srt_content(f.content):
            results.append(
                PreviewFileResult(
                    f.filename, season, episode, "invalid_content", warning="not a valid SRT"
                )
            )
            continue

        code = f"S{season:02d}E{episode:02d}"
        if coverage_by_season.get(season, {}).get(code, "missing") != "missing":
            results.append(PreviewFileResult(f.filename, season, episode, "already_covered"))
            continue

        results.append(
            PreviewFileResult(
                f.filename, season, episode, "ready", warning=_encoding_warning(f.content)
            )
        )

    return results
