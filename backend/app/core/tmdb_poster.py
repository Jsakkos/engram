"""Resolve a TMDB poster URL for a DiscJob.

Extracted from the /api/jobs/{id}/poster route so the Discord notifier and the
dashboard share one implementation. Best-effort by contract: every failure path
returns None rather than raising, because both callers treat the poster as
decoration.
"""

import asyncio

import requests
from loguru import logger

from app.models.disc_job import DiscJob


async def resolve_poster_url(job: DiscJob) -> str | None:
    """Return the TMDB poster URL for ``job``, or None.

    Prefers the authoritative ``job.tmdb_id`` (exact, immune to a garbled
    detected_title); falls back to a name search only when no id is set.
    """
    from app.core.tmdb_classifier import _build_auth
    from app.matcher.tmdb_client import BASE_IMAGE_URL
    from app.services.config_service import get_config

    config = await get_config()
    api_key = config.tmdb_api_key
    if not api_key:
        return None

    media = "movie" if job.content_type == "movie" else "tv"
    headers, params = _build_auth(api_key)

    try:
        if job.tmdb_id:
            # int() sanitizes the id into the URL (no path/host injection, and clears
            # CodeQL's partial-SSRF taint); the guard above ensures it is set.
            detail_url = f"https://api.themoviedb.org/3/{media}/{int(job.tmdb_id)}"
            response = await asyncio.to_thread(
                requests.get, detail_url, headers=headers, params=params, timeout=10
            )
            if response.status_code == 200:
                poster_path = response.json().get("poster_path")
                if poster_path:
                    return f"{BASE_IMAGE_URL}{poster_path}"
            return None

        if not job.detected_title:
            return None
        search_url = f"https://api.themoviedb.org/3/search/{media}"
        params["query"] = job.detected_title
        response = await asyncio.to_thread(
            requests.get, search_url, headers=headers, params=params, timeout=10
        )
        if response.status_code == 200:
            results = response.json().get("results", [])
            if results and results[0].get("poster_path"):
                return f"{BASE_IMAGE_URL}{results[0]['poster_path']}"
    except Exception as e:
        logger.warning(f"Error fetching poster: {e}", exc_info=True)

    return None
