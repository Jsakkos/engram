"""Resolve a show's effective output ordering (GitHub #200).

Bridges the persisted preferences (per-show ``ShowOrderingPreference`` row +
global ``AppConfig.episode_ordering_preference``) and the stateless projection
in ``app.core.episode_ordering``. Returns the effective ordering plus the TMDB
episode-group id that realises it.

This module only READS the DB. It never commits the caller's session — both
finalization call sites pass the session they're mid-transaction on, so a commit
here would prematurely flush their in-progress work. The episode-group id is
resolved on demand (cheap: cached at the TMDB layer); the per-show row's id is
populated eagerly by ``set_show_ordering`` when the user picks an ordering.
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core import episode_ordering
from app.models.app_config import AppConfig
from app.models.show_ordering import ShowOrderingPreference


async def resolve_show_ordering(
    tmdb_id: int | None, session: AsyncSession
) -> tuple[str, str | None]:
    """Return ``(ordering, episode_group_id)`` for a show.

    Resolution order: per-show override -> global default -> "aired". An ordering
    that isn't selectable in v1 (e.g. absolute) degrades to "aired". ``None`` group
    means no projection applies (caller treats numbers as canonical).
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

    # Prefer a group id already cached on the per-show row (written by
    # set_show_ordering). Otherwise resolve it on demand — off the event loop,
    # since resolve_episode_group_id makes a blocking requests.get on a cold
    # TMDB cache and this runs inside async request/finalization handlers.
    group_id = pref.episode_group_id if pref else None
    if not group_id:
        group_id = await asyncio.to_thread(
            episode_ordering.resolve_episode_group_id, str(tmdb_id), ordering, api_key
        )

    return (ordering, group_id)
