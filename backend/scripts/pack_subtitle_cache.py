"""Pack the Engram subtitle-vector cache from already-harvested SRTs on disk.

Unlike ``scripts/build_subtitle_cache.py``, this NEVER downloads subtitles. It
walks the local subtitle cache (``<cache>/data/<show>/<show> - SxxExx.srt``),
reduces every episode to the shared v2 hashed-TF-IDF vector format, verifies the
artifact, and optionally publishes it to the rolling GitHub release. Use it to
ship whatever is already on disk -- including shows added manually that
popularity-mode selection in ``build_subtitle_cache.py`` would never pick.

Show directories are stored under *sanitized* names (``sanitize_filename`` maps
``:`` -> `` -``, ``/`` -> ``-``, ...), but the runtime matcher looks the cache up
by the *canonical* TMDB show name (``manifest["shows"][show_name]``). By default
each show dir is resolved to its canonical name via TMDB (cache-first; this is
metadata only, never a subtitle download) and accepted only if it sanitizes back
to the dir name -- guaranteeing the runtime lookup + on-disk subdir handshake.
``--offline`` skips TMDB entirely and keys the manifest by the dir name; shows
whose real title contains ``:`` or ``/`` then won't be matched at runtime.

Usage (from backend/):
    uv run python scripts/pack_subtitle_cache.py            # build + verify
    uv run python scripts/pack_subtitle_cache.py --offline  # no TMDB
    uv run python scripts/pack_subtitle_cache.py --publish  # + upload release
"""

import argparse
import datetime
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

# Idempotent path insert so ``app.*`` and the sibling build script import whether
# run as ``python scripts/pack_subtitle_cache.py`` or imported in a test.
_backend_dir = str(Path(__file__).parent.parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import numpy as np

# Reuse the canonical DB-bootstrap helpers so the standalone script sets up the
# same schema/credentials path the running app uses (see build_subtitle_cache).
from build_subtitle_cache import _bootstrap_config_from_env, _ensure_db_schema
from loguru import logger
from scipy import sparse

from app.matcher.episode_identification import EpisodeMatcher, SubtitleCache
from app.matcher.subtitle_utils import sanitize_filename
from app.matcher.tmdb_client import fetch_show_details, fetch_show_id
from app.matcher.vectorizer_config import (
    CACHE_FORMAT_VERSION,
    HASHING_N_FEATURES,
    build_hashing_vectorizer,
    compute_idf,
    vectorizer_config_hash,
)

# "S01E01" (single) -- the only shape the harvester writes. A multi-episode file
# ("S01E01E02") is ambiguous to vectorize as one row, so it's logged and skipped.
_SINGLE_EP_RE = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,4})\.srt$")
_MULTI_EP_RE = re.compile(r"[Ss]\d{1,2}[Ee]\d{1,4}[Ee]\d{1,4}")

_TRAILING_YEAR_RE = re.compile(r"\s*\(\d{4}\)\s*$")


def _norm_title(s: str) -> str:
    """Collapse a title to comparable form: drop a trailing ``(YYYY)`` and all
    non-alphanumerics, lowercase. Lets a disk dir match its canonical TMDB title
    across cosmetic differences (``:`` stored as space, a Windows-stripped
    trailing dot, an added disambiguation year) while still requiring the actual
    title content to be identical -- so it won't accept a different show.
    """
    return re.sub(r"[^a-z0-9]", "", _TRAILING_YEAR_RE.sub("", s.lower()))


def _discover_shows(data_dir: Path) -> dict[str, dict[int, list[tuple[int, str, Path]]]]:
    """Walk ``data_dir`` into ``{dir_name: {season: [(ep, code, path)]}}``.

    ``dir_name`` is the sanitized show directory name as stored on disk;
    ``code`` is the normalized ``S%02dE%02d`` episode code.
    """
    shows: dict[str, dict[int, list[tuple[int, str, Path]]]] = {}
    for show_dir in sorted(p for p in data_dir.iterdir() if p.is_dir()):
        by_season: dict[int, list[tuple[int, str, Path]]] = {}
        for srt in sorted(show_dir.glob("*.srt")):
            if _MULTI_EP_RE.search(srt.name):
                logger.warning(f"  {show_dir.name}: skipping multi-episode file {srt.name}")
                continue
            m = _SINGLE_EP_RE.search(srt.name)
            if not m:
                logger.warning(f"  {show_dir.name}: unparseable filename {srt.name}; skipping")
                continue
            season, episode = int(m.group(1)), int(m.group(2))
            code = f"S{season:02d}E{episode:02d}"
            by_season.setdefault(season, []).append((episode, code, srt))
        if by_season:
            shows[show_dir.name] = by_season
    return shows


