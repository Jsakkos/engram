"""Unit tests for MatchingCoordinator's extras policy and rematch source routing.

The audio matcher / subtitle pipeline are not exercised here; these tests target
the branchy decision logic: _handle_extras' skip/ask/keep policies and
rematch_single_title's discdb-vs-engram routing. DB + websocket + organizer are
stubbed.
"""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.job_state_machine import JobStateMachine
from app.services.matching_coordinator import (
    MatchingCoordinator,
    _duration_matches_episode_runtime,
    episode_curator,
)
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

    async def test_keep_policy_threads_tmdb_id_and_year(self, monkeypatch, tmp_path):
        """When job carries tmdb_id + tmdb_year, organize_tv_extras must receive them
        as kwargs — otherwise match-time extras land in the bare show folder instead
        of the disambiguated one (e.g. "Frasier/..." vs "Frasier (1993) {tmdb-3452}/...").
        """
        _patch_config(monkeypatch, "keep")
        import app.core.organizer as org

        mock_org = Mock(return_value={"success": True, "final_path": "/lib/tv/Show/Extras/x.mkv"})
        monkeypatch.setattr(org, "organize_tv_extras", mock_org)
        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await _seed(session)
            # Simulate a job that has been identified with a specific TMDB entry.
            job.tmdb_id = 3452
            job.tmdb_year = 1993
            session.add(job)
            await session.commit()
            await session.refresh(job)
            await coord._handle_extras(
                job.id, title.id, title, job, tmp_path / "x.mkv", 10.0, [22, 44], session
            )

        mock_org.assert_called_once()
        kwargs = mock_org.call_args.kwargs
        assert kwargs["tmdb_id"] == "3452", (
            f"expected tmdb_id='3452', got {kwargs.get('tmdb_id')!r}"
        )
        assert kwargs["year"] == 1993, f"expected year=1993, got {kwargs.get('year')!r}"


# Real TMDB Season 1 runtimes from the job-41 bundle (max 44, min 39).
GILMORE_S1 = [44, 43, 43, 43, 42, 44, 44, 44, 44, 42, 44, 42, 39, 44, 43, 44, 44, 44, 42, 43, 43]


