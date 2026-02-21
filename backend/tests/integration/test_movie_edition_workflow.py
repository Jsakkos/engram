import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from app.models import ContentType, DiscJob, DiscTitle, JobState, TitleState
from app.services.job_manager import JobManager

# Ensure app can be imported
sys.path.insert(0, os.getcwd())


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=True)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        yield s


@pytest.fixture
def mock_db_session_factory(session):
    # Create a context manager that yields the session
    class MockSessionContext:
        def __init__(self, s):
            self.session = s

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    return lambda: MockSessionContext(session)


@pytest.mark.asyncio
@patch("app.services.job_manager.async_session")
async def test_movie_edition_review_workflow(
    mock_async_session, session, mock_db_session_factory, tmp_path
):
    # Patch async_session in job_manager
    mock_async_session.side_effect = mock_db_session_factory

    # Create dummy files
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    title1_path = staging_dir / "title_01.mkv"
    title1_path.touch()
    title2_path = staging_dir / "title_02.mkv"
    title2_path.touch()

    print("\n[DEBUG] Setting up Job...")
    job = DiscJob(
        drive_id="TEST_DRIVE",
        volume_label="LORD_OF_THE_RINGS",
        content_type=ContentType.MOVIE,
        state=JobState.REVIEW_NEEDED,
        detected_title="The Lord of the Rings",
        staging_path=str(staging_dir),
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    print(f"[DEBUG] Job created: {job.id}")

    print("[DEBUG] Creating Title 1...")
    title1 = DiscTitle(
        job_id=job.id,  # Should be 1
        title_index=1,
        duration_seconds=12000,  # Extended
        file_size_bytes=50000000000,
        video_resolution="4K",
        output_filename=str(title1_path),
        state=TitleState.COMPLETED,
    )
    session.add(title1)
    await session.commit()

    print("[DEBUG] Creating Title 2...")
    title2 = DiscTitle(
        job_id=job.id,
        title_index=2,
        duration_seconds=10000,  # Theatrical
        file_size_bytes=40000000000,
        video_resolution="1080p",
        output_filename=str(title2_path),
        state=TitleState.COMPLETED,
    )
    session.add(title2)
    await session.commit()
    print("[DEBUG] Title 2 created")

    # 2. Mock Organizer and WebSocket
    with patch("app.core.organizer.movie_organizer.organize") as mock_organize:
        mock_organize.return_value = {
            "success": True,
            "main_file": "/library/movies/LOTR (Extended).mkv",
        }

        # Initialize JobManager with mocks
        job_manager = JobManager()

        # 3. Apply Review: Select Title 1 as "Extended"
        print("[DEBUG] Applying Review...")
        await job_manager.apply_review(job_id=job.id, title_id=title1.id, edition="Extended")
        print("[DEBUG] Review applied")

        # 4. Verify Database Updates
        await session.refresh(title1)
        await session.refresh(job)

        assert title1.edition == "Extended"
        assert title1.match_confidence == 1.0
        assert job.state == JobState.COMPLETED
        assert job.final_path == "/library/movies/LOTR (Extended).mkv"

        call_args = mock_organize.call_args
        assert call_args is not None
        args, _ = call_args
        assert str(args[0]) == str(title1.output_filename)  # source_file


@pytest.mark.asyncio
@patch("app.services.job_manager.async_session")
async def test_movie_edition_skip_workflow(mock_async_session, session, mock_db_session_factory):
    mock_async_session.side_effect = mock_db_session_factory
    # Setup similar job
    job = DiscJob(
        drive_id="TEST_DRIVE_2",
        volume_label="BAD_MOVIE",
        content_type=ContentType.MOVIE,
        state=JobState.REVIEW_NEEDED,
        detected_title="Bad Movie",
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    title1 = DiscTitle(
        job_id=job.id,
        title_index=1,
        duration_seconds=5000,
        output_filename="/tmp/staging/bad.mkv",
        state=TitleState.COMPLETED,
    )
    session.add(title1)
    await session.commit()

    job_manager = JobManager()

    # Apply Review: Skip
    await job_manager.apply_review(job_id=job.id, title_id=title1.id, episode_code="skip")

    await session.refresh(title1)
    await session.refresh(job)

    assert title1.state == TitleState.FAILED


@pytest.mark.asyncio
@patch("app.services.job_manager.async_session")
async def test_movie_edition_prerip_workflow(mock_async_session, session, mock_db_session_factory):
    """Test selecting an edition BEFORE ripping (files do not exist)."""
    mock_async_session.side_effect = mock_db_session_factory

    # 1. Setup Job (REVIEW_NEEDED)
    job = DiscJob(
        drive_id="TEST_DRIVE_PRERIP",
        volume_label="LOTR_PRERIP",
        content_type=ContentType.MOVIE,
        state=JobState.REVIEW_NEEDED,
        detected_title="The Lord of the Rings",
        staging_path="/tmp/staging_prerip",  # Files DO NOT EXIST
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    title1 = DiscTitle(
        job_id=job.id,
        title_index=1,
        duration_seconds=12000,
        output_filename="/tmp/staging_prerip/title_01.mkv",
        state=TitleState.PENDING,
    )
    title2 = DiscTitle(
        job_id=job.id,
        title_index=2,
        duration_seconds=10000,
        output_filename="/tmp/staging_prerip/title_02.mkv",
        state=TitleState.PENDING,
    )
    session.add(title1)
    session.add(title2)
    await session.commit()

    # 2. Initialize JobManager and Mock Ripping
    job_manager = JobManager()

    # We need to mock _run_ripping to avoid actual execution,
    # but we want to verify it was scheduled.
    # However, apply_review calls it via asyncio.create_task(self._run_ripping(job_id))
    # We can patch _run_ripping on the instance.

    with patch.object(job_manager, "_run_ripping", new_callable=AsyncMock):
        # 3. Apply Review
        await job_manager.apply_review(job_id=job.id, title_id=title1.id, edition="Extended")

        # 4. Verify State Transition
        await session.refresh(job)
        await session.refresh(title1)
        await session.refresh(title2)

        # Job should be RIPPING
        assert job.state == JobState.RIPPING

        # Title 1 should be selected
        assert title1.is_selected is True
        assert title1.edition == "Extended"

        # Title 2 should NOT be selected
        assert title2.is_selected is False


@pytest.mark.asyncio
@patch("app.services.job_manager.async_session")
@patch("app.core.organizer.movie_organizer")
async def test_movie_ambiguous_rip_first_workflow(
    mock_movie_organizer, mock_async_session, session, mock_db_session_factory, tmp_path
):
    """Test 'Rip First, Review Later' workflow for ambiguous movies."""
    mock_async_session.side_effect = mock_db_session_factory

    # Setup Logic
    # 1. Create Job and Titles (Simulating Post-Rip state with multiple files)
    # real files needed for cleanup test
    staging_dir = tmp_path / "staging_ambiguous"
    staging_dir.mkdir()
    file1 = staging_dir / "title_t01.mkv"
    file2 = staging_dir / "title_t02.mkv"
    file1.touch()
    file2.touch()

    job = DiscJob(
        drive_id="TEST_DRIVE_AMB",
        volume_label="AMBIGUOUS_MOVIE",
        content_type=ContentType.MOVIE,
        state=JobState.RIPPING,  # Simulating end of ripping
        detected_title="Ambiguous Movie",
        staging_path=str(staging_dir),
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    title1 = DiscTitle(
        job_id=job.id,
        title_index=1,
        duration_seconds=9000,
        output_filename=str(file1),
        state=TitleState.COMPLETED,
        is_selected=True,
    )
    title2 = DiscTitle(
        job_id=job.id,
        title_index=2,
        duration_seconds=8500,
        output_filename=str(file2),
        state=TitleState.COMPLETED,
        is_selected=True,
    )
    session.add(title1)
    session.add(title2)
    await session.commit()

    # 2. Initialize JobManager
    job_manager = JobManager()

    # 3. Trigger _run_ripping completion logic
    # We can't easily call _run_ripping partially.
    # But we can verify the LOGIC by calling a helper or just testing apply_review cleanup?
    # To test the transition to REVIEW_NEEDED, we would need to run _run_ripping.
    # But checking if we can mock the extractor part.

    # Let's test `apply_review` cleanup logic first, assuming we got to REVIEW_NEEDED.
    job.state = JobState.REVIEW_NEEDED
    session.add(job)
    await session.commit()

    # Mock organizer success
    mock_movie_organizer.organize.return_value = {
        "success": True,
        "main_file": Path("/library/Movies/Ambiguous (2024)/Ambiguous.mkv"),
        "extras": [],
    }

    # 4. Apply Review (Select Title 1)
    await job_manager.apply_review(job_id=job.id, title_id=title1.id, edition="Extended")

    # 5. Assertions
    await session.refresh(job)
    await session.refresh(title1)
    await session.refresh(title2)

    # Job Completed
    assert job.state == JobState.COMPLETED
    assert title1.edition == "Extended"
    assert title1.state == TitleState.COMPLETED

    # Verify file1 exists
    assert file1.exists(), f"File1 {file1} missing!"

    # Title 2 Unselected & Deleted
    assert title2.state == TitleState.FAILED
    assert not file2.exists(), f"File2 {file2} should be deleted!"

    # Verify organizer called with correct file
    mock_movie_organizer.organize.assert_called_once()
    args, _ = mock_movie_organizer.organize.call_args
    # args[0] is source_file
    assert str(args[0]) == str(file1)
