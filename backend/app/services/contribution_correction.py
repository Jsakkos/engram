"""Reconcile fingerprint contributions when a user reassigns a track after the fact.

Retract the erroneous fingerprint (delete it locally if it never uploaded; otherwise
queue a /v1/retract via FingerprintRetraction) and re-contribute the corrected episode
as the highest-trust source (user_review).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.episode_codes import parse_episode_code
from app.matcher.chromaprint_extractor import ChromaprintResult
from app.models.disc_job import DiscJob, DiscTitle
from app.models.fingerprint import FingerprintContribution, FingerprintRetraction
from app.services.contribution_queue import ContributionQueue
from app.services.zstd_varint_codec import fingerprint_sha256


@dataclass(frozen=True)
class NewTarget:
    """Where a track is being reassigned to."""

    kind: Literal["episode", "extra", "discard"]
    episode_code: str | None = None  # required when kind == "episode"


class ContributionCorrectionService:
    """Retract a track's old fingerprint and (for episodes) re-contribute the new one."""

    async def correct_title_contribution(
        self,
        session: AsyncSession,
        title: DiscTitle,
        new_target: NewTarget,
        *,
        job: DiscJob,
        enable_contributions: bool,
        pseudonym: str | None,
    ) -> None:
        """Reconcile contributions for ``title`` against ``new_target``.

        Operates within the caller's session/transaction — the caller commits.
        Best-effort: never raises on contribution bookkeeping (a network/queue hiccup
        must not block the user-visible file + DB correction).
        """
        try:
            rows = (
                (
                    await session.execute(
                        select(FingerprintContribution).where(
                            FingerprintContribution.title_id == title.id
                        )
                    )
                )
                .scalars()
                .all()
            )

            for row in rows:
                if row.upload_status == "success":
                    # Already on the network — queue a retraction, then drop the local row.
                    try:
                        sha = fingerprint_sha256(
                            ChromaprintResult.from_blob(row.chromaprint_blob).hashes
                        )
                        session.add(
                            FingerprintRetraction(
                                pseudonym=row.pseudonym,
                                tmdb_id=row.tmdb_id,
                                season=row.season,
                                episode=row.episode,
                                fingerprint_sha256=sha,
                            )
                        )
                    except Exception:
                        # Should be unreachable: a "success" row had its blob
                        # deserialized at upload time (uploader marks malformed blobs
                        # "failed", never "success"). If it DOES fire we orphan a live
                        # fingerprint on the network — surface it in the error tail.
                        logger.error(
                            f"Could not derive sha256 for contrib {row.id}; "
                            "deleting local row WITHOUT queuing retraction (orphaned on network)",
                            exc_info=True,
                        )
                await session.delete(row)

            # Re-contribute only when the new target is a real episode.
            if new_target.kind == "episode" and new_target.episode_code:
                await self._recontribute(
                    session,
                    title,
                    job=job,
                    episode_code=new_target.episode_code,
                    enable_contributions=enable_contributions,
                    pseudonym=pseudonym,
                )
        except Exception:
            logger.error(f"Contribution correction failed for title {title.id}", exc_info=True)

    async def _recontribute(
        self,
        session: AsyncSession,
        title: DiscTitle,
        *,
        job: DiscJob,
        episode_code: str,
        enable_contributions: bool,
        pseudonym: str | None,
    ) -> None:
        if not (title.chromaprint_blob and pseudonym and job.tmdb_id):
            logger.debug(
                f"Skipping re-contribution for title {title.id}: "
                f"blob={bool(title.chromaprint_blob)} pseudonym={bool(pseudonym)} "
                f"tmdb_id={bool(job.tmdb_id)}"
            )
            return
        # A combined track (one file holding several episodes) has no single
        # episode to key a fingerprint on: contributing it as its first episode
        # would publish a 22min three-segment block as the fingerprint of a 7min
        # episode. Retraction of the old contribution has already happened; we
        # simply don't publish a new one. Mirrors disc_contribution_queue's
        # deliberate under-count for the same case.
        parsed = parse_episode_code(episode_code)
        if not parsed:
            return
        season, episodes = parsed
        if len(episodes) > 1:
            logger.info(
                f"Skipping re-contribution for title {title.id}: {episode_code} spans "
                f"{len(episodes)} episodes, which has no single fingerprint identity"
            )
            return
        try:
            tmdb_id_val = int(job.tmdb_id)
        except (TypeError, ValueError):
            return
        disc_hash = None
        if getattr(job, "content_hash", None):
            try:
                disc_hash = bytes.fromhex(job.content_hash)
            except (TypeError, ValueError):
                disc_hash = None
        await ContributionQueue().enqueue(
            session=session,
            title_id=title.id,
            chromaprint_blob=title.chromaprint_blob,
            tmdb_id=tmdb_id_val,
            season=season,
            episode=episodes[0],
            match_confidence=1.0,
            match_source="user_review",
            disc_content_hash=disc_hash,
            pseudonym=pseudonym,
            show_title=getattr(job, "tmdb_name", None) or getattr(job, "detected_title", None),
            contributions_enabled=enable_contributions,
        )