@pytest.mark.unit
class TestDurationMatchesEpisodeRuntime:
    """The duration pre-filter accepts a track as an episode candidate only if its
    runtime is close to a TMDB episode runtime. The window is ASYMMETRIC: DVD/BD
    tracks run longer than TMDB's nominal runtime (recap + full credits + "next
    time" preview), so we are tight below an episode runtime and lenient above it.

    Regression for bug report job 41 (Gilmore Girls S1 Disc 3): E09 "Rory's Dance"
    ran 2990s (49.8min) vs TMDB's 44min and the old symmetric ±5 window (ceiling
    49min) dumped it to Extras un-transcribed while its 46-48min siblings matched.
    """

    def test_long_episode_is_not_an_extra(self):
        # E09 "Rory's Dance": 2990s = 49.83min. Must clear the gate (the bug).
        assert _duration_matches_episode_runtime(2990 / 60, GILMORE_S1) is True

    def test_sibling_episodes_match(self):
        # E10/E11/E12 on the same disc (these matched correctly in the field).
        for seconds in (2896, 2866, 2763):
            assert _duration_matches_episode_runtime(seconds / 60, GILMORE_S1) is True

    def test_play_all_track_is_an_extra(self):
        # t00: 11515s = 191.9min — the concatenated "Play All" track stays an extra.
        assert _duration_matches_episode_runtime(11515 / 60, GILMORE_S1) is False

    def test_short_featurette_is_an_extra(self):
        # A 10-min track is well below any episode runtime → still an extra.
        assert _duration_matches_episode_runtime(10.0, [22, 44]) is False

    def test_upper_bound_is_inclusive(self):
        # Exactly runtime + 10 (over-tolerance) is accepted; just past it is not.
        assert _duration_matches_episode_runtime(54.0, [44]) is True
        assert _duration_matches_episode_runtime(54.01, [44]) is False

    def test_lower_bound_is_inclusive(self):
        # Exactly runtime - 5 (under-tolerance) is accepted; just under it is not.
        assert _duration_matches_episode_runtime(39.0, [44]) is True
        assert _duration_matches_episode_runtime(38.99, [44]) is False

    def test_empty_runtimes_never_matches(self):
        # No runtimes → the pre-filter caller skips entirely; the predicate is False.
        assert _duration_matches_episode_runtime(45.0, []) is False


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

        async def fake_match(
            job_id, title_id, file_path, num_points=None, min_vote_count=None, advisory=False
        ):
            dispatched["args"] = (job_id, title_id, file_path)
            dispatched["advisory"] = advisory

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
        # A direct coordinator call (the pipeline/escalation path) is NOT advisory —
        # only JobManager.rematch_single_title (the manual route) opts in.
        assert dispatched["advisory"] is False

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

    async def test_value_error_lands_on_subtitle_field_not_catchall(self, monkeypatch):
        """A ValueError from the subtitle pipeline (e.g. show/season not on TMDB)
        must land on the clearable subtitle_error_message, NOT the catch-all
        error_message — otherwise it survives a later successful re-download as a
        stale banner (Mad Men S3 'O Hristos xanastavronetai' regression).
        """
        coord = _make_coord()

        async def _noop(*a, **k):
            return None

        monkeypatch.setattr(ws_manager, "broadcast_subtitle_event", _noop)

        def _raise(show, season, tmdb_id=None):
            raise ValueError(f"No episodes found for {show} Season {season} on TMDB")

        monkeypatch.setattr("app.matcher.testing_service.download_subtitles", _raise)

        async with _unit_session_factory() as session:
            job, _title = await _seed(session)
            job_id = job.id

        await coord.download_subtitles(job_id, "O Hristos xanastavronetai", 3)

        async with _unit_session_factory() as session:
            refreshed = await session.get(DiscJob, job_id)
            assert refreshed.subtitle_status == "failed"
            assert "O Hristos xanastavronetai" in (refreshed.subtitle_error_message or "")
            # The catch-all must stay clean so the banner clears on a later success.
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
class TestNoSubtitleAIFallback:
    """When subtitle download fails, the AI episode matcher (ASR transcript +
    TMDB synopsis) runs as a fallback — when enabled, keyed, and the season is
    known — attaching an llm_suggestion to the REVIEW title instead of leaving it
    a bare manual-assignment. Otherwise the existing manual-review path is kept.
    """

    def _patch_ai_config(self, monkeypatch, *, enabled: bool, key: str = "k"):
        monkeypatch.setattr(
            "app.services.config_service.get_config",
            AsyncMock(
                return_value=SimpleNamespace(ai_episode_matching_enabled=enabled, ai_api_key=key)
            ),
        )

    async def _seed_failed(self, session, **job_overrides):
        job, title = await _seed(session)
        job.subtitle_status = "failed"
        for k, v in job_overrides.items():
            setattr(job, k, v)
        session.add(job)
        await session.commit()
        return job, title

    async def test_runs_llm_fallback_and_attaches_suggestion(self, monkeypatch, tmp_path):
        self._patch_ai_config(monkeypatch, enabled=True)
        suggestion = {
            "llm_suggestion": {
                "episode": 9,
                "confidence": 0.83,
                "reasoning": "matched against the synopsis",
                "runner_up": None,
                "model": "gemini-2.5-flash-lite",
            }
        }
        suggest = AsyncMock(return_value=suggestion)
        monkeypatch.setattr(episode_curator, "suggest_episode_via_llm", suggest)

        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await self._seed_failed(session)
            job_id, title_id = job.id, title.id

        await coord._run_match_single_file(job_id, title_id, tmp_path / "x.mkv")

        suggest.assert_awaited_once()
        async with _unit_session_factory() as session:
            t = await session.get(DiscTitle, title_id)
            assert t.state == TitleState.REVIEW
            details = json.loads(t.match_details)
            assert details["llm_suggestion"]["episode"] == 9
            # Must NOT auto-organize — it's only a suggestion for the user.
            assert t.organized_to is None
        coord._check_job_completion.assert_awaited()

    async def test_disabled_keeps_manual_review_path(self, monkeypatch, tmp_path):
        self._patch_ai_config(monkeypatch, enabled=False)
        suggest = AsyncMock()
        monkeypatch.setattr(episode_curator, "suggest_episode_via_llm", suggest)

        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await self._seed_failed(session)
            job_id, title_id = job.id, title.id

        await coord._run_match_single_file(job_id, title_id, tmp_path / "x.mkv")

        suggest.assert_not_called()
        async with _unit_session_factory() as session:
            t = await session.get(DiscTitle, title_id)
            assert t.state == TitleState.REVIEW
            assert json.loads(t.match_details)["error"] == "subtitle_download_failed"

    async def test_unknown_season_keeps_manual_review_path(self, monkeypatch, tmp_path):
        self._patch_ai_config(monkeypatch, enabled=True)
        suggest = AsyncMock(return_value={"llm_suggestion": {"episode": 1}})
        monkeypatch.setattr(episode_curator, "suggest_episode_via_llm", suggest)

        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await self._seed_failed(session, detected_season=None)
            job_id, title_id = job.id, title.id

        await coord._run_match_single_file(job_id, title_id, tmp_path / "x.mkv")

        suggest.assert_not_called()
        async with _unit_session_factory() as session:
            t = await session.get(DiscTitle, title_id)
            assert t.state == TitleState.REVIEW
            assert json.loads(t.match_details)["error"] == "subtitle_download_failed"

    async def test_no_suggestion_keeps_manual_review_path(self, monkeypatch, tmp_path):
        self._patch_ai_config(monkeypatch, enabled=True)
        # AI ran but couldn't produce a suggestion (e.g. LLM declined / TMDB miss).
        suggest = AsyncMock(return_value=None)
        monkeypatch.setattr(episode_curator, "suggest_episode_via_llm", suggest)

        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await self._seed_failed(session)
            job_id, title_id = job.id, title.id

        await coord._run_match_single_file(job_id, title_id, tmp_path / "x.mkv")

        suggest.assert_awaited_once()
        async with _unit_session_factory() as session:
            t = await session.get(DiscTitle, title_id)
            assert t.state == TitleState.REVIEW
            assert json.loads(t.match_details)["error"] == "subtitle_download_failed"

    async def test_fallback_exception_does_not_strand_title(self, monkeypatch, tmp_path):
        """An exception anywhere in the fallback must be swallowed so the title
        still lands in REVIEW — never stuck in MATCHING with no completion check.
        """
        monkeypatch.setattr(
            "app.services.config_service.get_config",
            AsyncMock(side_effect=RuntimeError("boom")),
        )
        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await self._seed_failed(session)
            job_id, title_id = job.id, title.id

        # Must not raise out of the match task.
        await coord._run_match_single_file(job_id, title_id, tmp_path / "x.mkv")

        async with _unit_session_factory() as session:
            t = await session.get(DiscTitle, title_id)
            assert t.state == TitleState.REVIEW
            assert json.loads(t.match_details)["error"] == "subtitle_download_failed"
        coord._check_job_completion.assert_awaited()


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

    async def test_passes_tmdb_id_to_each_season_download(self, monkeypatch):
        """Callers that know the show's tmdb_id must key every per-season download
        by it, or the name-resolver can pick a same-name twin (#370)."""
        coord = _make_coord()
        seen: list[tuple[int, int | None]] = []

        async def _noop(*a, **k):
            return None

        monkeypatch.setattr(ws_manager, "broadcast_subtitle_event", _noop)

        def fake_download(show, season, tmdb_id=None):
            seen.append((season, tmdb_id))
            return {"episodes": [{"status": "downloaded"}], "show_name": show}

        monkeypatch.setattr("app.matcher.testing_service.download_subtitles", fake_download)
        async with _unit_session_factory() as session:
            job, _t = await _seed(session)
            job_id = job.id
        coord._subtitle_ready[job_id] = asyncio.Event()

        await coord.download_subtitles_all_seasons(job_id, "Eureka", [1, 2], tmdb_id=4620)

        assert seen == [(1, 4620), (2, 4620)]


