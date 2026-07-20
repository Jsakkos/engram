"""Unit tests for JobManager's mockable handlers.

Targets _on_title_ripped (per-title completion routing) and _rerun_matching
(re-match dispatch + DiscDB restore). The heavy _run_ripping orchestration is
intentionally left to integration tests. The matching/finalization collaborators
and websocket layer are stubbed.
"""

import asyncio
import importlib
import json
import logging
import time
from unittest.mock import AsyncMock

import pytest
from sqlmodel import select

from app.api.websocket import manager as ws_manager
from app.core.extractor import RipResult
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.job_manager import job_manager
from tests.unit.conftest import _unit_session_factory

# Resolve the actual module (not the JobManager singleton, which shadows the
# submodule name in the app.services package namespace) — matches conftest.
jm_mod = importlib.import_module("app.services.job_manager")


@pytest.fixture(autouse=True)
def _quiet_ws(monkeypatch):
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ws_manager, "broadcast_title_update", _noop)


async def _seed(
    content_type=ContentType.TV,
    staging="/tmp/staging",
    identity_prompt_json=None,
    **title_kwargs,
):
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="SHOW_S1D1",
            content_type=content_type,
            state=JobState.RIPPING,
            detected_title="Some Show",
            detected_season=1,
            staging_path=staging,
            identity_prompt_json=identity_prompt_json,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        defaults = dict(
            job_id=job.id, title_index=0, duration_seconds=1380, state=TitleState.RIPPING
        )
        defaults.update(title_kwargs)
        title = DiscTitle(**defaults)
        session.add(title)
        await session.commit()
        await session.refresh(title)
        return job, title


async def _get_title(title_id):
    async with _unit_session_factory() as session:
        return await session.get(DiscTitle, title_id)


@pytest.mark.unit
class TestOnTitleRipped:
    async def test_movie_title_becomes_matched_without_dispatch(self, tmp_path, monkeypatch):
        job, title = await _seed(content_type=ContentType.MOVIE)
        dispatch = AsyncMock()
        monkeypatch.setattr(job_manager._matching, "match_single_file", dispatch)
        path = tmp_path / "movie_t00.mkv"
        path.write_text("")

        await job_manager._on_title_ripped(job.id, 1, path, [title])

        t = await _get_title(title.id)
        assert t.state == TitleState.MATCHED
        assert t.output_filename == str(path)
        dispatch.assert_not_called()

    async def test_tv_title_dispatches_match_when_no_discdb(self, tmp_path, monkeypatch):
        job, title = await _seed(content_type=ContentType.TV)
        dispatch = AsyncMock()
        monkeypatch.setattr(job_manager._matching, "match_single_file", dispatch)
        monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)
        path = tmp_path / "show_t00.mkv"
        path.write_text("")

        await job_manager._on_title_ripped(job.id, 1, path, [title])
        await asyncio.sleep(0)  # let the dispatched task run

        t = await _get_title(title.id)
        # Enqueued for matching → QUEUED (match_single_file is mocked, so the
        # post-semaphore QUEUED→MATCHING flip never runs here).
        assert t.state == TitleState.QUEUED
        dispatch.assert_awaited_once()

    async def test_tv_title_always_dispatches_match_regardless_of_discdb(
        self, tmp_path, monkeypatch
    ):
        """ASR-preferred precedence: match_single_file is always dispatched for TV titles.
        DiscDB assignment (if any) is now applied as a fallback inside _match_single_file_inner,
        not as a pre-dispatch short-circuit in _dispatch_title_match."""
        job, title = await _seed(content_type=ContentType.TV)
        dispatch = AsyncMock()
        monkeypatch.setattr(job_manager._matching, "match_single_file", dispatch)
        monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)
        path = tmp_path / "show_t00.mkv"
        path.write_text("")

        await job_manager._on_title_ripped(job.id, 1, path, [title])
        await asyncio.sleep(0)  # let the dispatched task run

        t = await _get_title(title.id)
        # Always dispatched — QUEUED (match_single_file is mocked, so the
        # post-semaphore QUEUED→MATCHING flip never runs here).
        assert t.state == TitleState.QUEUED
        dispatch.assert_awaited_once()

    async def test_unresolvable_file_is_noop(self, tmp_path):
        job, title = await _seed(content_type=ContentType.TV)
        path = tmp_path / "unrelated.mkv"
        path.write_text("")

        # No sorted_titles to map to → resolve returns None → early return.
        await job_manager._on_title_ripped(job.id, 1, path, [])

        t = await _get_title(title.id)
        assert t.state == TitleState.RIPPING  # unchanged


@pytest.mark.unit
class TestBackfillUnmatchedTitles:
    """Issue #517: backfill's own "already assigned?" dedup check must key off
    the disc-native title number (output_index when set), not the raw
    scan-order title_index — mirrors the fix already applied to
    resolve_title_from_filename/find_staging_file. Otherwise, on a disc whose
    native MakeMKV numbering doesn't start at t00, a title that was already
    correctly resolved during the normal rip flow gets misidentified as
    "unmatched" and reprocessed redundantly.
    """

    async def test_skips_already_assigned_title_on_native_numbering_offset(
        self, tmp_path, monkeypatch
    ):
        """Disc has no "t00" — native numbering starts at 1. Title scan-index 0
        has output_index=1 and output_filename already set (resolved correctly
        by resolve_title_from_filename during the normal rip flow). The staging
        file is literally named "..._t01.mkv". Backfill must recognize this
        file as already-assigned via the native number, not re-treat it as
        unmatched and reprocess it.
        """
        staging = tmp_path
        existing_path = staging / "Show_S01E01_t01.mkv"
        existing_path.write_text("")

        job, title = await _seed(
            content_type=ContentType.TV,
            staging=str(staging),
            title_index=0,
            output_index=1,
            output_filename=str(existing_path),
            state=TitleState.MATCHED,
        )

        called = []
        monkeypatch.setattr(
            job_manager,
            "_on_title_ripped",
            AsyncMock(side_effect=lambda *a, **k: called.append(a)),
        )

        await job_manager._backfill_unmatched_titles(job.id, staging, [title])

        # Must NOT be reprocessed — it's already assigned under its native number.
        assert called == []


_PROMPT = json.dumps({"kind": "name", "reason": "Disc label unreadable"})


