"""Normalize and deduplicate SRT filenames in the subtitle cache.

Walks ``<subtitles_cache_path>/data/<dir>/`` and rewrites every ``.srt`` file to
the canonical ``{show} - S{SS}E{EE}.srt`` form used by the matcher and the build
pipeline. The cache is keyed by tmdb_id (``data/195241/``), so the show-name
prefix for filenames is resolved from the id via TMDB; legacy name-keyed dirs
(``data/Frasier/``) use their own name. When two or more files in the same dir
parse to the same
(season, episode) pair (e.g. a manually downloaded ``... - 1x02 - Title.srt``
sitting next to ``... - S01E02.srt``), the script keeps the best candidate and
deletes the duplicates.

"Best" is decided in this order:
    1. Already canonically named.
    2. Largest valid SRT (file size as a proxy for completeness).

Run this AFTER manually dropping SRTs into the cache and BEFORE
``build_subtitle_cache.py`` — the build script harvests by canonical filename
via ``find_existing_subtitle``, so duplicates inflate the corpus and the
TF-IDF IDF fit if they aren't collapsed first.

Usage (from backend/):
    uv run python scripts/normalize_subtitle_cache.py                # all shows
    uv run python scripts/normalize_subtitle_cache.py --show "Bluey" # one show
    uv run python scripts/normalize_subtitle_cache.py --dry-run      # preview
"""

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# Idempotent — repeated importlib loads (e.g. one fixture per test file) would
# otherwise accumulate duplicate entries in sys.path on every exec_module call.
_backend_dir = str(Path(__file__).parent.parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from loguru import logger

from app.matcher.subtitle_utils import (
    corpus_dir_name,
    parse_season_episode_numbers,
    sanitize_filename,
)
from app.matcher.testing_service import is_valid_srt_file


@dataclass
class Tally:
    renamed: int = 0
    duplicates_deleted: int = 0
    invalid_deleted: int = 0
    unparseable: list[Path] = field(default_factory=list)
    already_canonical: int = 0
    shows_processed: int = 0


def _canonical_name(show_dir_name: str, season: int, episode: int) -> str:
    """Return the canonical SRT filename for a given (show, season, episode).

    Matches the format emitted by ``testing_service.download_subtitles`` so the
    matcher's ``find_existing_subtitle`` resolves it on the first pattern try.
    """
    return f"{show_dir_name} - S{season:02d}E{episode:02d}.srt"


def _rank_candidate(path: Path, canonical: str) -> tuple[int, int]:
    """Sort key — higher tuple wins. ``(is_canonical, size_bytes)``.

    Canonical-naming is the dominant signal so we never demote a correctly
    named file in favor of a larger badly-named one (renaming the larger one
    on top would clobber the canonical file we'd otherwise keep). Within the
    same naming bucket, larger files usually carry more dialogue lines and
    make better matching references.
    """
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return (1 if path.name == canonical else 0, size)


def _resolve_prefix(dir_name: str) -> str | None:
    """Return the show-name prefix to use for canonical filenames in this dir.

    The cache is keyed by tmdb_id now, so a numeric dir name (``data/195241/``)
    is a tmdb_id, not a show name. Files inside must still be NAME-prefixed
    (``Frasier - S01E02.srt``) because ``find_existing_subtitle`` (used by the
    downloader's cache-hit check) looks them up by the sanitized show name — so
    resolve the id back to its canonical title via TMDB. A non-numeric dir is a
    legacy name-keyed dir whose own name IS the prefix.

    Returns None when a numeric dir can't be resolved (offline / TMDB miss); the
    caller then SKIPS the dir rather than rewrite filenames to an id prefix,
    which would make the downloader's cache-hit check miss every episode.
    """
    if not dir_name.isdigit():
        return dir_name
    try:
        from app.matcher.tmdb_client import fetch_show_details

        details = fetch_show_details(int(dir_name))
        name = (details or {}).get("name")
        if name:
            return sanitize_filename(name)
        logger.warning(f"  {dir_name}: numeric dir but TMDB had no show for that id")
    except Exception as e:  # noqa: BLE001 - resolution is best-effort
        logger.warning(f"  {dir_name}: TMDB id resolution failed ({e})")
    return None


def _normalize_show_dir(show_dir: Path, prefix: str, *, dry_run: bool, tally: Tally) -> None:
    """Normalize a single ``<cache>/data/<dir>/`` directory in place.

    ``prefix`` is the show NAME used in canonical filenames (``{prefix} -
    S01E02.srt``). It is distinct from ``show_dir.name`` because the dir may be
    keyed by tmdb_id while the files stay name-prefixed.
    """
    # Group by parsed (season, episode); unparseable files surface as a
    # warning at the end and are not touched.
    groups: dict[tuple[int, int], list[Path]] = defaultdict(list)
    for srt in sorted(show_dir.glob("*.srt")):
        parsed = parse_season_episode_numbers(srt.stem)
        if parsed is None:
            tally.unparseable.append(srt)
            continue
        groups[parsed].append(srt)

    show_dir_name = prefix
    for (season, episode), candidates in sorted(groups.items()):
        canonical = _canonical_name(show_dir_name, season, episode)

        # Drop invalid files first so they don't get picked as "winner" by
        # virtue of being the largest. is_valid_srt_file rejects HTML
        # error pages, empty stubs, and other download accidents.
        kept: list[Path] = []
        for c in candidates:
            if is_valid_srt_file(c):
                kept.append(c)
            else:
                logger.warning(f"  invalid SRT, deleting: {c.name}")
                if not dry_run:
                    c.unlink(missing_ok=True)
                tally.invalid_deleted += 1

        if not kept:
            continue

        kept.sort(key=lambda p: _rank_candidate(p, canonical), reverse=True)
        winner, losers = kept[0], kept[1:]

        for loser in losers:
            logger.info(f"  dedup: removing {loser.name} (kept {winner.name})")
            if not dry_run:
                loser.unlink(missing_ok=True)
            tally.duplicates_deleted += 1

        target = show_dir / canonical
        if winner.name == canonical:
            tally.already_canonical += 1
            continue

        # Guard against an existing canonical file we didn't pick (shouldn't
        # happen because it would have been in `candidates` and won the
        # ranking, but if a stale file with that exact name exists outside
        # the parse-able set, refuse to clobber it).
        if target.exists() and target != winner:
            logger.warning(
                f"  rename target already exists, skipping: {winner.name} -> {canonical}"
            )
            continue

        logger.info(f"  rename: {winner.name} -> {canonical}")
        if not dry_run:
            winner.rename(target)
        tally.renamed += 1


def _resolve_cache_dir(override: str | None) -> Path:
    """Pick the data directory: ``--cache-dir`` wins, else AppConfig."""
    if override:
        return Path(override).expanduser() / "data"

    from app.services.config_service import get_config_sync

    config = get_config_sync()
    return Path(config.subtitles_cache_path).expanduser() / "data"


def _resolve_show_dir(data_dir: Path, show: str) -> Path | None:
    """Locate the on-disk dir for a ``--show`` argument under the id-keyed cache.

    Accepts either a numeric tmdb_id (``--show 195241`` → ``data/195241/``) or a
    show name (``--show Frasier``). For a name, resolve it to a tmdb_id via TMDB
    and prefer the id-keyed dir; fall back to the legacy sanitized-name dir when
    the id can't be resolved or that dir doesn't exist. Returns None if neither
    candidate directory exists.
    """
    if show.isdigit():
        return data_dir / show

    tmdb_id = None
    try:
        from app.matcher.tmdb_client import fetch_show_id

        tmdb_id = fetch_show_id(show)
    except Exception as e:  # noqa: BLE001 - resolution is best-effort
        logger.warning(f"TMDB lookup failed for {show!r} ({e}); trying legacy name dir")

    if tmdb_id is not None:
        id_dir = data_dir / corpus_dir_name(tmdb_id, show)
        if id_dir.is_dir():
            return id_dir

    # Apply the same sanitizer the writer uses so users can pass the raw show
    # name and still land on a legacy name-keyed directory.
    legacy_dir = data_dir / sanitize_filename(show)
    return legacy_dir if legacy_dir.is_dir() else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Normalize and deduplicate SRT filenames in the subtitle cache"
    )
    parser.add_argument(
        "--cache-dir",
        type=str,
        default="",
        help="Override the subtitles cache root (default: AppConfig.subtitles_cache_path)",
    )
    parser.add_argument(
        "--show",
        type=str,
        default="",
        help="Limit to one show by directory name (e.g. 'Bluey'); accepts unsanitized input",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without renaming or deleting anything",
    )
    args = parser.parse_args()

    data_dir = _resolve_cache_dir(args.cache_dir or None)
    if not data_dir.exists():
        logger.error(f"Subtitle cache data dir not found: {data_dir}")
        return 1

    if args.show:
        target_dir = _resolve_show_dir(data_dir, args.show)
        if target_dir is None or not target_dir.is_dir():
            logger.error(f"Show dir not found for {args.show!r} under {data_dir}")
            return 1
        show_dirs = [target_dir]
    else:
        show_dirs = sorted(d for d in data_dir.iterdir() if d.is_dir())

    tally = Tally()
    mode = "DRY RUN" if args.dry_run else "APPLY"
    logger.info(f"[{mode}] Normalizing {len(show_dirs)} show dir(s) under {data_dir}")

    for show_dir in show_dirs:
        prefix = _resolve_prefix(show_dir.name)
        if prefix is None:
            logger.warning(
                f"{show_dir.name}/: id-keyed dir with no resolvable TMDB name; "
                f"skipping (can't choose a name prefix for filenames)"
            )
            continue
        logger.info(f"{show_dir.name}/")
        _normalize_show_dir(show_dir, prefix, dry_run=args.dry_run, tally=tally)
        tally.shows_processed += 1

    logger.info(
        f"Done. shows={tally.shows_processed} "
        f"renamed={tally.renamed} "
        f"duplicates_deleted={tally.duplicates_deleted} "
        f"invalid_deleted={tally.invalid_deleted} "
        f"already_canonical={tally.already_canonical} "
        f"unparseable={len(tally.unparseable)}"
    )
    if tally.unparseable:
        logger.warning("Unparseable filenames (no season/episode detected):")
        for p in tally.unparseable:
            logger.warning(f"  {p.relative_to(data_dir)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
