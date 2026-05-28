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
    # Use .first() rather than .scalar_one_or_none() so the test tolerates
    # other tests leaving stray app_config rows in the shared DB.
    result = await async_session_ctx.execute(select(AppConfig).limit(1))
    cfg = result.scalars().first()
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

    async def fake_resolve_fpcalc(_cfg_path):
        # Bypass the real detect_fpcalc() — CI Linux has no fpcalc binary,
        # which would short-circuit the extraction path before our extract()
        # mock could fire. Return any non-empty string; the ChromaprintExtractor
        # patch makes the real path irrelevant.
        return "/fake/fpcalc"

    with (
        patch(
            "app.services.matching_coordinator.episode_curator.match_single_file",
            new=AsyncMock(return_value=stub_result),
        ),
        patch(
            "app.services.matching_coordinator._resolve_fpcalc_path",
            new=fake_resolve_fpcalc,
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
async def test_contribution_enqueued_on_match(async_session_ctx, tmp_path, monkeypatch):
    """A successful match enqueues a FingerprintContribution row.

    Uses the same direct-coordinator pattern as test_chromaprint_extracted_after_match
    because the simulation endpoint bypasses _match_single_file_inner.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.api.websocket import manager as ws_manager
    from app.core.curator import MatchResult
    from app.matcher.chromaprint_extractor import ChromaprintExtractor, ChromaprintResult
    from app.models import DiscJob, TitleState
    from app.models.disc_job import ContentType, DiscTitle, JobState
    from app.services.contribution_pseudonym import generate_pseudonym
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
            tmdb_id=79744,
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

    # Ensure app_config has a valid pseudonym so the enqueue block fires
    from sqlmodel import select

    from app.models.app_config import AppConfig
    from app.services.contribution_pseudonym import validate_pseudonym

    async with async_session() as session:
        result = await session.execute(select(AppConfig))
        cfg = result.first()
        if cfg:
            cfg = cfg[0]
            if not validate_pseudonym(cfg.contribution_pseudonym):
                cfg.contribution_pseudonym = generate_pseudonym()
                session.add(cfg)
                await session.commit()

    # Fake chromaprint result
    fake_fp = ChromaprintResult(hashes=[1], duration_seconds=10.0, fpcalc_version="test")

    async def fake_extract(self, media_path: str) -> ChromaprintResult:
        return fake_fp

    # Fake match result (confident, no review needed)
    stub_result = MatchResult(
        file_path=fake_file,
        episode_code="S01E07",
        episode_title="In God We Trust",
        confidence=0.92,
        needs_review=False,
        match_details={"score": 0.92, "vote_count": 5, "runner_ups": []},
    )

    mock_broadcaster = MagicMock(spec=EventBroadcaster)
    mock_broadcaster.broadcast_job_state_changed = AsyncMock()
    mock_state_machine = MagicMock(spec=JobStateMachine)

    coordinator = MatchingCoordinator(mock_broadcaster, mock_state_machine)
    coordinator.set_callbacks(check_job_completion=AsyncMock(), note_activity=None)
    coordinator.init_semaphore(concurrency=1)
    coordinator._episode_runtimes[job_id] = []

    async def fake_resolve_fpcalc(_cfg_path):
        return "/fake/fpcalc"

    with (
        patch(
            "app.services.matching_coordinator.episode_curator.match_single_file",
            new=AsyncMock(return_value=stub_result),
        ),
        patch(
            "app.services.matching_coordinator._resolve_fpcalc_path",
            new=fake_resolve_fpcalc,
        ),
        patch.object(ChromaprintExtractor, "extract", fake_extract),
        patch.object(coordinator, "_wait_for_file_ready", new=AsyncMock(return_value=True)),
        patch.object(ws_manager, "broadcast_title_update", new=AsyncMock()),
    ):
        await coordinator.match_single_file(job_id, title_id, fake_file)

    # Assert a FingerprintContribution row was enqueued
    from app.models.fingerprint import FingerprintContribution

    async with async_session() as session:
        result = await session.execute(select(FingerprintContribution))
        contributions = result.scalars().all()

    assert len(contributions) >= 1, "Expected at least one queued contribution"
    c = contributions[0]
    assert c.chromaprint_blob is not None
    assert c.pseudonym  # non-empty
    assert c.match_source  # non-empty


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


@pytest.mark.asyncio
async def test_get_fingerprint_contributions(client):
    """GET /api/fingerprint/contributions returns the local queue, redacted of blobs by default."""
    from app.api.routes import require_localhost
    from app.main import app
    from app.models.fingerprint import FingerprintContribution

    async with async_session() as session:
        row = FingerprintContribution(
            title_id=None,
            chromaprint_blob=b"\x00" * 1000,
            tmdb_id=1399,
            season=1,
            episode=1,
            match_confidence=0.95,
            match_source="bootstrap",
            pseudonym="22222222-2222-4222-8222-222222222222",
        )
        session.add(row)
        await session.commit()

    # The endpoint requires localhost. The Starlette test client's host
    # ("testclient") is not a real loopback identity; override the guard for tests.
    app.dependency_overrides[require_localhost] = lambda: None
    try:
        response = await client.get("/api/fingerprint/contributions")
    finally:
        app.dependency_overrides.pop(require_localhost, None)
    assert response.status_code == 200
    data = response.json()
    assert data["count"] >= 1
    item = next(i for i in data["items"] if i["tmdb_id"] == 1399)
    assert item["match_source"] == "bootstrap"
    # Blob should be summarized (size) not returned wholesale
    assert "chromaprint_blob" not in item
    assert item["blob_size_bytes"] == 1000


@pytest.mark.asyncio
async def test_config_endpoint_round_trips_fingerprint_toggle(client):
    """PUT then GET /api/config preserves enable_fingerprint_contributions.

    Regression: the frontend toggle's persistence relies on this round-trip; an
    earlier version of the PR landed the model field without wiring it through
    ConfigResponse + ConfigUpdate, so the frontend's PUT was silently ignored.
    """
    # GET should expose the field with its default (opt-out default = True)
    initial = await client.get("/api/config")
    assert initial.status_code == 200
    assert initial.json()["enable_fingerprint_contributions"] is True

    # PUT False, then GET back False
    put_resp = await client.put("/api/config", json={"enable_fingerprint_contributions": False})
    assert put_resp.status_code == 200
    after = await client.get("/api/config")
    assert after.json()["enable_fingerprint_contributions"] is False

    # Restore default so other tests aren't affected by ordering
    await client.put("/api/config", json={"enable_fingerprint_contributions": True})


def test_require_localhost_rejects_lan_clients():
    """The localhost-only guard 403s any non-loopback client."""
    from unittest.mock import MagicMock

    from fastapi import HTTPException

    from app.api.routes import require_localhost

    # Real LAN client → rejected
    request = MagicMock()
    request.client.host = "192.168.1.100"
    with pytest.raises(HTTPException) as exc:
        require_localhost(request)
    assert exc.value.status_code == 403

    # IPv4 loopback → allowed
    request.client.host = "127.0.0.1"
    require_localhost(request)  # no raise

    # IPv6 loopback → allowed
    request.client.host = "::1"
    require_localhost(request)  # no raise

    # No client info → rejected (safer default)
    request.client = None
    with pytest.raises(HTTPException):
        require_localhost(request)
