"""Unit tests for MatchingCoordinator's extras policy and rematch source routing.

The audio matcher / subtitle pipeline are not exercised here; these tests target
the branchy decision logic: _handle_extras' skip/ask/keep policies and
rematch_single_title's discdb-vs-engram routing. DB + websocket + organizer are
stubbed.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.job_state_machine import JobStateMachine
from app.services.matching_coordinator import MatchingCoordinator
from tests.unit.conftest import _unit_session_factory


@pytest.fixture(autouse=True)
def _patch_session_and_ws(monkeypatch):
    monkeypatch.setattr("app.services.matching_coordinator.async_session", _unit_session_factory)

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ws_manager, "broadcast_title_update", _noop)


def _make_coord() -> MatchingCoordinator:
    broadcaster = MagicMock()
    broadcaster.broadcast_job_completed = AsyncMock()
    broadcaster.broadcast_job_failed = AsyncMock()
    broadcaster.broadcast_job_state_changed = AsyncMock()
    coord = MatchingCoordinator(broadcaster, JobStateMachine(broadcaster))
    coord._check_job_completion = AsyncMock()
    return coord


async def _seed(session, **title_kwargs):
    job = DiscJob(
        drive_id="E:",
        volume_label="SHOW_S1D1",
        content_type=ContentType.TV,
        state=JobState.MATCHING,
        detected_title="Some Show",
        detected_season=1,
        disc_number=1,
        staging_path="/tmp/staging",
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    defaults = dict(job_id=job.id, title_index=0, duration_seconds=600, state=TitleState.MATCHING)
    defaults.update(title_kwargs)
    title = DiscTitle(**defaults)
    session.add(title)
    await session.commit()
    await session.refresh(title)
    return job, title


def _patch_config(monkeypatch, policy: str):
    monkeypatch.setattr(
        "app.services.config_service.get_config",
        AsyncMock(return_value=SimpleNamespace(extras_policy=policy)),
    )


@pytest.mark.unit
class TestHandleExtras:
    async def test_skip_policy_completes_and_discards(self, monkeypatch, tmp_path):
        _patch_config(monkeypatch, "skip")
        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await _seed(session)
            handled = await coord._handle_extras(
                job.id, title.id, title, job, tmp_path / "x.mkv", 10.0, [22, 44], session
            )
            assert handled is True
            assert title.state == TitleState.COMPLETED
            assert title.is_extra is True
            assert json.loads(title.match_details)["action"] == "skipped"
        coord._check_job_completion.assert_awaited_once()

    async def test_ask_policy_sends_to_review(self, monkeypatch, tmp_path):
        _patch_config(monkeypatch, "ask")
        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await _seed(session)
            handled = await coord._handle_extras(
                job.id, title.id, title, job, tmp_path / "x.mkv", 10.0, [22, 44], session
            )
            assert handled is True
            assert title.state == TitleState.REVIEW
            assert title.is_extra is True
            assert json.loads(title.match_details)["action"] == "review"

    async def test_keep_policy_organizes_to_extras(self, monkeypatch, tmp_path):
        _patch_config(monkeypatch, "keep")
        import app.core.organizer as org

        monkeypatch.setattr(
            org,
            "organize_tv_extras",
            Mock(return_value={"success": True, "final_path": "/lib/tv/Show/Extras/x.mkv"}),
        )
        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await _seed(session)
            handled = await coord._handle_extras(
                job.id, title.id, title, job, tmp_path / "x.mkv", 10.0, [22, 44], session
            )
            assert handled is True
            assert title.state == TitleState.COMPLETED
            assert title.is_extra is True
            assert title.organized_to == "/lib/tv/Show/Extras/x.mkv"
            assert json.loads(title.match_details)["action"] == "kept"

    async def test_keep_policy_records_organize_error(self, monkeypatch, tmp_path):
        _patch_config(monkeypatch, "keep")
        import app.core.organizer as org

        monkeypatch.setattr(
            org,
            "organize_tv_extras",
            Mock(return_value={"success": False, "error": "boom"}),
        )
        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await _seed(session)
            await coord._handle_extras(
                job.id, title.id, title, job, tmp_path / "x.mkv", 10.0, [22, 44], session
            )
            assert title.state == TitleState.COMPLETED
            # The title IS an extra — the duration pre-filter classified it as one;
            # the move just failed (e.g. destination already exists from a previous
            # rip). The UI should still show it as EXTRA, not as a vanilla completed
            # track. Without this flag, the chip silently disappears.
            assert title.is_extra is True
            assert json.loads(title.match_details)["organize_error"] == "boom"


@pytest.mark.unit
class TestRematchSingleTitle:
    async def test_discdb_restores_from_stored_details(self):
        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await _seed(
                session,
                discdb_match_details=json.dumps({"matched_episode": "S01E03"}),
            )
            job_id, title_id = job.id, title.id

        await coord.rematch_single_title(job_id, title_id, source_preference="discdb")

        async with _unit_session_factory() as session:
            t = await session.get(DiscTitle, title_id)
            assert t.state == TitleState.MATCHED
            assert t.matched_episode == "S01E03"
            assert t.match_source == "discdb"
            assert t.match_confidence == 0.99

    async def test_discdb_falls_back_to_in_memory_mappings(self):
        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await _seed(
                session,
                title_index=2,
                discdb_match_details=json.dumps({"some": "data"}),  # no matched_episode
            )
            job_id, title_id = job.id, title.id

        coord.set_discdb_mappings(job_id, [SimpleNamespace(index=2, season=1, episode=7)])

        await coord.rematch_single_title(job_id, title_id, source_preference="discdb")

        async with _unit_session_factory() as session:
            t = await session.get(DiscTitle, title_id)
            assert t.matched_episode == "S01E07"
            assert t.state == TitleState.MATCHED

    async def test_engram_resets_and_dispatches_match(self, tmp_path):
        coord = _make_coord()
        dispatched: dict = {}

        async def fake_match(job_id, title_id, file_path, num_points=None, min_vote_count=None):
            dispatched["args"] = (job_id, title_id, file_path)

        coord.match_single_file = fake_match
        coord.on_match_task_done = lambda *a, **k: None

        f = tmp_path / "show_t00.mkv"
        f.write_text("")
        async with _unit_session_factory() as session:
            job, title = await _seed(
                session,
                output_filename=str(f),
                state=TitleState.MATCHED,
                matched_episode="S01E01",
            )
            job.staging_path = str(tmp_path)
            session.add(job)
            await session.commit()
            job_id, title_id = job.id, title.id

        await coord.rematch_single_title(job_id, title_id, source_preference="engram")
        await asyncio.sleep(0)  # let the dispatched task run

        async with _unit_session_factory() as session:
            t = await session.get(DiscTitle, title_id)
            assert t.state == TitleState.MATCHING
            assert t.matched_episode is None
            assert t.match_source is None
        assert dispatched["args"][1] == title_id

    async def test_engram_missing_file_raises(self, tmp_path):
        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await _seed(session)
            job.staging_path = str(tmp_path)  # empty dir
            session.add(job)
            await session.commit()
            job_id, title_id = job.id, title.id

        with pytest.raises(ValueError, match="Staging file not found"):
            await coord.rematch_single_title(job_id, title_id, source_preference="engram")

    async def test_unknown_title_raises(self):
        coord = _make_coord()
        with pytest.raises(ValueError):
            await coord.rematch_single_title(9999, 9999, source_preference="discdb")


@pytest.mark.unit
class TestDownloadSubtitlesMessaging:
    """When a show has no reference subtitles anywhere, the actionable detail
    lives on the dedicated subtitle_error_message field (not the catch-all
    error_message, which other failure paths also write to).
    """

    def _mock(self, monkeypatch, episodes):
        async def _noop(*a, **k):
            return None

        monkeypatch.setattr(ws_manager, "broadcast_subtitle_event", _noop)
        monkeypatch.setattr(
            "app.matcher.testing_service.download_subtitles",
            lambda show, season, tmdb_id=None: {"episodes": episodes, "show_name": show},
        )

    async def test_no_subtitles_sets_actionable_show_specific_message(self, monkeypatch):
        coord = _make_coord()
        self._mock(monkeypatch, [{"status": "not_found"}, {"status": "not_found"}])

        async with _unit_session_factory() as session:
            job, _title = await _seed(session)
            job_id = job.id

        await coord.download_subtitles(job_id, "The Osbournes", 1)

        async with _unit_session_factory() as session:
            refreshed = await session.get(DiscJob, job_id)
            assert refreshed.subtitle_status == "failed"
            msg = refreshed.subtitle_error_message or ""
            assert "The Osbournes" in msg
            assert "manually" in msg.lower()
            # The catch-all field must stay clean so it can't leak into other banners.
            assert refreshed.error_message is None

    async def test_partial_download_clears_stale_subtitle_error(self, monkeypatch):
        coord = _make_coord()
        self._mock(monkeypatch, [{"status": "downloaded"}, {"status": "not_found"}])

        async with _unit_session_factory() as session:
            job, _title = await _seed(session)
            job.subtitle_error_message = "stale message from a prior attempt"
            session.add(job)
            await session.commit()
            job_id = job.id

        await coord.download_subtitles(job_id, "The Osbournes", 1)

        async with _unit_session_factory() as session:
            refreshed = await session.get(DiscJob, job_id)
            assert refreshed.subtitle_status == "partial"
            assert refreshed.subtitle_error_message is None


@pytest.mark.unit
class TestDownloadSubtitlesAllSeasons:
    """Unknown-season import downloads references for every candidate season and
    aggregates them into a single subtitle_status / ready event so the existing
    matching gate works unchanged.
    """

    def _mock(self, monkeypatch, per_season):
        async def _noop(*a, **k):
            return None

        monkeypatch.setattr(ws_manager, "broadcast_subtitle_event", _noop)
        monkeypatch.setattr(
            "app.matcher.testing_service.download_subtitles",
            lambda show, season, tmdb_id=None: {
                "episodes": per_season.get(season, []),
                "show_name": show,
            },
        )

    async def test_completes_when_any_season_has_references(self, monkeypatch):
        coord = _make_coord()
        self._mock(
            monkeypatch,
            {
                1: [{"status": "not_found"}],
                2: [{"status": "downloaded"}, {"status": "cached"}],
                3: [{"status": "precomputed"}],
            },
        )
        async with _unit_session_factory() as session:
            job, _t = await _seed(session)
            job_id = job.id
        coord._subtitle_ready[job_id] = asyncio.Event()

        await coord.download_subtitles_all_seasons(job_id, "The Expanse", [1, 2, 3])

        async with _unit_session_factory() as session:
            refreshed = await session.get(DiscJob, job_id)
            assert refreshed.subtitle_status == "completed"
            assert refreshed.subtitle_error_message is None
        # The ready event must be set so the matching gate unblocks.
        assert coord._subtitle_ready[job_id].is_set()

    async def test_fails_when_no_season_has_references(self, monkeypatch):
        coord = _make_coord()
        self._mock(
            monkeypatch,
            {1: [{"status": "not_found"}], 2: [{"status": "failed"}]},
        )
        async with _unit_session_factory() as session:
            job, _t = await _seed(session)
            job_id = job.id
        coord._subtitle_ready[job_id] = asyncio.Event()

        await coord.download_subtitles_all_seasons(job_id, "Obscure Show", [1, 2])

        async with _unit_session_factory() as session:
            refreshed = await session.get(DiscJob, job_id)
            assert refreshed.subtitle_status == "failed"
            assert "Obscure Show" in (refreshed.subtitle_error_message or "")
            assert refreshed.error_message is None
        # The ready event must be set so the matching gate unblocks.
        assert coord._subtitle_ready[job_id].is_set()

    async def test_sets_subtitle_ready_event(self, monkeypatch):
        coord = _make_coord()
        self._mock(monkeypatch, {1: [{"status": "downloaded"}]})
        async with _unit_session_factory() as session:
            job, _t = await _seed(session)
            job_id = job.id
        coord._subtitle_ready[job_id] = asyncio.Event()

        await coord.download_subtitles_all_seasons(job_id, "Show", [1])

        assert coord._subtitle_ready[job_id].is_set()
