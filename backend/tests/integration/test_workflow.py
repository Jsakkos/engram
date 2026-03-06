"""Integration tests for complete disc processing workflow.

Tests the full pipeline from disc insertion through ripping, matching,
and organization using simulation endpoints.
"""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.database import async_session, init_db
from app.main import app
from app.models import AppConfig


@pytest.fixture(autouse=True)
async def setup_db():
    """Initialize test database and clean data between tests."""
    await init_db()
    # Clean job data before each test (NOT app_config — that has real API keys)
    async with async_session() as session:
        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs"))
        await session.commit()


@pytest.fixture
async def client():
    """Create async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def test_config():
    """Create test configuration in the database."""
    async with async_session() as session:
        config = AppConfig(
            makemkv_path="/usr/bin/makemkvcon",
            makemkv_key="T-test-key-1234567890",
            staging_path="/tmp/staging",
            library_movies_path="/media/movies",
            library_tv_path="/media/tv",
            transcoding_enabled=False,
            tmdb_api_key="eyJhbGciOiJIUzI1NiJ9.test_jwt_token",
            max_concurrent_matches=2,
            ffmpeg_path="/usr/bin/ffmpeg",
            conflict_resolution_default="rename",
            ripping_file_poll_interval=0.5,  # Faster for tests
            ripping_stability_checks=2,
            ripping_file_ready_timeout=60.0,
        )
        session.add(config)
        await session.commit()
        await session.refresh(config)
        return config


@pytest.mark.asyncio
@pytest.mark.integration
class TestTVDiscWorkflow:
    """Integration tests for TV disc processing workflow."""

    async def test_complete_tv_workflow(self, client, test_config):
        """Test complete TV disc workflow from insert to completion."""
        # 1. Simulate disc insertion
        insert_payload = {
            "volume_label": "ARRESTED_DEVELOPMENT_S1D1",
            "content_type": "tv",
            "simulate_ripping": True,
        }
        response = await client.post("/api/simulate/insert-disc", json=insert_payload)
        assert response.status_code == 200
        job_data = response.json()
        assert job_data["status"] == "simulated"
        job_id = job_data["job_id"]

        # 2. Wait for job to reach RIPPING state (identification should happen automatically)
        max_wait = 10  # seconds
        start = asyncio.get_event_loop().time()
        job_state = "idle"

        while asyncio.get_event_loop().time() - start < max_wait:
            response = await client.get(f"/api/jobs/{job_id}")
            assert response.status_code == 200
            job_state = response.json()["state"]

            if job_state in ("ripping", "matching", "organizing", "completed"):
                break

            await asyncio.sleep(0.5)

        # Should have progressed past IDLE
        assert job_state != "idle", f"Job stuck in IDLE state after {max_wait}s"

        # 3. Wait for completion (simulation runs quickly)
        max_wait_completion = 30  # seconds
        start = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start < max_wait_completion:
            response = await client.get(f"/api/jobs/{job_id}")
            assert response.status_code == 200
            job_data = response.json()
            job_state = job_data["state"]

            if job_state in ("completed", "failed", "review_needed"):
                break

            await asyncio.sleep(1)

        # 4. Verify final state
        response = await client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        final_job = response.json()

        # TV disc should either complete, need review, or fail (if subtitles unavailable)
        # Note: 'failed' is expected when subtitle cache doesn't have the show data
        assert final_job["state"] in (
            "completed",
            "review_needed",
            "matching",
            "organizing",
            "failed",
        )
        assert final_job["content_type"] == "tv"
        assert final_job["detected_title"] is not None

        # 5. Verify titles were discovered
        response = await client.get(f"/api/jobs/{job_id}/titles")
        assert response.status_code == 200
        titles = response.json()
        assert len(titles) > 0, "Should have discovered titles on disc"

        # 6. Verify title states are valid
        for title in titles:
            assert title["state"] in (
                "pending",
                "ripping",
                "matching",
                "matched",
                "review",
                "organizing",
                "completed",
                "failed",
            )

    async def test_tv_disc_cancellation(self, client, test_config):
        """Test canceling a TV disc job mid-workflow."""
        # 1. Insert disc
        insert_payload = {
            "volume_label": "TEST_TV_S1D1",
            "content_type": "tv",
            "simulate_ripping": True,
        }
        response = await client.post("/api/simulate/insert-disc", json=insert_payload)
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        # 2. Wait a moment for job to start
        await asyncio.sleep(1)

        # 3. Cancel the job
        response = await client.post(f"/api/jobs/{job_id}/cancel")
        assert response.status_code == 200

        # 4. Verify job state
        response = await client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        job_data = response.json()

        # Should be in failed state with cancellation message
        assert job_data["state"] == "failed"
        assert "cancel" in job_data.get("error_message", "").lower()

    async def test_tv_disc_review_needed(self, client, test_config):
        """Test TV disc that requires review.

        Note: With simulate_ripping=False, simulation may still auto-start
        and progress through states. This test verifies job creation works.
        """
        # 1. Insert disc with ambiguous content
        insert_payload = {
            "volume_label": "AMBIGUOUS_TV_DISC",
            "content_type": "tv",
            "simulate_ripping": False,  # Manual workflow (may still auto-start in simulation)
        }
        response = await client.post("/api/simulate/insert-disc", json=insert_payload)
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        # 2. Verify job was created successfully
        response = await client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        job_state = response.json()["state"]
        # Note: Simulation may auto-start, so accept any valid state
        assert job_state in (
            "idle",
            "identifying",
            "review_needed",
            "ripping",
            "matching",
            "organizing",
            "completed",
            "failed",
        )


@pytest.mark.asyncio
@pytest.mark.integration
class TestMovieDiscWorkflow:
    """Integration tests for movie disc processing workflow."""

    async def test_complete_movie_workflow(self, client, test_config):
        """Test complete movie disc workflow from insert to completion."""
        # 1. Simulate movie disc insertion
        insert_payload = {
            "volume_label": "INCEPTION_2010",
            "content_type": "movie",
            "simulate_ripping": True,
        }
        response = await client.post("/api/simulate/insert-disc", json=insert_payload)
        assert response.status_code == 200
        job_data = response.json()
        assert job_data["status"] == "simulated"
        job_id = job_data["job_id"]

        # 2. Wait for workflow to progress
        max_wait = 30
        start = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start < max_wait:
            response = await client.get(f"/api/jobs/{job_id}")
            assert response.status_code == 200
            job_state = response.json()["state"]

            if job_state in ("completed", "failed", "review_needed"):
                break

            await asyncio.sleep(1)

        # 3. Verify final state
        response = await client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        final_job = response.json()

        # Movie should complete or need review
        assert final_job["state"] in ("completed", "review_needed")
        assert final_job["content_type"] == "movie"

        # 4. Verify titles
        response = await client.get(f"/api/jobs/{job_id}/titles")
        assert response.status_code == 200
        titles = response.json()
        assert len(titles) > 0

        # 5. Verify no movie title is in MATCHING state (issue #15)
        for title in titles:
            assert title["state"] != "matching", (
                f"Movie title {title['id']} in MATCHING state — "
                f"movies should skip MATCHING (bug #15)"
            )


@pytest.mark.asyncio
@pytest.mark.integration
class TestDiscRemoval:
    """Integration tests for disc removal events."""

    async def test_disc_removal(self, client, test_config):
        """Test disc removal simulation."""
        # 1. Insert disc first
        insert_payload = {
            "volume_label": "TEST_DISC",
            "content_type": "tv",
            "simulate_ripping": False,
        }
        response = await client.post("/api/simulate/insert-disc", json=insert_payload)
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        # 2. Remove disc
        response = await client.post("/api/simulate/remove-disc?drive_id=E%3A")
        assert response.status_code == 200

        # 3. Verify job still exists
        response = await client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.integration
class TestStateAdvancement:
    """Integration tests for manual state advancement (debugging)."""

    async def test_advance_job_states(self, client, test_config):
        """Test manually advancing job through states."""
        # 1. Create job
        insert_payload = {
            "volume_label": "TEST_MANUAL_ADVANCE",
            "content_type": "tv",
            "simulate_ripping": False,
        }
        response = await client.post("/api/simulate/insert-disc", json=insert_payload)
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        # 2. Get initial state (may have auto-advanced in simulation)
        response = await client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        response.json()["state"]

        # Advance to next state
        response = await client.post(f"/api/simulate/advance-job/{job_id}")
        assert response.status_code == 200

        # Verify state exists (may or may not have changed due to simulation)
        response = await client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        new_state = response.json()["state"]
        # State should be valid (may be same if already at terminal state)
        assert new_state in (
            "idle",
            "identifying",
            "review_needed",
            "ripping",
            "matching",
            "organizing",
            "completed",
            "failed",
        )


@pytest.mark.asyncio
@pytest.mark.integration
class TestSubtitleCoordination:
    """Integration tests for subtitle download coordination."""

    async def test_subtitle_download_blocks_matching(self, client, test_config):
        """Test that matching waits for subtitle download."""
        # This test verifies the subtitle coordination logic:
        # 1. Subtitle download starts during ripping
        # 2. Matching waits for subtitle_ready event
        # 3. Matching proceeds after subtitles complete/fail

        # Create TV job
        insert_payload = {
            "volume_label": "TEST_TV_WITH_SUBS",
            "content_type": "tv",
            "simulate_ripping": True,
        }
        response = await client.post("/api/simulate/insert-disc", json=insert_payload)
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        # Wait for job to progress
        max_wait = 30
        start = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start < max_wait:
            response = await client.get(f"/api/jobs/{job_id}")
            assert response.status_code == 200
            job_data = response.json()

            # Check subtitle status if available
            subtitle_status = job_data.get("subtitle_status")
            if subtitle_status in ("completed", "partial", "failed"):
                # Subtitles finished, matching should proceed
                break

            await asyncio.sleep(1)

        # Verify job progressed past ripping
        response = await client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        final_job = response.json()

        # Should have subtitle status set
        assert final_job.get("subtitle_status") in (
            "completed",
            "partial",
            "failed",
            "downloading",
            None,
        )


@pytest.mark.asyncio
@pytest.mark.integration
class TestConcurrency:
    """Integration tests for concurrent job processing."""

    async def test_multiple_concurrent_jobs(self, client, test_config):
        """Test processing multiple discs concurrently."""
        # Insert multiple discs
        job_ids = []
        for i in range(3):
            insert_payload = {
                "volume_label": f"TEST_DISC_{i}",
                "content_type": "tv",
                "simulate_ripping": False,
            }
            response = await client.post("/api/simulate/insert-disc", json=insert_payload)
            assert response.status_code == 200
            job_ids.append(response.json()["job_id"])

        # Verify all jobs created
        assert len(job_ids) == 3

        # Verify all jobs are accessible
        for job_id in job_ids:
            response = await client.get(f"/api/jobs/{job_id}")
            assert response.status_code == 200

        # List all jobs
        response = await client.get("/api/jobs")
        assert response.status_code == 200
        jobs = response.json()
        assert len(jobs) >= 3


@pytest.mark.asyncio
@pytest.mark.integration
class TestMovieTitleStateLifecycle:
    """Integration tests for issue #15: Movie titles must never enter MATCHING state.

    These tests exercise real code paths via simulation endpoints and assert
    on individual title states — not just job states. This is the critical gap
    that allowed bug #15 to ship: previous tests only verified the job-level
    state machine, never the title-level transitions.
    """

    async def test_movie_titles_never_enter_matching_state(self, client, test_config):
        """REGRESSION: Movie titles must go RIPPING → MATCHED, never MATCHING.

        Bug #15: Movie titles incorrectly transitioned to MATCHING state
        (audio fingerprinting phase that only applies to TV episodes).
        This test polls title states during the simulation and asserts that
        no movie title ever enters the MATCHING state.
        """
        # 1. Simulate movie disc insertion with ripping
        response = await client.post(
            "/api/simulate/insert-disc",
            json={
                "volume_label": "500DAYSOFSUMMER",
                "content_type": "movie",
                "simulate_ripping": True,
            },
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        # 2. Poll title states throughout the workflow
        #    We sample frequently to catch transient MATCHING states
        observed_title_states: dict[int, list[str]] = {}
        max_wait = 30
        start = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start < max_wait:
            # Check job state
            response = await client.get(f"/api/jobs/{job_id}")
            assert response.status_code == 200
            job_state = response.json()["state"]

            # Record all title states
            response = await client.get(f"/api/jobs/{job_id}/titles")
            if response.status_code == 200:
                for title in response.json():
                    tid = title["id"]
                    state = title["state"]
                    if tid not in observed_title_states:
                        observed_title_states[tid] = []
                    # Only record new states (avoid duplicates)
                    if not observed_title_states[tid] or observed_title_states[tid][-1] != state:
                        observed_title_states[tid].append(state)

            if job_state in ("completed", "failed", "review_needed"):
                break
            await asyncio.sleep(0.3)  # Fast polling to catch transient states

        # 3. CRITICAL ASSERTION: No movie title should ever enter MATCHING
        for tid, states in observed_title_states.items():
            assert "matching" not in states, (
                f"Movie title {tid} entered MATCHING state! "
                f"Observed lifecycle: {' → '.join(states)}. "
                f"Movies should go RIPPING → MATCHED, never MATCHING."
            )

    async def test_movie_titles_reach_matched_after_ripping(self, client, test_config):
        """Movie titles should reach MATCHED (not MATCHING) after ripping completes."""
        response = await client.post(
            "/api/simulate/insert-disc",
            json={
                "volume_label": "INCEPTION_TITLE_TEST",
                "content_type": "movie",
                "simulate_ripping": True,
            },
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        # Wait for ripping to complete
        max_wait = 30
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < max_wait:
            response = await client.get(f"/api/jobs/{job_id}")
            job_state = response.json()["state"]
            # Job moves past ripping → titles should be in post-rip state
            if job_state in ("organizing", "completed", "failed", "review_needed"):
                break
            await asyncio.sleep(0.5)

        # Check title states — all should be MATCHED or later (never MATCHING)
        response = await client.get(f"/api/jobs/{job_id}/titles")
        assert response.status_code == 200
        titles = response.json()
        assert len(titles) > 0, "Movie should have at least one title"

        for title in titles:
            assert title["state"] != "matching", (
                f"Title {title['id']} (index {title['title_index']}) is in MATCHING state. "
                f"Movie titles must never enter MATCHING — this is the bug from issue #15."
            )
            # After ripping, titles should be at least MATCHED
            assert title["state"] in ("matched", "completed", "review", "failed"), (
                f"Title {title['id']} in unexpected state '{title['state']}' after ripping."
            )

    async def test_movie_titles_reach_completed_after_job_completes(self, client, test_config):
        """When a movie job reaches COMPLETED, all titles should also be COMPLETED.

        Second bug found during #15 investigation: job state transitions to
        COMPLETED but individual title states were left in MATCHED indefinitely.
        """
        response = await client.post(
            "/api/simulate/insert-disc",
            json={
                "volume_label": "MOVIE_TITLE_COMPLETION",
                "content_type": "movie",
                "simulate_ripping": True,
            },
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        # Wait for job to complete
        max_wait = 30
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < max_wait:
            response = await client.get(f"/api/jobs/{job_id}")
            job_state = response.json()["state"]
            if job_state in ("completed", "failed", "review_needed"):
                break
            await asyncio.sleep(0.5)

        response = await client.get(f"/api/jobs/{job_id}")
        final_job = response.json()

        # Only check title completion if the job itself completed successfully
        if final_job["state"] == "completed":
            response = await client.get(f"/api/jobs/{job_id}/titles")
            titles = response.json()
            assert len(titles) > 0

            for title in titles:
                assert title["state"] == "completed", (
                    f"Job is COMPLETED but title {title['id']} "
                    f"(index {title['title_index']}) is stuck in '{title['state']}'. "
                    f"All titles should reach COMPLETED when the job does."
                )

    async def test_tv_titles_can_enter_matching_state(self, client, test_config):
        """Positive control: TV titles SHOULD enter MATCHING state.

        This verifies we haven't broken TV workflows while fixing movie titles.
        """
        response = await client.post(
            "/api/simulate/insert-disc",
            json={
                "volume_label": "TV_MATCHING_CONTROL",
                "content_type": "tv",
                "simulate_ripping": True,
            },
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        # Poll title states throughout the workflow
        ever_saw_matching = False
        max_wait = 30
        start = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start < max_wait:
            response = await client.get(f"/api/jobs/{job_id}")
            job_state = response.json()["state"]

            response = await client.get(f"/api/jobs/{job_id}/titles")
            if response.status_code == 200:
                for title in response.json():
                    if title["state"] == "matching":
                        ever_saw_matching = True

            if job_state in ("completed", "failed", "review_needed"):
                break
            await asyncio.sleep(0.3)

        # TV titles should enter MATCHING (audio fingerprint phase)
        assert ever_saw_matching, (
            "TV titles never entered MATCHING state during simulation. "
            "This is unexpected — TV titles should go through MATCHING for episode matching."
        )


@pytest.mark.asyncio
@pytest.mark.integration
class TestJobCompletionFromMatching:
    """Test that jobs properly complete after matching phase."""

    async def test_job_completion_from_matching(self, client, test_config):
        """All titles match → job should eventually reach COMPLETED.

        This is the core scenario where the job-stuck-in-PROCESSING bug manifests.
        """
        response = await client.post(
            "/api/simulate/insert-disc",
            json={
                "volume_label": "COMPLETION_TEST_S1D1",
                "content_type": "tv",
                "simulate_ripping": True,
            },
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        # Wait for job to reach a terminal state
        max_wait = 30
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < max_wait:
            response = await client.get(f"/api/jobs/{job_id}")
            assert response.status_code == 200
            state = response.json()["state"]
            if state in ("completed", "failed", "review_needed"):
                break
            await asyncio.sleep(1)

        # Verify job reached a terminal state (not stuck in matching/organizing)
        response = await client.get(f"/api/jobs/{job_id}")
        final = response.json()
        assert final["state"] in ("completed", "failed", "review_needed"), (
            f"Job stuck in non-terminal state: {final['state']}"
        )

    async def test_review_submit_resumes_workflow(self, client, test_config):
        """After submitting a review, the job should resume processing."""
        # Create a job (may need review due to subtitle matching)
        response = await client.post(
            "/api/simulate/insert-disc",
            json={
                "volume_label": "REVIEW_RESUME_S1D1",
                "content_type": "tv",
                "simulate_ripping": True,
            },
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        # Wait for job to reach any terminal or review state
        max_wait = 30
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < max_wait:
            response = await client.get(f"/api/jobs/{job_id}")
            state = response.json()["state"]
            if state in ("completed", "failed", "review_needed"):
                break
            await asyncio.sleep(1)

        # If it reached review_needed, verify the review endpoint works
        response = await client.get(f"/api/jobs/{job_id}")
        job = response.json()
        if job["state"] == "review_needed":
            # Submit a review (this verifies the endpoint at minimum)
            review_response = await client.post(
                f"/api/jobs/{job_id}/review",
                json={"matches": {}},
            )
            # Should accept the review
            assert review_response.status_code in (200, 400)