@pytest.mark.unit
class TestOnTitleRippedIdentityGate:
    """Walk-away Phase B: an unanswered identity prompt parks ripped titles in
    QUEUED with no dispatch (matching with no/uncertain show identity wastes ASR
    against the wrong — or no — reference corpus). dispatch_pending_matches
    releases them once the prompt is answered."""

    def _stub_dispatch(self, monkeypatch):
        dispatch = AsyncMock()
        monkeypatch.setattr(job_manager._matching, "match_single_file", dispatch)
        monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)
        return dispatch

    async def test_tv_with_prompt_parks_queued_without_dispatch(self, tmp_path, monkeypatch):
        job, title = await _seed(content_type=ContentType.TV, identity_prompt_json=_PROMPT)
        dispatch = self._stub_dispatch(monkeypatch)
        path = tmp_path / "show_t00.mkv"
        path.write_text("")

        await job_manager._on_title_ripped(job.id, 1, path, [title])
        await asyncio.sleep(0)

        t = await _get_title(title.id)
        assert t.state == TitleState.QUEUED
        assert t.output_filename == str(path)
        dispatch.assert_not_called()

    @pytest.mark.parametrize("content_type", [ContentType.UNKNOWN, ContentType.MOVIE])
    async def test_non_tv_with_prompt_parks_queued_not_matched(
        self, tmp_path, monkeypatch, content_type
    ):
        """An identity-pending job must NOT fall into the non-TV → MATCHED branch:
        that would mark titles matched with no identity."""
        job, title = await _seed(content_type=content_type, identity_prompt_json=_PROMPT)
        dispatch = self._stub_dispatch(monkeypatch)
        path = tmp_path / "disc_t00.mkv"
        path.write_text("")

        await job_manager._on_title_ripped(job.id, 1, path, [title])
        await asyncio.sleep(0)

        t = await _get_title(title.id)
        assert t.state == TitleState.QUEUED
        dispatch.assert_not_called()

    async def test_reidentify_prompt_also_parks(self, tmp_path, monkeypatch):
        """kind=reidentify means no confirmed identity — same park as kind=name."""
        job, title = await _seed(
            content_type=ContentType.TV,
            identity_prompt_json=json.dumps({"kind": "reidentify", "reason": "twins"}),
        )
        dispatch = self._stub_dispatch(monkeypatch)
        path = tmp_path / "show_t00.mkv"
        path.write_text("")

        await job_manager._on_title_ripped(job.id, 1, path, [title])
        await asyncio.sleep(0)

        assert (await _get_title(title.id)).state == TitleState.QUEUED
        dispatch.assert_not_called()

    async def test_season_prompt_dispatches_normally(self, tmp_path, monkeypatch):
        """kind=season is a shortcut CTA, not a blocking identity question: the
        ripped title goes QUEUED→dispatch like any TV title (B2 item 1). Parking
        it would hang the job forever — the season-prompt job reaches MATCHING
        and _has_pending_match_work refreshes the watchdog clock with nothing
        ever dispatching."""
        job, title = await _seed(content_type=ContentType.TV, identity_prompt_json=_SEASON_PROMPT)
        dispatch = self._stub_dispatch(monkeypatch)
        path = tmp_path / "show_t00.mkv"
        path.write_text("")

        await job_manager._on_title_ripped(job.id, 1, path, [title])
        await asyncio.sleep(0)

        t = await _get_title(title.id)
        assert t.state == TitleState.QUEUED  # flip to MATCHING is post-semaphore
        # ASR-preferred precedence: match_single_file is always dispatched.
        # try_discdb_assignment is applied as a fallback inside _match_single_file_inner.
        dispatch.assert_awaited_once()


@pytest.mark.unit
class TestDispatchPendingMatches:
    """dispatch_pending_matches releases identity-gated QUEUED titles: dispatch
    exactly the QUEUED titles whose ripped file exists, idempotently."""

    def _stub_dispatch(self, monkeypatch):
        dispatch = AsyncMock()
        monkeypatch.setattr(job_manager._matching, "match_single_file", dispatch)
        monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)
        return dispatch

    async def _add_title(self, job_id, index, state, output=None):
        async with _unit_session_factory() as session:
            t = DiscTitle(
                job_id=job_id,
                title_index=index,
                duration_seconds=1380,
                state=state,
                output_filename=output,
            )
            session.add(t)
            await session.commit()
            await session.refresh(t)
            return t

    async def test_dispatches_only_queued_titles_with_existing_files(self, tmp_path, monkeypatch):
        job, ripping = await _seed(content_type=ContentType.TV, identity_prompt_json=_PROMPT)
        f = tmp_path / "show_t01.mkv"
        f.write_text("")
        queued_ok = await self._add_title(job.id, 1, TitleState.QUEUED, output=str(f))
        await self._add_title(job.id, 2, TitleState.QUEUED, output=str(tmp_path / "missing.mkv"))
        await self._add_title(job.id, 3, TitleState.QUEUED, output=None)
        g = tmp_path / "show_t04.mkv"
        g.write_text("")
        await self._add_title(job.id, 4, TitleState.MATCHED, output=str(g))
        dispatch = self._stub_dispatch(monkeypatch)

        count = await job_manager.dispatch_pending_matches(job.id)
        await asyncio.sleep(0)

        assert count == 1
        dispatch.assert_awaited_once_with(job.id, queued_ok.id, f)
        # The RIPPING seed title is untouched.
        assert (await _get_title(ripping.id)).state == TitleState.RIPPING

    async def test_discdb_assignment_path_dispatches_match(self, tmp_path, monkeypatch):
        """ASR-preferred precedence: match_single_file is always dispatched.
        DiscDB assignment is now applied as a fallback inside _match_single_file_inner,
        not as a pre-dispatch short-circuit in _dispatch_title_match."""
        job, _ = await _seed(content_type=ContentType.TV)
        f = tmp_path / "show_t01.mkv"
        f.write_text("")
        queued = await self._add_title(job.id, 1, TitleState.QUEUED, output=str(f))
        dispatch = self._stub_dispatch(monkeypatch)

        count = await job_manager.dispatch_pending_matches(job.id)
        await asyncio.sleep(0)

        assert count == 1
        dispatch.assert_awaited_once_with(job.id, queued.id, f)

    async def test_double_dispatch_does_not_double_match(self, tmp_path, monkeypatch):
        """The in-flight guard makes repeat dispatch safe: the QUEUED→MATCHING
        flip happens only post-semaphore in match_single_file, so a title whose
        task is still parked waiting for a slot is still QUEUED — state alone
        cannot prevent a double spawn."""
        job, _ = await _seed(content_type=ContentType.TV, identity_prompt_json=_PROMPT)
        f = tmp_path / "show_t01.mkv"
        f.write_text("")
        title = await self._add_title(job.id, 1, TitleState.QUEUED, output=str(f))

        release = asyncio.Event()
        calls = []

        async def slow_match(jid, tid, path):
            calls.append((jid, tid))
            await release.wait()  # park like a saturated match semaphore

        monkeypatch.setattr(job_manager._matching, "match_single_file", slow_match)
        monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)

        first = await job_manager.dispatch_pending_matches(job.id)
        await asyncio.sleep(0)  # let the match task start and park on the event
        # Title is still QUEUED (flip is post-semaphore) — a second dispatch
        # must not spawn a second match for it.
        assert (await _get_title(title.id)).state == TitleState.QUEUED
        second = await job_manager.dispatch_pending_matches(job.id)
        await asyncio.sleep(0)

        assert first == 1
        assert second == 0
        assert calls == [(job.id, title.id)]

        # Let the task finish; the done callback releases the guard so a later
        # legitimate re-dispatch isn't blocked forever.
        release.set()
        await asyncio.sleep(0.05)
        assert title.id not in job_manager._inflight_match_dispatch

    async def test_no_queued_titles_is_noop(self, tmp_path, monkeypatch):
        job, _ = await _seed(content_type=ContentType.TV)
        dispatch = self._stub_dispatch(monkeypatch)

        assert await job_manager.dispatch_pending_matches(job.id) == 0
        dispatch.assert_not_called()

    async def test_non_tv_job_is_skipped(self, tmp_path, monkeypatch):
        """Defensive guard: a misrouted caller passing a MOVIE job must not
        episode-match its titles, even with QUEUED titles whose files exist."""
        job, _ = await _seed(content_type=ContentType.MOVIE)
        f = tmp_path / "movie_t01.mkv"
        f.write_text("")
        await self._add_title(job.id, 1, TitleState.QUEUED, output=str(f))
        dispatch = self._stub_dispatch(monkeypatch)

        assert await job_manager.dispatch_pending_matches(job.id) == 0
        dispatch.assert_not_called()


