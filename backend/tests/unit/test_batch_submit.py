"""Unit tests for batch release group submission (Wave 3)."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.core.discdb_submitter import SubmissionResult
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState
from tests.unit.conftest import _unit_session_factory


async def _seed_release_group(
    num_jobs: int = 3,
    *,
    release_group_id: str = "test-group-uuid",
    states: list[JobState] | None = None,
) -> tuple[str, list[DiscJob]]:
    """Create N jobs in a release group with one title each."""
    if states is None:
        states = [JobState.COMPLETED] * num_jobs

    jobs = []
    async with _unit_session_factory() as session:
        for i in range(num_jobs):
            job = DiscJob(
                drive_id="E:",
                volume_label=f"TEST_DISC_{i}",
                content_type=ContentType.TV,
                state=states[i],
                content_hash=f"HASH{i:032d}",
                detected_title="Test Show",
                detected_season=1,
                release_group_id=release_group_id,
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)

            title = DiscTitle(
                job_id=job.id,
                title_index=0,
                duration_seconds=2400,
                file_size_bytes=1_000_000_000,
                state=TitleState.COMPLETED,
                matched_episode=f"S01E0{i + 1}",
                match_confidence=0.99,
                match_details=json.dumps({"source": "subtitle"}),
            )
            session.add(title)
            await session.commit()
            jobs.append(job)

    return release_group_id, jobs


@pytest.mark.asyncio
async def test_batch_submit_all_succeed():
    """All jobs in a release group submit successfully."""
    from app.core.discdb_submitter import submit_release_group

    group_id, jobs = await _seed_release_group(3)

    mock_result = SubmissionResult(
        success=True,
        submission_id="sub-123",
        contribute_url="https://thediscdb.com/contribute/engram/123",
    )

    with patch("app.core.discdb_submitter.submit_job", new_callable=AsyncMock) as mock_submit:
        mock_submit.return_value = mock_result

        async with _unit_session_factory() as session:
            from app.models.app_config import AppConfig

            config = AppConfig()
            result = await submit_release_group(group_id, session, config)

    assert result.submitted == 3
    assert result.failed == 0
    assert len(result.results) == 3
    assert all(r["success"] for r in result.results)
    assert result.contribute_url is not None


@pytest.mark.asyncio
async def test_batch_submit_partial_failure():
    """One job fails, others succeed — partial failure reported."""
    from app.core.discdb_submitter import submit_release_group

    group_id, jobs = await _seed_release_group(3)

    call_count = 0

    async def mock_submit_side_effect(job, titles, config, app_version="0.4.4"):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            return SubmissionResult(success=False, error="Network timeout")
        return SubmissionResult(
            success=True,
            submission_id=f"sub-{call_count}",
            contribute_url=f"https://thediscdb.com/contribute/engram/{call_count}",
        )

    with patch(
        "app.core.discdb_submitter.submit_job",
        new_callable=AsyncMock,
        side_effect=mock_submit_side_effect,
    ):
        async with _unit_session_factory() as session:
            from app.models.app_config import AppConfig

            config = AppConfig()
            result = await submit_release_group(group_id, session, config)

    assert result.submitted == 2
    assert result.failed == 1
    assert len(result.results) == 3

    failed = [r for r in result.results if not r["success"]]
    assert len(failed) == 1
    assert failed[0]["error"] == "Network timeout"


@pytest.mark.asyncio
async def test_batch_submit_empty_group():
    """No jobs match the release group ID — returns empty results."""
    from app.core.discdb_submitter import submit_release_group

    async with _unit_session_factory() as session:
        from app.models.app_config import AppConfig

        config = AppConfig()
        result = await submit_release_group("nonexistent-group-uuid", session, config)

    assert result.submitted == 0
    assert result.failed == 0
    assert result.results == []


@pytest.mark.asyncio
async def test_batch_submit_skips_non_completed():
    """Only COMPLETED jobs are submitted; FAILED jobs are skipped."""
    from app.core.discdb_submitter import submit_release_group

    group_id, jobs = await _seed_release_group(
        3, states=[JobState.COMPLETED, JobState.FAILED, JobState.COMPLETED]
    )

    mock_result = SubmissionResult(
        success=True,
        submission_id="sub-ok",
        contribute_url="https://thediscdb.com/contribute/engram/ok",
    )

    with patch("app.core.discdb_submitter.submit_job", new_callable=AsyncMock) as mock_submit:
        mock_submit.return_value = mock_result

        async with _unit_session_factory() as session:
            from app.models.app_config import AppConfig

            config = AppConfig()
            result = await submit_release_group(group_id, session, config)

    # Only 2 COMPLETED jobs should be submitted
    assert result.submitted == 2
    assert result.failed == 0
    assert len(result.results) == 2
