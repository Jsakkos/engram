"""Integration test: ejecting a disc mid-rip salvages what already ripped.

This drives the seam no unit test covers: an HTTP eject reaching a rip that is
actually running, and that rip then performing the salvage itself. The endpoint
does NOT route titles; it only aborts the rip and opens the tray. The running
rip observes the abort flag and routes unfinished titles to REVIEW. Seeding a
job with no rip task in flight would therefore prove nothing.

The simulated rip stands in for MakeMKV. Note ``rip_speed_multiplier``: it
defaults to 10, which finishes a whole disc faster than a single HTTP
round-trip, so the eject would always arrive too late. 1 gives roughly two
seconds per title.

The hardware call is stubbed at ``app.core.sentinel.eject_disc``, which works
because every caller imports it function-locally (resolved at call time). Do
not hoist that import.
"""

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.database import async_session, init_db
from app.main import app
from app.models.disc_job import DiscJob, DiscTitle, JobState, TitleState
from app.services.job_manager import job_manager

# Terminal-ish resting states for a job that has stopped moving.
_SETTLED = (JobState.REVIEW_NEEDED, JobState.COMPLETED, JobState.FAILED)


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
async def _clean_db():
    await init_db()
    async with async_session() as s:
        await s.execute(text("DELETE FROM disc_titles"))
        await s.execute(text("DELETE FROM disc_jobs"))
        await s.commit()


@pytest.fixture(autouse=True)
def _no_real_eject(monkeypatch):
    """Never touch a real drive, and never leave an eject flag behind.

    The flag lives on the module-level extractor singleton, so a leaked entry
    would make a later test's rip believe it had been ejected.
    """
    monkeypatch.setattr("app.core.sentinel.eject_disc", lambda drive: True)
    yield
    job_manager._extractor._ejected_jobs.clear()
    job_manager._extractor._cancelled_jobs.clear()


async def _insert_disc(client, **overrides) -> int:
    payload = {
        "volume_label": "ARRESTED_DEVELOPMENT_S1D1",
        "content_type": "tv",
        "simulate_ripping": True,
        # See the module docstring: the default of 10 outruns the test.
        "rip_speed_multiplier": 1,
    }
    payload.update(overrides)
    response = await client.post("/api/simulate/insert-disc", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["job_id"]


async def _wait_for_job_state(job_id: int, states, timeout: float = 60.0) -> JobState:
    """Poll the job row until it reaches one of *states*."""
    deadline = asyncio.get_running_loop().time() + timeout
    last = None
    while asyncio.get_running_loop().time() < deadline:
        async with async_session() as s:
            job = await s.get(DiscJob, job_id)
            last = job.state if job else None
        if last in states:
            return last
        await asyncio.sleep(0.1)
    raise AssertionError(f"job {job_id} never reached {states}; last state was {last}")


async def _titles(job_id: int) -> list[DiscTitle]:
    async with async_session() as s:
        rows = await s.execute(text("SELECT id FROM disc_titles WHERE job_id = :j"), {"j": job_id})
        ids = [r[0] for r in rows]
        return [await s.get(DiscTitle, tid) for tid in ids]


@pytest.mark.asyncio
async def test_eject_midrip_salvages_ripped_tracks_and_parks_the_rest(client):
    """The whole point of the feature, driven through the real running rip."""
    job_id = await _insert_disc(client)
    await _wait_for_job_state(job_id, (JobState.RIPPING,))

    # Let at least one title finish so there is something to salvage.
    await asyncio.sleep(3.0)

    response = await client.post(f"/api/jobs/{job_id}/eject")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["action"] == "rip_stopped"
    assert body["job_id"] == job_id

    settled = await _wait_for_job_state(job_id, _SETTLED)

    titles = await _titles(job_id)
    ejected = [t for t in titles if t.state == TitleState.REVIEW]
    finished = [t for t in titles if t.state == TitleState.COMPLETED]

    # An eject is not a cancel. The job must never land in FAILED.
    assert settled != JobState.FAILED, "an eject must not fail the job"

    # Something was cut short, or the test raced the rip to completion and is
    # not exercising the feature at all.
    assert ejected, (
        "no track was parked in review; the eject arrived after the rip "
        "finished, so this test proved nothing"
    )

    # Every parked track is recoverable, not lost.
    for title in ejected:
        details = json.loads(title.match_details)
        assert details["error"] == "rip_ejected", (
            f"title {title.title_index} parked with {details.get('error')!r}"
        )
        assert details["rerip_eligible"] is True
        assert "reinsert" in details["message"].lower()

    # Tracks that ripped cleanly went all the way through, and none of them
    # picked up an eject marker.
    for title in finished:
        assert title.match_details is None or "rip_ejected" not in title.match_details

    # A job still holding tracks for the user must say so.
    assert settled == JobState.REVIEW_NEEDED, (
        f"job settled {settled.value} while {len(ejected)} track(s) await the user"
    )


@pytest.mark.asyncio
async def test_eject_reports_a_tray_that_would_not_open(client, monkeypatch):
    """A stuck tray still stops the rip and still salvages; it just says so."""
    monkeypatch.setattr("app.core.sentinel.eject_disc", lambda drive: False)

    job_id = await _insert_disc(client)
    await _wait_for_job_state(job_id, (JobState.RIPPING,))
    await asyncio.sleep(3.0)

    response = await client.post(f"/api/jobs/{job_id}/eject")
    assert response.status_code == 200
    assert response.json()["ejected"] is False

    await _wait_for_job_state(job_id, _SETTLED)
    titles = await _titles(job_id)
    assert any(t.state == TitleState.REVIEW for t in titles), (
        "a failed tray-open must not suppress the salvage"
    )


@pytest.mark.asyncio
async def test_eject_is_rejected_once_the_drive_is_free(client):
    """Post-rip states run after the disc has already been released."""
    job_id = await _insert_disc(client)
    await _wait_for_job_state(job_id, _SETTLED, timeout=90.0)

    response = await client.post(f"/api/jobs/{job_id}/eject")

    assert response.status_code == 409
    assert "Cannot eject" in response.json()["detail"]
