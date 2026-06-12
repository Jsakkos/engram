"""End-to-end walk-away workflow tests (Phase B closing pass, B9).

Two scenarios from the spec (docs/superpowers/specs/2026-06-10):

1. **Pooled review** — a disc rips to completion with an unanswered blocking
   identity prompt and parks ONCE, at the end, in REVIEW_NEEDED carrying the
   prompt's reason verbatim. No intermediate review stop is ever observed.
2. **Mid-rip answer, zero stops** — answering the identity CTA while the job
   is RIPPING never sends it through REVIEW_NEEDED for identity.

Mid-rip pacing note: the answer-while-ripping test uses ``simulate_ripping=False``
(static RIPPING) so the "answer lands while RIPPING" precondition is a fact,
not a race — at the default speed multiplier the simulated rip window is
~50 ms/title, far too short to POST into reliably on a CI runner, and losing
the race would route the answer down the review-resume path (which spawns a
REAL rip task — catastrophic against a sim job). The full auto-rip pipeline
around an early answer is covered separately at ``rip_speed_multiplier=1``
(a multi-second window, answered within milliseconds of RIPPING appearing).
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlmodel import select

from app.database import async_session, init_db
from app.main import app
from app.models import DiscJob, JobState
from app.models.disc_job import DiscTitle, TitleState
from app.services.job_manager import job_manager

# The sim's verbatim kind="name" reason literal (frontend contract — mirrors
# IdentificationCoordinator's gate A; see SimulationService._build_identity_prompt).
UNREADABLE_REASON = "Disc label unreadable. Please enter the title to continue."

TERMINAL_OR_REVIEW = (JobState.REVIEW_NEEDED, JobState.COMPLETED, JobState.FAILED)


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
    """Create async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _patch_identity_network():
    """Keep the answer endpoints off the network (TMDB + subtitle prefetch).

    set_name_and_resume resolves the user-provided name on TMDB and kicks the
    reference-subtitle prefetch; both are real-network seams irrelevant to the
    state-machine behavior under test (and the integration DB may carry a real
    TMDB key — see the integration-test DB hazard in tests/integration docs).
    """
    coordinator = job_manager._identification
    return (
        patch.object(coordinator, "_resolve_missing_tmdb_id", new=AsyncMock()),
        patch.object(coordinator, "_start_tv_subtitle_prefetch", new=AsyncMock()),
    )


async def _get_job(job_id: int) -> DiscJob | None:
    async with async_session() as session:
        return await session.get(DiscJob, job_id)


async def _poll_states(job_id: int, *, timeout: float = 30.0) -> list[JobState]:
    """Sample job state every 50 ms until terminal/review (or timeout).

    Returns the observed state history (consecutive duplicates collapsed).
    """
    history: list[JobState] = []
    for _ in range(int(timeout / 0.05)):
        await asyncio.sleep(0.05)
        job = await _get_job(job_id)
        if job and (not history or history[-1] != job.state):
            history.append(job.state)
        if job and job.state in TERMINAL_OR_REVIEW:
            break
    return history


@pytest.mark.asyncio
async def test_walk_away_unanswered_prompt_pools_into_single_review(client):
    """No answer → rip runs to the end → exactly ONE REVIEW_NEEDED, verbatim reason."""
    response = await client.post(
        "/api/simulate/insert-disc",
        json={
            "volume_label": "UNREADABLE_DISC",
            "content_type": "tv",
            "detected_title": "Unreadable Disc",
            "simulate_ripping": True,
            "rip_speed_multiplier": 10,
            "identity_pending": "name",
            "titles": [
                {"duration_seconds": 1320, "file_size_bytes": 10_000_000},
                {"duration_seconds": 1350, "file_size_bytes": 11_000_000},
            ],
        },
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    history = await _poll_states(job_id)

    # The job converged to pooled review at rip end...
    assert history, "job never reached a terminal/review state"
    assert history[-1] == JobState.REVIEW_NEEDED, f"state history: {history}"
    # ...and REVIEW_NEEDED was never observed BEFORE the end (the walk-away
    # promise: identity questions no longer park the job pre-rip).
    assert JobState.REVIEW_NEEDED not in history[:-1], f"state history: {history}"
    assert all(s in (JobState.IDENTIFYING, JobState.RIPPING) for s in history[:-1]), (
        f"state history: {history}"
    )

    job = await _get_job(job_id)
    # The prompt's reason converts VERBATIM into the blocking review reason (B4).
    assert job.review_reason == UNREADABLE_REASON
    # The prompt itself is retired by the conversion.
    assert job.identity_prompt_json is None

    # Titles stayed parked in QUEUED behind the identity gate (B3) — the
    # pooled review owns them now; nothing was matched against a guessed show.
    async with async_session() as session:
        titles = (
            (await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id)))
            .scalars()
            .all()
        )
    assert titles
    assert all(t.state == TitleState.QUEUED for t in titles), [t.state for t in titles]

    # The pooled review is stable — it does not bounce back out of review.
    await asyncio.sleep(0.5)
    job = await _get_job(job_id)
    assert job.state == JobState.REVIEW_NEEDED
    assert job.review_reason == UNREADABLE_REASON