@pytest.mark.unit
class TestRerunMatching:
    async def test_discdb_preference_restores_matches(self):
        job, title = await _seed(
            content_type=ContentType.TV,
            is_selected=True,
            discdb_match_details=json.dumps({"matched_episode": "S01E04"}),
        )

        await job_manager._rerun_matching(job.id, source_preference="discdb")

        t = await _get_title(title.id)
        assert t.state == TitleState.MATCHED
        assert t.matched_episode == "S01E04"
        assert t.match_source == "discdb"
        assert t.match_confidence == 0.99

    async def test_engram_resets_and_dispatches(self, tmp_path, monkeypatch):
        f = tmp_path / "show_t00.mkv"
        f.write_text("")
        job, title = await _seed(
            content_type=ContentType.TV,
            staging=str(tmp_path),
            is_selected=True,
            output_filename=str(f),
            state=TitleState.MATCHED,
            matched_episode="S01E01",
        )
        dispatch = AsyncMock()
        monkeypatch.setattr(job_manager._matching, "match_single_file", dispatch)
        monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)

        await job_manager._rerun_matching(job.id)
        await asyncio.sleep(0)

        t = await _get_title(title.id)
        # Re-enqueued for matching → QUEUED (match_single_file is mocked).
        assert t.state == TitleState.QUEUED
        assert t.matched_episode is None
        dispatch.assert_awaited_once()

    async def test_missing_job_is_noop(self):
        # Must not raise for an unknown job id.
        await job_manager._rerun_matching(999999)


@pytest.fixture
def rip_env(monkeypatch, tmp_path):
    """Neutralize the side-effecting steps in _run_ripping that are orthogonal
    to DB-session scoping: physical eject, the makemkv log directory, and the
    terminal-state callbacks (staging cleanup / cache clearing).

    Stubbing the terminal callbacks is purely for isolation, not error
    avoidance: conftest patches their async_session bindings to the in-memory
    engine, so they no longer raise "no such table". They are still stubbed
    here so completing-job tests don't trigger real staging deletion (default
    policy is on_success) or cache clearing, which are irrelevant to what these
    scoping tests assert."""
    import app.core.discdb_exporter as exporter_mod
    import app.core.sentinel as sentinel_mod

    monkeypatch.setattr(sentinel_mod, "eject_disc", lambda drive_id: None)
    monkeypatch.setattr(exporter_mod, "get_makemkv_log_dir", lambda job_id: tmp_path)
    monkeypatch.setattr(jm_mod.state_machine, "_on_terminal_callbacks", [])
    return tmp_path


def _mock_rip(monkeypatch, result, on_call=None):
    """Replace the real (multi-hour, makemkvcon) rip with an instant no-op."""

    async def _run(*args, **kwargs):
        if on_call is not None:
            on_call()
        return result

    monkeypatch.setattr(job_manager._extractor, "rip_titles", AsyncMock(side_effect=_run))


