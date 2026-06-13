"""Tests for the whole-disc layout contribution enqueue (Phase C client write path)."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.disc_job import ContentType, DiscJob, DiscTitle, TitleState
from app.models.fingerprint import DiscContribution
from app.services.disc_contribution_queue import (
    _DISC_MATCH_SOURCE_TO_CONTRIB,
    build_title_rows,
    enqueue_disc_contribution,
)

# A 16-byte content hash, uppercase hex like DiscJob.content_hash stores it.
HASH_HEX = "ABCDEF0123456789ABCDEF0123456789"
PSEUDO = "11111111-1111-4111-8111-111111111111"


def _tv_job(**overrides) -> DiscJob:
    base = dict(
        drive_id="E:",
        content_type=ContentType.TV,
        content_hash=HASH_HEX,
        tmdb_id=1399,
        detected_season=2,
    )
    base.update(overrides)
    return DiscJob(**base)


def _movie_job(**overrides) -> DiscJob:
    base = dict(
        drive_id="E:",
        content_type=ContentType.MOVIE,
        content_hash=HASH_HEX,
        tmdb_id=27205,
        detected_season=None,
    )
    base.update(overrides)
    return DiscJob(**base)


def _title(idx, **overrides) -> DiscTitle:
    base = dict(
        job_id=1,
        title_index=idx,
        duration_seconds=1300,
        file_size_bytes=1_000_000 + idx,
        match_confidence=0.9,
        state=TitleState.COMPLETED,
    )
    base.update(overrides)
    return DiscTitle(**base)


# --------------------------------------------------------------------------- #
# Model shape
# --------------------------------------------------------------------------- #


def test_disc_contribution_mirrors_upload_state_fields():
    fields = DiscContribution.model_fields
    for required in (
        "id",
        "queued_at",
        "disc_content_hash",
        "tmdb_id",
        "content_type",
        "season",
        "titles_json",
        "pseudonym",
        "uploaded_at",
        "upload_attempts",
        "upload_status",
        "upload_error_msg",
    ):
        assert required in fields, f"DiscContribution missing field: {required}"
    # client_version is added at UPLOAD time, not stored on the row.
    assert "client_version" not in fields


# --------------------------------------------------------------------------- #
# Assignment derivation
# --------------------------------------------------------------------------- #


def test_build_rows_tv_episode_parsed():
    titles = [_title(1, matched_episode="S02E03", match_source="engram")]
    rows = build_title_rows(_tv_job(), titles)
    assert len(rows) == 1
    r = rows[0]
    assert r["assignment"] == "episode"
    assert r["season"] == 2
    assert r["episode"] == 3
    assert r["title_index"] == 1
    assert r["size_bytes"] == titles[0].file_size_bytes
    assert r["match_source"] == "engram_asr"


def test_build_rows_extra():
    titles = [_title(2, is_extra=True, matched_episode="extra", match_source="user")]
    rows = build_title_rows(_tv_job(), titles)
    assert rows[0]["assignment"] == "extra"
    assert rows[0]["season"] is None
    assert rows[0]["episode"] is None


def test_build_rows_movie_main_feature():
    # Kept main feature: not extra, organized (COMPLETED), no episode code.
    titles = [
        _title(
            0,
            is_extra=False,
            matched_episode=None,
            match_source="discdb",
            state=TitleState.COMPLETED,
        ),
        _title(5, is_extra=True, matched_episode="extra", state=TitleState.COMPLETED),
    ]
    rows = build_title_rows(_movie_job(), titles)
    by_idx = {r["title_index"]: r for r in rows}
    assert by_idx[0]["assignment"] == "main_movie"
    assert by_idx[0]["season"] is None
    assert by_idx[0]["episode"] is None
    assert by_idx[0]["match_source"] == "engram_discdb"
    assert by_idx[5]["assignment"] == "extra"


def test_build_rows_discarded_track():
    # Unmatched / not-organized track on a TV disc → discarded.
    titles = [
        _title(1, matched_episode="S01E01", match_source="engram"),
        _title(9, matched_episode=None, state=TitleState.FAILED),
    ]
    rows = build_title_rows(_tv_job(), titles)
    by_idx = {r["title_index"]: r for r in rows}
    assert by_idx[9]["assignment"] == "discarded"
    assert by_idx[9]["season"] is None
    assert by_idx[9]["episode"] is None


def test_build_rows_includes_all_titles():
    titles = [
        _title(1, matched_episode="S01E01", match_source="engram"),
        _title(2, is_extra=True, matched_episode="extra"),
        _title(3, matched_episode=None, state=TitleState.FAILED),
    ]
    rows = build_title_rows(_tv_job(), titles)
    assert {r["title_index"] for r in rows} == {1, 2, 3}


# --------------------------------------------------------------------------- #
# Source mapping
# --------------------------------------------------------------------------- #


def test_network_disc_source_not_relabeled():
    assert _DISC_MATCH_SOURCE_TO_CONTRIB["network_disc"] == "network_disc"
    titles = [_title(1, matched_episode="S01E01", match_source="network_disc")]
    rows = build_title_rows(_tv_job(), titles)
    assert rows[0]["match_source"] == "network_disc"


def test_unknown_source_defaults_to_engram_asr():
    titles = [_title(1, matched_episode="S01E01", match_source="engram")]
    rows = build_title_rows(_tv_job(), titles)
    assert rows[0]["match_source"] == "engram_asr"


# --------------------------------------------------------------------------- #
# Gates
# --------------------------------------------------------------------------- #


async def _enqueue(job, titles, **kw):
    session = AsyncMock()
    session.add = MagicMock()
    defaults = dict(contributions_enabled=True, pseudonym=PSEUDO)
    defaults.update(kw)
    await enqueue_disc_contribution(session, job, titles, **defaults)
    return session


@pytest.mark.asyncio
async def test_enqueue_inserts_row_with_bytes_hash_and_json_roundtrip():
    titles = [_title(1, matched_episode="S02E05", match_source="engram")]
    session = await _enqueue(_tv_job(), titles)
    session.add.assert_called_once()
    row = session.add.call_args[0][0]
    assert isinstance(row, DiscContribution)
    assert row.disc_content_hash == bytes.fromhex(HASH_HEX)
    assert row.tmdb_id == 1399
    assert row.content_type == "tv"
    assert row.season == 2
    assert row.upload_status is None
    parsed = json.loads(row.titles_json)
    assert parsed[0]["assignment"] == "episode"
    assert parsed[0]["episode"] == 5


@pytest.mark.asyncio
async def test_enqueue_movie_has_no_season():
    titles = [_title(0, is_extra=False, matched_episode=None, match_source="discdb")]
    session = await _enqueue(_movie_job(), titles)
    row = session.add.call_args[0][0]
    assert row.content_type == "movie"
    assert row.season is None


@pytest.mark.asyncio
async def test_enqueue_disabled_is_noop():
    titles = [_title(1, matched_episode="S01E01", match_source="engram")]
    session = await _enqueue(_tv_job(), titles, contributions_enabled=False)
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_enqueue_no_pseudonym_is_noop():
    titles = [_title(1, matched_episode="S01E01", match_source="engram")]
    session = await _enqueue(_tv_job(), titles, pseudonym=None)
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_enqueue_no_hash_is_noop():
    titles = [_title(1, matched_episode="S01E01", match_source="engram")]
    session = await _enqueue(_tv_job(content_hash=None), titles)
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_enqueue_bad_hash_is_noop():
    titles = [_title(1, matched_episode="S01E01", match_source="engram")]
    session = await _enqueue(_tv_job(content_hash="not-hex-zz"), titles)
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_enqueue_no_tmdb_is_noop():
    titles = [_title(1, matched_episode="S01E01", match_source="engram")]
    session = await _enqueue(_tv_job(tmdb_id=None), titles)
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_enqueue_unknown_content_type_is_noop():
    titles = [_title(1, matched_episode="S01E01", match_source="engram")]
    session = await _enqueue(_tv_job(content_type=ContentType.UNKNOWN), titles)
    session.add.assert_not_called()


# --------------------------------------------------------------------------- #
# Real-assignment gate + anti-feedback skip
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_enqueue_skips_when_no_real_assignment():
    # Only extras + discarded → nothing identified worth contributing.
    titles = [
        _title(2, is_extra=True, matched_episode="extra"),
        _title(9, matched_episode=None, state=TitleState.FAILED),
    ]
    session = await _enqueue(_tv_job(), titles)
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_enqueue_skips_when_all_real_assignments_from_network():
    # Every episode came FROM the network → don't re-contribute (feedback loop).
    titles = [
        _title(1, matched_episode="S01E01", match_source="network_disc"),
        _title(2, matched_episode="S01E02", match_source="network_disc"),
        _title(3, is_extra=True, matched_episode="extra", match_source="network_disc"),
    ]
    session = await _enqueue(_tv_job(), titles)
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_enqueue_proceeds_for_mixed_network_and_independent():
    # At least one independently-matched real assignment → DOES enqueue.
    titles = [
        _title(1, matched_episode="S01E01", match_source="network_disc"),
        _title(2, matched_episode="S01E02", match_source="engram"),
    ]
    session = await _enqueue(_tv_job(), titles)
    session.add.assert_called_once()


# --------------------------------------------------------------------------- #
# Real DB insertion + terminal-state callback wiring
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_enqueue_persists_real_row_to_db():
    """Against the in-memory DB, a DiscContribution row lands and round-trips."""
    from sqlmodel import select as _select

    from app.database import async_session

    async with async_session() as session:
        job = _tv_job()
        job.id = 1
        session.add(job)
        await session.commit()
        titles = [_title(1, matched_episode="S02E05", match_source="engram")]
        await enqueue_disc_contribution(
            session, job, titles, contributions_enabled=True, pseudonym=PSEUDO
        )
        await session.commit()

    async with async_session() as session:
        rows = (await session.execute(_select(DiscContribution))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.disc_content_hash == bytes.fromhex(HASH_HEX)
    assert row.upload_status is None
    assert row.upload_attempts == 0
    parsed = json.loads(row.titles_json)
    assert parsed[0]["assignment"] == "episode"


@pytest.mark.asyncio
async def test_terminal_callback_enqueues_on_completed_not_failed():
    """The job_manager terminal hook enqueues on COMPLETED and is a no-op on FAILED."""
    from sqlmodel import select as _select

    from app.database import async_session
    from app.models import JobState
    from app.services.config_service import update_config
    from app.services.job_manager import job_manager

    # Ensure contributions enabled + a pseudonym set in the in-memory config.
    await update_config(enable_fingerprint_contributions=True, contribution_pseudonym=PSEUDO)

    async with async_session() as session:
        job = _tv_job()
        job.id = 7
        session.add(job)
        session.add(_title(1, job_id=7, matched_episode="S02E01", match_source="engram"))
        await session.commit()

    # FAILED → no row.
    await job_manager._enqueue_disc_contribution_on_terminal(7, JobState.FAILED)
    async with async_session() as session:
        rows = (await session.execute(_select(DiscContribution))).scalars().all()
    assert rows == []

    # COMPLETED → one row.
    await job_manager._enqueue_disc_contribution_on_terminal(7, JobState.COMPLETED)
    async with async_session() as session:
        rows = (await session.execute(_select(DiscContribution))).scalars().all()
    assert len(rows) == 1
    assert rows[0].tmdb_id == 1399
