"""Decide, switch and dismiss a job's episode namespace (spec 2026-10-08).

``decide_job_namespace`` runs once per subtitle download (the one point every
TV job passes after its show and season are known, and the point matching
already waits on). It never raises: any TVDB problem leaves the job on TMDB.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import update

from app.core import episode_namespace as ns
from app.database import async_session
from app.matcher import tmdb_client, tvdb_client
from app.models.disc_job import ContentType, DiscJob, DiscTitle
from app.models.show_ordering import ShowOrderingPreference

TVDB_UNAVAILABLE_NOTE = "TheTVDB unavailable; matched with TMDB numbering"


class TvdbUnavailableError(RuntimeError):
    """TheTVDB could not supply a roster, so the switch was not applied."""


async def _rosters(tmdb_id: int, season: int, config, tvdb_id: int | None):
    key = tvdb_client.resolve_api_key(config)
    if not key:
        return None, None, None
    if tvdb_id is None:
        tvdb_id = await asyncio.to_thread(
            tmdb_client.fetch_tvdb_id, str(tmdb_id), config.tmdb_api_key
        )
    if not tvdb_id:
        return None, None, None
    tmdb_eps, tvdb_eps = await asyncio.gather(
        asyncio.to_thread(
            tmdb_client.fetch_season_episodes, str(tmdb_id), season, config.tmdb_api_key
        ),
        asyncio.to_thread(tvdb_client.fetch_season_roster, tvdb_id, season, api_key=key),
    )
    return tvdb_id, tmdb_eps or None, tvdb_eps or None


def _crosswalk_json(season: int, tmdb_eps: list[dict], tvdb_eps: list[dict]) -> str:
    return json.dumps(ns.build_crosswalk(season, tmdb_eps, tvdb_eps))


async def decide_job_namespace(job_id: int) -> str:
    """Set and return the job's namespace; record divergence when on TMDB."""
    from app.services.config_service import get_config

    try:
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if job is None:
                return ns.NAMESPACE_TMDB
            current = job.episode_namespace or ns.NAMESPACE_TMDB
            if job.content_type != ContentType.TV or not job.tmdb_id or job.detected_season is None:
                return current
            tmdb_id, season = job.tmdb_id, job.detected_season
            pref = await session.get(ShowOrderingPreference, tmdb_id)
            known_tvdb_id = pref.tvdb_id if pref else None
            if (
                pref is not None
                and pref.ordering != ns.NAMESPACE_TVDB
                and pref.tvdb_suggestion_dismissed
            ):
                # The show does not use TheTVDB and the user declined the
                # suggestion, so nothing a TheTVDB round-trip finds would be
                # shown: skip it. The incoherence repair below needs no network.
                if job.episode_namespace == ns.NAMESPACE_TVDB:
                    job.episode_namespace = ns.NAMESPACE_TMDB
                    job.episode_crosswalk_json = None
                    await session.commit()
                return job.episode_namespace or ns.NAMESPACE_TMDB

        # No session is open across the TMDB/TheTVDB round-trips.
        config = await get_config()
        tvdb_id, tmdb_eps, tvdb_eps = await _rosters(tmdb_id, season, config, known_tvdb_id)

        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if (
                job is None
                or job.content_type != ContentType.TV
                or job.tmdb_id != tmdb_id
                or job.detected_season != season
            ):
                # Identity changed while we were fetching: do nothing.
                return (job.episode_namespace if job else None) or ns.NAMESPACE_TMDB
            pref = await session.get(ShowOrderingPreference, tmdb_id)
            if pref is not None and tvdb_id and pref.tvdb_id != tvdb_id:
                pref.tvdb_id = tvdb_id

            if pref is not None and pref.ordering == ns.NAMESPACE_TVDB:
                if tvdb_eps and tmdb_eps:
                    job.episode_namespace = ns.NAMESPACE_TVDB
                    job.episode_crosswalk_json = _crosswalk_json(season, tmdb_eps, tvdb_eps)
                    job.episode_namespace_note = None
                else:
                    # Intended: the user explicitly chose TVDB, so say why it isn't used.
                    job.episode_namespace = ns.NAMESPACE_TMDB
                    job.episode_crosswalk_json = None
                    job.episode_namespace_note = TVDB_UNAVAILABLE_NOTE
            else:
                if job.episode_namespace == ns.NAMESPACE_TVDB:
                    # Incoherent: the job says tvdb but the show no longer prefers it.
                    job.episode_namespace = ns.NAMESPACE_TMDB
                    job.episode_crosswalk_json = None
                if tmdb_eps and tvdb_eps:
                    div = ns.detect_divergence(season, tmdb_eps, tvdb_eps)
                    job.tvdb_divergence_json = div.to_json() if div else None
            await session.commit()
            return job.episode_namespace or ns.NAMESPACE_TMDB
    except Exception as e:  # noqa: BLE001 - the namespace decision must never fail a job
        logger.warning(f"Episode-namespace decision failed for job {job_id}: {e}", exc_info=True)
        return ns.NAMESPACE_TMDB