@pytest.mark.unit
class TestRunRippingSessionScoping:
    async def test_setup_session_closed_before_rip(self, rip_env, monkeypatch):
        """The setup session must be released before the long rip is awaited —
        no JobManager DB session may be held while rip_titles is in flight."""
        job, _title = await _seed(
            content_type=ContentType.TV, staging=str(rip_env), is_selected=True
        )
        monkeypatch.setattr(job_manager, "_backfill_unmatched_titles", AsyncMock())

        open_sessions = {"count": 0}

        class _TrackingSession:
            def __init__(self):
                self._real = _unit_session_factory()

            async def __aenter__(self):
                open_sessions["count"] += 1
                return await self._real.__aenter__()

            async def __aexit__(self, *exc):
                result = await self._real.__aexit__(*exc)
                open_sessions["count"] -= 1
                return result

        monkeypatch.setattr(jm_mod, "async_session", _TrackingSession)

        captured = {}
        _mock_rip(
            monkeypatch,
            RipResult(success=True, output_files=[]),
            on_call=lambda: captured.__setitem__("open_at_rip", open_sessions["count"]),
        )

        await job_manager._run_ripping(job.id)

        assert captured.get("open_at_rip") == 0, (
            f"Expected 0 open JobManager sessions when rip_titles was awaited, "
            f"got {captured.get('open_at_rip')!r} (None means rip_titles was never reached)"
        )

    async def test_tv_path_transitions_to_matching(self, rip_env, monkeypatch):
        job, _title = await _seed(
            content_type=ContentType.TV, staging=str(rip_env), is_selected=True
        )
        monkeypatch.setattr(job_manager, "_backfill_unmatched_titles", AsyncMock())
        _mock_rip(
            monkeypatch,
            RipResult(success=True, output_files=[rip_env / "show_t00.mkv"]),
        )

        await job_manager._run_ripping(job.id)

        async with _unit_session_factory() as session:
            refreshed = await session.get(DiscJob, job.id)
        assert refreshed.state == JobState.MATCHING

    async def test_movie_single_title_completes(self, rip_env, monkeypatch):
        job, _title = await _seed(
            content_type=ContentType.MOVIE, staging=str(rip_env), is_selected=True
        )
        out_file = rip_env / "movie.mkv"
        monkeypatch.setattr(
            jm_mod.movie_organizer,
            "organize",
            lambda *a, **k: {"success": True, "main_file": out_file},
        )
        _mock_rip(monkeypatch, RipResult(success=True, output_files=[out_file]))

        await job_manager._run_ripping(job.id)

        async with _unit_session_factory() as session:
            refreshed = await session.get(DiscJob, job.id)
        assert refreshed.state == JobState.COMPLETED
        assert refreshed.final_path == str(out_file)

    async def test_rip_failure_fails_job(self, rip_env, monkeypatch):
        job, _title = await _seed(
            content_type=ContentType.TV, staging=str(rip_env), is_selected=True
        )
        _mock_rip(
            monkeypatch,
            RipResult(success=False, output_files=[], error_message="disc read error"),
        )

        await job_manager._run_ripping(job.id)

        async with _unit_session_factory() as session:
            refreshed = await session.get(DiscJob, job.id)
        assert refreshed.state == JobState.FAILED
        assert refreshed.error_message == "disc read error"

    async def test_fail_job_helper_missing_job_is_noop(self):
        # Must not raise for an unknown job id.
        await job_manager._fail_job(999999, "boom")

    async def test_stalled_titles_routed_to_review_and_job_holds(self, rip_env, monkeypatch):
        # A rip that reports stalled titles must NOT fail the job: the stalled
        # title is sent to REVIEW (rip_stalled, re-rippable — Feature C) and the
        # job holds in REVIEW_NEEDED until the user acts on the title.
        job, title = await _seed(
            content_type=ContentType.TV, staging=str(rip_env), is_selected=True, title_index=0
        )
        monkeypatch.setattr(job_manager, "_backfill_unmatched_titles", AsyncMock())
        _mock_rip(
            monkeypatch,
            RipResult(success=False, output_files=[], stalled_titles=[1]),
        )

        await job_manager._run_ripping(job.id)

        async with _unit_session_factory() as session:
            refreshed_job = await session.get(DiscJob, job.id)
            refreshed_title = await session.get(DiscTitle, title.id)
        assert refreshed_title.state == TitleState.REVIEW
        d = json.loads(refreshed_title.match_details)
        assert d["error"] == "rip_stalled"
        assert d["rerip_eligible"] is True
        assert refreshed_job.state == JobState.REVIEW_NEEDED

    async def test_cancelled_rip_fails_job_and_reraises(self, rip_env, monkeypatch):
        # Cancelling the rip must fail the job AND re-raise so the task is
        # actually marked cancelled (asyncio convention).
        job, _title = await _seed(
            content_type=ContentType.TV, staging=str(rip_env), is_selected=True
        )

        async def _cancel(*args, **kwargs):
            raise asyncio.CancelledError

        monkeypatch.setattr(job_manager._extractor, "rip_titles", AsyncMock(side_effect=_cancel))

        with pytest.raises(asyncio.CancelledError):
            await job_manager._run_ripping(job.id)

        async with _unit_session_factory() as session:
            refreshed = await session.get(DiscJob, job.id)
        assert refreshed.state == JobState.FAILED
        assert refreshed.error_message == "Cancelled by user"


_SEASON_PROMPT = json.dumps(
    {"kind": "season", "reason": "Couldn't determine the season. Please select a season."}
)


@pytest.mark.unit
class TestBlockingIdentityPrompt:
    """Walk-away B4: kind-aware identity-prompt predicate. kind=season never
    blocks (identity known, cross-season matching proceeds); name/reidentify
    block; malformed JSON / unrecognized kinds fail closed (blocking)."""

    def _job(self, prompt):
        return DiscJob(drive_id="E:", volume_label="X", identity_prompt_json=prompt)

    def test_no_prompt_is_not_blocking(self):
        assert job_manager._blocking_identity_prompt(None) is None
        assert job_manager._blocking_identity_prompt(self._job(None)) is None

    @pytest.mark.parametrize("kind", ["name", "reidentify"])
    def test_name_and_reidentify_block(self, kind):
        prompt = job_manager._blocking_identity_prompt(
            self._job(json.dumps({"kind": kind, "reason": "why"}))
        )
        assert prompt == {"kind": kind, "reason": "why"}

    def test_season_kind_does_not_block(self):
        job = self._job(json.dumps({"kind": "season", "reason": "pick a season"}))
        assert job_manager._blocking_identity_prompt(job) is None

    @pytest.mark.parametrize("raw", ["{not json", "[1, 2]", '"just a string"'])
    def test_malformed_prompt_blocks_with_fallback_reason(self, raw, caplog):
        with caplog.at_level(logging.WARNING, logger="app.services.job_manager"):
            prompt = job_manager._blocking_identity_prompt(self._job(raw))
        assert prompt is not None
        assert prompt["reason"] == jm_mod._FALLBACK_IDENTITY_REVIEW_REASON
        assert any("malformed identity_prompt_json" in r.message for r in caplog.records)

    def test_unrecognized_kind_blocks(self):
        prompt = job_manager._blocking_identity_prompt(
            self._job(json.dumps({"kind": "mystery", "reason": "??"}))
        )
        assert prompt == {"kind": "mystery", "reason": "??"}


