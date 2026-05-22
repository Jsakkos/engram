"""Build the precomputed subtitle-vector cache shipped with Engram.

Harvests subtitles for the most-voted TV shows on TMDB, reduces each episode to a
HASHED TF-IDF vector (no readable vocabulary -- see app/matcher/vectorizer_config),
discards the raw SRT, and packages the vectors into a `.tar.gz` plus a manifest
for hosting on GitHub Releases.

Usage (from backend/):
    uv run python scripts/build_subtitle_cache.py --limit 300
    uv run python scripts/build_subtitle_cache.py --shows "The Expanse,Arrested Development"
    uv run python scripts/build_subtitle_cache.py --show-list scripts/curated_shows.csv

TMDB / OpenSubtitles credentials are read from the AppConfig DB row; in CI they
are bootstrapped from the env vars TMDB_API_KEY, OPENSUBTITLES_API_KEY,
OPENSUBTITLES_USERNAME, OPENSUBTITLES_PASSWORD.
"""

import argparse
import csv
import datetime
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# Idempotent — repeated importlib loads (e.g. one fixture per test file) would
# otherwise accumulate duplicate entries in sys.path on every exec_module call.
_backend_dir = str(Path(__file__).parent.parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from datetime import UTC

import numpy as np
from loguru import logger
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from scipy import sparse

from app.matcher import coverage_tracker
from app.matcher.episode_identification import SubtitleCache
from app.matcher.subtitle_utils import sanitize_filename
from app.matcher.testing_service import download_subtitles, get_last_quota
from app.matcher.tmdb_client import (
    fetch_show_details,
    fetch_show_id,
    fetch_shows_by_vote_count,
)
from app.matcher.vectorizer_config import (
    CACHE_FORMAT_VERSION,
    HASHING_N_FEATURES,
    build_hashing_vectorizer,
    compute_idf,
    vectorizer_config_hash,
)

_VALID_STATUSES = {"cached", "downloaded"}


@dataclass
class RunTally:
    """Running counters for the build run, surfaced in the final summary.

    Mutated in place by ``_harvest_show`` so per-show progress can be
    rendered without changing call-site semantics.
    """

    downloaded: int = 0
    cache_hits: int = 0
    not_found: int = 0
    seasons_done: int = 0
    seasons_skipped_below_threshold: int = 0
    seasons_failed: int = 0
    # Per-provider download counts (opensubtitles_api, addic7ed, tvsubtitles).
    # Surfaces which provider served each NEW download so a quiet fallback's
    # contribution is visible in the final summary. Cache hits are excluded --
    # they're reported separately and carry no originating-provider info.
    by_source: Counter[str] = field(default_factory=Counter)
    start_time: float = field(default_factory=time.monotonic)

    def elapsed_str(self) -> str:
        secs = int(time.monotonic() - self.start_time)
        return f"{secs // 3600}:{(secs % 3600) // 60:02d}:{secs % 60:02d}"

    @property
    def cache_hit_rate(self) -> float:
        denom = self.cache_hits + self.downloaded
        return self.cache_hits / denom if denom else 0.0


def _ensure_db_schema() -> None:
    """Create and migrate the DB schema via the app's canonical ``init_db()``.

    The standalone script runs without the FastAPI lifespan that normally
    calls ``database.init_db()``. A fresh ``engram.db`` (e.g. a CI runner)
    needs its tables created; a pre-existing dev database needs any
    recently-added columns applied. ``create_all`` alone does the former but
    never the latter, so an older local ``engram.db`` ends up missing newer
    columns such as ``app_config.precomputed_cache_enabled``. Delegating to
    ``init_db()`` runs the same create-all + ``_add_missing_columns`` +
    Alembic migration path the running app uses on startup.
    """
    import asyncio

    from app.database import init_db

    asyncio.run(init_db())


def _bootstrap_config_from_env() -> None:
    """Populate the AppConfig DB row with credentials from env vars (for CI)."""
    env_map = {
        "tmdb_api_key": "TMDB_API_KEY",
        "opensubtitles_api_key": "OPENSUBTITLES_API_KEY",
        "opensubtitles_username": "OPENSUBTITLES_USERNAME",
        "opensubtitles_password": "OPENSUBTITLES_PASSWORD",
    }
    updates = {field: os.environ[env] for field, env in env_map.items() if os.environ.get(env)}
    if not updates:
        return

    from sqlmodel import Session, select

    from app.models.app_config import AppConfig
    from app.services.config_service import _get_sync_engine

    with Session(_get_sync_engine()) as session:
        config = session.exec(select(AppConfig).limit(1)).first()
        if config is None:
            config = AppConfig()
            session.add(config)
        for field, value in updates.items():
            setattr(config, field, value)
        session.commit()
    logger.info(f"Bootstrapped config from env: {sorted(updates)}")


def _read_show_list(path: str) -> list[dict]:
    """Parse a curated show-list file into ``[{name, id}]`` candidates.

    Two formats are supported:
    - A CSV with a ``tmdb_id`` column (e.g. the curated_shows.csv produced by the
      curation tooling). IDs are used directly, so name-collision titles like the
      US vs UK "The Office" resolve unambiguously. A ``name`` column is kept for
      logging; any row whose tmdb_id is missing/non-numeric falls back to a name
      lookup via ``fetch_show_id``.
    - A plain name list: a ``.txt`` with one show per line, or a CSV whose only
      useful column is ``name``. Each name is resolved via ``fetch_show_id`` (the
      same path as ``--shows``).

    Blank lines and ``#`` comment lines are ignored.
    """
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"--show-list file not found: {path}")
    text = p.read_text(encoding="utf-8-sig")

    if p.suffix.lower() == ".csv":
        rows = list(csv.DictReader(io.StringIO(text)))
        fields = set(rows[0].keys()) if rows else set()
        if "tmdb_id" in fields:
            candidates = []
            for r in rows:
                tid = (r.get("tmdb_id") or "").strip()
                name = (r.get("name") or "").strip()
                if tid.isdigit():
                    candidates.append({"name": name or tid, "id": int(tid)})
                elif name:
                    candidates.append({"name": name, "id": fetch_show_id(name)})
            return candidates
        if "name" in fields:
            return [
                {"name": n, "id": fetch_show_id(n)}
                for r in rows
                if (n := (r.get("name") or "").strip())
            ]

    names = [
        ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
    ]
    return [{"name": n, "id": fetch_show_id(n)} for n in names]