def _validate_tv_job(job: DiscJob | None) -> DiscJob:
    if job is None or job.content_type != ContentType.TV or not job.tmdb_id:
        raise ValueError("not an identified TV job")
    if job.detected_season is None:
        raise ValueError("the job's season is not known yet")
    return job


async def switch_job_namespace(job_id: int, namespace: str) -> DiscJob:
    """Move a job (and its show's preference) to ``namespace``.

    Raises ``TvdbUnavailableError`` when switching to TVDB without a roster,
    ``ValueError`` for an unknown namespace or a job that is not an identified
    TV job. Re-downloading and re-matching are the caller's job (the route).
    """
    from app.services.config_service import get_config

    if namespace not in ns.NAMESPACES:
        raise ValueError(f"namespace must be one of {sorted(ns.NAMESPACES)}")
    async with async_session() as session:
        job = _validate_tv_job(await session.get(DiscJob, job_id))
        tmdb_id, season = job.tmdb_id, job.detected_season
        pref = await session.get(ShowOrderingPreference, tmdb_id)
        known_tvdb_id = pref.tvdb_id if pref else None

    tvdb_id = tmdb_eps = tvdb_eps = None
    if namespace == ns.NAMESPACE_TVDB:
        # No session is open here, and nothing is written if TheTVDB is down.
        config = await get_config()
        tvdb_id, tmdb_eps, tvdb_eps = await _rosters(tmdb_id, season, config, known_tvdb_id)
        if not (tvdb_id and tmdb_eps and tvdb_eps):
            raise TvdbUnavailableError("TheTVDB did not return this season")

    async with async_session() as session:
        job = _validate_tv_job(await session.get(DiscJob, job_id))
        if job.tmdb_id != tmdb_id or job.detected_season != season:
            raise ValueError("the job's identity changed; try again")
        pref = await session.get(ShowOrderingPreference, tmdb_id)
        if pref is None:
            pref = ShowOrderingPreference(tmdb_id=tmdb_id, ordering="")
            session.add(pref)

        if namespace == ns.NAMESPACE_TVDB:
            pref.tvdb_id = tvdb_id
            pref.ordering = ns.NAMESPACE_TVDB
            pref.episode_group_id = None
            job.episode_namespace = ns.NAMESPACE_TVDB
            job.episode_crosswalk_json = _crosswalk_json(season, tmdb_eps, tvdb_eps)
            job.tvdb_divergence_json = None  # the suggestion is accepted
        else:
            if pref.ordering == ns.NAMESPACE_TVDB:
                pref.ordering = ""
            # An explicit revert declines TheTVDB: without this the re-download
            # re-detects the divergence and the suggestion reappears at once.
            pref.tvdb_suggestion_dismissed = True
            job.episode_namespace = ns.NAMESPACE_TMDB
            job.episode_crosswalk_json = None
        job.episode_namespace_note = None
        pref.updated_at = datetime.now(UTC)
        job.updated_at = datetime.now(UTC)
        # A title's discdb_match_details holds a code in the OLD numbering. The
        # in-memory DiscDB mappings stay TMDB-raw and are re-translated on the
        # re-match, so drop the stale per-title copies with the switch.
        await session.execute(
            update(DiscTitle).where(DiscTitle.job_id == job_id).values(discdb_match_details=None)
        )
        await session.commit()
        await session.refresh(job)
        return job


async def dismiss_tvdb_suggestion(tmdb_id: int) -> None:
    """Remember that the user declined TheTVDB numbering for this show.

    A new preference row is created with ordering "" so it keeps following the
    global default ordering rather than pinning the show to aired.
    """
    async with async_session() as session:
        pref = await session.get(ShowOrderingPreference, tmdb_id)
        if pref is None:
            pref = ShowOrderingPreference(tmdb_id=tmdb_id, ordering="")
            session.add(pref)
        pref.tvdb_suggestion_dismissed = True
        pref.updated_at = datetime.now(UTC)
        await session.commit()
