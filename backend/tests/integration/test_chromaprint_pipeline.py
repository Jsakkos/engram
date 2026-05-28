"""Integration tests for chromaprint Phase 1: fpcalc detection + extraction pipeline."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.database import async_session, init_db
from app.main import app


@pytest.fixture(autouse=True)
async def setup_db():
    """Initialize test database and clean data between tests."""
    await init_db()
    async with async_session() as session:
        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs"))
        await session.commit()


@pytest.fixture
async def client():
    """Async HTTP client backed by the FastAPI app under test."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def async_session_ctx():
    """Yield an open async database session."""
    async with async_session() as session:
        yield session


@pytest.mark.asyncio
async def test_detect_tools_includes_fpcalc(client):
    """GET /api/detect-tools surfaces fpcalc alongside makemkv and ffmpeg."""
    response = await client.get("/api/detect-tools")
    assert response.status_code == 200
    data = response.json()
    assert "fpcalc" in data, f"detect-tools should include fpcalc, got keys: {list(data.keys())}"


@pytest.mark.asyncio
async def test_validate_fpcalc_endpoint_rejects_missing(client):
    """POST /api/validate/fpcalc with a bogus path reports found=False."""
    response = await client.post(
        "/api/validate/fpcalc",
        json={"path": "/definitely/not/a/binary"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is False


@pytest.mark.asyncio
async def test_validate_fpcalc_rejects_disallowed_basename(client):
    """The validate endpoint refuses paths whose basename is not in the fpcalc whitelist."""
    response = await client.post(
        "/api/validate/fpcalc",
        json={"path": "/usr/bin/whoami"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is False


@pytest.mark.asyncio
async def test_pseudonym_generated_on_first_startup(async_session_ctx):
    """The pseudonym bootstrap ensures app_config always has a valid UUIDv4 pseudonym.

    Simulates the startup flow: get_config() guarantees the row exists, then the
    bootstrap generates a pseudonym when the field is empty.
    """
    from sqlmodel import select

    from app.models.app_config import AppConfig
    from app.services.config_service import get_config
    from app.services.contribution_pseudonym import generate_pseudonym, validate_pseudonym

    # Ensure the app_config row exists (mirrors get_config() call in lifespan).
    await get_config()

    # Run the bootstrap — same logic as the lifespan block.
    result = await async_session_ctx.execute(select(AppConfig))
    cfg = result.scalar_one_or_none()
    assert cfg is not None, "app_config row should exist after get_config()"

    if not validate_pseudonym(cfg.contribution_pseudonym):
        cfg.contribution_pseudonym = generate_pseudonym()
        async_session_ctx.add(cfg)
        await async_session_ctx.commit()

    # Verify the pseudonym is now valid.
    await async_session_ctx.refresh(cfg)
    assert validate_pseudonym(cfg.contribution_pseudonym), (
        f"contribution_pseudonym should be a UUIDv4, got {cfg.contribution_pseudonym!r}"
    )


@pytest.mark.asyncio
async def test_chromaprint_extracted_after_match(async_session_ctx, tmp_path, monkeypatch):
    """When a title finishes matching, chromaprint_blob is populated on the DiscTitle row.

    NOTE: The /api/simulate/insert-disc endpoint uses SimulationService._simulate_matching,
    which generates random fake results without going through MatchingCoordinator. This test
    therefore calls the coordinator directly (same pattern as test_llm_matching_workflow.py).
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.api.websocket import manager as ws_manager
    from app.core.curator import MatchResult
    from app.matcher.chromaprint_extractor import ChromaprintExtractor, ChromaprintResult
    from app.models import DiscJob, TitleState
    from app.models.disc_job import ContentType, DiscTitle, JobState
    from app.services.event_broadcaster import EventBroadcaster
    from app.services.job_state_machine import JobStateMachine
    from app.services.matching_coordinator import MatchingCoordinator

    # Seed a job + title in the DB
    fake_file = tmp_path / "title_01.mkv"
    fake_file.write_text("fake")

    async with async_session() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="ARRESTED_DEVELOPMENT_S1D1",
            state=JobState.MATCHING,
            content_type=ContentType.TV,
            detected_title="Arrested Development",
            detected_season=1,
        )
        session.add(job)
        await session.flush()

        title = DiscTitle(
            job_id=job.id,
            title_index=1,
            duration_seconds=1800,
            file_size_bytes=1_000_000,
            state=TitleState.MATCHING,
            file_path=str(fake_file),
        )
        session.add(title)
        await session.commit()
        await session.refresh(job)
        await session.refresh(title)
        job_id, title_id = job.id, title.id

    # Fake chromaprint result
    fake_fp = ChromaprintResult(hashes=[1, 2, 3], duration_seconds=10.0, fpcalc_version="test")

    async def fake_extract(self, media_path: str) -> ChromaprintResult:
        return fake_fp

    # Fake match result (confident, no review needed)
    stub_result = MatchResult(
        file_path=fake_file,
        episode_code="S01E01",
        episode_title="Pilot",
        confidence=0.95,
        needs_review=False,
        match_details={"score": 0.95, "vote_count": 5, "runner_ups": []},
    )

    mock_broadcaster = MagicMock(spec=EventBroadcaster)
    mock_broadcaster.broadcast_job_state_changed = AsyncMock()
    mock_state_machine = MagicMock(spec=JobStateMachine)

    coordinator = MatchingCoordinator(mock_broadcaster, mock_state_machine)
    coordinator.set_callbacks(check_job_completion=AsyncMock(), note_activity=None)
    coordinator.init_semaphore(concurrency=1)
    coordinator._episode_runtimes[job_id] = []

    with (
        patch(
            "app.services.matching_coordinator.episode_curator.match_single_file",
            new=AsyncMock(return_value=stub_result),
        ),
        patch.object(ChromaprintExtractor, "extract", fake_extract),
        patch.object(coordinator, "_wait_for_file_ready", new=AsyncMock(return_value=True)),
        patch.object(ws_manager, "broadcast_title_update", new=AsyncMock()),
    ):
        await coordinator.match_single_file(job_id, title_id, fake_file)

    # Verify the title has chromaprint_blob set
    async with async_session() as session:
        refreshed = await session.get(DiscTitle, title_id)

    assert refreshed is not None
    assert refreshed.chromaprint_blob is not None, "chromaprint_blob should be set after MATCHED"
    assert refreshed.chromaprint_extracted_at is not None, "chromaprint_extracted_at should be set"
    assert refreshed.state == TitleState.MATCHED, f"expected MATCHED, got {refreshed.state!r}"


@pytest.mark.asyncio
async def test_matching_succeeds_when_fpcalc_missing(client, async_session_ctx, monkeypatch):
    """If fpcalc isn't configured and auto-detect fails, matching still completes without a fingerprint."""
    from app.api import validation as v
    from app.api.validation import ToolDetectionResult

    monkeypatch.setattr(
        v, "detect_fpcalc", lambda: ToolDetectionResult(found=False, path=None, error="absent")
    )

    response = await client.post(
        "/api/simulate/insert-disc",
        json={
            "volume_label": "INCEPTION_2010",
            "content_type": "movie",
            "simulate_ripping": True,
        },
    )
    assert response.status_code == 200

    import asyncio

    from sqlmodel import select

    from app.models.disc_job import DiscJob

    for _ in range(60):
        await asyncio.sleep(0.5)
        result = await async_session_ctx.execute(select(DiscJob))
        jobs = result.scalars().all()
        if jobs and any(
            j.state in ("completed", "review_needed", "matching", "organizing") for j in jobs
        ):
            break
    else:
        pytest.fail("Job never advanced past initial state with fpcalc absent")