@pytest.mark.unit
class TestRunRippingIdentityConvergence:
    """Walk-away B4: post-rip convergence for identity-pending jobs.

    A BLOCKING identity prompt (kind=name/reidentify) that survives to rip end
    converts into pooled review: RIPPING→REVIEW_NEEDED, review_reason carries
    the prompt's reason verbatim (frontend literal contracts), the prompt is
    cleared, and the WS broadcast clears it with "" (enumerated-WS clear
    pattern). kind=season never blocks: the job takes today's path with the
    prompt left in place."""

    def _spy_broadcast(self, monkeypatch):
        calls = []

        async def spy(job_id, state, **kwargs):
            calls.append((job_id, state, kwargs))

        monkeypatch.setattr(ws_manager, "broadcast_job_update", spy)
        return calls

    async def test_blocking_prompt_converges_to_review(self, rip_env, monkeypatch):
        job, _title = await _seed(
            content_type=ContentType.TV,
            staging=str(rip_env),
            is_selected=True,
            identity_prompt_json=_PROMPT,
        )
        monkeypatch.setattr(job_manager, "_backfill_unmatched_titles", AsyncMock())
        calls = self._spy_broadcast(monkeypatch)
        kicked = []
        monkeypatch.setattr(job_manager._prewarmer, "kickoff", lambda jid: kicked.append(jid))
        _mock_rip(monkeypatch, RipResult(success=True, output_files=[]))

        await job_manager._run_ripping(job.id)

        async with _unit_session_factory() as session:
            refreshed = await session.get(DiscJob, job.id)
        assert refreshed.state == JobState.REVIEW_NEEDED
        assert refreshed.review_reason == "Disc label unreadable"
        assert refreshed.identity_prompt_json is None

        review_calls = [c for c in calls if c[1] == JobState.REVIEW_NEEDED.value]
        assert len(review_calls) == 1
        _jid, _state, kwargs = review_calls[0]
        assert kwargs["review_reason"] == "Disc label unreadable"
        assert kwargs["identity_prompt_json"] == ""
        # Phase A prewarmer fires for free via the on_transition observer.
        assert kicked == [job.id]

    async def test_season_prompt_does_not_block_convergence(self, rip_env, monkeypatch):
        job, _title = await _seed(
            content_type=ContentType.TV,
            staging=str(rip_env),
            is_selected=True,
            identity_prompt_json=_SEASON_PROMPT,
        )
        monkeypatch.setattr(job_manager, "_backfill_unmatched_titles", AsyncMock())
        _mock_rip(monkeypatch, RipResult(success=True, output_files=[]))

        await job_manager._run_ripping(job.id)

        async with _unit_session_factory() as session:
            refreshed = await session.get(DiscJob, job.id)
        assert refreshed.state == JobState.MATCHING  # today's TV path
        assert refreshed.identity_prompt_json == _SEASON_PROMPT  # untouched
        assert refreshed.review_reason is None

    async def test_unknown_content_type_with_blocking_prompt_converges(self, rip_env, monkeypatch):
        job, _title = await _seed(
            content_type=ContentType.UNKNOWN,
            staging=str(rip_env),
            is_selected=True,
            identity_prompt_json=json.dumps({"kind": "reidentify", "reason": "Ambiguous match"}),
        )
        monkeypatch.setattr(job_manager, "_backfill_unmatched_titles", AsyncMock())
        _mock_rip(monkeypatch, RipResult(success=True, output_files=[]))

        await job_manager._run_ripping(job.id)

        async with _unit_session_factory() as session:
            refreshed = await session.get(DiscJob, job.id)
        assert refreshed.state == JobState.REVIEW_NEEDED
        assert refreshed.review_reason == "Ambiguous match"
        assert refreshed.identity_prompt_json is None

    async def test_malformed_prompt_is_blocking(self, rip_env, monkeypatch, caplog):
        job, _title = await _seed(
            content_type=ContentType.TV,
            staging=str(rip_env),
            is_selected=True,
            identity_prompt_json="{not json",
        )
        monkeypatch.setattr(job_manager, "_backfill_unmatched_titles", AsyncMock())
        _mock_rip(monkeypatch, RipResult(success=True, output_files=[]))

        with caplog.at_level(logging.WARNING, logger="app.services.job_manager"):
            await job_manager._run_ripping(job.id)

        async with _unit_session_factory() as session:
            refreshed = await session.get(DiscJob, job.id)
        assert refreshed.state == JobState.REVIEW_NEEDED
        assert refreshed.review_reason == jm_mod._FALLBACK_IDENTITY_REVIEW_REASON
        assert refreshed.identity_prompt_json is None
        assert any("malformed identity_prompt_json" in r.message for r in caplog.records)

    async def test_content_type_reread_after_rip(self, rip_env, monkeypatch):
        """The post-rip fork follows the DB row, not the setup-time local: a
        mid-rip mutation (B5 answer) flipping TV→MOVIE must land the job on the
        movie path (with the fresh detected_title), not RIPPING→MATCHING."""
        job, _title = await _seed(
            content_type=ContentType.TV, staging=str(rip_env), is_selected=True
        )
        out_file = rip_env / "movie.mkv"
        organize_calls = []

        def fake_organize(output_dir, volume_label, detected_title):
            organize_calls.append(detected_title)
            return {"success": True, "main_file": out_file}

        monkeypatch.setattr(jm_mod.movie_organizer, "organize", fake_organize)

        async def _rip(*args, **kwargs):
            # Mutate the row mid-"rip", as a future answer endpoint would.
            async with _unit_session_factory() as s:
                j = await s.get(DiscJob, job.id)
                j.content_type = ContentType.MOVIE
                j.detected_title = "Fresh Title"
                await s.commit()
            return RipResult(success=True, output_files=[out_file])

        monkeypatch.setattr(job_manager._extractor, "rip_titles", AsyncMock(side_effect=_rip))

        await job_manager._run_ripping(job.id)

        async with _unit_session_factory() as session:
            refreshed = await session.get(DiscJob, job.id)
        assert refreshed.state == JobState.COMPLETED  # movie path, not MATCHING
        assert refreshed.final_path == str(out_file)
        assert organize_calls == ["Fresh Title"]  # fresh detected_title, not the stale local

    async def test_converge_skips_job_already_out_of_ripping(self, rip_env):
        """All-titles-stalled rips park in REVIEW_NEEDED before convergence runs;
        the convergence must not stomp that review_reason and leaves the prompt
        in place (the review flow / answer endpoints own it)."""
        job, _title = await _seed(
            content_type=ContentType.TV, staging=str(rip_env), identity_prompt_json=_PROMPT
        )
        async with _unit_session_factory() as s:
            j = await s.get(DiscJob, job.id)
            j.state = JobState.REVIEW_NEEDED
            j.review_reason = "stall reason"
            await s.commit()

        await job_manager._converge_identity_pending_job(
            job.id, {"kind": "name", "reason": "Disc label unreadable"}
        )

        async with _unit_session_factory() as session:
            refreshed = await session.get(DiscJob, job.id)
        assert refreshed.state == JobState.REVIEW_NEEDED
        assert refreshed.review_reason == "stall reason"  # not overwritten
        assert refreshed.identity_prompt_json == _PROMPT  # left in place

    async def test_converge_missing_reason_uses_fallback(self, rip_env):
        job, _title = await _seed(
            content_type=ContentType.TV,
            staging=str(rip_env),
            identity_prompt_json=json.dumps({"kind": "name"}),
        )

        assert await job_manager._converge_identity_pending_job(job.id, {"kind": "name"}) is True

        async with _unit_session_factory() as session:
            refreshed = await session.get(DiscJob, job.id)
        assert refreshed.state == JobState.REVIEW_NEEDED
        assert refreshed.review_reason == jm_mod._FALLBACK_IDENTITY_REVIEW_REASON
        assert refreshed.identity_prompt_json is None

    async def test_converge_skips_park_when_prompt_answered_mid_window(self, rip_env):
        """B5 race tolerance (the B4 TOCTOU note): an answer landing between
        the rip-end prompt read and convergence clears the prompt on the row —
        convergence must re-check the fresh row and NOT park the answered job
        behind a stale question."""
        job, _title = await _seed(
            content_type=ContentType.TV,
            staging=str(rip_env),
            identity_prompt_json=None,  # the answer already cleared it
        )

        converged = await job_manager._converge_identity_pending_job(
            job.id, {"kind": "name", "reason": "Disc label unreadable"}
        )

        assert converged is False  # caller falls through to the normal flow
        async with _unit_session_factory() as session:
            refreshed = await session.get(DiscJob, job.id)
        assert refreshed.state == JobState.RIPPING  # untouched
        assert refreshed.review_reason is None

    async def test_run_ripping_falls_through_when_answered_during_convergence(
        self, rip_env, monkeypatch
    ):
        """End-to-end race: the answer lands during the backfill/reconcile
        window. The job must continue down the normal TV post-rip path
        (RIPPING→MATCHING) instead of parking in review with a stale reason."""
        job, _title = await _seed(
            content_type=ContentType.TV,
            staging=str(rip_env),
            is_selected=True,
            identity_prompt_json=_PROMPT,
        )

        async def answer_during_backfill(*args, **kwargs):
            # Simulate the B5 answer endpoint committing mid-window.
            async with _unit_session_factory() as s:
                j = await s.get(DiscJob, job.id)
                j.identity_prompt_json = None
                j.detected_title = "Answered Show"
                await s.commit()

        monkeypatch.setattr(
            job_manager, "_backfill_unmatched_titles", AsyncMock(side_effect=answer_during_backfill)
        )
        _mock_rip(monkeypatch, RipResult(success=True, output_files=[]))

        await job_manager._run_ripping(job.id)

        async with _unit_session_factory() as session:
            refreshed = await session.get(DiscJob, job.id)
        assert refreshed.state == JobState.MATCHING  # normal TV path, not REVIEW_NEEDED
        assert refreshed.review_reason is None


