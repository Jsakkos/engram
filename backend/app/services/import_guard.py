"""Ownership rules for manual-import staging paths (issue #571).

A staging path is *owned* by an in-flight job and merely *recorded* by a
finished one:

* in-flight (any non-terminal state, including REVIEW_NEEDED) hard-blocks, because
  a second job would race the first over the same files on disk;
* COMPLETED soft-blocks, because it is history rather than a live claim, and an
  explicit user re-import overrides it;
* FAILED never blocks.

Deliberately NOT a copy of the disc-side guard in ``job_manager._on_disc_inserted``,
which lets REVIEW_NEEDED through: a disc in review has been ejected and the drive
is free, whereas an import in review still holds its files.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select as sa_select

from app.models import TERMINAL_JOB_STATES, DiscJob, JobState


class ImportBlock(StrEnum):
    """Why a staging path refused a new job."""

    IN_FLIGHT = "in_flight"
    ALREADY_IMPORTED = "already_imported"


@dataclass(frozen=True)
class StagingJobResult:
    """Return type of ``create_job_from_staging``.

    Lives here rather than in ``job_manager`` so ``routes.py`` can name it
    without importing job-manager internals.
    """

    job_id: int | None = None
    block: ImportBlock | None = None
    blocking_job_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if (self.job_id is None) == (self.block is None):
            raise ValueError(
                "StagingJobResult requires exactly one of job_id/block to be set, "
                f"got job_id={self.job_id!r} block={self.block!r}"
            )


def unit_key_for(dedup_path: str) -> str:
    """Return the stable, opaque key identifying an import unit.

    Hashed rather than sent as the raw path for two reasons: a client cannot
    parse a digest into a path and start depending on its structure, which makes
    the opacity of the wire contract enforceable rather than merely documented;
    and absolute filesystem paths stay out of a value the UI round-trips.

    The key is derived from the *normalized* path (``str(Path(dedup_path))``),
    the same form the DB stores it in (see ``JobManager.create_job_from_staging``),
    so it matches regardless of trailing separators.

    Pure function of the path, so no server-side state is needed: the forced
    second call re-scans, re-derives a key per unit, and matches the incoming
    ``force_keys`` against those.
    """
    return hashlib.sha256(str(Path(dedup_path)).encode("utf-8")).hexdigest()[:16]


async def classify_staging_path(
    session: AsyncSession, staging_path: str
) -> tuple[ImportBlock | None, list[int]]:
    """Classify a staging path's ownership.

    Returns the block tier and the ids of the jobs responsible for it, or
    ``(None, [])`` when the path is free. In-flight beats completed: a path with
    both a finished and a live job is owned by the live one. Ids within a tier
    are returned in ascending order.
    """
    result = await session.execute(
        sa_select(DiscJob).where(DiscJob.staging_path == staging_path).order_by(DiscJob.id)
    )
    jobs = list(result.scalars().all())

    in_flight = [j.id for j in jobs if j.state not in TERMINAL_JOB_STATES]
    if in_flight:
        return ImportBlock.IN_FLIGHT, in_flight

    completed = [j.id for j in jobs if j.state == JobState.COMPLETED]
    if completed:
        return ImportBlock.ALREADY_IMPORTED, completed

    return None, []
