"""Integration tests for the post-rip movie gate (_resolve_multi_title_movie).

Verifies that a movie with long bonus tracks auto-selects the feature and tags
extras (no review), while genuinely competing features still require review.
This is the path that previously flagged Marty Supreme as "Multiple versions
ripped" because every ripped title counted as a candidate.
"""

import json
import types
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from app.models import ContentType, DiscJob, DiscTitle, JobState, TitleState
from app.services.job_manager import JobManager


@pytest_asyncio.fixture
async def session():
    # StaticPool: each :memory: connection is its own empty DB; pin to one shared connection to avoid "no such table".
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with async_session() as s:
            yield s
    finally:
        await engine.dispose()


def _cfg(extras_policy="keep"):
    return types.SimpleNamespace(
        analyst_movie_min_duration=4800, tmdb_api_key="test_key", extras_policy=extras_policy
    )


async def _make_movie_job(session, title_specs):
    """title_specs: list of (minutes, mbps). Returns (job, [DiscTitle])."""
    job = DiscJob(
        drive_id="D:",
        volume_label="MARTY_SUPREME",
        content_type=ContentType.MOVIE,
        state=JobState.RIPPING,
        detected_title="Marty Supreme",
        tmdb_id=1317288,
        staging_path="/tmp/staging",
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    titles = []
    for idx, (minutes, mbps) in enumerate(title_specs):
        dur = int(minutes * 60)
        size = int(mbps * 1_000_000 * dur / 8)
        t = DiscTitle(
            job_id=job.id,
            title_index=idx,
            duration_seconds=dur,
            file_size_bytes=size,
            chapter_count=12,
            state=TitleState.MATCHED,
            is_selected=True,
        )
        session.add(t)
        titles.append(t)
    await session.commit()
    for t in titles:
        await session.refresh(t)
    return job, titles


@pytest.fixture
def jm(mocker):
    mgr = JobManager()
    mgr._matching.get_discdb_mappings = MagicMock(return_value=[])
    mocker.patch("app.services.job_manager.ws_manager", new=AsyncMock())
    mocker.patch("app.services.config_service.get_config", new=AsyncMock(return_value=_cfg()))
    return mgr


async def test_long_bonus_tracks_no_review(jm, session, mocker):
    """149min feature + short bonus tracks → no review, extras tagged."""
    mocker.patch("app.matcher.tmdb_client.fetch_movie_runtime", return_value=149)
    job, titles = await _make_movie_job(session, [(149.7, 38), (20, 19), (4, 17), (4, 18)])

    sent_to_review = await jm._resolve_multi_title_movie(job, job.id, titles, session)

    assert sent_to_review is False
    assert job.state == JobState.RIPPING  # not pushed to review
    assert titles[0].is_extra is False and titles[0].is_selected is True
    # Extras are tagged and deselected so they never read as the feature.
    assert all(t.is_extra and t.is_selected is False for t in titles[1:])


async def test_two_real_features_needs_review(jm, session, mocker):
    """Theatrical + extended cut → review with both kept as candidates."""
    mocker.patch("app.matcher.tmdb_client.fetch_movie_runtime", return_value=120)
    job, titles = await _make_movie_job(session, [(120, 36), (145, 35), (5, 18)])

    sent_to_review = await jm._resolve_multi_title_movie(job, job.id, titles, session)

    assert sent_to_review is True
    assert job.state == JobState.REVIEW_NEEDED
    # Competing features stay selected; the short extra is dropped from the picker.
    assert titles[0].is_selected and titles[1].is_selected
    assert titles[2].is_extra and titles[2].is_selected is False


async def test_discdb_mainmovie_skips_review(jm, session, mocker):
    """A TheDiscDB MainMovie tag selects the feature and skips review."""
    mocker.patch("app.matcher.tmdb_client.fetch_movie_runtime", return_value=149)
    jm._matching.get_discdb_mappings = MagicMock(
        return_value=[types.SimpleNamespace(index=1, title_type="MainMovie")]
    )
    job, titles = await _make_movie_job(session, [(149, 38), (149, 37), (4, 18)])

    sent_to_review = await jm._resolve_multi_title_movie(job, job.id, titles, session)

    assert sent_to_review is False
    assert job.state == JobState.RIPPING
    assert titles[1].is_selected and titles[1].is_extra is False
    assert all(t.is_extra and t.is_selected is False for t in (titles[0], titles[2]))


# --- extras_policy on the movie path (#773) ---


def _use_policy(mocker, policy):
    mocker.patch("app.services.config_service.get_config", new=AsyncMock(return_value=_cfg(policy)))


async def test_skip_policy_discards_extras_instead_of_filing_them(jm, session, mocker):
    """Under "skip" the bonus tracks are completed as skipped, never organized."""
    _use_policy(mocker, "skip")
    mocker.patch("app.matcher.tmdb_client.fetch_movie_runtime", return_value=149)
    job, titles = await _make_movie_job(session, [(149.7, 38), (20, 19), (4, 17)])

    sent_to_review = await jm._resolve_multi_title_movie(job, job.id, titles, session)

    assert sent_to_review is False
    assert titles[0].state == TitleState.MATCHED and titles[0].is_selected
    for extra in titles[1:]:
        assert extra.is_extra and extra.state == TitleState.COMPLETED
        assert extra.organized_to is None
        assert json.loads(extra.match_details)["action"] == "skipped"


async def test_skip_policy_applies_to_discdb_main_movie(jm, session, mocker):
    _use_policy(mocker, "skip")
    jm._matching.get_discdb_mappings = MagicMock(
        return_value=[types.SimpleNamespace(index=1, title_type="MainMovie")]
    )
    job, titles = await _make_movie_job(session, [(95, 30), (149, 38), (4, 18)])

    assert await jm._resolve_multi_title_movie(job, job.id, titles, session) is False
    assert titles[1].state == TitleState.MATCHED
    assert all(t.state == TitleState.COMPLETED for t in (titles[0], titles[2]))


async def test_skip_policy_keeps_every_cut_on_a_multi_cut_disc(jm, session, mocker):
    """Never skip the feature: competing cuts still go to review; only the extra goes."""
    _use_policy(mocker, "skip")
    mocker.patch("app.matcher.tmdb_client.fetch_movie_runtime", return_value=120)
    job, titles = await _make_movie_job(session, [(120, 36), (145, 35), (5, 18)])

    assert await jm._resolve_multi_title_movie(job, job.id, titles, session) is True
    assert job.state == JobState.REVIEW_NEEDED
    assert titles[0].state == TitleState.MATCHED and titles[1].state == TitleState.MATCHED
    assert titles[2].state == TitleState.COMPLETED


async def test_ask_policy_sends_extras_to_review(jm, session, mocker):
    """Under "ask" the extras go to review as extras, like a TV disc's."""
    _use_policy(mocker, "ask")
    mocker.patch("app.matcher.tmdb_client.fetch_movie_runtime", return_value=149)
    job, titles = await _make_movie_job(session, [(149.7, 38), (20, 19), (4, 17)])

    sent_to_review = await jm._resolve_multi_title_movie(job, job.id, titles, session)

    assert sent_to_review is True
    assert job.state == JobState.REVIEW_NEEDED
    assert titles[0].is_selected and titles[0].state == TitleState.MATCHED
    for extra in titles[1:]:
        assert extra.is_extra and extra.state == TitleState.REVIEW
        assert json.loads(extra.match_details)["action"] == "review"


async def test_keep_policy_leaves_extras_to_the_organizer(jm, session, mocker):
    mocker.patch("app.matcher.tmdb_client.fetch_movie_runtime", return_value=149)
    job, titles = await _make_movie_job(session, [(149.7, 38), (20, 19)])

    assert await jm._resolve_multi_title_movie(job, job.id, titles, session) is False
    assert titles[1].is_extra and titles[1].state == TitleState.MATCHED
