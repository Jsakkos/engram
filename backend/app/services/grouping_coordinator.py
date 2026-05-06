"""Grouping Coordinator - Auto-assign release_group_id for sibling discs.

Runs after a job reaches COMPLETED. Derives a group key from hybrid signals
(volume-label parse first, TMDB fallback) and shares a release_group_id across
sibling jobs without requiring user intervention. Users can still override via
the existing release-group endpoints.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.analyst import DiscAnalyst
from app.core.discdb_submitter import ensure_release_group_id
from app.database import async_session
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType

logger = logging.getLogger(__name__)


GroupKey = tuple


def derive_group_key(job: DiscJob) -> GroupKey | None:
    """Derive a stable group key for a job using hybrid signals.

    Order:
        1. Parse volume_label → (show_name, season). Use when both present.
        2. (tmdb_id, detected_season) for TV.
        3. (tmdb_id,) for movies.
        4. None (solo group).

    Returns a tuple keyed by source so label-parsed and TMDB-parsed groups
    never collide (even if the values overlap by coincidence).
    """
    if job.volume_label:
        show_name, season, _disc = DiscAnalyst._parse_volume_label(job.volume_label)
        if show_name and season is not None:
            return ("label", show_name.upper().strip(), season)

    if job.tmdb_id is not None:
        if job.content_type == ContentType.TV:
            return ("tmdb_tv", job.tmdb_id, job.detected_season)
        if job.content_type == ContentType.MOVIE:
            return ("tmdb_movie", job.tmdb_id)

    return None


async def auto_assign_release_group(job: DiscJob, session: AsyncSession) -> str | None:
    """Auto-assign a release_group_id to ``job`` based on hybrid signals.

    Looks up sibling COMPLETED jobs sharing the same group key. If any
    already have a release_group_id, this job adopts that id. Otherwise a
    fresh UUID is minted on this job.

    No-op if the job already has a release_group_id (preserves user/manual
    grouping). Caller is responsible for committing the session.

    Returns the assigned id, or None if no group key could be derived.
    """
    if job.release_group_id:
        return job.release_group_id

    key = derive_group_key(job)
    if key is None:
        logger.debug(f"Job {job.id}: no group key derivable from signals; skipping auto-group")
        return None

    stmt = select(DiscJob).where(
        DiscJob.id != job.id,
        DiscJob.state == JobState.COMPLETED,
        DiscJob.release_group_id.is_not(None),
    )
    candidates = list((await session.execute(stmt)).scalars().all())
    siblings = [c for c in candidates if derive_group_key(c) == key]

    if siblings:
        # Multiple siblings *should* share the same UUID; if they don't (manual
        # split + later auto-add), pick the most recently completed one.
        siblings.sort(key=lambda s: s.completed_at or s.id, reverse=True)
        adopted = siblings[0].release_group_id
        job.release_group_id = adopted
        logger.info(
            f"Job {job.id}: adopted release_group_id={adopted[:8]}… "
            f"from {len(siblings)} sibling(s) with key={key}"
        )
    else:
        new_id = ensure_release_group_id(job)
        logger.info(
            f"Job {job.id}: minted new release_group_id={new_id[:8]}… (key={key})"
        )

    session.add(job)
    return job.release_group_id


async def auto_assign_release_group_for_job(job_id: int) -> str | None:
    """Convenience wrapper: open a session, fetch the job, auto-assign, commit.

    Used by CleanupService.on_job_terminal where no session is in scope.
    """
    async with async_session() as session:
        job = await session.get(DiscJob, job_id)
        if not job:
            return None
        result = await auto_assign_release_group(job, session)
        await session.commit()
        return result
