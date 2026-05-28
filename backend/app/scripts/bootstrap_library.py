"""Bootstrap-library CLI.

Walks a directory of MKVs labeled `Show - SnnEnn.mkv`, fingerprints each one,
and enqueues a FingerprintContribution row tagged `match_source="bootstrap"`.

Phase 1 scope: **TV shows only.** Pointing this at a movies directory will
produce TMDB misses for every file (since lookups go through TMDB *TV* search)
and end with `queued=0, skipped=N`. Movie-bootstrap support lives in a future
phase alongside the broader movie identification flow.

Usage:
  uv run python -m app.scripts.bootstrap_library /path/to/tv-library [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import re
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path

from loguru import logger

EP_REGEX = re.compile(
    r"(?P<show>.+?)\s*-\s*S(?P<season>\d{1,2})E(?P<ep>\d{1,3})\s*\.\w+$",
    re.IGNORECASE,
)

# Commit accumulated contributions in batches so a mid-run extractor failure
# (e.g. a corrupt MKV partway through a 5000-file library) doesn't discard
# the contributions already collected.
BOOTSTRAP_BATCH_SIZE = 200

SearchFn = Callable[[str, str], Awaitable[int | None]]


def parse_episode_filename(name: str) -> tuple[str, int, int] | None:
    """Return (show, season, episode) for canonical 'Show - SnnEnn.ext' names, else None."""
    m = EP_REGEX.match(name)
    if not m:
        return None
    return m["show"].strip(), int(m["season"]), int(m["ep"])


def walk_library(root: Path) -> Iterable[tuple[Path, tuple[str, int, int]]]:
    """Yield (file_path, (show, season, episode)) for every labeled MKV under root, skipping Extras."""
    for mkv in sorted(root.rglob("*.mkv")):
        if "Extras" in mkv.parts:
            continue
        label = parse_episode_filename(mkv.name)
        if label is None:
            continue
        yield mkv, label


async def resolve_tmdb_id(
    show: str,
    content_type: str,
    *,
    search_fn: SearchFn,
    cache: dict[tuple[str, str], int],
) -> int | None:
    """Resolve a show name to a TMDB ID with an in-memory cache.

    Misses are NOT cached so a later run can succeed on a transient lookup failure.
    """
    key = (show, content_type)
    if key in cache:
        return cache[key]
    result = await search_fn(show, content_type)
    if result is not None:
        cache[key] = result
    return result


async def _default_search(show: str, content_type: str) -> int | None:
    """Resolve a name -> TMDB ID using the existing fetch_show_id / fetch_movie_id helpers.

    These are synchronous functions backed by @lru_cache, so we offload them to
    a thread to keep the event loop free.

    fetch_show_id / fetch_movie_id return a *string* TMDB ID (e.g. "12345") or
    None; we cast to int before returning.
    """
    try:
        if content_type == "tv":
            from app.matcher.tmdb_client import fetch_show_id

            raw = await asyncio.to_thread(fetch_show_id, show)
        else:
            from app.matcher.tmdb_client import fetch_movie_id

            raw = await asyncio.to_thread(fetch_movie_id, show)
    except Exception as e:
        # Network errors, bad TMDB token, etc. — treat as a miss so the bootstrap
        # loop continues with the next file instead of aborting the whole run.
        logger.warning(f"TMDB lookup for {show!r} failed: {e}")
        return None

    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(f"TMDB returned non-integer ID {raw!r} for {show!r}")
        return None


async def bootstrap_directory(
    root: Path,
    *,
    dry_run: bool = False,
    fpcalc_path: str | None = None,
    search_fn: SearchFn | None = None,
) -> dict[str, int]:
    """Walk `root`, extract + enqueue contributions, return summary counts.

    ``search_fn`` is injectable for testability; defaults to `_default_search`.
    """
    from app.database import async_session
    from app.matcher.chromaprint_extractor import ChromaprintExtractor
    from app.services.config_service import get_config
    from app.services.contribution_queue import ContributionQueue

    counters: dict[str, int] = {
        "scanned": 0,
        "skipped": 0,
        "extracted": 0,
        "queued": 0,
        "errors": 0,
    }
    cache: dict[tuple[str, str], int] = {}
    if search_fn is None:
        search_fn = _default_search

    if fpcalc_path is None:
        from app.api.validation import detect_fpcalc

        detected = await asyncio.to_thread(detect_fpcalc)
        fpcalc_path = detected.path if detected.found else None
    if not fpcalc_path:
        logger.error("fpcalc not configured; cannot bootstrap")
        return counters

    extractor = ChromaprintExtractor(fpcalc_path=fpcalc_path)

    async with async_session() as session:
        cfg = await get_config()
        pseudonym = cfg.contribution_pseudonym
        if not pseudonym:
            logger.error("contribution_pseudonym not set; start the app once before bootstrapping")
            return counters

        for path, (show, season, episode) in walk_library(root):
            counters["scanned"] += 1
            tmdb_id = await resolve_tmdb_id(show, "tv", search_fn=search_fn, cache=cache)
            if tmdb_id is None:
                logger.warning(f"Could not resolve TMDB ID for {show!r}; skipping")
                counters["skipped"] += 1
                continue

            try:
                fp = await extractor.extract(str(path))
                counters["extracted"] += 1
            except Exception as e:
                logger.error(f"fpcalc failed on {path}: {e}")
                counters["errors"] += 1
                continue

            if dry_run:
                logger.info(
                    f"[dry-run] would queue {show} s{season}e{episode} ({len(fp.hashes)} hashes)"
                )
                continue

            await ContributionQueue().enqueue(
                session=session,
                title_id=None,  # bootstrap rows have no DiscTitle parent
                chromaprint_blob=fp.to_blob(),
                tmdb_id=tmdb_id,
                season=season,
                episode=episode,
                match_confidence=1.0,  # filename was the source of truth
                match_source="bootstrap",
                disc_content_hash=None,
                pseudonym=pseudonym,
                contributions_enabled=cfg.enable_fingerprint_contributions,
            )
            counters["queued"] += 1
            # Commit in batches so a mid-run extractor failure doesn't discard
            # the contributions already queued. Trigger on `scanned` (every
            # file walked, success or failure) rather than `queued` — that way
            # a streak of failures right after a batch boundary doesn't push
            # the next commit arbitrarily far into the future.
            if counters["scanned"] % BOOTSTRAP_BATCH_SIZE == 0:
                await session.commit()
        if not dry_run:
            await session.commit()

    return counters


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap chromaprint contributions from an existing library"
    )
    parser.add_argument("library_root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fpcalc", type=str, default=None, help="Override fpcalc binary path")
    args = parser.parse_args()

    counters = asyncio.run(
        bootstrap_directory(args.library_root, dry_run=args.dry_run, fpcalc_path=args.fpcalc)
    )
    logger.info(f"Bootstrap done: {counters}")
    return 0 if counters["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(_main())
