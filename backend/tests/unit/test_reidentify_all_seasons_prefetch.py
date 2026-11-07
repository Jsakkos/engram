"""A review-time re-identify with no season must still fetch references.

``re_identify`` restarted the subtitle download only when the job had a
season. A TV disc re-identified from review with the season left blank
("match across all seasons") therefore started no reference download at all:
the stale event and ``subtitle_status`` from the old identity were left in
place, and matching ran against nothing. ``set_name_and_resume`` and the
mid-rip re-identify branch already fall back to the all-seasons prefetch
(#370); the review paths now do the same.
"""

from unittest.mock import AsyncMock

import pytest

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType
from app.services.identification_coordinator import IdentificationCoordinator
from tests.unit.conftest import _unit_session_factory


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(
        "app.services.identification_coordinator.async_session", _unit_session_factory
    )
    monkeypatch.setattr(
        "app.services.identification_coordinator._resolve_year", lambda *a, **k: None
    )

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ws_manager, "broadcast_job_update", _noop)


def _coord(calls: list) -> IdentificationCoordinator:
    """Bare coordinator whose subtitle hooks record their order into ``calls``."""
    coord = IdentificationCoordinator.__new__(IdentificationCoordinator)

    async def restart(job_id, title, season, tmdb_id):
        calls.append(("restart", job_id, title, season, tmdb_id))

    async def cancel(job_id):
        calls.append(("cancel", job_id))

    async def prefetch(job):
        calls.append(("prefetch", job.id, job.detected_title, job.detected_season))

    async def next_state(drive_id):
        return JobState.RIPPING

    coord._restart_subtitle_download = AsyncMock(side_effect=restart)
    coord._cancel_subtitle_download = AsyncMock(side_effect=cancel)
    coord._start_tv_subtitle_prefetch = prefetch
    coord._next_state_after_identify = next_state
    return coord


async def _seed(staging, **kw) -> int:
    fields = dict(
        drive_id="E:",
        volume_label="DISC_1",
        content_type=ContentType.TV,
        state=JobState.REVIEW_NEEDED,
        staging_path=str(staging),
        detected_title="Wrong Show",
    )
    fields.update(kw)
    async with _unit_session_factory() as session:
        job = DiscJob(**fields)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


@pytest.mark.unit
class TestReIdentifySubtitleRefresh:
    async def test_post_rip_tv_without_season_prefetches_all_seasons(self, tmp_path):
        (tmp_path / "title_t00.mkv").write_text("")
        job_id = await _seed(tmp_path)
        calls: list = []

        result = await _coord(calls).re_identify(job_id, "Frasier", "tv", tmdb_id=195241)

        assert result["resume_action"] == "rerun_matching"
        # Cancel the old identity's download first, then prefetch every season.
        assert calls == [("cancel", job_id), ("prefetch", job_id, "Frasier", None)]

    async def test_pre_rip_tv_without_season_prefetches_all_seasons(self, tmp_path):
        job_id = await _seed(tmp_path)
        calls: list = []

        result = await _coord(calls).re_identify(job_id, "Frasier", "tv", tmdb_id=195241)

        assert result["resume_action"] == "start_rip"
        assert calls == [("cancel", job_id), ("prefetch", job_id, "Frasier", None)]

    async def test_known_season_keeps_the_restart_path(self, tmp_path):
        (tmp_path / "title_t00.mkv").write_text("")
        job_id = await _seed(tmp_path)
        calls: list = []

        await _coord(calls).re_identify(job_id, "Frasier", "tv", season=2, tmdb_id=195241)

        assert calls == [("restart", job_id, "Frasier", 2, 195241)]

    async def test_movie_without_season_starts_no_subtitle_download(self, tmp_path):
        (tmp_path / "title_t00.mkv").write_text("")
        job_id = await _seed(tmp_path)
        calls: list = []

        await _coord(calls).re_identify(job_id, "Inception", "movie", tmdb_id=27205)

        assert calls == []