async def _seed_two_selected(staging):
    """Seed a TV job with two selected titles (so a full-disc single pass fires)."""
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="SHOW_S1D1",
            content_type=ContentType.TV,
            state=JobState.RIPPING,
            detected_title="Some Show",
            detected_season=1,
            staging_path=staging,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        for idx in (0, 1):
            session.add(
                DiscTitle(
                    job_id=job.id,
                    title_index=idx,
                    duration_seconds=1380,
                    state=TitleState.RIPPING,
                    is_selected=True,
                )
            )
        await session.commit()
        return job


@pytest.mark.unit
class TestOnePassRipFallback:
    """Step 4: rip the whole disc in one MakeMKV pass when every title is
    selected, and re-rip only the still-missing titles individually if it fails."""

    def test_has_complete_output_detects_nonempty_title_file(self, tmp_path):
        from types import SimpleNamespace

        t0 = SimpleNamespace(title_index=0, output_index=None)
        t1 = SimpleNamespace(title_index=1, output_index=None)
        assert job_manager._has_complete_output(tmp_path, t0) is False
        (tmp_path / "Some Show_t00.mkv").write_bytes(b"data")
        assert job_manager._has_complete_output(tmp_path, t0) is True
        # An empty file is not "complete".
        (tmp_path / "Some Show_t01.mkv").write_bytes(b"")
        assert job_manager._has_complete_output(tmp_path, t1) is False

    def test_has_complete_output_prefers_output_index_when_numbering_offset(self, tmp_path):
        """Issue #517: disc has no "t00" — MakeMKV's native numbering starts at 1.

        Title scan-index 0 has output_index=1 (its suggested filename was
        "..._t01.mkv"). The one-pass fallback's "is this title already done?"
        check must consult output_index, not title_index, or it will treat an
        already-ripped title as still missing and needlessly re-rip it.
        """
        from types import SimpleNamespace

        t0 = SimpleNamespace(title_index=0, output_index=1)
        assert job_manager._has_complete_output(tmp_path, t0) is False
        (tmp_path / "Some Show_t01.mkv").write_bytes(b"data")
        assert job_manager._has_complete_output(tmp_path, t0) is True

    async def test_all_selected_rips_in_single_pass(self, rip_env, monkeypatch):
        """When every title is selected, the rip uses one 'all' invocation
        (title_indices=None) instead of one command per title."""
        job, title = await _seed(
            content_type=ContentType.TV, staging=str(rip_env), is_selected=True, title_index=0
        )
        monkeypatch.setattr(job_manager, "_backfill_unmatched_titles", AsyncMock())

        async def _run(*args, **kwargs):
            # Simulate the single pass finishing the title so no fallback fires.
            async with _unit_session_factory() as s:
                t = await s.get(DiscTitle, title.id)
                t.state = TitleState.MATCHED
                await s.commit()
            return RipResult(success=True, output_files=[])

        rip = AsyncMock(side_effect=_run)
        monkeypatch.setattr(job_manager._extractor, "rip_titles", rip)

        await job_manager._run_ripping(job.id)

        assert rip.await_count == 1
        assert rip.await_args_list[0].kwargs["title_indices"] is None

    async def test_single_pass_failure_reripsonly_missing(self, rip_env, monkeypatch):
        """A single pass that leaves titles unripped triggers a per-title
        fallback for exactly the missing titles."""
        job = await _seed_two_selected(str(rip_env))
        monkeypatch.setattr(job_manager, "_backfill_unmatched_titles", AsyncMock())

        # Both passes produce no files, so both titles remain missing after the
        # 'all' pass → fallback re-rips them individually.
        rip = AsyncMock(return_value=RipResult(success=True, output_files=[]))
        monkeypatch.setattr(job_manager._extractor, "rip_titles", rip)

        await job_manager._run_ripping(job.id)

        assert rip.await_count == 2
        assert rip.await_args_list[0].kwargs["title_indices"] is None  # one-pass
        assert sorted(rip.await_args_list[1].kwargs["title_indices"]) == [0, 1]  # fallback


