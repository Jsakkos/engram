"""Build the precomputed subtitle-vector cache shipped with Engram.

Harvests subtitles for the most-voted TV shows on TMDB, reduces each episode to a
HASHED TF-IDF vector (no readable vocabulary -- see app/matcher/vectorizer_config),
discards the raw SRT, and packages the vectors into a `.tar.gz` plus a manifest
for hosting on GitHub Releases.

Usage (from backend/):
    uv run python scripts/build_subtitle_cache.py --limit 300
    uv run python scripts/build_subtitle_cache.py --shows "The Expanse,Arrested Development"

TMDB / OpenSubtitles credentials are read from the AppConfig DB row; in CI they
are bootstrapped from the env vars TMDB_API_KEY, OPENSUBTITLES_API_KEY,
OPENSUBTITLES_USERNAME, OPENSUBTITLES_PASSWORD.
"""

import argparse
import datetime
import hashlib
import json
import os
import shutil
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
from loguru import logger
from scipy import sparse

from app.matcher.episode_identification import SubtitleCache
from app.matcher.subtitle_utils import sanitize_filename
from app.matcher.testing_service import download_subtitles
from app.matcher.tmdb_client import (
    fetch_show_details,
    fetch_show_id,
    fetch_shows_by_vote_count,
)
from app.matcher.vectorizer_config import (
    CACHE_FORMAT_VERSION,
    HASHING_N_FEATURES,
    apply_tfidf,
    build_hashing_vectorizer,
    compute_idf,
    vectorizer_config_hash,
)

_VALID_STATUSES = {"cached", "downloaded"}


def _ensure_db_schema() -> None:
    """Create DB tables — the standalone script has no app lifespan to do it.

    The running app creates the schema in ``database.init_db()``. This script
    runs without that lifespan, so a fresh ``engram.db`` (e.g. on a CI runner)
    has no tables. ``create_all`` is idempotent, so this is safe against an
    existing dev database too.
    """
    from sqlmodel import SQLModel

    # Importing app.database registers AppConfig/DiscJob on SQLModel.metadata.
    import app.database  # noqa: F401
    from app.services.config_service import _get_sync_engine

    SQLModel.metadata.create_all(_get_sync_engine())


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


def _select_shows(args) -> list[dict]:
    """Return [{name, tmdb_id, seasons}] for the shows to cache."""
    if args.shows:
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


def _harvest_show(show: dict, args) -> list[tuple[int, str, Path]]:
    """Download subtitles for every season. Returns [(season, episode_code, srt_path)]."""
    harvested: list[tuple[int, str, Path]] = []
    canonical = show["name"]
    for season in range(1, show["seasons"] + 1):
        try:
            result = download_subtitles(canonical, season)
        except Exception as e:
            logger.warning(f"  {canonical} S{season:02d}: harvest failed ({e})")
            continue

        episodes = [
            ep for ep in result["episodes"] if ep["status"] in _VALID_STATUSES and ep.get("path")
        ]
        total = result.get("total_episodes", 0) or len(result["episodes"])
        ratio = len(episodes) / total if total else 0.0
        if ratio < args.min_episodes_ratio:
            logger.info(
                f"  {canonical} S{season:02d}: {len(episodes)}/{total} episodes "
                f"({ratio:.0%}) below threshold {args.min_episodes_ratio:.0%}; skipping season"
            )
            continue

        for ep in episodes:
            harvested.append((season, ep["code"], Path(ep["path"])))
        logger.info(f"  {canonical} S{season:02d}: {len(episodes)}/{total} episodes")
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
    args = parser.parse_args()

    if args.keep_srt:
        if args.clean_srt:
            logger.warning(
                "--keep-srt is deprecated (no-op); --clean-srt takes precedence — "
                "SRTs will be deleted after the build."
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
        logger.warning("TMDB API key not configured — show lookups will fail")

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

    for idx, show in enumerate(shows, 1):
        logger.info(f"[{idx}/{len(shows)}] {show['name']} (TMDB {show['tmdb_id']})")
        harvested = _harvest_show(show, args)
        if not harvested:
            logger.warning(f"  {show['name']}: no usable seasons; omitting from cache")
            continue

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

    # --- Write per-(show, season) L2-normalized TF-IDF matrices ----------------
    for show_name, season, codes, counts in blocks:
        show_dir = precomputed_dir / sanitize_filename(show_name)
        show_dir.mkdir(parents=True, exist_ok=True)
        tfidf = apply_tfidf(counts, idf)
        sparse.save_npz(show_dir / f"S{season:02d}.npz", tfidf)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