@pytest.mark.asyncio
async def test_mid_rip_answer_clears_prompt_without_state_change(client):
    """Answering the CTA while RIPPING mutates metadata only — no review, no new rip."""
    response = await client.post(
        "/api/simulate/insert-disc",
        json={
            "volume_label": "UNREADABLE_DISC",
            "content_type": "tv",
            "detected_title": "Unreadable Disc",
            "simulate_ripping": False,  # static RIPPING — race-free (see module docstring)
            "identity_pending": "name",
            "titles": [
                {"duration_seconds": 1320, "file_size_bytes": 10_000_000},
                {"duration_seconds": 1350, "file_size_bytes": 11_000_000},
            ],
        },
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    job = await _get_job(job_id)
    assert job.state == JobState.RIPPING
    assert job.identity_prompt_json is not None

    p1, p2 = _patch_identity_network()
    with p1, p2:
        answer = await client.post(
            f"/api/jobs/{job_id}/set-name",
            json={"name": "Seinfeld", "content_type": "tv", "season": 3},
        )
    assert answer.status_code == 200

    job = await _get_job(job_id)
    # Mid-rip answer contract (B5): metadata updated, prompt cleared, and the
    # job NEVER routed through REVIEW_NEEDED — it is still RIPPING.
    assert job.state == JobState.RIPPING
    assert job.identity_prompt_json is None
    assert job.detected_title == "Seinfeld"
    assert job.detected_season == 3
    assert job.review_reason is None


@pytest.mark.asyncio
async def test_mid_rip_answer_full_pipeline_completes_with_zero_review_stops(client):
    """Auto-rip + mid-rip answer → the job finishes without ever entering review."""
    response = await client.post(
        "/api/simulate/insert-disc",
        json={
            "volume_label": "UNREADABLE_DISC",
            "content_type": "tv",
            "detected_title": "Unreadable Disc",
            "simulate_ripping": True,
            # Slowest pacing: 2 titles x 20 steps x 0.1 s ≈ 4 s of RIPPING —
            # a wide-open window for the immediate answer below.
            "rip_speed_multiplier": 1,
            "identity_pending": "name",
            "titles": [
                {"duration_seconds": 1320, "file_size_bytes": 10_000_000},
                {"duration_seconds": 1350, "file_size_bytes": 11_000_000},
            ],
        },
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    # Wait for the rip task to flip the job to RIPPING (it starts IDENTIFYING).
    for _ in range(100):
        job = await _get_job(job_id)
        if job and job.state == JobState.RIPPING:
            break
        await asyncio.sleep(0.05)
    assert job.state == JobState.RIPPING, f"never reached RIPPING: {job.state}"

    # Answer immediately — milliseconds into a ~4 s rip window.
    p1, p2 = _patch_identity_network()
    with p1, p2:
        answer = await client.post(
            f"/api/jobs/{job_id}/set-name",
            json={"name": "Seinfeld", "content_type": "tv", "season": 3},
        )
    assert answer.status_code == 200

    job = await _get_job(job_id)
    assert job.state == JobState.RIPPING, "mid-rip answer must not change state"
    assert job.identity_prompt_json is None

    # The answered prompt means rip-end convergence is skipped and the sim
    # pipeline proceeds: MATCHING → ... → COMPLETED, with no review stop.
    history = await _poll_states(job_id)
    assert history, "job never reached a terminal state"
    assert JobState.REVIEW_NEEDED not in history, f"state history: {history}"
    assert history[-1] == JobState.COMPLETED, f"state history: {history}"

    job = await _get_job(job_id)
    assert job.identity_prompt_json is None
    assert job.review_reason is None
