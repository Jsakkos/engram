"""Unit tests for JobManager's mockable handlers.

Targets _on_title_ripped (per-title completion routing) and _rerun_matching
(re-match dispatch + DiscDB restore). The heavy _run_ripping orchestration is
intentionally left to integration tests. The matching/finalization collaborators
and websocket layer are stubbed.
"""

import asyncio
import importlib
import json
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


async def _seed(content_type=ContentType.TV, staging="/tmp/staging", **title_kwargs):
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="SHOW_S1D1",
            content_type=content_type,
            state=JobState.RIPPING,
            detected_title="Some Show",
            detected_season=1,
            staging_path=staging,
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
        monkeypatch.setattr(
            job_manager._matching, "try_discdb_assignment", AsyncMock(return_value=False)
        )
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

    async def test_tv_title_with_discdb_checks_completion(self, tmp_path, monkeypatch):
        job, title = await _seed(content_type=ContentType.TV)
        monkeypatch.setattr(
            job_manager._matching, "try_discdb_assignment", AsyncMock(return_value=True)
        )
        dispatch = AsyncMock()
        monkeypatch.setattr(job_manager._matching, "match_single_file", dispatch)
        completion = AsyncMock()
        monkeypatch.setattr(job_manager._finalization, "check_job_completion", completion)
        path = tmp_path / "show_t00.mkv"
        path.write_text("")

        await job_manager._on_title_ripped(job.id, 1, path, [title])

        completion.assert_awaited_once()
        dispatch.assert_not_called()

    async def test_unresolvable_file_is_noop(self, tmp_path):
        job, title = await _seed(content_type=ContentType.TV)
        path = tmp_path / "unrelated.mkv"
        path.write_text("")

        # No sorted_titles to map to → resolve returns None → early return.
        await job_manager._on_title_ripped(job.id, 1, path, [])

        t = await _get_title(title.id)
        assert t.state == TitleState.RIPPING  # unchanged


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
        assert job_manager._has_complete_output(tmp_path, 0) is False
        (tmp_path / "Some Show_t00.mkv").write_bytes(b"data")
        assert job_manager._has_complete_output(tmp_path, 0) is True
        # An empty file is not "complete".
        (tmp_path / "Some Show_t01.mkv").write_bytes(b"")
        assert job_manager._has_complete_output(tmp_path, 1) is False

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
