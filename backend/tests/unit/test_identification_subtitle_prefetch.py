"""TV subtitle prefetch + unknown-season handling (#370).

A disc labeled by disc number only ("Eureka D3") identifies the show but not
the season. The disc path used to gate subtitle download on detected_season,
silently skipping it — zero reference subtitles, every title failed matching
at confidence 0, and the whole disc dead-ended in review. v2 design: the job
parks in REVIEW_NEEDED for a season pick; the shared _start_tv_subtitle_prefetch
helper covers the season-known (single download) and season-unknown ("match
across all seasons" escape hatch) resume paths, keyed by the job's tmdb_id.
"""

from unittest.mock import MagicMock, Mock

import pytest

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType
from app.services.identification_coordinator import IdentificationCoordinator
from tests.unit.conftest import _unit_session_factory


def _coord():
    """Bare coordinator: __new__ skips the heavyweight __init__ wiring."""
    coord = IdentificationCoordinator.__new__(IdentificationCoordinator)
    coord._start_subtitle_download = Mock()
    coord._start_subtitle_download_all_seasons = Mock()
    return coord


def _tv_job(season):
    job = DiscJob(
        drive_id="D:",
        volume_label="EUREKA_D3",
        content_type=ContentType.TV,
        detected_title="Eureka",
        detected_season=season,
        tmdb_id=4620,
    )
    job.id = 7
    return job


@pytest.mark.unit
class TestStartTvSubtitlePrefetch:
    async def test_known_season_downloads_that_season_only(self):
        coord = _coord()

        await coord._start_tv_subtitle_prefetch(_tv_job(season=2))

        coord._start_subtitle_download.assert_called_once_with(7, "Eureka", 2, 4620)
        coord._start_subtitle_download_all_seasons.assert_not_called()

    async def test_unknown_season_prefetches_all_seasons_by_tmdb_id(self):
        coord = _coord()
        captured = {}

        async def fake_resolve(title, tmdb_id=None):
            captured["args"] = (title, tmdb_id)
            return [1, 2, 3, 4, 5]

        coord._resolve_all_season_numbers = fake_resolve

        await coord._start_tv_subtitle_prefetch(_tv_job(season=None))

        assert captured["args"] == ("Eureka", 4620)
        coord._start_subtitle_download_all_seasons.assert_called_once_with(
            7, "Eureka", [1, 2, 3, 4, 5], tmdb_id=4620
        )
        coord._start_subtitle_download.assert_not_called()

    async def test_unknown_season_unresolvable_show_starts_nothing(self):
        coord = _coord()

        async def fake_resolve(title, tmdb_id=None):
            return []

        coord._resolve_all_season_numbers = fake_resolve

        await coord._start_tv_subtitle_prefetch(_tv_job(season=None))

        coord._start_subtitle_download.assert_not_called()
        coord._start_subtitle_download_all_seasons.assert_not_called()


@pytest.mark.unit
class TestResolveAllSeasonNumbersTmdbId:
    async def test_uses_tmdb_id_directly_when_known(self, monkeypatch):
        """With the job's tmdb_id in hand, never re-resolve by name — that picks
        the dominant same-name twin (the Frasier-class bug)."""
        coord = IdentificationCoordinator.__new__(IdentificationCoordinator)
        fetch_id = MagicMock(
            side_effect=AssertionError("must not name-resolve when tmdb_id is known")
        )
        seen = {}

        def fake_count(show_id):
            seen["show_id"] = show_id
            return 5

        monkeypatch.setattr("app.matcher.tmdb_client.fetch_show_id", fetch_id)
        monkeypatch.setattr("app.matcher.tmdb_client.get_number_of_seasons", fake_count)

        seasons = await coord._resolve_all_season_numbers("Eureka", tmdb_id=4620)

        assert seasons == [1, 2, 3, 4, 5]
        assert seen["show_id"] == "4620"
        fetch_id.assert_not_called()

    async def test_falls_back_to_name_resolution_without_tmdb_id(self, monkeypatch):
        coord = IdentificationCoordinator.__new__(IdentificationCoordinator)
        monkeypatch.setattr("app.matcher.tmdb_client.fetch_show_id", lambda title: "4620")
        monkeypatch.setattr("app.matcher.tmdb_client.get_number_of_seasons", lambda sid: 3)

        seasons = await coord._resolve_all_season_numbers("Eureka")

        assert seasons == [1, 2, 3]


@pytest.mark.unit
class TestSetNameAndResumeStartsSubtitles:
    """set_name_and_resume never started a subtitle download (pre-existing gap,
    masked by locally-cached references). The season-prompt modal resumes through
    this path, so it must kick the prefetch — single-season for a picked season,
    all-seasons for the "match across all seasons" choice (season=None)."""

    @pytest.fixture(autouse=True)
    def _patch_session_and_ws(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.identification_coordinator.async_session", _unit_session_factory
        )

        async def _noop(*a, **k):
            return None

        monkeypatch.setattr(ws_manager, "broadcast_job_update", _noop)

    async def _seed_review_job(self):
        async with _unit_session_factory() as session:
            job = DiscJob(
                drive_id="D:",
                volume_label="EUREKA_D3",
                content_type=ContentType.TV,
                state=JobState.REVIEW_NEEDED,
                detected_title="Eureka",
                tmdb_id=4620,
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            return job.id

    def _resumable_coord(self, prefetch_calls):
        coord = IdentificationCoordinator.__new__(IdentificationCoordinator)

        async def fake_resolve_tmdb(job):
            return None

        async def fake_prefetch(job):
            prefetch_calls.append((job.id, job.detected_season))

        coord._resolve_missing_tmdb_id = fake_resolve_tmdb
        coord._start_tv_subtitle_prefetch = fake_prefetch
        return coord

    async def test_picked_season_resumes_with_single_season_prefetch(self):
        job_id = await self._seed_review_job()
        prefetch_calls = []
        coord = self._resumable_coord(prefetch_calls)

        await coord.set_name_and_resume(job_id, "Eureka", "tv", season=3)

        assert prefetch_calls == [(job_id, 3)]
        async with _unit_session_factory() as session:
            job = await session.get(DiscJob, job_id)
            assert job.state == JobState.RIPPING
            assert job.detected_season == 3
            assert job.review_reason is None

    async def test_all_seasons_choice_resumes_with_unknown_season(self):
        job_id = await self._seed_review_job()
        prefetch_calls = []
        coord = self._resumable_coord(prefetch_calls)

        await coord.set_name_and_resume(job_id, "Eureka", "tv", season=None)

        # detected_season stays None -> the helper does the all-seasons prefetch.
        assert prefetch_calls == [(job_id, None)]

    async def test_movie_resume_does_not_prefetch(self):
        job_id = await self._seed_review_job()
        prefetch_calls = []
        coord = self._resumable_coord(prefetch_calls)

        await coord.set_name_and_resume(job_id, "Inception", "movie")

        assert prefetch_calls == []