def _resolve_canonical(dir_name: str, offline: bool) -> tuple[str, int | None, bool]:
    """Map a sanitized show dir name to ``(manifest_key, tmdb_id, resolved)``.

    Search TMDB for the dir name and accept its canonical title when it matches
    the dir -- either exactly via ``sanitize_filename`` or, failing that, via
    ``_norm_title`` (which tolerates cosmetic differences). The returned key is
    always the canonical title, and the cache subdir/manifest key are both
    derived from it (``sanitize_filename(key)``), so runtime lookup works
    regardless of the on-disk dir name. On any miss -- offline, no TMDB hit, or a
    non-matching title -- fall back to the dir name with no tmdb_id, so
    non-punctuated titles still work.
    """
    if offline:
        return dir_name, None, False
    try:
        cid = fetch_show_id(dir_name)
        if not cid:
            logger.warning(f"  {dir_name}: no TMDB match; keying by dir name")
            return dir_name, None, False
        details = fetch_show_details(cid)
        canonical = (details or {}).get("name")
        if canonical and sanitize_filename(canonical) == dir_name:
            return canonical, cid, True
        if canonical and _norm_title(canonical) == _norm_title(dir_name):
            logger.info(f"  {dir_name}: matched canonical {canonical!r} (normalized)")
            return canonical, cid, True
        logger.warning(
            f"  {dir_name}: TMDB returned {canonical!r} (id {cid}) which does not "
            f"match the dir name; keying by dir name"
        )
        return dir_name, None, False
    except Exception as e:  # noqa: BLE001 - per-show resolution must never abort the build
        logger.warning(f"  {dir_name}: TMDB resolution failed ({e}); keying by dir name")
        return dir_name, None, False


