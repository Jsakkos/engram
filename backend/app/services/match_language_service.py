"""Decide a job's match language (issue #241, phase 2).

``decide_job_match_language`` runs at the subtitle download, after
``decide_job_namespace`` (the reference folder depends on both). The show's
TMDB ``original_language`` is read here rather than at identification: a job's
show is set in many places (identification, the name prompt, re-identification,
imports, a correction on the review page), and every one of them passes this
point. It never raises: anything it cannot decide leaves the job on English.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

from app.core.episode_namespace import namespace_context
from app.core.match_language import (
    ENGLISH,
    content_language,
    match_language_context,
)
from app.database import async_session
from app.matcher import tmdb_client
from app.matcher.episode_identification import MIN_USABLE_REFERENCES, downloaded_episode_codes
from app.matcher.subtitle_utils import corpus_dir_name
from app.models.disc_job import ContentType, DiscJob


def _reference_count(
    cache_dir: Path, tmdb_id: int, show_name: str, season: int, namespace: str, language: str
) -> int:
    """Episodes of ``season`` with a reference in ``language``'s own folder."""
    with namespace_context(namespace), match_language_context(language):
        data_dir = cache_dir / "data" / corpus_dir_name(tmdb_id, show_name)
    return len(downloaded_episode_codes(data_dir, season))


def _cache_dir(config) -> Path:
    if config and config.subtitles_cache_path:
        return Path(config.subtitles_cache_path).expanduser()
    return Path.home() / ".engram" / "cache"


async def decide_job_match_language(job_id: int) -> str:
    """Set and return the job's match language; English unless references exist in the show's."""
    from app.services.config_service import get_config

    try:
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if job is None:
                return ENGLISH
            current = job.match_language or ENGLISH
            if job.content_type != ContentType.TV or not job.tmdb_id or job.detected_season is None:
                return current
            tmdb_id, season = job.tmdb_id, job.detected_season
            show_name = job.detected_title or ""
            namespace = job.episode_namespace or "tmdb"

        # No session is open across the TMDB round-trip or the folder scan.
        details = await asyncio.to_thread(tmdb_client.fetch_show_details, tmdb_id)
        if details is None:
            return current
        content = content_language(details)
        language = ENGLISH
        if content and content != ENGLISH:
            config = await get_config()
            count = await asyncio.to_thread(
                _reference_count, _cache_dir(config), tmdb_id, show_name, season, namespace, content
            )
            if count >= MIN_USABLE_REFERENCES:
                language = content
            else:
                logger.info(
                    f"Job {job_id}: show language {content} has {count} reference(s) for "
                    f"season {season}; matching in English"
                )

        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if job is None or job.tmdb_id != tmdb_id or job.detected_season != season:
                # Identity changed while we were looking: do nothing.
                return (job.match_language if job else None) or ENGLISH
            job.content_language = content
            job.match_language = language
            await session.commit()
        return language
    except Exception as e:  # noqa: BLE001 - a failed decision leaves the job on English
        logger.warning(f"Job {job_id}: match language not decided: {e}", exc_info=True)
        return ENGLISH