@pytest.mark.unit
class TestRunRippingCallsNotifyEjected:
    async def test_notify_ejected_called_after_rip(self, rip_env, monkeypatch):
        """_run_ripping must call notify_ejected on the drive monitor after ejecting."""
        from unittest.mock import MagicMock

        job, _ = await _seed(content_type=ContentType.TV, staging=str(rip_env), is_selected=True)
        monkeypatch.setattr(job_manager, "_backfill_unmatched_titles", AsyncMock())
        _mock_rip(monkeypatch, RipResult(success=True, output_files=[]))

        spy = MagicMock()
        monkeypatch.setattr(job_manager._drive_monitor, "notify_ejected", spy)

        await job_manager._run_ripping(job.id)

        spy.assert_called_once_with(job.drive_id)


@pytest.mark.unit
class TestNotifyEjected:
    def test_resets_drive_state_and_clears_pending(self):
        """notify_ejected resets _drive_states and clears any pending debounce."""
        from app.core.sentinel import DriveMonitor

        monitor = DriveMonitor()
        monitor._drive_states["/dev/sr0"] = True
        monitor._pending_changes["/dev/sr0"] = 1

        monitor.notify_ejected("/dev/sr0")

        assert monitor._drive_states["/dev/sr0"] is False
        assert "/dev/sr0" not in monitor._pending_changes

    def test_no_error_when_drive_not_tracked(self):
        """notify_ejected on an untracked drive is a no-op, does not raise."""
        from app.core.sentinel import DriveMonitor

        monitor = DriveMonitor()
        monitor.notify_ejected("/dev/sr99")  # never added to _drive_states