def _verify(precomputed_dir: Path, output_path: Path, manifest: dict) -> None:
    """Validate the staged artifact, then prove a fresh consumer accepts it.

    Raises ``SystemExit`` on any failure so a broken cache is never published.
    """
    if manifest["cache_format_version"] != CACHE_FORMAT_VERSION:
        raise SystemExit(
            f"verify: format {manifest['cache_format_version']} != {CACHE_FORMAT_VERSION}"
        )
    if manifest["vectorizer_config_hash"] != vectorizer_config_hash():
        raise SystemExit("verify: vectorizer_config_hash mismatch")
    if manifest["n_features"] != HASHING_N_FEATURES:
        raise SystemExit(f"verify: n_features {manifest['n_features']} != {HASHING_N_FEATURES}")

    idf = np.load(precomputed_dir / "idf.npy")
    if idf.shape[0] != HASHING_N_FEATURES:
        raise SystemExit(f"verify: idf length {idf.shape[0]} != {HASHING_N_FEATURES}")

    for key, entry in manifest["shows"].items():
        show_dir = precomputed_dir / sanitize_filename(key)
        for season in entry["seasons"]:
            npz = show_dir / f"S{season:02d}.npz"
            index = show_dir / f"S{season:02d}.index.json"
            if not npz.exists() or not index.exists():
                raise SystemExit(f"verify: missing files for {key} S{season:02d}")
            n_rows = sparse.load_npz(npz).shape[0]
            n_codes = len(json.loads(index.read_text(encoding="utf-8")))
            if n_rows != n_codes:
                raise SystemExit(f"verify: row/index mismatch for {key} S{season:02d}")

    # End-to-end consumer round-trip: extract the tarball and load it through the
    # exact runtime path. Prefer a colon-titled show -- that's the case a dir-name
    # key would silently break.
    sample_keys = [k for k in manifest["shows"] if ":" in k or "/" in k][:1]
    sample_keys += [k for k in manifest["shows"] if k not in sample_keys][:2]
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with tarfile.open(output_path, "r:gz") as tar:
            # filter="data" (Python 3.11.4+) rejects members that escape the
            # destination -- path traversal, absolute paths, unsafe links.
            # Matches the runtime extractor in precomputed_cache_service.py; the
            # project already standardizes on 3.11.4+ for tarball extraction.
            tar.extractall(tmp_path, filter="data")
        for key in sample_keys:
            matcher = EpisodeMatcher(cache_dir=tmp_path, show_name=key)
            season = manifest["shows"][key]["seasons"][0]
            loaded = matcher.load_precomputed_season(season)
            if loaded is None or loaded[0].shape[0] == 0:
                raise SystemExit(f"verify: consumer round-trip failed for {key} S{season:02d}")
            logger.info(f"  round-trip OK: {key} S{season:02d} ({loaded[0].shape[0]} eps)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Pack the Engram subtitle cache from disk")
    parser.add_argument(
        "--data-dir", default="", help="SRT cache dir (default: <subtitles_cache_path>/data)"
    )
    parser.add_argument(
        "--output", default="engram-subtitle-cache.tar.gz", help="Output tarball path"
    )
    parser.add_argument(
        "--content-version", default="", help="Cache content version (default: today)"
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip TMDB; key the manifest by dir name (colon/slash titles won't match at runtime)",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Upload to the rolling release with gh (default: print the command only)",
    )
    parser.add_argument("--cache-tag", default="subtitle-cache-latest", help="Release tag")
    args = parser.parse_args()

    _ensure_db_schema()
    _bootstrap_config_from_env()

    from app.services.config_service import get_config_sync

    config = get_config_sync()
    if not args.offline and not config.tmdb_api_key:
        logger.error("TMDB API key not configured; pass --offline to key by dir name instead")
        return 1

    cache_dir = Path(config.subtitles_cache_path).expanduser()
    data_dir = Path(args.data_dir).expanduser() if args.data_dir else cache_dir / "data"
    if not data_dir.is_dir():
        logger.error(f"Data dir not found: {data_dir}")
        return 1

    precomputed_dir = cache_dir / "precomputed"
    if precomputed_dir.exists():
        shutil.rmtree(precomputed_dir)
    precomputed_dir.mkdir(parents=True, exist_ok=True)

    content_version = args.content_version or datetime.date.today().isoformat()
    mode = "offline (dir-name keys)" if args.offline else "TMDB-resolved canonical names"
    logger.info(f"Packing cache from {data_dir} [{mode}]")

    discovered = _discover_shows(data_dir)
    if not discovered:
        logger.error("No harvested SRTs found; nothing to pack")
        return 1
    logger.info(f"Discovered {len(discovered)} show dirs on disk")

    subtitle_cache = SubtitleCache()
    hv = build_hashing_vectorizer()
    blocks: list[tuple[str, int, list[str], object]] = []
    manifest_shows: dict[str, dict] = {}
    fallbacks: list[str] = []

    for dir_name, by_season in discovered.items():
        key, tmdb_id, resolved = _resolve_canonical(dir_name, args.offline)
        if not args.offline and not resolved:
            fallbacks.append(dir_name)

        show_seasons: list[int] = []
        episode_counts: dict[str, int] = {}
        for season in sorted(by_season):
            episodes = sorted(by_season[season], key=lambda x: x[0])
            texts, codes = [], []
            for _ep, code, path in episodes:
                text = subtitle_cache.get_full_text(str(path))
                if text:
                    texts.append(text)
                    codes.append(code)
            if not texts:
                continue
            counts = hv.transform(texts)  # raw hashed term counts
            blocks.append((key, season, codes, counts))
            show_seasons.append(season)
            episode_counts[str(season)] = len(codes)

        if show_seasons:
            manifest_shows[key] = {
                "tmdb_id": tmdb_id,
                "seasons": show_seasons,
                "episode_counts": episode_counts,
            }

    if not blocks:
        logger.error("No usable subtitle text extracted; nothing to pack")
        return 1

    # --- Fit one global IDF across the whole corpus ---------------------------
    all_counts = sparse.vstack([b[3] for b in blocks], format="csr")
    idf = compute_idf(all_counts)
    np.save(precomputed_dir / "idf.npy", idf)
    logger.info(f"Global IDF fit over {all_counts.shape[0]} episodes")

    # --- Write per-(show, season) uint16 hashed-count matrices (cache v2) -----
    u16_max = np.iinfo(np.uint16).max
    for show_name, season, codes, counts in blocks:
        show_dir = precomputed_dir / sanitize_filename(show_name)
        show_dir.mkdir(parents=True, exist_ok=True)
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

    # --- In-tarball manifest (read by the matcher) ----------------------------
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

    # --- Package + checksum ---------------------------------------------------
    output_path = Path(args.output).resolve()
    with tarfile.open(output_path, "w:gz") as tar:
        tar.add(precomputed_dir, arcname="precomputed")

    sha = hashlib.sha256()
    with open(output_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            sha.update(chunk)

    release_manifest = dict(manifest, tarball_sha256=sha.hexdigest())
    release_manifest_path = output_path.with_name("manifest.json")
    with open(release_manifest_path, "w", encoding="utf-8") as fh:
        json.dump(release_manifest, fh, indent=2)

    # --- Verify before anyone can publish a broken cache ----------------------
    logger.info("Verifying artifact...")
    _verify(precomputed_dir, output_path, manifest)

    total_episodes = sum(len(c) for _, _, c, _ in blocks)
    size_mb = output_path.stat().st_size / (1024 * 1024)
    logger.info(
        f"Packed {len(manifest_shows)} shows, {total_episodes} episodes, "
        f"{size_mb:.1f} MB -> {output_path}"
    )
    logger.info(f"Release manifest -> {release_manifest_path}")
    if fallbacks:
        logger.warning(
            f"{len(fallbacks)} show(s) keyed by dir name (TMDB unresolved); these won't "
            f"match at runtime if their real title has ':' or '/': {', '.join(sorted(fallbacks))}"
        )

    # --- Publish (rolling release, in-place asset replace) --------------------
    upload_cmd = [
        "gh",
        "release",
        "upload",
        args.cache_tag,
        str(output_path),
        str(release_manifest_path),
        "--clobber",
    ]
    if args.publish:
        logger.info(f"Publishing to release {args.cache_tag} ...")
        subprocess.run(upload_cmd, check=True)
        logger.info("Published. Verify with: gh release view " + args.cache_tag)
    else:
        logger.info("Dry run (no --publish). To publish, run:\n  " + " ".join(upload_cmd))

    return 0


if __name__ == "__main__":
    sys.exit(main())
