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
from app.matcher.subtitle_utils import REFERENCES_UNREADABLE_ERROR_CODE
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.job_state_machine import JobStateMachine
from app.services.matching_coordinator import (
    CONJOINED_SCAN_POINTS,
    MAX_CONJOINED_EPISODES,
    MULTI_EPISODE_ERROR_CODE,
    FileWaitResult,
    MatchingCoordinator,
    _apply_multi_episode_review,
    _conjoined_episode_count,
    _duration_matches_episode_runtime,
    _may_contribute_fingerprint,
    _route_unconfirmable_title,
    _same_episode_code,
    _scan_points_for_hint,
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

    async def test_keep_policy_defers_as_matched_extra(self, monkeypatch, tmp_path):
        """keep no longer organizes mid-match. The extra rests as MATCHED with the
        synthetic "extra" code so it rides the normal end-of-disc finalize path
        (auto-files into Extras/ on a clean disc, or stays editable if the disc
        goes to review). Organizing here would set COMPLETED early and freeze the
        title in the review UI's read-only "Processed" list."""
        _patch_config(monkeypatch, "keep")
        import app.core.organizer as org

        org_spy = Mock()
        monkeypatch.setattr(org, "organize_tv_extras", org_spy)
        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await _seed(session)
            handled = await coord._handle_extras(
                job.id, title.id, title, job, tmp_path / "x.mkv", 10.0, [22, 44], session
            )
            assert handled is True
            assert title.state == TitleState.MATCHED
            assert title.is_extra is True
            assert title.matched_episode == "extra"
            assert title.organized_to is None
            assert title.match_confidence == 1.0
            assert json.loads(title.match_details)["action"] == "deferred"
        org_spy.assert_not_called()
        coord._check_job_completion.assert_awaited_once()


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

    def _patch_ai_config(
        self, monkeypatch, *, enabled: bool, key: str = "k", provider: str = "anthropic"
    ):
        # ai_provider is required, not incidental: the gate is ai_is_configured(
        # provider, key), because a local provider (ollama / lmstudio) has no key
        # at all. A stub without it raises AttributeError inside the fallback,
        # which the coordinator swallows as "AI fallback failed" and the test
        # then fails for the wrong reason.
        monkeypatch.setattr(
            "app.services.config_service.get_config",
            AsyncMock(
                return_value=SimpleNamespace(
                    ai_episode_matching_enabled=enabled,
                    ai_api_key=key,
                    ai_provider=provider,
                    ai_local_base_url="",
                )
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

    async def test_local_provider_runs_the_fallback_without_a_key(self, monkeypatch, tmp_path):
        """A keyless local provider must reach the LLM fallback, not be gated out.

        This path once guarded on `if not config.ai_api_key`, which silently
        disabled Ollama and LM Studio while the settings page looked correctly
        filled in. The gate is now ai_is_configured(provider, key), and this is
        the behavioural proof that an empty key no longer stops it.
        """
        self._patch_ai_config(monkeypatch, enabled=True, key="", provider="ollama")
        suggest = AsyncMock(return_value=None)
        monkeypatch.setattr(episode_curator, "suggest_episode_via_llm", suggest)

        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await self._seed_failed(session)
            job_id, title_id = job.id, title.id

        await coord._run_match_single_file(job_id, title_id, tmp_path / "x.mkv")

        suggest.assert_awaited_once()

    async def test_remote_provider_without_a_key_is_still_gated_out(self, monkeypatch, tmp_path):
        """The relaxation must not let a hosted provider through unconfigured."""
        self._patch_ai_config(monkeypatch, enabled=True, key="", provider="anthropic")
        suggest = AsyncMock(return_value=None)
        monkeypatch.setattr(episode_curator, "suggest_episode_via_llm", suggest)

        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await self._seed_failed(session)
            job_id, title_id = job.id, title.id

        await coord._run_match_single_file(job_id, title_id, tmp_path / "x.mkv")

        suggest.assert_not_awaited()

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


@pytest.mark.unit
class TestConjoinedEpisodeCount:
    """Cartoon discs put two or three ~11-minute segments in one physical track.
    TMDB lists each segment as its own episode, so the track only matches a SUM of
    runtimes, never a single one. This predicate admits such a track to ASR; the
    positional vote runs (app/matcher/multi_episode.py) make the real call.
    """

    WEEKENDERS_S1 = [11] * 16

    def test_two_segment_track_admits_as_two(self):
        # The reported bug: job 60, a 23-minute track of two 11-minute segments.
        assert _conjoined_episode_count(23.0, self.WEEKENDERS_S1) == 2

    def test_three_segment_track_admits_as_three(self):
        # 3 x 11 = 33, window [28, 43].
        assert _conjoined_episode_count(34.0, self.WEEKENDERS_S1) == 3

    def test_single_episode_is_not_conjoined(self):
        # 11 minutes is one segment. Callers test the single-episode window first;
        # this predicate must not claim it.
        assert _conjoined_episode_count(11.0, self.WEEKENDERS_S1) is None

    def test_smallest_n_wins_when_windows_overlap(self):
        # N=2 window [17, 32] and N=3 window [28, 43] overlap at [28, 32].
        # Return the smaller; the vote runs correct it if wrong.
        assert _conjoined_episode_count(30.0, self.WEEKENDERS_S1) == 2

    def test_play_all_track_is_not_conjoined(self):
        # Gilmore t00: 11515s = 191.9min. Above every N<=3 window, so it stays an
        # extra. This is the existing test_play_all_track_is_an_extra invariant.
        assert _conjoined_episode_count(11515 / 60, GILMORE_S1) is None

    def test_four_segments_exceeds_the_cap(self):
        # 4 x 11 = 44. Beyond MAX_CONJOINED_EPISODES: 10 scan points cannot
        # resolve 4 runs (needs ~2 votes/run + seams), so refuse rather than guess.
        assert _conjoined_episode_count(44.0, self.WEEKENDERS_S1) is None

    def test_short_featurette_is_not_conjoined(self):
        # 10 minutes against 22/44-minute episodes: below every sum window.
        assert _conjoined_episode_count(10.0, [22, 44]) is None

    def test_uses_consecutive_runtime_windows(self):
        # A season whose runtimes vary: 22+22 = 44, window [39, 54].
        assert _conjoined_episode_count(45.0, [22, 22, 44, 44]) == 2

    def test_empty_runtimes_never_matches(self):
        assert _conjoined_episode_count(23.0, []) is None


@pytest.mark.unit
class TestConjoinedAdmission:
    """The duration pre-filter must ADMIT a plausibly-conjoined track to matching
    instead of filing it as an extra (issue #622), and must still file a genuine
    extra. These cover the branch decision only; the wiring is exercised by the
    integration path.
    """

    WEEKENDERS_S1 = [11] * 16

    def test_conjoined_track_is_admitted_not_filed(self):
        # 23min against [11]*16: no single runtime matches, but 2 x 11 does, so
        # the track proceeds to ASR instead of reaching _handle_extras.
        assert _duration_matches_episode_runtime(23.0, self.WEEKENDERS_S1) is False
        assert _conjoined_episode_count(23.0, self.WEEKENDERS_S1) == 2

    def test_featurette_still_files_as_extra(self):
        # Neither a single runtime nor any sum: must still reach _handle_extras.
        # (Note: 11 - EPISODE_DURATION_UNDER_TOLERANCE_MIN(5) = 6, so the single-
        # episode window's lower bound is inclusive at 6.0 minutes; 5.0 sits
        # clearly below every window instead.)
        assert _duration_matches_episode_runtime(5.0, self.WEEKENDERS_S1) is False
        assert _conjoined_episode_count(5.0, self.WEEKENDERS_S1) is None


@pytest.mark.unit
class TestMultiEpisodeReviewRouting:
    """A track that is, or might be, several conjoined episodes must land in REVIEW
    rather than MATCHED. Auto-organizing it under one code silently loses the rest,
    which is the failure this whole change exists to prevent.
    """

    def _title(self, details: dict | None):
        return SimpleNamespace(
            state=TitleState.MATCHED,
            match_details=json.dumps(details) if details is not None else None,
            match_source="engram",
        )

    def test_confirmed_multi_episode_goes_to_review(self):
        from app.services.matching_coordinator import MULTI_EPISODE_ERROR_CODE

        details = {
            "multi_episode": {
                "is_multi_episode": True,
                "reason": "contiguous_runs",
                "codes": ["S01E01", "S01E02"],
            }
        }
        title = self._title(details)
        _apply_multi_episode_review(title, conjoined_hint=2)
        assert title.state == TitleState.REVIEW
        parsed = json.loads(title.match_details)
        assert parsed["error"] == MULTI_EPISODE_ERROR_CODE
        assert "S01E01, S01E02" in parsed["message"]

    def test_admitted_but_unconfirmed_goes_to_review(self):
        # The votes could not confirm it (insufficient scan depth). ASR still
        # returned one confident episode, so naming the file would drop the rest.
        from app.services.matching_coordinator import MULTI_EPISODE_ERROR_CODE

        details = {
            "multi_episode": {
                "is_multi_episode": False,
                "reason": "insufficient_scan_depth",
                "codes": [],
            }
        }
        title = self._title(details)
        _apply_multi_episode_review(title, conjoined_hint=3)
        assert title.state == TitleState.REVIEW
        parsed = json.loads(title.match_details)
        assert parsed["error"] == MULTI_EPISODE_ERROR_CODE
        assert "insufficient_scan_depth" in parsed["message"]

    def test_prior_warning_is_carried_forward_not_clobbered(self):
        """The advisory path can flag "file_exists" (a sibling already organized
        this code) before the multi-episode check runs. Multi-episode takes the
        error slot because it drives the non-rematchable routing, but dropping the
        earlier warning would leave the reviewer with no hint that a sibling
        already claimed the code.
        """
        from app.services.matching_coordinator import MULTI_EPISODE_ERROR_CODE

        details = {
            "error": "file_exists",
            "message": "S01E01 is already organized by another track on this disc.",
            "multi_episode": {
                "is_multi_episode": True,
                "reason": "contiguous_runs",
                "codes": ["S01E01", "S01E02"],
            },
        }
        title = self._title(details)
        _apply_multi_episode_review(title, conjoined_hint=None)
        parsed = json.loads(title.match_details)
        assert parsed["error"] == MULTI_EPISODE_ERROR_CODE
        assert parsed["superseded_error"] == "file_exists"
        assert "already organized by another track" in parsed["message"]
        assert "S01E01, S01E02" in parsed["message"]

    def test_ordinary_single_episode_is_untouched(self):
        details = {"multi_episode": {"is_multi_episode": False, "reason": "single_episode"}}
        title = self._title(details)
        _apply_multi_episode_review(title, conjoined_hint=None)
        assert title.state == TitleState.MATCHED
        assert "error" not in json.loads(title.match_details)

    def test_malformed_match_details_does_not_raise(self):
        title = SimpleNamespace(
            state=TitleState.MATCHED, match_details="not json", match_source="engram"
        )
        _apply_multi_episode_review(title, conjoined_hint=None)
        assert title.state == TitleState.MATCHED


@pytest.mark.unit
class TestConjoinedDiscDbBypass:
    """A conjoined track must not escape review via the DiscDB disc-order fallback
    or the fingerprint contribution enqueue. Both run inside _match_single_file_inner
    BEFORE the review routing at the end of that function, so this exercises the
    real call, not just the extracted helper (issue #622, code-review Critical).
    """

    async def test_conjoined_track_does_not_take_discdb_fallback(self, monkeypatch, tmp_path):
        from app.core.discdb_classifier import DiscDbTitleMapping

        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await _seed(session, title_index=0)

        mapping = DiscDbTitleMapping(
            index=0,
            title_type="Episode",
            episode_title="Segment A",
            season=1,
            episode=1,
            duration_seconds=600,
            size_bytes=1024**3,
        )
        coord._discdb_mappings = {job.id: [mapping]}

        mock_result = SimpleNamespace(
            episode_code="S01E01",
            confidence=0.3,  # below DISCDB_FALLBACK_ASR_FLOOR
            needs_review=True,
            match_details={"score": 0.3},
        )
        mock_curator = MagicMock()
        mock_curator.match_single_file = AsyncMock(return_value=mock_result)
        monkeypatch.setattr("app.services.matching_coordinator.episode_curator", mock_curator)

        await coord._match_single_file_inner(
            job.id, title.id, tmp_path / "title_t00.mkv", conjoined_hint=2
        )

        async with _unit_session_factory() as session:
            title = await session.get(DiscTitle, title.id)
            assert title.state == TitleState.REVIEW
            parsed = json.loads(title.match_details)
            assert parsed["error"] == MULTI_EPISODE_ERROR_CODE
            # Not the DiscDB fallback's fingerprint (source/episode_title keys).
            assert "source" not in parsed

    async def test_ordinary_low_confidence_track_still_takes_discdb_fallback(
        self, monkeypatch, tmp_path
    ):
        """Regression guard: an ordinary (non-conjoined) low-confidence track must
        still take the DiscDB disc-order fallback exactly as before."""
        from app.core.discdb_classifier import DiscDbTitleMapping

        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await _seed(session, title_index=0)

        mapping = DiscDbTitleMapping(
            index=0,
            title_type="Episode",
            episode_title="Pilot",
            season=1,
            episode=1,
            duration_seconds=600,
            size_bytes=1024**3,
        )
        coord._discdb_mappings = {job.id: [mapping]}

        mock_result = SimpleNamespace(
            episode_code="S01E07",
            confidence=0.3,
            needs_review=True,
            match_details={"score": 0.3},
        )
        mock_curator = MagicMock()
        mock_curator.match_single_file = AsyncMock(return_value=mock_result)
        monkeypatch.setattr("app.services.matching_coordinator.episode_curator", mock_curator)

        await coord._match_single_file_inner(
            job.id, title.id, tmp_path / "title_t00.mkv", conjoined_hint=None
        )

        async with _unit_session_factory() as session:
            title = await session.get(DiscTitle, title.id)
            assert title.state == TitleState.MATCHED
            assert title.matched_episode == "S01E01"
            assert title.match_source == "discdb"

    async def test_run_match_single_file_forwards_conjoined_hint_to_inner(
        self, monkeypatch, tmp_path
    ):
        """Drives the PUBLIC entry point (_run_match_single_file), not the inner
        function directly, so this exercises the conjoined_hint forwarding at
        that call site. The other tests in this class supply conjoined_hint to
        _match_single_file_inner themselves and would not notice if the outer
        function stopped forwarding its computed hint (#622)."""
        coord = _make_coord()
        coord._episode_runtimes_for_job = AsyncMock(return_value=[11] * 16)
        coord._wait_for_file_ready = AsyncMock(return_value=FileWaitResult.READY)

        async with _unit_session_factory() as session:
            # 23-minute track: matches no single 11-minute runtime, but matches
            # the sum of two consecutive ones -> conjoined_hint == 2.
            job, title = await _seed(session, title_index=0, duration_seconds=23 * 60)
            job_id, title_id = job.id, title.id

        mock_result = SimpleNamespace(
            episode_code="S01E01",
            confidence=0.9,
            needs_review=False,
            match_details={"score": 0.9},
        )
        mock_curator = MagicMock()
        mock_curator.match_single_file = AsyncMock(return_value=mock_result)
        monkeypatch.setattr("app.services.matching_coordinator.episode_curator", mock_curator)

        await coord._run_match_single_file(job_id, title_id, tmp_path / "title_t00.mkv")

        async with _unit_session_factory() as session:
            title = await session.get(DiscTitle, title_id)
            assert title.state == TitleState.REVIEW
            parsed = json.loads(title.match_details)
            assert parsed["error"] == MULTI_EPISODE_ERROR_CODE
        # The hinted track is scanned deeply enough to confirm three segments.
        assert mock_curator.match_single_file.await_args.kwargs["num_points"] == (
            CONJOINED_SCAN_POINTS
        )

    async def test_conjoined_track_with_unset_hint_still_blocks_discdb_fallback(
        self, monkeypatch, tmp_path
    ):
        """The real case the multi_episode-result clause protects: a conjoined
        track whose duration happens to match a single TMDB runtime (so the
        duration pre-filter never sets conjoined_hint) but whose vote runs
        detected the split anyway. Only the `_is_multi_episode_result` check
        keeps this out of the DiscDB disc-order fallback and the low-confidence
        review path must carry the multi-episode error (#622)."""
        from app.core.discdb_classifier import DiscDbTitleMapping

        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await _seed(session, title_index=0)

        mapping = DiscDbTitleMapping(
            index=0,
            title_type="Episode",
            episode_title="Segment A",
            season=1,
            episode=1,
            duration_seconds=600,
            size_bytes=1024**3,
        )
        coord._discdb_mappings = {job.id: [mapping]}

        mock_result = SimpleNamespace(
            episode_code="S01E01",
            confidence=0.3,  # below DISCDB_FALLBACK_ASR_FLOOR
            needs_review=True,
            match_details={
                "multi_episode": {
                    "is_multi_episode": True,
                    "reason": "contiguous_runs",
                    "codes": ["S01E01", "S01E02"],
                }
            },
        )
        mock_curator = MagicMock()
        mock_curator.match_single_file = AsyncMock(return_value=mock_result)
        monkeypatch.setattr("app.services.matching_coordinator.episode_curator", mock_curator)

        # conjoined_hint=None: the duration pre-filter did not flag this track;
        # only the multi_episode result content protects it here.
        await coord._match_single_file_inner(
            job.id, title.id, tmp_path / "title_t00.mkv", conjoined_hint=None
        )

        async with _unit_session_factory() as session:
            title = await session.get(DiscTitle, title.id)
            assert title.state == TitleState.REVIEW
            parsed = json.loads(title.match_details)
            assert parsed["error"] == MULTI_EPISODE_ERROR_CODE
            # Not the DiscDB fallback's fingerprint (source/episode_title keys).
            assert "source" not in parsed


@pytest.mark.unit
class TestUnreadableReferencesEndToEnd:
    """A refused result flows through _match_single_file_inner into the database: it
    lands in REVIEW with the reviewer message, unless a DiscDB mapping assigns it."""

    def _refused_result(self):
        return SimpleNamespace(
            episode_code=None,
            confidence=0.0,
            needs_review=True,
            match_details={
                "error": REFERENCES_UNREADABLE_ERROR_CODE,
                "usable_references": 1,
                "total_references": 1,
            },
        )

    async def test_refused_title_lands_in_review_with_the_message(self, monkeypatch, tmp_path):
        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await _seed(session, title_index=0)
        coord._discdb_mappings = {}

        mock_curator = MagicMock()
        mock_curator.match_single_file = AsyncMock(return_value=self._refused_result())
        monkeypatch.setattr("app.services.matching_coordinator.episode_curator", mock_curator)

        await coord._match_single_file_inner(job.id, title.id, tmp_path / "title_t00.mkv")

        async with _unit_session_factory() as session:
            title = await session.get(DiscTitle, title.id)
            assert title.state == TitleState.REVIEW
            parsed = json.loads(title.match_details)
            assert parsed["error"] == REFERENCES_UNREADABLE_ERROR_CODE
            assert "too few to tell its episodes apart" in parsed["message"]

    async def test_discdb_mapping_still_assigns_a_refused_title(self, monkeypatch, tmp_path):
        from app.core.discdb_classifier import DiscDbTitleMapping

        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await _seed(session, title_index=0)

        mapping = DiscDbTitleMapping(
            index=0,
            title_type="Episode",
            episode_title="Pilot",
            season=1,
            episode=1,
            duration_seconds=600,
            size_bytes=1024**3,
        )
        coord._discdb_mappings = {job.id: [mapping]}

        mock_curator = MagicMock()
        mock_curator.match_single_file = AsyncMock(return_value=self._refused_result())
        monkeypatch.setattr("app.services.matching_coordinator.episode_curator", mock_curator)

        await coord._match_single_file_inner(job.id, title.id, tmp_path / "title_t00.mkv")

        async with _unit_session_factory() as session:
            title = await session.get(DiscTitle, title.id)
            assert title.state == TitleState.MATCHED
            assert title.matched_episode == "S01E01"
            assert title.match_source == "discdb"
            assert json.loads(title.match_details or "{}").get("error") != (
                REFERENCES_UNREADABLE_ERROR_CODE
            )

    async def test_rematch_without_details_does_not_repeat_an_old_refusal(
        self, monkeypatch, tmp_path
    ):
        """A re-match after the references were fixed can come back with no
        match_details (a curator exception, a failed duration probe). The earlier
        refusal must not survive it and resurface its "could not be read" message."""
        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await _seed(session, title_index=0)
            stored = await session.get(DiscTitle, title.id)
            stored.match_details = json.dumps(
                {
                    "error": REFERENCES_UNREADABLE_ERROR_CODE,
                    "usable_references": 0,
                    "total_references": 37,
                    "message": "The reference subtitles for this season could not be read",
                }
            )
            await session.commit()
        coord._discdb_mappings = {}

        no_details = SimpleNamespace(
            episode_code=None, confidence=0.0, needs_review=True, match_details=None
        )
        mock_curator = MagicMock()
        mock_curator.match_single_file = AsyncMock(return_value=no_details)
        monkeypatch.setattr("app.services.matching_coordinator.episode_curator", mock_curator)

        await coord._match_single_file_inner(job.id, title.id, tmp_path / "title_t00.mkv")

        async with _unit_session_factory() as session:
            title = await session.get(DiscTitle, title.id)
            parsed = json.loads(title.match_details or "{}")
            assert parsed.get("error") != REFERENCES_UNREADABLE_ERROR_CODE
            assert "could not be read" not in (parsed.get("message") or "")


@pytest.mark.unit
class TestUnreadableReferencesReviewRouting:
    """A title the matcher refused for lack of usable references goes to review with
    a message naming the cause, even when the runtime also hinted that the track is
    several conjoined episodes."""

    def _title(self, details: dict | None):
        return SimpleNamespace(
            state=TitleState.MATCHED,
            match_details=json.dumps(details) if details is not None else None,
            match_source="engram",
        )

    def _refused(self, usable: int, total: int) -> dict:
        return {
            "error": REFERENCES_UNREADABLE_ERROR_CODE,
            "usable_references": usable,
            "total_references": total,
        }

    def test_unreadable_references_go_to_review_with_counts(self):
        title = self._title(self._refused(1, 37))
        routed = _route_unconfirmable_title(title, conjoined_hint=None)
        assert routed == REFERENCES_UNREADABLE_ERROR_CODE
        assert title.state == TitleState.REVIEW
        parsed = json.loads(title.match_details)
        assert parsed["error"] == REFERENCES_UNREADABLE_ERROR_CODE
        assert "could not be read (only 1 of 37 had any text)" in parsed["message"]

    def test_single_readable_reference_says_too_few_not_unreadable(self):
        title = self._title(self._refused(1, 1))
        assert _route_unconfirmable_title(title, conjoined_hint=None) == (
            REFERENCES_UNREADABLE_ERROR_CODE
        )
        message = json.loads(title.match_details)["message"]
        assert "Only 1 reference subtitle is available" in message
        assert "too few" in message
        assert "could not be read" not in message

    def test_unreadable_references_win_over_a_conjoined_hint(self):
        title = self._title(self._refused(1, 37))
        routed = _route_unconfirmable_title(title, conjoined_hint=3)
        assert routed == REFERENCES_UNREADABLE_ERROR_CODE
        parsed = json.loads(title.match_details)
        assert parsed["error"] == REFERENCES_UNREADABLE_ERROR_CODE
        assert "joined together" not in parsed["message"]

    def test_hinted_track_without_the_error_still_routes_as_multi_episode(self):
        title = self._title(
            {"multi_episode": {"is_multi_episode": False, "reason": "single_episode"}}
        )
        assert _route_unconfirmable_title(title, conjoined_hint=3) == MULTI_EPISODE_ERROR_CODE

    def test_ordinary_title_is_untouched(self):
        title = self._title({"episode": "S1E1"})
        assert _route_unconfirmable_title(title, conjoined_hint=None) is None
        assert title.state == TitleState.MATCHED

    def test_unreadable_references_are_never_auto_rematched(self):
        from app.services.finalization_coordinator import _NON_REMATCHABLE_REVIEW_ERRORS

        assert REFERENCES_UNREADABLE_ERROR_CODE in _NON_REMATCHABLE_REVIEW_ERRORS


@pytest.mark.unit
class TestConjoinedScanDepth:
    """Three-segment cartoon tracks (Dexter's Laboratory, Looney Tunes) could never
    be confirmed at the default 10 scan points: decompose_vote_runs needs more than
    3 points per run plus one."""

    def test_unhinted_track_keeps_the_requested_depth(self):
        assert _scan_points_for_hint(None, None) is None
        assert _scan_points_for_hint(37, None) == 37

    def test_hinted_track_scans_at_least_the_conjoined_depth(self):
        assert _scan_points_for_hint(None, 2) == CONJOINED_SCAN_POINTS
        assert _scan_points_for_hint(10, 3) == CONJOINED_SCAN_POINTS

    def test_deeper_requested_scan_is_not_reduced(self):
        assert _scan_points_for_hint(73, 3) == 73

    def test_conjoined_depth_is_a_lattice_level_that_confirms_the_cap(self):
        from app.matcher.episode_identification import snap_to_lattice_level
        from app.matcher.multi_episode import MIN_SCAN_POINTS_PER_RUN

        assert snap_to_lattice_level(CONJOINED_SCAN_POINTS) == CONJOINED_SCAN_POINTS
        assert CONJOINED_SCAN_POINTS > MIN_SCAN_POINTS_PER_RUN * MAX_CONJOINED_EPISODES + 1

    def test_three_segment_track_confirms_at_conjoined_depth_but_not_at_ten(self):
        from app.matcher.multi_episode import decompose_vote_runs

        deep = (
            [(s, "S01E01") for s in (0, 60, 120, 180, 240, 300)]
            + [(s, "S01E02") for s in (420, 480, 540, 600, 660)]
            + [(s, "S01E03") for s in (780, 840, 900, 960, 1020, 1080)]
        )
        verdict = decompose_vote_runs(deep, CONJOINED_SCAN_POINTS)
        assert verdict.is_multi_episode is True
        assert verdict.codes == ("S01E01", "S01E02", "S01E03")

        shallow = (
            [(s, "S01E01") for s in (0, 120, 240)]
            + [(s, "S01E02") for s in (480, 600)]
            + [(s, "S01E03") for s in (840, 960, 1080)]
        )
        assert decompose_vote_runs(shallow, 10).reason == "insufficient_scan_depth"


@pytest.mark.unit
class TestSameEpisodeCode:
    """Overlap, not equality: the caller asks "has another track taken this?".

    A prefix-anchored regex compared only the FIRST episode of each code, so a
    combined track hid every episode after its first from that question and a
    second track could claim one of them unchallenged.
    """

    def test_identical_codes_overlap(self):
        assert _same_episode_code("S01E01", "S01E01") is True

    def test_zero_padding_is_ignored(self):
        assert _same_episode_code("S1E9", "S01E09") is True

    def test_different_episodes_do_not_overlap(self):
        assert _same_episode_code("S01E01", "S01E02") is False

    def test_different_seasons_do_not_overlap(self):
        assert _same_episode_code("S01E01", "S02E01") is False

    def test_a_combined_track_overlaps_each_episode_it_claims(self):
        assert _same_episode_code("S01E01-E03", "S01E01") is True
        # The bug: E02 sits inside the range but is not its first episode.
        assert _same_episode_code("S01E01-E03", "S01E02") is True
        assert _same_episode_code("S01E01-E03", "S01E03") is True

    def test_a_combined_track_does_not_overlap_outside_its_range(self):
        assert _same_episode_code("S01E01-E03", "S01E04") is False

    def test_two_combined_tracks_overlap_on_a_shared_episode(self):
        assert _same_episode_code("S01E01-E03", "S01E03-E05") is True
        assert _same_episode_code("S01E01-E02", "S01E03-E04") is False

    def test_non_codes_fall_back_to_string_equality(self):
        assert _same_episode_code("extra", "extra") is True
        assert _same_episode_code("extra", "skip") is False
        assert _same_episode_code(None, "S01E01") is False


@pytest.mark.unit
class TestCombinedDiscDbMappingGoesToReview:
    """A DiscDB title covering several episodes must not be auto-matched.

    Conflict detection groups whole codes, so an auto-matched S02E17-E18 would never
    collide with another track's S02E18 and the library would get E18 twice. The
    combined code is pre-filled and parked for a person to confirm."""

    def _mapping(self, episodes):
        from app.core.discdb_classifier import DiscDbTitleMapping

        return DiscDbTitleMapping(
            index=0,
            title_type="Episode",
            episode_title="Serpent of Evil River / The Transplant",
            season=2,
            episode=episodes[0],
            episodes=list(episodes),
            duration_seconds=1363,
            size_bytes=1024**3,
        )

    async def test_combined_mapping_lands_in_review_prefilled(self, monkeypatch, tmp_path):
        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await _seed(session, title_index=0)
        coord._discdb_mappings = {job.id: [self._mapping([17, 18])]}

        low = SimpleNamespace(
            episode_code="S02E05", confidence=0.3, needs_review=True, match_details={"score": 0.3}
        )
        mock_curator = MagicMock()
        mock_curator.match_single_file = AsyncMock(return_value=low)
        monkeypatch.setattr("app.services.matching_coordinator.episode_curator", mock_curator)

        await coord._match_single_file_inner(
            job.id, title.id, tmp_path / "title_t00.mkv", conjoined_hint=None
        )

        async with _unit_session_factory() as session:
            title = await session.get(DiscTitle, title.id)
            assert title.state == TitleState.REVIEW
            assert title.matched_episode == "S02E17-E18"
            parsed = json.loads(title.match_details)
            assert parsed["error"] == MULTI_EPISODE_ERROR_CODE
            assert "S02E17-E18" in parsed["message"]

    async def test_single_episode_mapping_is_still_matched(self, monkeypatch, tmp_path):
        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await _seed(session, title_index=0)
        coord._discdb_mappings = {job.id: [self._mapping([17])]}

        low = SimpleNamespace(
            episode_code="S02E05", confidence=0.3, needs_review=True, match_details={"score": 0.3}
        )
        mock_curator = MagicMock()
        mock_curator.match_single_file = AsyncMock(return_value=low)
        monkeypatch.setattr("app.services.matching_coordinator.episode_curator", mock_curator)

        await coord._match_single_file_inner(
            job.id, title.id, tmp_path / "title_t00.mkv", conjoined_hint=None
        )

        async with _unit_session_factory() as session:
            title = await session.get(DiscTitle, title.id)
            assert title.state == TitleState.MATCHED
            assert title.matched_episode == "S02E17"

    async def test_restored_combined_code_lands_in_review(self):
        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await _seed(
                session,
                discdb_match_details=json.dumps(
                    {"source": "discdb", "matched_episode": "S02E17-E18"}
                ),
            )
            job_id, title_id = job.id, title.id

        await coord.rematch_single_title(job_id, title_id, source_preference="discdb")

        async with _unit_session_factory() as session:
            t = await session.get(DiscTitle, title_id)
            assert t.state == TitleState.REVIEW
            assert t.matched_episode == "S02E17-E18"
            parsed = json.loads(t.match_details)
            assert parsed["error"] == MULTI_EPISODE_ERROR_CODE
            assert "S02E17-E18" in parsed["message"]

    async def test_restored_combined_code_from_in_memory_mapping_lands_in_review(self):
        coord = _make_coord()
        async with _unit_session_factory() as session:
            job, title = await _seed(session, discdb_match_details=json.dumps({"some": "data"}))
            job_id, title_id = job.id, title.id
        coord.set_discdb_mappings(job_id, [self._mapping([17, 18])])

        await coord.rematch_single_title(job_id, title_id, source_preference="discdb")

        async with _unit_session_factory() as session:
            t = await session.get(DiscTitle, title_id)
            assert t.state == TitleState.REVIEW
            assert t.matched_episode == "S02E17-E18"
            assert json.loads(t.match_details)["error"] == MULTI_EPISODE_ERROR_CODE


@pytest.mark.unit
class TestFingerprintContributionGuard:
    """A fingerprint row names one episode, so a track holding several must never be
    contributed, whichever signal says so: the runtime hint, the vote verdict, or a
    combined code in matched_episode. All three live in one rule so a future writer
    of a combined code before the enqueue cannot slip past it."""

    def test_single_episode_track_may_contribute(self):
        assert _may_contribute_fingerprint("S01E05", None, {"score": 0.9}) is True

    def test_no_code_never_contributes(self):
        assert _may_contribute_fingerprint(None, None, None) is False

    def test_runtime_hint_blocks(self):
        assert _may_contribute_fingerprint("S01E05", 2, None) is False

    def test_confirmed_verdict_blocks(self):
        details = {"multi_episode": {"is_multi_episode": True, "codes": ["S01E01", "S01E02"]}}
        assert _may_contribute_fingerprint("S01E01", None, details) is False

    def test_combined_code_blocks(self):
        assert _may_contribute_fingerprint("S01E01-E02", None, {"source": "discdb"}) is False


@pytest.mark.unit
class TestCombinedAssignmentPrefill:
    """A confirmed multi-episode verdict pre-fills the combined code the organizer can
    now name. The title still lands in REVIEW so a person confirms it."""

    def _title(self, codes, matched="S01E01"):
        details = {
            "multi_episode": {
                "is_multi_episode": True,
                "reason": "contiguous_runs",
                "codes": codes,
            }
        }
        return SimpleNamespace(
            state=TitleState.MATCHED,
            matched_episode=matched,
            match_details=json.dumps(details),
            match_source="engram",
        )

    def test_contiguous_segments_prefill_a_range(self):
        title = self._title(["S01E01", "S01E02", "S01E03"])
        _apply_multi_episode_review(title, conjoined_hint=3)
        assert title.state == TitleState.REVIEW
        assert title.matched_episode == "S01E01-E03"
        message = json.loads(title.match_details)["message"]
        assert "appears to contain 3 episodes" in message
        assert "S01E01-E03" in message
        assert "cannot name a combined file" not in message

    def test_playback_order_is_kept(self):
        # A disc can pair segments TMDB does not number consecutively; the file's
        # own order is the one to name.
        title = self._title(["S01E03", "S01E01"])
        _apply_multi_episode_review(title, conjoined_hint=None)
        assert title.matched_episode == "S01E03E01"

    def test_codes_from_different_seasons_are_not_prefilled(self):
        title = self._title(["S01E13", "S02E01"])
        _apply_multi_episode_review(title, conjoined_hint=None)
        assert title.state == TitleState.REVIEW
        assert title.matched_episode == "S01E01"
        assert "not all from one season" in json.loads(title.match_details)["message"]

    def test_unconfirmed_hint_is_not_prefilled(self):
        details = {
            "multi_episode": {
                "is_multi_episode": False,
                "reason": "insufficient_scan_depth",
                "codes": [],
            }
        }
        title = SimpleNamespace(
            state=TitleState.MATCHED,
            matched_episode="S01E01",
            match_details=json.dumps(details),
            match_source="engram",
        )
        _apply_multi_episode_review(title, conjoined_hint=3)
        assert title.state == TitleState.REVIEW
        assert title.matched_episode == "S01E01"