def _select_shows(args) -> list[dict]:
    """Return [{name, tmdb_id, seasons}] for the shows to cache."""
    if args.show_list:
        candidates = _read_show_list(args.show_list)
        logger.info(f"Loaded {len(candidates)} shows from {args.show_list}")
    elif args.shows:
        names = [s.strip() for s in args.shows.split(",") if s.strip()]
        candidates = [{"name": n, "id": fetch_show_id(n)} for n in names]
    else:
        # Discover returns shows already ranked by vote_count desc; iterating
        # pages in order and deduping preserves that ranking (dict is ordered).
        seen: dict[int, dict] = {}
        for page in range(1, args.pages + 1):
            for show in fetch_shows_by_vote_count(page):
                if show.get("id") and show["id"] not in seen:
                    seen[show["id"]] = show
            time.sleep(args.sleep)
        ranked = list(seen.values())
        candidates = [{"name": s["name"], "id": s["id"]} for s in ranked[: args.limit]]

    shows = []
    for cand in candidates:
        if not cand["id"]:
            logger.warning(f"Skipping '{cand['name']}': not found on TMDB")
            continue
        details = fetch_show_details(cand["id"])
        if not details:
            logger.warning(f"Skipping '{cand['name']}': could not fetch TMDB details")
            continue
        shows.append(
            {
                "name": details.get("name", cand["name"]),
                "tmdb_id": cand["id"],
                "seasons": details.get("number_of_seasons", 0),
            }
        )
        time.sleep(args.sleep)
    return shows


