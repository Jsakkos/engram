"""Unit tests for the conflict re-match endpoint.

POST /api/jobs/{job_id}/rematch-conflict re-runs the audio matcher for *every*
title currently claiming a given episode code, using stricter parameters
(denser sampling + higher minimum vote count) so a contested episode can break
either way. The matcher itself (ASR) is stubbed here — these tests cover the
title-selection and parameter-passing logic.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import get_session
from app.main import app
from app.models import DiscJob, DiscTitle
from app.models.disc_job import ContentType, JobState, TitleState
from app.services.matching_coordinator import STRICT_MIN_VOTES, STRICT_SCAN_POINTS
from tests.unit.conftest import _unit_session_factory


async def _seed_job() -> DiscJob:
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="SHOW_S1D1",
            content_type=ContentType.TV,
            state=JobState.REVIEW_NEEDED,
            detected_title="Some Show",
            detected_season=1,
            staging_path="/tmp/staging/job_1",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


async def _seed_title(
    job_id: int,
    index: int,
    matched_episode: str,
    state: TitleState = TitleState.MATCHED,
    match_details: str | None = None,
) -> DiscTitle:
    async with _unit_session_factory() as session:
        title = DiscTitle(
            job_id=job_id,
            title_index=index,
            duration_seconds=1380,
            file_size_bytes=1_100_000_000,
            matched_episode=matched_episode,
            match_confidence=0.6,
            state=state,
            match_details=match_details,
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)
        return title


@pytest.fixture(autouse=True)
def _patch_coordinator_session(monkeypatch):
    """matching_coordinator binds async_session at import; point it at the test DB."""
    monkeypatch.setattr("app.services.matching_coordinator.async_session", _unit_session_factory)


@pytest.fixture
async def client():
    async def override_get_session():
        async with _unit_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.mark.unit
class TestRematchConflict:
    async def test_rematches_every_title_claiming_the_episode(self, client, monkeypatch):
        job = await _seed_job()
        t1 = await _seed_title(job.id, 0, "S01E05")
        t2 = await _seed_title(job.id, 1, "S01E05")
        await _seed_title(job.id, 2, "S01E02")  # not in the conflict

        from app.services.job_manager import job_manager

        calls: list[tuple] = []

        async def fake_rematch_single(
            job_id, title_id, source_preference=None, num_points=None, min_vote_count=None
        ):
            calls.append((title_id, source_preference, num_points, min_vote_count))

        monkeypatch.setattr(job_manager._matching, "rematch_single_title", fake_rematch_single)

        resp = await client.post(
            f"/api/jobs/{job.id}/rematch-conflict", json={"episode_code": "S01E05"}
        )

        assert resp.status_code == 200
        data = resp.json()
        assert set(data["title_ids"]) == {t1.id, t2.id}

        # Only the two contested titles were re-matched, with strict engram params.
        assert {c[0] for c in calls} == {t1.id, t2.id}
        for _tid, src, npts, mv in calls:
            assert src == "engram"
            assert npts == STRICT_SCAN_POINTS
            assert mv == STRICT_MIN_VOTES

    async def test_skips_titles_whose_staging_file_is_missing(self, client, monkeypatch):
        """A missing staging file for one claimant must not fail the whole batch."""
        job = await _seed_job()
        t1 = await _seed_title(job.id, 0, "S01E05")  # file present
        t2 = await _seed_title(job.id, 1, "S01E05")  # file missing → ValueError

        from app.services.job_manager import job_manager

        async def fake_rematch_single(job_id, title_id, source_preference=None, **kw):
            if title_id == t2.id:
                raise ValueError("Staging file not found")

        monkeypatch.setattr(job_manager._matching, "rematch_single_title", fake_rematch_single)

        resp = await client.post(
            f"/api/jobs/{job.id}/rematch-conflict", json={"episode_code": "S01E05"}
        )

        assert resp.status_code == 200
        body = resp.json()
        # Only the title whose file existed was re-matched...
        assert body["title_ids"] == [t1.id]
        # ...and the skipped one is reported with a reason.
        assert [s["title_id"] for s in body["skipped"]] == [t2.id]
        assert "not found" in body["skipped"][0]["reason"].lower()

    async def test_non_value_error_is_skipped_not_raised(self, client, monkeypatch):
        """A non-ValueError (e.g. transient DB/IO error) must be reported as
        skipped, not escape the loop — otherwise the auto-escalation caller never
        records its pass and re-dispatches the same depth indefinitely."""
        job = await _seed_job()
        t1 = await _seed_title(job.id, 0, "S01E05")
        t2 = await _seed_title(job.id, 1, "S01E05")

        from app.services.job_manager import job_manager

        async def fake_rematch_single(job_id, title_id, source_preference=None, **kw):
            if title_id == t2.id:
                raise RuntimeError("db lock timeout")

        monkeypatch.setattr(job_manager._matching, "rematch_single_title", fake_rematch_single)

        resp = await client.post(
            f"/api/jobs/{job.id}/rematch-conflict", json={"episode_code": "S01E05"}
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["title_ids"] == [t1.id]
        assert [s["title_id"] for s in body["skipped"]] == [t2.id]
        assert "lock timeout" in body["skipped"][0]["reason"].lower()

    async def test_404_when_no_title_claims_the_episode(self, client, monkeypatch):
        job = await _seed_job()
        await _seed_title(job.id, 0, "S01E01")

        from app.services.job_manager import job_manager

        async def fake_rematch_single(*a, **k):
            raise AssertionError("should not be called when there is no conflict")

        monkeypatch.setattr(job_manager._matching, "rematch_single_title", fake_rematch_single)

        resp = await client.post(
            f"/api/jobs/{job.id}/rematch-conflict", json={"episode_code": "S09E09"}
        )
        assert resp.status_code == 404


@pytest.mark.unit
class TestRematchConflictCombinedCodes:
    """The contested episode can sit inside a combined code ("S01E01-E03").

    The inspector sends the single contested episode, so selecting claimants by
    exact string equality missed the combined side of the very collision it had
    just surfaced, and two overlapping combined codes dispatched nothing at all.
    """

    async def _stub_rematch(self, monkeypatch) -> list[int]:
        from app.services.job_manager import job_manager

        dispatched: list[int] = []

        async def fake_rematch_single(job_id, title_id, source_preference=None, **kw):
            dispatched.append(title_id)

        monkeypatch.setattr(job_manager._matching, "rematch_single_title", fake_rematch_single)
        return dispatched

    async def test_a_combined_claimant_is_rematched(self, client, monkeypatch):
        job = await _seed_job()
        combined = await _seed_title(job.id, 0, "S01E01-E03")
        sibling = await _seed_title(job.id, 1, "S01E02")
        await _seed_title(job.id, 2, "S01E05")  # not in the conflict
        dispatched = await self._stub_rematch(monkeypatch)

        resp = await client.post(
            f"/api/jobs/{job.id}/rematch-conflict", json={"episode_code": "S01E02"}
        )

        assert resp.status_code == 200
        assert set(resp.json()["title_ids"]) == {combined.id, sibling.id}
        assert set(dispatched) == {combined.id, sibling.id}

    async def test_two_overlapping_combined_codes_both_rematch(self, client, monkeypatch):
        # Neither code equals the contested episode, so exact matching dispatched
        # nothing and returned a 200 that looked like success.
        job = await _seed_job()
        first = await _seed_title(job.id, 0, "S01E01-E02")
        second = await _seed_title(job.id, 1, "S01E02-E03")
        dispatched = await self._stub_rematch(monkeypatch)

        resp = await client.post(
            f"/api/jobs/{job.id}/rematch-conflict", json={"episode_code": "S01E02"}
        )

        assert resp.status_code == 200
        assert set(resp.json()["title_ids"]) == {first.id, second.id}
        assert set(dispatched) == {first.id, second.id}

    async def test_a_track_parked_as_non_rematchable_is_left_alone(self, client, monkeypatch):
        """A combined track parked in REVIEW carries the multi-episode code, which a
        denser matcher pass cannot resolve. Re-matching it would also throw away the
        pre-filled assignment a person is being asked to confirm.
        """
        import json as _json

        job = await _seed_job()
        await _seed_title(
            job.id,
            0,
            "S01E01-E03",
            state=TitleState.REVIEW,
            match_details=_json.dumps({"error": "multi_episode_detected", "message": "x"}),
        )
        sibling = await _seed_title(job.id, 1, "S01E02")
        dispatched = await self._stub_rematch(monkeypatch)

        resp = await client.post(
            f"/api/jobs/{job.id}/rematch-conflict", json={"episode_code": "S01E02"}
        )

        assert resp.status_code == 200
        assert resp.json()["title_ids"] == [sibling.id]
        assert dispatched == [sibling.id]