@pytest.mark.unit
class TestCreateJobForDiscDedup:
    """A disc inserted while the prior job for the same drive is *past ripping*
    (MATCHING/ORGANIZING) must start a new job: ripping ejects the disc + calls
    notify_ejected() before the RIPPING->MATCHING transition, so the drive is
    physically free. A re-insert of the SAME disc (same volume_label) while such a
    job runs is still skipped, so a disc that lingered after a reported-but-
    incomplete eject can't spawn a duplicate. Disc-required states
    (IDLE/IDENTIFYING/RIPPING) always block regardless of label.

    Regression for: new disc not detected while the prior job is MATCHING.
    """

    @pytest.fixture(autouse=True)
    def _neutralize(self, monkeypatch):
        from types import SimpleNamespace

        # A created job must not spawn a real disc-identification task.
        monkeypatch.setattr(job_manager._identification, "identify_disc", AsyncMock())
        # These tests exercise label-based dedup; stub the disc-hash probe to
        # None (no fingerprint → label fallback) so they don't pay real disk I/O
        # plus retry sleeps probing a fake drive.
        monkeypatch.setattr(job_manager, "_compute_disc_hash", AsyncMock(return_value=None))
        # The unit DB's default AppConfig has setup_complete=False, which would
        # trip the first-run gate and park every insert — these tests exercise
        # dedup on a configured install.
        monkeypatch.setattr(
            "app.services.config_service.get_config",
            AsyncMock(
                return_value=SimpleNamespace(staging_path="/tmp/staging", setup_complete=True)
            ),
        )
        # Start each test with clean per-drive cooldown + lock state.
        job_manager._last_job_created_at.clear()
        job_manager._drive_locks.clear()
        # _on_task_done removes the entry on the *next* loop iteration, so a
        # prior test's no-op task can leave a stale entry — clear it explicitly.
        job_manager._active_jobs.clear()

    async def _seed_job(self, state, label, drive="E:"):
        async with _unit_session_factory() as session:
            job = DiscJob(
                drive_id=drive,
                volume_label=label,
                state=state,
                staging_path="/tmp/seed",
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            return job.id

    async def _insert(self, label, drive="E:"):
        await job_manager._create_job_for_disc(drive, label)
        # Let any spawned (no-op) identify task settle so it doesn't dangle.
        await asyncio.sleep(0)

    async def _jobs_for_drive(self, drive="E:"):
        async with _unit_session_factory() as session:
            result = await session.execute(select(DiscJob).where(DiscJob.drive_id == drive))
            return result.scalars().all()

    async def test_new_disc_during_matching_creates_job(self):
        """The regression: a *different* disc inserted during MATCHING starts a job."""
        await self._seed_job(JobState.MATCHING, "GILMORE_GIRLS_S1_D1")

        await self._insert("GILMORE_GIRLS_S1_D2")

        jobs = await self._jobs_for_drive()
        assert len(jobs) == 2
        assert any(
            j.state == JobState.IDENTIFYING and j.volume_label == "GILMORE_GIRLS_S1_D2"
            for j in jobs
        )

    async def test_same_label_during_matching_is_skipped(self):
        """A same-disc re-insert during MATCHING is skipped (no duplicate / no loop)."""
        await self._seed_job(JobState.MATCHING, "DVD_VIDEO")

        await self._insert("DVD_VIDEO")

        assert len(await self._jobs_for_drive()) == 1

    async def test_organizing_different_label_allowed(self):
        """ORGANIZING (disc long gone) does not block a different disc."""
        await self._seed_job(JobState.ORGANIZING, "MOVIE_A")

        await self._insert("MOVIE_B")

        assert len(await self._jobs_for_drive()) == 2

    async def test_organizing_same_label_is_skipped(self):
        """A same-disc re-insert during ORGANIZING is skipped — symmetric with MATCHING."""
        await self._seed_job(JobState.ORGANIZING, "MOVIE_A")

        await self._insert("MOVIE_A")

        assert len(await self._jobs_for_drive()) == 1

    @pytest.mark.parametrize("state", [JobState.IDLE, JobState.IDENTIFYING, JobState.RIPPING])
    async def test_disc_required_state_blocks_even_different_label(self, state):
        """Pre-eject states keep blocking — the disc is still in the drive."""
        await self._seed_job(state, "DISC_A")

        await self._insert("DISC_B")

        assert len(await self._jobs_for_drive()) == 1

    @pytest.mark.parametrize("state", [JobState.REVIEW_NEEDED, JobState.FAILED, JobState.COMPLETED])
    async def test_terminal_or_review_does_not_block(self, state):
        """Terminal / review jobs never block, and re-detect does not loop."""
        await self._seed_job(state, "DISC_A")

        await self._insert("DISC_A")

        assert len(await self._jobs_for_drive()) == 2

    async def test_cooldown_still_gates_rapid_recreate(self):
        """The 15s per-drive cooldown still suppresses a too-soon second create."""
        job_manager._last_job_created_at["E:"] = time.monotonic()

        await self._insert("DISC_B")

        assert len(await self._jobs_for_drive()) == 0


@pytest.mark.unit
class TestDispatchTitleMatchTOCTOU:
    """Fix 1 (B3 review): the in-flight sentinel is claimed BEFORE any await, so
    two concurrent ``_dispatch_title_match`` calls for the same title cannot both
    slip through the membership check and spawn duplicate match tasks (TOCTOU).

    The match task is always dispatched — there is no DiscDB early-return in
    ``_dispatch_title_match``.  The sentinel is claimed atomically before the
    dispatch ``await``, held for the lifetime of the spawned task, and released
    by ``_on_match_dispatch_done`` when the task completes.  Any early-return
    path that does NOT dispatch a task must discard the sentinel so a future
    legitimate dispatch is not permanently blocked."""

    def _stub_matching(self, monkeypatch):
        release = asyncio.Event()
        calls = []

        async def slow_match(jid, tid, path):
            calls.append((jid, tid))
            await release.wait()

        monkeypatch.setattr(job_manager._matching, "match_single_file", slow_match)
        monkeypatch.setattr(job_manager._matching, "on_match_task_done", lambda *a, **k: None)
        return calls, release

    async def test_concurrent_calls_spawn_exactly_one_task(self, tmp_path, monkeypatch):
        """Two concurrent _dispatch_title_match for the same title must not
        double-spawn: the sentinel is claimed before any await so both coroutines
        can't both pass the membership check."""
        job, title = await _seed(content_type=ContentType.TV)
        calls, release = self._stub_matching(monkeypatch)

        f = tmp_path / "show_t00.mkv"
        f.write_text("")

        # Fire both dispatches concurrently — no await between them on the
        # caller side, so both enter _dispatch_title_match before either
        # has a chance to add to the set.  The fixed code claims the slot
        # synchronously before the first await, making this race impossible.
        first, second = await asyncio.gather(
            job_manager._dispatch_title_match(job.id, title.id, f),
            job_manager._dispatch_title_match(job.id, title.id, f),
        )
        await asyncio.sleep(0)

        # Exactly one dispatch returns True, the other False.
        assert sorted([first, second]) == [False, True]
        assert calls == [(job.id, title.id)]

        # Let the task finish; sentinel released so future dispatches work.
        release.set()
        await asyncio.sleep(0.05)
        assert title.id not in job_manager._inflight_match_dispatch

    async def test_match_task_releases_sentinel_on_completion(self, tmp_path, monkeypatch):
        """The in-flight sentinel is released by _on_match_dispatch_done once the
        match task finishes. ASR-preferred precedence: _dispatch_title_match always
        spawns a match task (no DiscDB pre-dispatch short-circuit)."""
        job, title = await _seed(content_type=ContentType.TV)
        calls, release = self._stub_matching(monkeypatch)

        f = tmp_path / "show_t00.mkv"
        f.write_text("")

        result = await job_manager._dispatch_title_match(job.id, title.id, f)
        assert result is True
        # Sentinel is held while the task is running.
        assert title.id in job_manager._inflight_match_dispatch

        # Let the task finish; sentinel is released by _on_match_dispatch_done.
        release.set()
        await asyncio.sleep(0.05)
        assert title.id not in job_manager._inflight_match_dispatch

        # A follow-up dispatch (e.g. after a re-rip) must not be blocked.
        result2 = await job_manager._dispatch_title_match(job.id, title.id, f)
        assert result2 is True
        release.set()
        await asyncio.sleep(0.05)


@pytest.mark.unit
class TestTransitionTitleOutOfRippingGate:
    """Fix 2 (B3 review): ``_transition_title_out_of_ripping`` applies the same
    identity gate as ``_on_title_ripped``.  A pending identity prompt must park
    every content-type in QUEUED, not MATCHED, so titles stay visible to
    ``dispatch_pending_matches`` and don't leak through unmatched."""

    async def test_tv_without_prompt_parks_queued(self, monkeypatch):
        job, title = await _seed(content_type=ContentType.TV)
        await job_manager._transition_title_out_of_ripping(
            job.id, title.id, ContentType.TV, expected_size=1024, actual_size=1024
        )
        t = await _get_title(title.id)
        assert t.state == TitleState.QUEUED

    async def test_movie_without_prompt_becomes_matched(self, monkeypatch):
        job, title = await _seed(content_type=ContentType.MOVIE)
        await job_manager._transition_title_out_of_ripping(
            job.id, title.id, ContentType.MOVIE, expected_size=1024, actual_size=1024
        )
        t = await _get_title(title.id)
        assert t.state == TitleState.MATCHED

    @pytest.mark.parametrize("content_type", [ContentType.UNKNOWN, ContentType.MOVIE])
    async def test_non_tv_with_prompt_parks_queued_not_matched(self, monkeypatch, content_type):
        """An identity-pending job must NOT leak to MATCHED via the fs-monitor
        path, just as it cannot via _on_title_ripped."""
        job, title = await _seed(content_type=content_type, identity_prompt_json=_PROMPT)
        await job_manager._transition_title_out_of_ripping(
            job.id, title.id, content_type, expected_size=1024, actual_size=1024
        )
        t = await _get_title(title.id)
        assert t.state == TitleState.QUEUED

    async def test_tv_with_prompt_parks_queued(self, monkeypatch):
        job, title = await _seed(content_type=ContentType.TV, identity_prompt_json=_PROMPT)
        await job_manager._transition_title_out_of_ripping(
            job.id, title.id, ContentType.TV, expected_size=1024, actual_size=1024
        )
        t = await _get_title(title.id)
        assert t.state == TitleState.QUEUED

    async def test_non_ripping_title_is_untouched(self, monkeypatch):
        """Guard: only RIPPING titles are transitioned."""
        job, title = await _seed(content_type=ContentType.TV, state=TitleState.QUEUED)
        await job_manager._transition_title_out_of_ripping(
            job.id, title.id, ContentType.TV, expected_size=1024, actual_size=1024
        )
        t = await _get_title(title.id)
        assert t.state == TitleState.QUEUED  # unchanged (was QUEUED, not RIPPING)
