"""Fetch-on-first-run for the precomputed subtitle-vector cache.

On startup the app downloads a hashed TF-IDF cache -- covering the most popular
TV shows -- from a GitHub Release and extracts it into the subtitle cache
directory. The matcher then uses these vectors instead of scraping subtitles.

This is strictly best-effort: any failure (offline, 404, checksum mismatch,
disk error) is logged and swallowed. Subtitle scraping remains the functional
fallback for every show, and startup must never fail because of this.
"""

import hashlib
import json
import shutil
import tarfile
from pathlib import Path

import httpx
from loguru import logger

from app.config import settings
from app.matcher.vectorizer_config import CACHE_FORMAT_VERSION

_CACHE_TAG = f"subtitle-cache-v{CACHE_FORMAT_VERSION}"
_MANIFEST_NAME = "manifest.json"
_TARBALL_NAME = "engram-subtitle-cache.tar.gz"
_MANIFEST_TIMEOUT = 30.0
_DOWNLOAD_TIMEOUT = 600.0


def _release_url(filename: str) -> str:
    base = settings.precomputed_cache_base_url.rstrip("/")
    return f"{base}/{_CACHE_TAG}/{filename}"


async def ensure_precomputed_cache() -> None:
    """Download/refresh the precomputed subtitle cache if needed. Never raises."""
    try:
        await _ensure_precomputed_cache_inner()
    except Exception as e:  # defensive: this must never break startup
        logger.warning(f"Precomputed cache check failed ({e}); subtitle scraping still works")


async def _ensure_precomputed_cache_inner() -> None:
    from app.services.config_service import get_config, update_config

    config = await get_config()
    if not config.precomputed_cache_enabled:
        logger.info("Precomputed subtitle cache disabled in config; skipping")
        return

    cache_dir = Path(config.subtitles_cache_path).expanduser()
    cache_dir.mkdir(parents=True, exist_ok=True)
    precomputed_dir = cache_dir / "precomputed"

    local_manifest = _read_local_manifest(precomputed_dir)

    remote_manifest = await _fetch_remote_manifest()
    if remote_manifest is None:
        logger.info("Precomputed cache: remote manifest unavailable (offline?); skipping")
        return

    remote_format = remote_manifest.get("cache_format_version")
    remote_content = remote_manifest.get("content_version", "")
    if remote_format != CACHE_FORMAT_VERSION:
        logger.info(
            f"Precomputed cache: remote format {remote_format!r} is not supported "
            f"version {CACHE_FORMAT_VERSION!r}; skipping"
        )
        return

    up_to_date = (
        local_manifest is not None
        and local_manifest.get("cache_format_version") == CACHE_FORMAT_VERSION
        and remote_content != ""
        and config.precomputed_cache_version == remote_content
    )
    if up_to_date:
        logger.info(f"Precomputed subtitle cache up to date (version {remote_content})")
        return

    logger.info(
        f"Downloading precomputed subtitle cache (version {remote_content or 'unknown'})..."
    )
    if await _download_and_extract(remote_manifest, cache_dir, precomputed_dir):
        await update_config(precomputed_cache_version=remote_content)
        logger.info(
            f"Precomputed subtitle cache installed ({len(remote_manifest.get('shows', {}))} shows)"
        )


def _read_local_manifest(precomputed_dir: Path) -> dict | None:
    try:
        with open(precomputed_dir / _MANIFEST_NAME, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


async def _fetch_remote_manifest() -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=_MANIFEST_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(_release_url(_MANIFEST_NAME))
            resp.raise_for_status()
            return resp.json()
    except (httpx.HTTPError, json.JSONDecodeError) as e:
        logger.debug(f"Precomputed cache manifest fetch failed: {e}")
        return None


async def _download_and_extract(
    remote_manifest: dict, cache_dir: Path, precomputed_dir: Path
) -> bool:
    expected_sha = remote_manifest.get("tarball_sha256", "")
    tarball_path = cache_dir / f".{_TARBALL_NAME}.download"
    staging_dir = cache_dir / ".precomputed_staging"

    try:
        sha = hashlib.sha256()
        with open(tarball_path, "wb") as fh:
            async with httpx.AsyncClient(
                timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True
            ) as client:
                async with client.stream("GET", _release_url(_TARBALL_NAME)) as resp:
                    resp.raise_for_status()
                    async for chunk in resp.aiter_bytes():
                        fh.write(chunk)
                        sha.update(chunk)

        if expected_sha and sha.hexdigest() != expected_sha:
            logger.warning("Precomputed cache: tarball checksum mismatch; discarding download")
            return False

        # Extract into a staging dir, then atomically swap it into place.
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tarball_path, "r:gz") as tar:
            # filter="data" (Python 3.11.4+) rejects members that escape the
            # destination -- path traversal, absolute paths, unsafe links.
            tar.extractall(staging_dir, filter="data")

        extracted = staging_dir / "precomputed"
        if not (extracted / _MANIFEST_NAME).exists():
            logger.warning("Precomputed cache: extracted archive missing manifest; aborting")
            return False

        shutil.rmtree(precomputed_dir, ignore_errors=True)
        shutil.move(str(extracted), str(precomputed_dir))
        return True
    except (httpx.HTTPError, OSError, tarfile.TarError) as e:
        logger.warning(f"Precomputed cache download/extract failed ({e}); using scraping")
        return False
    finally:
        tarball_path.unlink(missing_ok=True)
        shutil.rmtree(staging_dir, ignore_errors=True)
