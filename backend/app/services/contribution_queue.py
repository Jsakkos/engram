"""Local-only fingerprint contribution queue (Phase 1).

Phase 2 adds an uploader that drains this queue over HTTPS. For Phase 1 the queue
is append-only and never uploads anything — it exists so that contributions are
captured from day one, ready to flow when the server lands.
"""

from __future__ import annotations

from loguru import logger
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.fingerprint import FingerprintContribution


class ContributionQueue:
    """Append rows to the local FingerprintContribution table."""

    async def enqueue(
        self,
        *,
        session: AsyncSession,
        title_id: int | None,
        chromaprint_blob: bytes,
        tmdb_id: int,
        season: int | None,
        episode: int | None,
        match_confidence: float,
        match_source: str,
        disc_content_hash: bytes | None,
        pseudonym: str,
        contributions_enabled: bool = True,
    ) -> None:
        """Append a contribution if the user has opt-in (default True)."""
        if not contributions_enabled:
            logger.debug(f"Skipping contribution for title {title_id}: contributions disabled")
            return
        row = FingerprintContribution(
            title_id=title_id,
            chromaprint_blob=chromaprint_blob,
            tmdb_id=tmdb_id,
            season=season,
            episode=episode,
            match_confidence=match_confidence,
            match_source=match_source,
            disc_content_hash=disc_content_hash,
            pseudonym=pseudonym,
        )
        session.add(row)
        logger.info(
            f"Queued contribution for title {title_id} (tmdb={tmdb_id} s{season}e{episode}, "
            f"source={match_source}, conf={match_confidence:.2f})"
        )