@pytest.mark.unit
class TestEpisodeRuntimesShowIdentity:
    """The duration pre-filter fetches episode runtimes to flag non-episode tracks
    as extras. It must resolve the show by the job's authoritative ``tmdb_id`` — not
    by re-resolving the name, which returns the dominant same-name twin (Frasier 1993
    #3452, 24×23min) instead of a re-identified revival (#195241, 10 eps) and
    misclassifies real episodes as extras (the live PR #287/#288 Frasier regression:
    runtimes were fetched for show 3452, so the revival's ~28min episodes looked like
    bonus tracks and landed in Season 1/Extras/ instead of being matched).
    """

    async def test_uses_job_tmdb_id_and_skips_name_lookup(self, monkeypatch):
        coord = _make_coord()
        captured: dict = {}

        def fake_runtimes(show_id, season):
            captured["show_id"] = show_id
            captured["season"] = season
            return [22, 22, 22]

        # Resolves to the WRONG same-name twin if (incorrectly) consulted.
        fake_fetch_id = MagicMock(return_value="3452")
        monkeypatch.setattr("app.matcher.tmdb_client.fetch_season_episode_runtimes", fake_runtimes)
        monkeypatch.setattr("app.matcher.tmdb_client.fetch_show_id", fake_fetch_id)

        async with _unit_session_factory() as session:
            job, _title = await _seed(session)
            job.detected_title = "Frasier"
            job.detected_season = 1
            job.tmdb_id = 195241  # user re-identified to the 2023 revival
            session.add(job)
            await session.commit()
            await session.refresh(job)

            runtimes = await coord._episode_runtimes_for_job(
                job.id, job.tmdb_id, job.detected_title, job.detected_season
            )

        assert runtimes == [22, 22, 22]
        # The known id flowed straight through — NOT the name-resolved twin (3452).
        assert captured["show_id"] == "195241"
        assert captured["season"] == 1
        fake_fetch_id.assert_not_called()

    async def test_falls_back_to_name_lookup_without_tmdb_id(self, monkeypatch):
        """Without a known id (legacy / not-yet-identified job), the pre-filter must
        still resolve by name so the common non-collision case keeps working."""
        coord = _make_coord()
        captured: dict = {}

        def fake_runtimes(show_id, season):
            captured["show_id"] = show_id
            return [23, 23]

        fake_fetch_id = MagicMock(return_value="3452")
        monkeypatch.setattr("app.matcher.tmdb_client.fetch_season_episode_runtimes", fake_runtimes)
        monkeypatch.setattr("app.matcher.tmdb_client.fetch_show_id", fake_fetch_id)

        async with _unit_session_factory() as session:
            job, _title = await _seed(session)
            job.detected_title = "Frasier"
            job.detected_season = 1
            job.tmdb_id = None  # not yet resolved to a tmdb_id
            session.add(job)
            await session.commit()
            await session.refresh(job)

            runtimes = await coord._episode_runtimes_for_job(
                job.id, job.tmdb_id, job.detected_title, job.detected_season
            )

        assert runtimes == [23, 23]
        fake_fetch_id.assert_called_once_with("Frasier")
        assert captured["show_id"] == "3452"