def _harvest_show(
    show: dict,
    args,
    tally: RunTally,
    on_season_done=None,
) -> list[tuple[int, str, Path]]:
    """Download subtitles for every season. Returns [(season, episode_code, srt_path)].

    Mutates ``tally`` in place with per-status counts so the caller can render
    progress without re-walking the episode lists. ``on_season_done``, if
    given, is invoked after each season finishes (success, skip, or fail) so
    the caller can advance a Progress bar without coupling to its
    implementation.
    """
    harvested: list[tuple[int, str, Path]] = []
    canonical = show["name"]
    for season in range(1, show["seasons"] + 1):
        if not args.retry_low_coverage:
            skip, prev = coverage_tracker.should_skip(
                show["tmdb_id"],
                season,
                args.min_episodes_ratio,
                args.skip_window_days,
            )
            if skip:
                from datetime import datetime

                ts = datetime.fromtimestamp(prev["attempted_at"], tz=UTC).strftime("%Y-%m-%d")
                logger.info(
                    f"  {canonical} S{season:02d}: skipping (prior coverage "
                    f"{prev['coverage_ratio']:.0%} on {ts}; "
                    f"pass --retry-low-coverage to retry)"
                )
                tally.seasons_skipped_below_threshold += 1
                if on_season_done is not None:
                    on_season_done()
                continue

        try:
            # Always re-harvest when building the cache, even if a prior
            # precomputed build already covers this season.
            result = download_subtitles(canonical, season, use_precomputed=False)
        except Exception as e:
            # exc_info=True per CLAUDE.md: the warning string alone (often
            # just "429 Too Many Requests") doesn't say which provider in
            # the OS/TMDB retry chain raised — the traceback is the only
            # signal for diagnosing flaky seasons after the fact.
            logger.warning(f"  {canonical} S{season:02d}: harvest failed ({e})", exc_info=True)
            tally.seasons_failed += 1
            if on_season_done is not None:
                on_season_done()
            continue

        # Tally every episode (including failures) so the running totals
        # match what actually happened, not what we chose to keep.
        for ep in result["episodes"]:
            status = ep.get("status")
            if status == "cached":
                tally.cache_hits += 1
            elif status == "downloaded":
                tally.downloaded += 1
            elif status == "not_found":
                tally.not_found += 1
            source = ep.get("source")
            if source and status == "downloaded":
                tally.by_source[source] += 1

        episodes = [
            ep for ep in result["episodes"] if ep["status"] in _VALID_STATUSES and ep.get("path")
        ]
        total = result.get("total_episodes", 0) or len(result["episodes"])
        ratio = len(episodes) / total if total else 0.0

        # Record EVERY season we actually attempted (success or below-threshold).
        # This is what powers the skip-list on the next run — without it, low
        # coverage seasons keep getting re-attempted every day, burning
        # rate-limit quota on shows that simply don't have subtitles.
        coverage_tracker.record(show["tmdb_id"], season, total, len(episodes))

        if ratio < args.min_episodes_ratio:
            logger.info(
                f"  {canonical} S{season:02d}: {len(episodes)}/{total} episodes "
                f"({ratio:.0%}) below threshold {args.min_episodes_ratio:.0%}; skipping season"
            )
            tally.seasons_skipped_below_threshold += 1
            if on_season_done is not None:
                on_season_done()
            continue

        for ep in episodes:
            harvested.append((season, ep["code"], Path(ep["path"])))
        logger.info(f"  {canonical} S{season:02d}: {len(episodes)}/{total} episodes")
        tally.seasons_done += 1
        if on_season_done is not None:
            on_season_done()
        time.sleep(args.sleep)
    return harvested


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Engram subtitle-vector cache")
    parser.add_argument(
        "--limit", type=int, default=300, help="Number of shows (top by TMDB vote count)"
    )
    parser.add_argument("--pages", type=int, default=15, help="TMDB discover pages to scan")
    parser.add_argument(
        "--shows", type=str, default="", help="Comma-separated show names (overrides popular)"
    )
    parser.add_argument(
        "--show-list",
        type=str,
        default="",
        help=(
            "Path to a curated show-list file (overrides --shows and popularity). "
            "A CSV with a tmdb_id column (e.g. scripts/curated_shows.csv) is matched "
            "by ID for unambiguous lookup; a plain name-per-line .txt also works."
        ),
    )
    parser.add_argument(
        "--min-episodes-ratio", type=float, default=0.6, help="Min episode coverage per season"
    )
    parser.add_argument("--sleep", type=float, default=1.0, help="Seconds between API/scrape calls")
    parser.add_argument(
        "--output", type=str, default="engram-subtitle-cache.tar.gz", help="Output tarball path"
    )
    parser.add_argument(
        "--content-version", type=str, default="", help="Cache content version (default: today)"
    )
    parser.add_argument(
        "--clean-srt",
        action="store_true",
        help="Delete harvested SRTs after building (default: keep them so re-runs resume)",
    )
    # Deprecated: SRTs are now kept by default; --keep-srt is a no-op kept for compatibility.
    parser.add_argument("--keep-srt", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--retry-low-coverage",
        action="store_true",
        help=(
            "Bypass the skip-list and re-attempt seasons that previously fell below "
            "the coverage threshold. Use after VIP quota refills or after enabling a "
            "new provider."
        ),
    )
    parser.add_argument(
        "--skip-window-days",
        type=int,
        default=30,
        help=(
            "How long a low-coverage season stays on the skip-list before it is "
            "retried automatically (default: 30)."
        ),
    )
    args = parser.parse_args()

    if args.keep_srt:
        if args.clean_srt:
            logger.warning(
                "--keep-srt is deprecated and has no effect; SRTs will be "
                "deleted because --clean-srt is set."
            )
        else:
            logger.warning(
                "--keep-srt is deprecated and has no effect; SRTs are kept by default. "
                "Pass --clean-srt to delete them after the build."
            )

    _ensure_db_schema()
    _bootstrap_config_from_env()

    from app.services.config_service import get_config_sync

    config = get_config_sync()
    if (
        config.opensubtitles_api_key
        and config.opensubtitles_username
        and config.opensubtitles_password
    ):
        logger.info("OpenSubtitles API: ACTIVE — bulk season downloads enabled")
    else:
        logger.warning(
            "OpenSubtitles API: INACTIVE — credentials missing; falling back to "
            "rate-limited scrapers (slow, flaky). Set opensubtitles_api_key, "
            "opensubtitles_username and opensubtitles_password to enable it."
        )
    if not config.tmdb_api_key:
        logger.error("TMDB API key not configured — show lookups require it; aborting")
        return 1

    cache_dir = Path(config.subtitles_cache_path).expanduser()
    precomputed_dir = cache_dir / "precomputed"
    if precomputed_dir.exists():
        shutil.rmtree(precomputed_dir)
    precomputed_dir.mkdir(parents=True, exist_ok=True)

    content_version = args.content_version or datetime.date.today().isoformat()

    shows = _select_shows(args)
    logger.info(f"Selected {len(shows)} shows for the cache")

    # --- Harvest SRT + extract cleaned full text per episode -------------------
    subtitle_cache = SubtitleCache()
    # blocks: list of (show_name, season, [episode_codes], count_csr)
    blocks: list[tuple[str, int, list[str], object]] = []
    hv = build_hashing_vectorizer()
    manifest_shows: dict[str, dict] = {}
    tally = RunTally()

    # Rich Console auto-detects TTY: in a local terminal we get a live
    # updating progress bar; under GitHub Actions (no TTY) it degrades to
    # one log line per console.log() call and the bar updates are
    # effectively no-ops — exactly the shape that's readable in CI logs.
    console = Console()
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    ) as progress:
        shows_task = progress.add_task("Building cache", total=len(shows))
        for idx, show in enumerate(shows, 1):
            show_start = time.monotonic()
            tally_snapshot = (tally.cache_hits, tally.downloaded, tally.not_found)
            # `transient` is a Progress() constructor argument that makes
            # the whole bar vanish on exit, not a per-task option — passing
            # it here is silently stored as task metadata and has no visual
            # effect. The per-show task is removed cleanly by the
            # `progress.remove_task(season_task)` call below after the
            # show finishes.
            season_task = progress.add_task(
                f"  {show['name']}",
                total=show["seasons"],
            )

            logger.info(f"[{idx}/{len(shows)}] {show['name']} (TMDB {show['tmdb_id']})")
            # Bind season_task via default arg so the closure captures THIS
            # iteration's task id, not the loop variable (B023).
            harvested = _harvest_show(
                show,
                args,
                tally,
                on_season_done=lambda task=season_task: progress.advance(task),
            )
            progress.remove_task(season_task)
            progress.advance(shows_task)

            if not harvested:
                # All seasons either failed or fell below the coverage
                # threshold — emit a yellow SKIP banner instead of the green
                # OK banner so the log line matches what actually happened.
                console.log(
                    f"[yellow]SKIP[/] {show['name']} — no usable seasons "
                    f"(omitted from cache) in {int(time.monotonic() - show_start)}s"
                )
                logger.warning(f"  {show['name']}: no usable seasons; omitting from cache")
                continue

            # Per-show summary — show what we did this iteration.
            delta_hits = tally.cache_hits - tally_snapshot[0]
            delta_dls = tally.downloaded - tally_snapshot[1]
            delta_nf = tally.not_found - tally_snapshot[2]
            console.log(
                f"[green]OK[/] {show['name']} — "
                f"{delta_hits + delta_dls}/{delta_hits + delta_dls + delta_nf} episodes "
                f"({delta_hits} cached, {delta_dls} new, {delta_nf} missing) "
                f"in {int(time.monotonic() - show_start)}s"
            )

            by_season: dict[int, list[tuple[str, Path]]] = {}
            for season, code, path in harvested:
                by_season.setdefault(season, []).append((code, path))

            show_seasons: list[int] = []
            episode_counts: dict[str, int] = {}
            for season in sorted(by_season):
                episodes = sorted(by_season[season], key=lambda x: x[0])
                texts, codes = [], []
                for code, path in episodes:
                    text = subtitle_cache.get_full_text(str(path))
                    if text:
                        texts.append(text)
                        codes.append(code)
                if not texts:
                    continue
                counts = hv.transform(texts)  # raw hashed term counts
                blocks.append((show["name"], season, codes, counts))
                show_seasons.append(season)
                episode_counts[str(season)] = len(codes)

            if show_seasons:
                manifest_shows[show["name"]] = {
                    "tmdb_id": show["tmdb_id"],
                    "seasons": show_seasons,
                    "episode_counts": episode_counts,
                }

    if not blocks:
        logger.error("No subtitles harvested; nothing to build")
        return 1

    # --- Fit one global IDF across the whole corpus ----------------------------
    all_counts = sparse.vstack([b[3] for b in blocks], format="csr")
    idf = compute_idf(all_counts)
    np.save(precomputed_dir / "idf.npy", idf)
    logger.info(f"Global IDF fit over {all_counts.shape[0]} episodes")

    # --- Write per-(show, season) raw hashed-count matrices --------------------
    # Cache v2 stores uint16 counts on disk rather than the L2-normalized
    # float64 TF-IDF rows v1 used. The loader applies apply_tfidf(counts, idf)
    # at startup; the matcher sees the same matrix it always did. Integer
    # counts cast to uint16 are ~4x smaller in nnz bytes, and DEFLATE in
    # .npz collapses the long runs of 1s much better than it ever could on
    # floats — measured ~85% reduction (~8 KB/episode vs. ~66 KB for v1).
    u16_max = np.iinfo(np.uint16).max
    for show_name, season, codes, counts in blocks:
        show_dir = precomputed_dir / sanitize_filename(show_name)
        show_dir.mkdir(parents=True, exist_ok=True)
        # HashingVectorizer emits float64 counts even with alternate_sign=False;
        # cast to uint16 (clipped defensively — real per-episode token counts
        # are 1-10, but a pathological transcript shouldn't blow up the build).
        counts_u16 = sparse.csr_matrix(
            (
                np.minimum(counts.data, u16_max).astype(np.uint16),
                counts.indices,
                counts.indptr,
            ),
            shape=counts.shape,
        )
        sparse.save_npz(show_dir / f"S{season:02d}.npz", counts_u16)
        with open(show_dir / f"S{season:02d}.index.json", "w", encoding="utf-8") as fh:
            json.dump(codes, fh)

    # --- In-tarball manifest (read by the matcher) -----------------------------
    manifest = {
        "cache_format_version": CACHE_FORMAT_VERSION,
        "vectorizer_config_hash": vectorizer_config_hash(),
        "content_version": content_version,
        "built_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "n_features": HASHING_N_FEATURES,
        "shows": manifest_shows,
    }
    with open(precomputed_dir / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    # --- Package + checksum ----------------------------------------------------
    output_path = Path(args.output).resolve()
    with tarfile.open(output_path, "w:gz") as tar:
        tar.add(precomputed_dir, arcname="precomputed")

    sha = hashlib.sha256()
    with open(output_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            sha.update(chunk)

    # Release-asset manifest = in-tar manifest + the tarball checksum.
    release_manifest = dict(manifest, tarball_sha256=sha.hexdigest())
    release_manifest_path = output_path.with_name("manifest.json")
    with open(release_manifest_path, "w", encoding="utf-8") as fh:
        json.dump(release_manifest, fh, indent=2)

    if args.clean_srt:
        data_dir = cache_dir / "data"
        if data_dir.exists():
            shutil.rmtree(data_dir)
            logger.info("Deleted harvested SRT files (--clean-srt)")

    total_episodes = sum(len(c) for _, _, c, _ in blocks)
    size_mb = output_path.stat().st_size / (1024 * 1024)
    logger.info(
        f"Built cache: {len(manifest_shows)} shows, {total_episodes} episodes, "
        f"{size_mb:.1f} MB -> {output_path}"
    )
    logger.info(f"Release manifest -> {release_manifest_path}")

    # --- Final summary --------------------------------------------------------
    # Single block readable in CI logs. ``console`` was created above in the
    # Progress context but stays usable after the ``with`` exits.
    quota = get_last_quota()
    quota_str = f"{quota['remaining']}" if quota and quota.get("remaining") is not None else "n/a"
    by_source = ", ".join(f"{s}={n}" for s, n in sorted(tally.by_source.items())) or "none"
    console.log(
        "[bold]Final summary[/]: "
        f"{len(manifest_shows)} shows, {total_episodes} episodes packaged "
        f"({size_mb:.1f} MB)\n"
        f"  episodes seen:    {tally.cache_hits + tally.downloaded + tally.not_found}\n"
        f"  cache hits:       {tally.cache_hits}\n"
        f"  new downloads:    {tally.downloaded}\n"
        f"  not found:        {tally.not_found}\n"
        f"  cache hit rate:   {tally.cache_hit_rate:.0%}\n"
        f"  by source:        {by_source}\n"
        f"  seasons OK:       {tally.seasons_done}\n"
        f"  seasons skipped:  {tally.seasons_skipped_below_threshold} (below coverage threshold)\n"
        f"  seasons failed:   {tally.seasons_failed}\n"
        f"  elapsed:          {tally.elapsed_str()}\n"
        f"  OS quota left:    {quota_str} downloads today"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
