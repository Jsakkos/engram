"""Per-show episode-ordering override (GitHub #200).

Episode-ordering divergence (aired vs DVD vs digital ...) is a property of the
*show*, not of a single rip — so the preference is keyed by ``tmdb_id`` and
persists across jobs. A row here overrides the global
``AppConfig.episode_ordering_preference``; absence means "use the global default".

Kept as a dedicated table rather than a JSON column on AppConfig because
``database._migrate_app_config`` drops and recreates the app_config row on any
schema change — a JSON blob of per-show prefs would be clobbered, whereas a
separate table is untouched by that reconciler and gives primary-key lookups.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text
from sqlmodel import Field, SQLModel


class ShowOrderingPreference(SQLModel, table=True):
    """A show's chosen output ordering, keyed by its TMDB id."""

    __tablename__ = "show_ordering_preferences"

    # tmdb_id is the natural key — one preference per show.
    tmdb_id: int = Field(primary_key=True)
    # One of episode_ordering.PER_SHOW_ORDERINGS ("aired", "dvd", "tvdb"); "" means
    # "use the global default".
    ordering: str = Field(default="aired")
    # The resolved TMDB episode-group id for this ordering, cached so the
    # projection can skip re-resolving the group on every organize. NULL until
    # first resolved, or when ordering == "aired" (no group needed).
    episode_group_id: str | None = Field(default=None)
    # TheTVDB series id, resolved from TMDB external_ids and cached here so the
    # namespace decision does not re-resolve it per disc.
    tvdb_id: int | None = Field(default=None)
    # The user dismissed the "TheTVDB numbers this differently" suggestion.
    # server_default 0: an upgraded row must read as "not dismissed".
    tvdb_suggestion_dismissed: bool = Field(
        default=False, sa_column_kwargs={"server_default": text("0")}
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
