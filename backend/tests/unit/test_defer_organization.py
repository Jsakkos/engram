"""Organization must be deferred until the ENTIRE disc is resolved.

Regression guard for eager per-title organization: when any title still needs
review (including a conflict loser created during finalization), the disc must
not be organized at all — files stay in staging until the whole disc is clean.
"""

import json

import pytest

from app.models import DiscJob, DiscTitle
from app.models.disc_job import ContentType, JobState, TitleState
from tests.unit.conftest import _unit_session_factory


async def _seed_job() -> DiscJob:
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="F:",
            volume_label="SHOW_S1D2",
            content_type=ContentType.TV,
            state=JobState.MATCHING,
            detected_title="Some Show",
            detected_season=1,
            staging_path="/tmp/staging/job_x",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


async def _seed_title(job_id, index, ep, conf, votes, runner_ups=None, output=None) -> DiscTitle:
    async with _unit_session_factory() as session:
        t = DiscTitle(
            job_id=job_id,
            title_index=index,
            duration_seconds=1380,
            file_size_bytes=1_000_000_000,
            matched_episode=ep,
            match_confidence=conf,
            state=TitleState.MATCHED,
            output_filename=output,
            match_details=json.dumps(
                {
                    "score": conf,
                    "vote_count": votes,
                    "file_cov": conf,
                    "runner_ups": runner_ups or [],
                }
            ),
        )
        session.add(t)
        await session.commit()
        await session.refresh(t)
        return t


@pytest.fixture(autouse=True)
def _patch_session(monkeypatch):
    monkeypatch.setattr(
        "app.services.finalization_coordinator.async_session", _unit_session_factory
    )


@pytest.mark.unit
class TestDeferOrganization:
    async def test_does_not_organize_while_a_title_needs_review(self, monkeypatch, tmp_path):
        """A conflict whose loser has no runner-up → REVIEW → organize nothing."""
        wfile = tmp_path / "winner.mkv"
        wfile.write_bytes(b"x")  # winner has a real file, so organize WOULD run without the fix
        job = await _seed_job()
        winner = await _seed_title(job.id, 7, "S01E14", 0.9, votes=9, output=str(wfile))
        loser = await _seed_title(job.id, 8, "S01E14", 0.5, votes=4)  # no runner_ups → REVIEW

        from app.services.job_manager import job_manager

        calls: list = []
        monkeypatch.setattr(
            "app.core.organizer.tv_organizer.organize",
            lambda *a, **k: calls.append(a) or {"success": True, "final_path": "/lib/x.mkv"},
        )

        await job_manager._finalization.finalize_disc_job(job.id)

        # Nothing organized while a title still needs review.
        assert calls == []
        async with _unit_session_factory() as s:
            j = await s.get(DiscJob, job.id)
            w = await s.get(DiscTitle, winner.id)
            ll = await s.get(DiscTitle, loser.id)
            assert j.state == JobState.REVIEW_NEEDED
            assert w.state == TitleState.MATCHED  # winner held, NOT organized
            assert w.organized_to is None
            assert ll.state == TitleState.REVIEW

    async def test_organizes_everything_when_disc_is_clean(self, monkeypatch, tmp_path):
        """No conflicts / no review → organize all in one pass → COMPLETED."""
        f0 = tmp_path / "t00.mkv"
        f1 = tmp_path / "t01.mkv"
        f0.write_bytes(b"x")
        f1.write_bytes(b"y")
        job = await _seed_job()
        await _seed_title(job.id, 0, "S01E01", 0.9, votes=9, output=str(f0))
        await _seed_title(job.id, 1, "S01E02", 0.9, votes=9, output=str(f1))

        from app.services.job_manager import job_manager

        calls: list = []
        monkeypatch.setattr(
            "app.core.organizer.tv_organizer.organize",
            lambda *a, **k: calls.append(a) or {"success": True, "final_path": "/lib/x.mkv"},
        )

        await job_manager._finalization.finalize_disc_job(job.id)

        assert len(calls) == 2  # both organized
        async with _unit_session_factory() as s:
            j = await s.get(DiscJob, job.id)
            assert j.state == JobState.COMPLETED
