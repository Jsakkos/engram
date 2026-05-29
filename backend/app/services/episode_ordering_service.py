"""Resolve a show's effective output ordering (GitHub #200).

Bridges the persisted preferences (per-show ``ShowOrderingPreference`` row +
global ``AppConfig.episode_ordering_preference``) and the stateless projection
in ``app.core.episode_ordering``. Returns the effective ordering plus the TMDB
episode-group id that realises it, lazily resolving and caching that id onto an
existing per-show row.

This module reads/writes the DB; the core projection module stays pure.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core import episode_ordering
from app.models.app_config import AppConfig
from app.models.show_ordering import ShowOrderingPreference

logger = logging.getLogger(__name__)


async def resolve_show_ordering(
    tmdb_id: int | None, session: AsyncSession
) -> tuple[str, str | None]:
    """Return ``(ordering, episode_group_id)`` for a show.

    Resolution order: per-show override -> global default -> "aired". An ordering
    that isn't selectable in v1 (e.g. absolute) degrades to "aired". For a
    non-aired ordering the episode-group id is resolved via TMDB (cached) and
    persisted onto an existing per-show row; ``None`` group means no projection
    applies (caller treats numbers as canonical).
    """
    if tmdb_id is None:
        return ("aired", None)

    config = (await session.execute(select(AppConfig).limit(1))).scalar_one_or_none()
    api_key = config.tmdb_api_key if config else ""
    global_default = (
        config.episode_ordering_preference if config else episode_ordering.ORDERING_AIRED
    )

    pref = await session.get(ShowOrderingPreference, tmdb_id)
    ordering = pref.ordering if (pref and pref.ordering) else global_default

    if (
        ordering == episode_ordering.ORDERING_AIRED
        or ordering not in episode_ordering.ALLOWED_ORDERINGS
    ):
        return (episode_ordering.ORDERING_AIRED, None)

    # Reuse a cached group id from the per-show row when present.
    group_id = pref.episode_group_id if pref else None
    if not group_id:
        group_id = episode_ordering.resolve_episode_group_id(str(tmdb_id), ordering, api_key)
        # Persist only onto an existing per-show row (a global default has none).
        if pref is not None and group_id:
            pref.episode_group_id = group_id
            pref.updated_at = datetime.now(UTC)
            session.add(pref)
            await session.commit()

    return (ordering, group_id)