@pytest.mark.unit
class TestEpisodeRuntimesCacheConcurrency:
    """When many titles of the same job hit the duration pre-filter at once (the
    cold window of a multi-season import), the episode-runtimes lookup must
    collapse to a SINGLE TMDB fetch. A stampede here means one in-flight TMDB
    round-trip per title — and, in the caller, one held DB connection per title
    across that round-trip — which is the connection-pool exhaustion aggravator
    this fix targets.
    """

    async def test_concurrent_calls_for_same_job_fetch_once(self, monkeypatch):
        coord = _make_coord()
        calls: list[tuple] = []

        def slow_runtimes(show_id, season):
            # Append (GIL-atomic) BEFORE sleeping so a stampede records every
            # entrant; the sleep keeps the first fetch in-flight long enough for
            # all concurrent callers to race past the cache check.
            calls.append((show_id, season))
            time.sleep(0.05)
            return [22, 22, 22]

        monkeypatch.setattr("app.matcher.tmdb_client.fetch_season_episode_runtimes", slow_runtimes)
        monkeypatch.setattr("app.matcher.tmdb_client.fetch_show_id", MagicMock(return_value="999"))

        async with _unit_session_factory() as session:
            job, _title = await _seed(session)
            job.tmdb_id = 195241
            session.add(job)
            await session.commit()
            await session.refresh(job)

            results = await asyncio.gather(
                *[
                    coord._episode_runtimes_for_job(
                        job.id, job.tmdb_id, job.detected_title, job.detected_season
                    )
                    for _ in range(8)
                ]
            )

        assert all(r == [22, 22, 22] for r in results)
        assert len(calls) == 1, f"expected one fetch, got {len(calls)} (stampede)"


