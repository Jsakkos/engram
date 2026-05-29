"""Tests for the per-show ordering resolver (GitHub #200).

Resolution order: per-show ShowOrderingPreference row -> global
AppConfig.episode_ordering_preference -> "aired". For a non-aired result the
TMDB episode-group id is resolved (cached at the TMDB layer) and persisted onto
an existing per-show row so later organizes skip re-resolution.
"""

from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from app.models.app_config import AppConfig
from app.models.show_ordering import ShowOrderingPreference
from app.services.episode_ordering_service import resolve_show_ordering


@pytest.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        # Every test needs a config row (for global default + api key).
        s.add(AppConfig(tmdb_api_key="k", episode_ordering_preference="aired"))
        await s.commit()
        yield s
    await engine.dispose()


async def _set_global(session, ordering):
    cfg = await session.get(AppConfig, 1)
    cfg.episode_ordering_preference = ordering
    await session.commit()


@pytest.mark.unit
class TestResolveShowOrdering:
    async def test_no_pref_global_aired_returns_aired_without_tmdb(self, session):
        with patch(
            "app.services.episode_ordering_service.episode_ordering.resolve_episode_group_id"
        ) as m:
            ordering, group_id = await resolve_show_ordering(1437, session)
        assert (ordering, group_id) == ("aired", None)
        assert m.call_count == 0

    async def test_tmdb_id_none_returns_aired(self, session):
        ordering, group_id = await resolve_show_ordering(None, session)
        assert (ordering, group_id) == ("aired", None)

    async def test_per_show_override_resolves_group_without_writing_session(self, session):
        session.add(ShowOrderingPreference(tmdb_id=1437, ordering="dvd"))
        await session.commit()
        with patch(
            "app.services.episode_ordering_service.episode_ordering.resolve_episode_group_id",
            return_value="grp_dvd",
        ) as m:
            ordering, group_id = await resolve_show_ordering(1437, session)
        assert (ordering, group_id) == ("dvd", "grp_dvd")
        m.assert_called_once_with("1437", "dvd", "k")
        # Pure read: the resolver must NOT mutate/commit the caller's session
        # (both finalization call sites are mid-transaction). The per-show row's
        # group id is populated by set_show_ordering, not here.
        row = await session.get(ShowOrderingPreference, 1437)
        assert row.episode_group_id is None

    async def test_does_not_commit_callers_session(self, session, monkeypatch):
        # Bug guard: finalize_disc_job / _finalize_tv_if_resolved call this
        # mid-transaction with the session they're still using. A commit here
        # would prematurely flush their in-progress work.
        session.add(ShowOrderingPreference(tmdb_id=1437, ordering="dvd"))
        await session.commit()

        commits = {"n": 0}
        orig = session.commit

        async def counting_commit(*a, **k):
            commits["n"] += 1
            return await orig(*a, **k)

        monkeypatch.setattr(session, "commit", counting_commit)
        with patch(
            "app.services.episode_ordering_service.episode_ordering.resolve_episode_group_id",
            return_value="grp_dvd",
        ):
            await resolve_show_ordering(1437, session)
        assert commits["n"] == 0

    async def test_per_show_with_cached_group_skips_resolution(self, session):
        session.add(
            ShowOrderingPreference(tmdb_id=1437, ordering="dvd", episode_group_id="grp_dvd")
        )
        await session.commit()
        with patch(
            "app.services.episode_ordering_service.episode_ordering.resolve_episode_group_id"
        ) as m:
            ordering, group_id = await resolve_show_ordering(1437, session)
        assert (ordering, group_id) == ("dvd", "grp_dvd")
        assert m.call_count == 0

    async def test_global_override_used_when_no_per_show_row(self, session):
        await _set_global(session, "dvd")
        with patch(
            "app.services.episode_ordering_service.episode_ordering.resolve_episode_group_id",
            return_value="grp_dvd",
        ):
            ordering, group_id = await resolve_show_ordering(1437, session)
        assert (ordering, group_id) == ("dvd", "grp_dvd")
        # a global default does NOT create a per-show row
        assert await session.get(ShowOrderingPreference, 1437) is None

    async def test_per_show_overrides_global(self, session):
        await _set_global(session, "dvd")
        session.add(ShowOrderingPreference(tmdb_id=1437, ordering="aired"))
        await session.commit()
        ordering, group_id = await resolve_show_ordering(1437, session)
        assert (ordering, group_id) == ("aired", None)

    async def test_non_aired_without_group_returns_none_group(self, session):
        session.add(ShowOrderingPreference(tmdb_id=1437, ordering="dvd"))
        await session.commit()
        with patch(
            "app.services.episode_ordering_service.episode_ordering.resolve_episode_group_id",
            return_value=None,
        ):
            ordering, group_id = await resolve_show_ordering(1437, session)
        assert (ordering, group_id) == ("dvd", None)
        # nothing to persist when no group was found
        row = await session.get(ShowOrderingPreference, 1437)
        assert row.episode_group_id is None

    async def test_disallowed_ordering_treated_as_aired(self, session):
        await _set_global(session, "absolute")  # deferred in v1
        ordering, group_id = await resolve_show_ordering(1437, session)
        assert (ordering, group_id) == ("aired", None)
