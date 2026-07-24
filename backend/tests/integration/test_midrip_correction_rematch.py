"""A mid-rip identity correction re-matches an already-processed title.

Exercises the real re_identify → _apply_identity_resume_action("rematch_ripped")
→ _rematch_ripped_titles path against the app DB (not pure simulation), with
match_single_file stubbed to record dispatches.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from app.database import async_session, init_db
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.job_manager import job_manager


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()
    async with async_session() as session:
        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs"))
        await session.commit()


@pytest.mark.asyncio
async def test_midrip_correction_rematches_already_matched_title(monkeypatch, tmp_path):
    # Keep re_identify off the network.
    coord = job_manager._identification
    monkeypatch.setattr(coord, "_start_tv_subtitle_prefetch", AsyncMock())
    monkeypatch.setattr(coord, "_cancel_subtitle_download", AsyncMock())
    monkeypatch.setattr(coord, "_restart_subtitle_download", AsyncMock())
    monkeypatch.setattr(
        "app.services.identification_coordinator._resolve_show_year",
        lambda tmdb_id, signal: None,
    )

    dispatched: list[int] = []

    async def fake_match(job_id, title_id, file_path):
        dispatched.append(title_id)

    monkeypatch.setattr(job_manager._matching, "match_single_file", fake_match)
    monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)

    # A confidently-identified (but wrong) TV disc, one title already MATCHED.
    f = tmp_path / "SHOW_A_t00.mkv"
    f.write_text("x")
    async with async_session() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="SHOW_A_S1D1",
            content_type=ContentType.TV,
            state=JobState.RIPPING,
            staging_path=str(tmp_path),
            detected_title="Show A",
            tmdb_id=111,
            identity_prompt_json=None,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        title = DiscTitle(
            job_id=job.id,
            title_index=0,
            duration_seconds=1380,
            state=TitleState.MATCHED,
            output_filename=str(f),
            is_selected=True,
            match_confidence=0.9,
            matched_episode="S01E01",
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)
        job_id, title_id = job.id, title.id

    await job_manager.re_identify_job(job_id, "Show B", "tv", season=1, tmdb_id=999)
    await asyncio.sleep(0)

    assert title_id in dispatched
    async with async_session() as session:
        refreshed = await session.get(DiscTitle, title_id)
        assert refreshed.state in (TitleState.QUEUED, TitleState.MATCHING)
        assert refreshed.matched_episode is None
        job_row = await session.get(DiscJob, job_id)
        assert job_row.detected_title == "Show B"
        assert job_row.identity_prompt_json is None
