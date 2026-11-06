"""Re-identify must hand the namespace decision a usable TV identity (#862).

Job 120 (Justice League S1, label DISC_1) was corrected by typing the title
without picking a search result. ``re_identify`` then ran ``classify_from_tmdb``
without the user's content type, and TMDB's 2017 *movie* "Justice League"
(popularity 14.6) edged out the 2001 series (14.5). The TV job was bound to a
movie id, every ``/tv/{id}`` lookup 404'd, and ``decide_job_namespace`` could
never link the show to TheTVDB, so no suggestion was ever offered.
"""

import json
from unittest.mock import AsyncMock

import pytest

from app.api.websocket import manager as ws_manager
from app.core.tmdb_classifier import TmdbSignal
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType
from app.services.identification_coordinator import IdentificationCoordinator
from tests.unit.conftest import _unit_session_factory

_JL_SERIES = TmdbSignal(
    content_type=ContentType.TV, confidence=0.7, tmdb_id=1618, tmdb_name="Justice League"
)
_JL_MOVIE = TmdbSignal(
    content_type=ContentType.MOVIE, confidence=0.6, tmdb_id=141052, tmdb_name="Justice League"
)


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

    class _Config:
        tmdb_api_key = "eyJ-test"

    async def fake_get_config():
        return _Config()

    monkeypatch.setattr("app.services.config_service.get_config", fake_get_config)


@pytest.fixture
def classify(monkeypatch):
    """Record classify_from_tmdb calls; answer like live TMDB does for the title."""
    calls = []

    def fake(name, api_key, timeout=5.0, prefer_content_type=None):
        calls.append(prefer_content_type)
        return _JL_SERIES if prefer_content_type == ContentType.TV else _JL_MOVIE

    monkeypatch.setattr("app.core.tmdb_classifier.classify_from_tmdb", fake)
    return calls


def _coord() -> IdentificationCoordinator:
    coord = IdentificationCoordinator.__new__(IdentificationCoordinator)
    coord._restart_subtitle_download = AsyncMock()
    coord._cancel_subtitle_download = AsyncMock()
    coord._start_tv_subtitle_prefetch = AsyncMock()
    return coord


async def _seed(tmp_path, **kw) -> int:
    (tmp_path / "title_t00.mkv").write_text("")
    fields = dict(
        drive_id="E:",
        volume_label="DISC_1",
        content_type=ContentType.TV,
        state=JobState.REVIEW_NEEDED,
        staging_path=str(tmp_path),
    )
    fields.update(kw)
    async with _unit_session_factory() as session:
        job = DiscJob(**fields)
        session.add(job)
        await session.commit()
        return job.id


async def _get(job_id) -> DiscJob:
    async with _unit_session_factory() as session:
        return await session.get(DiscJob, job_id)


@pytest.mark.unit
class TestReIdentifyLookupHonoursContentType:
    async def test_typed_tv_title_binds_the_series_not_the_same_named_movie(
        self, tmp_path, classify
    ):
        job_id = await _seed(tmp_path)
        coord = _coord()

        await coord.re_identify(job_id, "Justice League", "tv", season=1)

        assert classify == [ContentType.TV]
        job = await _get(job_id)
        assert job.tmdb_id == 1618
        # The namespace decision runs inside the restarted download, for the series.
        coord._restart_subtitle_download.assert_awaited_once_with(job_id, "Justice League", 1, 1618)

    async def test_a_lookup_of_the_other_type_is_never_bound(self, tmp_path, monkeypatch):
        """A TV correction with only a movie hit must not take the movie's id."""

        def movie_only(name, api_key, timeout=5.0, prefer_content_type=None):
            return _JL_MOVIE

        monkeypatch.setattr("app.core.tmdb_classifier.classify_from_tmdb", movie_only)
        job_id = await _seed(tmp_path, tmdb_id=None)

        await _coord().re_identify(job_id, "Justice League", "tv", season=1)

        assert (await _get(job_id)).tmdb_id is None

    async def test_movie_correction_still_looks_up_without_a_tv_preference(
        self, tmp_path, classify
    ):
        job_id = await _seed(tmp_path)

        await _coord().re_identify(job_id, "Justice League", "movie")

        assert classify == [ContentType.MOVIE]
        assert (await _get(job_id)).tmdb_id == 141052


@pytest.mark.unit
class TestReIdentifyResetsNumberingDecision:
    async def test_identity_change_drops_the_previous_shows_numbering(self, tmp_path):
        job_id = await _seed(
            tmp_path,
            tmdb_id=141052,
            detected_title="Justice League",
            detected_season=1,
            episode_namespace="tvdb",
            episode_crosswalk_json=json.dumps({"S01E04": "S01E02"}),
            tvdb_divergence_json=json.dumps({"season": 1, "tmdb": 24, "tvdb": 26}),
            episode_namespace_note="TheTVDB unavailable; matched with TMDB numbering",
        )

        await _coord().re_identify(job_id, "Justice League", "tv", season=1, tmdb_id=1618)

        job = await _get(job_id)
        assert job.episode_namespace == "tmdb"
        assert job.episode_crosswalk_json is None
        assert job.tvdb_divergence_json is None
        assert job.episode_namespace_note is None

    async def test_same_identity_keeps_the_numbering(self, tmp_path):
        divergence = json.dumps({"season": 1, "tmdb": 24, "tvdb": 26})
        job_id = await _seed(
            tmp_path,
            tmdb_id=1618,
            detected_title="Justice League",
            detected_season=1,
            tvdb_divergence_json=divergence,
        )

        await _coord().re_identify(job_id, "Justice League", "tv", season=1, tmdb_id=1618)

        assert (await _get(job_id)).tvdb_divergence_json == divergence