@pytest.mark.unit
class TestConvergeJobToMatching:
    """The matching coordinator converges the parent job to MATCHING the moment a
    title starts matching — belt-and-suspenders so the import-folder dashboard can't
    stay stuck on the scanning radar if the identify-time MATCHING broadcast is ever
    missed (the PR #307 symptom). Only a job still in IDENTIFYING is moved; an
    already-MATCHING or REVIEW_NEEDED (deep-rematch) job is left alone.
    """

    async def _seed_job(self, session, state: JobState) -> int:
        job = DiscJob(
            drive_id="import",
            volume_label="TRUE_DETECTIVE_S1",
            content_type=ContentType.TV,
            state=state,
            detected_title="True Detective",
            detected_season=1,
            staging_path="/tmp/staging",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id

    async def test_identifying_job_converges_and_broadcasts(self, monkeypatch):
        coord = _make_coord()
        broadcast = AsyncMock()
        monkeypatch.setattr(ws_manager, "broadcast_job_update", broadcast)

        async with _unit_session_factory() as session:
            job_id = await self._seed_job(session, JobState.IDENTIFYING)
            await coord._converge_job_to_matching(session, job_id)

        async with _unit_session_factory() as session:
            job = await session.get(DiscJob, job_id)
            assert job.state == JobState.MATCHING
        broadcast.assert_awaited_once_with(job_id, JobState.MATCHING.value)

    async def test_already_matching_job_is_noop(self, monkeypatch):
        coord = _make_coord()
        broadcast = AsyncMock()
        monkeypatch.setattr(ws_manager, "broadcast_job_update", broadcast)

        async with _unit_session_factory() as session:
            job_id = await self._seed_job(session, JobState.MATCHING)
            await coord._converge_job_to_matching(session, job_id)

        # Already MATCHING — no re-transition, no duplicate broadcast.
        broadcast.assert_not_awaited()

    async def test_review_needed_job_is_untouched(self, monkeypatch):
        coord = _make_coord()
        broadcast = AsyncMock()
        monkeypatch.setattr(ws_manager, "broadcast_job_update", broadcast)

        async with _unit_session_factory() as session:
            job_id = await self._seed_job(session, JobState.REVIEW_NEEDED)
            await coord._converge_job_to_matching(session, job_id)

        # The deep-rematch path runs while the job is REVIEW_NEEDED; it must not be
        # yanked back into MATCHING by the guard.
        async with _unit_session_factory() as session:
            job = await session.get(DiscJob, job_id)
            assert job.state == JobState.REVIEW_NEEDED
        broadcast.assert_not_awaited()


SEASON_PROMPT = json.dumps(
    {
        "kind": "season",
        "reason": (
            "Identified as 'Some Show' but the season could not be detected "
            "from the disc label — select a season to continue."
        ),
    }
)
NAME_PROMPT = json.dumps({"kind": "name", "reason": "Disc label was unreadable"})


async def _seed_pin_job(
    session,
    titles,
    *,
    drive_id="E:",
    detected_season=None,
    identity_prompt_json=None,
):
    """Seed an unknown-season job + (TitleState, matched_episode) titles."""
    job = DiscJob(
        drive_id=drive_id,
        volume_label="SHOW_BOXSET_D3",
        content_type=ContentType.TV,
        state=JobState.MATCHING,
        detected_title="Some Show",
        detected_season=detected_season,
        identity_prompt_json=identity_prompt_json,
        staging_path="/tmp/staging",
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    for idx, (tstate, episode) in enumerate(titles):
        session.add(
            DiscTitle(
                job_id=job.id,
                title_index=idx,
                duration_seconds=2600,
                state=tstate,
                matched_episode=episode,
            )
        )
    await session.commit()
    return job


@pytest.mark.unit
class TestSeasonPinning:
    """Walk-away B6: _maybe_pin_converged_season pins detected_season once
    cross-season matching converges (>=2 MATCHED agree, zero MATCHED disagree),
    so later titles take the cheap single-season path."""

    async def test_two_agree_zero_disagree_pins_and_broadcasts(self, monkeypatch):
        coord = _make_coord()
        broadcast = AsyncMock()
        monkeypatch.setattr(ws_manager, "broadcast_job_update", broadcast)

        async with _unit_session_factory() as session:
            job = await _seed_pin_job(
                session,
                [
                    (TitleState.MATCHED, "S03E01"),
                    (TitleState.MATCHED, "S03E02"),
                ],
            )
            await coord._maybe_pin_converged_season(session, job.id)

        async with _unit_session_factory() as session:
            fresh = await session.get(DiscJob, job.id)
            assert fresh.detected_season == 3
        # state=None ("unchanged"); detected_season rides the enumerated-WS update.
        broadcast.assert_awaited_once_with(
            job.id, None, detected_season=3, identity_prompt_json=None
        )

    async def test_unpadded_episode_codes_still_pin(self, monkeypatch):
        coord = _make_coord()
        monkeypatch.setattr(ws_manager, "broadcast_job_update", AsyncMock())

        async with _unit_session_factory() as session:
            job = await _seed_pin_job(
                session,
                [
                    (TitleState.MATCHED, "S3E1"),
                    (TitleState.MATCHED, "s03e02"),
                ],
            )
            await coord._maybe_pin_converged_season(session, job.id)
            await session.refresh(job)
            assert job.detected_season == 3

    async def test_matched_disagreement_blocks_pin(self, monkeypatch):
        coord = _make_coord()
        broadcast = AsyncMock()
        monkeypatch.setattr(ws_manager, "broadcast_job_update", broadcast)

        async with _unit_session_factory() as session:
            job = await _seed_pin_job(
                session,
                [
                    (TitleState.MATCHED, "S03E01"),
                    (TitleState.MATCHED, "S03E02"),
                    (TitleState.MATCHED, "S04E01"),
                ],
            )
            await coord._maybe_pin_converged_season(session, job.id)
            await session.refresh(job)
            assert job.detected_season is None
        broadcast.assert_not_awaited()

    async def test_single_matched_title_does_not_pin(self, monkeypatch):
        coord = _make_coord()
        broadcast = AsyncMock()
        monkeypatch.setattr(ws_manager, "broadcast_job_update", broadcast)

        async with _unit_session_factory() as session:
            job = await _seed_pin_job(session, [(TitleState.MATCHED, "S03E01")])
            await coord._maybe_pin_converged_season(session, job.id)
            await session.refresh(job)
            assert job.detected_season is None
        broadcast.assert_not_awaited()

    async def test_review_titles_never_vote_for_a_pin(self, monkeypatch):
        # REVIEW titles are low-confidence guesses — two of them agreeing with
        # one MATCHED title must NOT pin.
        coord = _make_coord()
        broadcast = AsyncMock()
        monkeypatch.setattr(ws_manager, "broadcast_job_update", broadcast)

        async with _unit_session_factory() as session:
            job = await _seed_pin_job(
                session,
                [
                    (TitleState.MATCHED, "S03E01"),
                    (TitleState.REVIEW, "S03E02"),
                    (TitleState.REVIEW, "S03E03"),
                ],
            )
            await coord._maybe_pin_converged_season(session, job.id)
            await session.refresh(job)
            assert job.detected_season is None
        broadcast.assert_not_awaited()

    async def test_review_disagreement_does_not_block_pin(self, monkeypatch):
        # Symmetric rule: a disagreeing REVIEW guess doesn't veto two
        # confident MATCHED votes either.
        coord = _make_coord()
        monkeypatch.setattr(ws_manager, "broadcast_job_update", AsyncMock())

        async with _unit_session_factory() as session:
            job = await _seed_pin_job(
                session,
                [
                    (TitleState.MATCHED, "S03E01"),
                    (TitleState.MATCHED, "S03E02"),
                    (TitleState.REVIEW, "S04E01"),
                ],
            )
            await coord._maybe_pin_converged_season(session, job.id)
            await session.refresh(job)
            assert job.detected_season == 3

    async def test_extra_and_none_codes_are_ignored(self, monkeypatch):
        coord = _make_coord()
        monkeypatch.setattr(ws_manager, "broadcast_job_update", AsyncMock())

        async with _unit_session_factory() as session:
            job = await _seed_pin_job(
                session,
                [
                    (TitleState.MATCHED, "S03E01"),
                    (TitleState.MATCHED, "S03E02"),
                    (TitleState.MATCHED, "extra"),
                    (TitleState.MATCHED, None),
                ],
            )
            await coord._maybe_pin_converged_season(session, job.id)
            await session.refresh(job)
            assert job.detected_season == 3

    async def test_already_pinned_job_is_noop(self, monkeypatch):
        coord = _make_coord()
        broadcast = AsyncMock()
        monkeypatch.setattr(ws_manager, "broadcast_job_update", broadcast)

        async with _unit_session_factory() as session:
            job = await _seed_pin_job(
                session,
                [
                    (TitleState.MATCHED, "S03E01"),
                    (TitleState.MATCHED, "S03E02"),
                ],
                detected_season=2,
            )
            await coord._maybe_pin_converged_season(session, job.id)
            await session.refresh(job)
            # Untouched — even though the matches say S3, the pin never
            # overrides an existing season.
            assert job.detected_season == 2
        broadcast.assert_not_awaited()

    async def test_import_job_never_pins(self, monkeypatch):
        # Flat watch-folder imports legitimately mix seasons; two same-season
        # early matches must not force later titles onto a single season.
        coord = _make_coord()
        broadcast = AsyncMock()
        monkeypatch.setattr(ws_manager, "broadcast_job_update", broadcast)

        async with _unit_session_factory() as session:
            job = await _seed_pin_job(
                session,
                [
                    (TitleState.MATCHED, "S03E01"),
                    (TitleState.MATCHED, "S03E02"),
                ],
                drive_id="import",
            )
            await coord._maybe_pin_converged_season(session, job.id)
            await session.refresh(job)
            assert job.detected_season is None
        broadcast.assert_not_awaited()

    async def test_pin_retires_season_prompt_in_same_commit(self, monkeypatch):
        coord = _make_coord()
        broadcast = AsyncMock()
        monkeypatch.setattr(ws_manager, "broadcast_job_update", broadcast)

        async with _unit_session_factory() as session:
            job = await _seed_pin_job(
                session,
                [
                    (TitleState.MATCHED, "S03E01"),
                    (TitleState.MATCHED, "S03E02"),
                ],
                identity_prompt_json=SEASON_PROMPT,
            )
            await coord._maybe_pin_converged_season(session, job.id)

        async with _unit_session_factory() as session:
            fresh = await session.get(DiscJob, job.id)
            assert fresh.detected_season == 3
            assert fresh.identity_prompt_json is None
        # "" clears the CTA on the frontend merge (enumerated-WS clear pattern).
        broadcast.assert_awaited_once_with(job.id, None, detected_season=3, identity_prompt_json="")

    async def test_pin_leaves_name_prompt_alone(self, monkeypatch):
        # A blocking name prompt shouldn't coexist with MATCHED titles (it
        # parks them QUEUED), but if it ever does, only the answer endpoints
        # may clear it — pinning must not.
        coord = _make_coord()
        broadcast = AsyncMock()
        monkeypatch.setattr(ws_manager, "broadcast_job_update", broadcast)

        async with _unit_session_factory() as session:
            job = await _seed_pin_job(
                session,
                [
                    (TitleState.MATCHED, "S03E01"),
                    (TitleState.MATCHED, "S03E02"),
                ],
                identity_prompt_json=NAME_PROMPT,
            )
            await coord._maybe_pin_converged_season(session, job.id)
            await session.refresh(job)
            assert job.detected_season == 3
            assert job.identity_prompt_json == NAME_PROMPT
        broadcast.assert_awaited_once_with(
            job.id, None, detected_season=3, identity_prompt_json=None
        )

    async def test_malformed_prompt_json_is_left_alone_and_does_not_crash(self, monkeypatch):
        # prompt_kind() returns None for unparseable JSON, so the pin proceeds
        # but must neither clear the payload (fail-closed interpretation of a
        # malformed prompt belongs to _blocking_identity_prompt) nor raise.
        coord = _make_coord()
        broadcast = AsyncMock()
        monkeypatch.setattr(ws_manager, "broadcast_job_update", broadcast)

        async with _unit_session_factory() as session:
            job = await _seed_pin_job(
                session,
                [
                    (TitleState.MATCHED, "S03E01"),
                    (TitleState.MATCHED, "S03E02"),
                ],
                identity_prompt_json="{not valid json",
            )
            await coord._maybe_pin_converged_season(session, job.id)
            await session.refresh(job)
            assert job.detected_season == 3
            assert job.identity_prompt_json == "{not valid json"
        broadcast.assert_awaited_once_with(
            job.id, None, detected_season=3, identity_prompt_json=None
        )


@pytest.mark.unit
class TestTryDiscdbAssignmentSource:
    """try_discdb_assignment stamps match_source/details from mapping.source."""

    async def test_network_disc_mapping_stamps_network_source(self):
        from app.core.discdb_classifier import DiscDbTitleMapping

        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await _seed(session, title_index=4)
            job_id, title_id = job.id, title.id

        coord.set_discdb_mappings(
            job_id,
            [
                DiscDbTitleMapping(
                    index=4,
                    title_type="Episode",
                    season=1,
                    episode=3,
                    source="network_disc",
                )
            ],
        )

        async with _unit_session_factory() as session:
            title = await session.get(DiscTitle, title_id)
            assigned = await coord.try_discdb_assignment(job_id, title, session)
            assert assigned is True

        async with _unit_session_factory() as session:
            t = await session.get(DiscTitle, title_id)
            assert t.matched_episode == "S01E03"
            assert t.match_source == "network_disc"
            assert json.loads(t.match_details)["source"] == "network_disc"

    async def test_default_mapping_stamps_discdb_source(self):
        from app.core.discdb_classifier import DiscDbTitleMapping

        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await _seed(session, title_index=4)
            job_id, title_id = job.id, title.id

        # Default source (omitted) -> "discdb".
        coord.set_discdb_mappings(
            job_id,
            [DiscDbTitleMapping(index=4, title_type="Episode", season=1, episode=3)],
        )

        async with _unit_session_factory() as session:
            title = await session.get(DiscTitle, title_id)
            assigned = await coord.try_discdb_assignment(job_id, title, session)
            assert assigned is True

        async with _unit_session_factory() as session:
            t = await session.get(DiscTitle, title_id)
            assert t.match_source == "discdb"
            assert json.loads(t.match_details)["source"] == "discdb"
