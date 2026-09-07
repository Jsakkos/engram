"""The BACKING_UP phase: copy the disc, then rip from the copy.

The governing rule is that every backup problem degrades to a direct rip from
the drive, which is exactly what the job would have done with the setting off.
Turning backups on must never make a disc less likely to finish. The one
exception is a backup that succeeded but cannot be reconciled against the disc
scan: that parks for review, because the drive has already been released and
the disc may be out of it.

Everything external is patched at its boundary. No makemkvcon is ever launched.
Every job here stays NON-terminal on purpose: a job reaching COMPLETED/FAILED
spawns a Discord notification task that leaks a pooled connection in tests.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.extractor import BackupResult
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.job_manager import JobManager, job_manager
from app.services.ripping_helpers import expected_native_index
from tests.unit.conftest import _unit_session_factory

_JM = sys.modules["app.services.job_manager"]


@pytest.fixture(autouse=True)
def _quiet_ws(monkeypatch):
    """No websocket traffic, and no real broadcaster fan-out."""
    from app.api.websocket import manager as ws_manager

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ws_manager, "broadcast_job_update", _noop)
    monkeypatch.setattr(ws_manager, "broadcast_job_state_changed", _noop, raising=False)


@pytest.fixture
def spies(monkeypatch, tmp_path):
    """Patch every boundary _run_backup touches, and hand back the spies."""
    release = AsyncMock(return_value=True)
    run_ripping = AsyncMock(return_value=None)
    backup_disc = AsyncMock(return_value=BackupResult(success=True, dest=tmp_path / "dest"))
    scan_disc = AsyncMock(return_value=([], ""))
    broadcast = AsyncMock(return_value=None)

    monkeypatch.setattr(job_manager, "_release_drive", release)
    monkeypatch.setattr(job_manager, "_run_ripping", run_ripping)
    monkeypatch.setattr(job_manager._extractor, "backup_disc", backup_disc)
    monkeypatch.setattr(job_manager._extractor, "scan_disc", scan_disc)
    monkeypatch.setattr(_JM.event_broadcaster, "broadcast_backup_progress", broadcast)

    # Module-level names are looked up on the module, not through the singleton.
    has_room = MagicMock(return_value=True)
    monkeypatch.setattr(_JM, "has_room_for_backup", has_room)
    monkeypatch.setattr(_JM, "resolve_disc_index", AsyncMock(return_value="disc:0"))

    config = SimpleNamespace(
        makemkv_path="makemkvcon",
        auto_eject_enabled=False,
        # Keeps the REVIEW_NEEDED Discord observer a clean no-op.
        discord_webhook_url=None,
    )
    monkeypatch.setattr("app.services.config_service.get_config", AsyncMock(return_value=config))

    return SimpleNamespace(
        release=release,
        run_ripping=run_ripping,
        backup_disc=backup_disc,
        scan_disc=scan_disc,
        broadcast=broadcast,
        has_room=has_room,
        config=config,
    )


@pytest.fixture
def dest(monkeypatch, tmp_path, spies):
    """The computed backup destination, patched to a tmp path."""
    target = tmp_path / "backups" / "TV" / "Show" / "Disc 1"
    monkeypatch.setattr(_JM, "backup_destination", lambda job, config: target)
    spies.backup_disc.return_value = BackupResult(success=True, dest=target)
    return target


async def _seed(*, indices=(0, 1), durations=(2600, 2601), output_indices=None) -> int:
    """Seed a job with two titles, as a modern disc scan would leave them.

    ``output_index`` is populated on purpose: identification_coordinator sets it
    on every scan, and ``ripping_helpers.expected_native_index`` PREFERS it over
    ``title_index``. A seed that left it None made every resolution site fall
    back to ``title_index`` and so hid the stale-``output_index`` bug entirely.
    """
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="SHOW_S1_D1",
            content_type=ContentType.TV,
            state=JobState.BACKING_UP,
            detected_title="Show",
            detected_season=1,
            staging_path="/tmp/staging/job",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        outs = output_indices if output_indices is not None else indices
        for idx, dur, out in zip(indices, durations, outs, strict=True):
            session.add(
                DiscTitle(
                    job_id=job.id,
                    title_index=idx,
                    output_index=out,
                    duration_seconds=dur,
                    file_size_bytes=5 * 1024**3,
                    state=TitleState.PENDING,
                    source_filename=f"0000{idx}.m2ts",
                    segment_map=str(idx + 1),
                )
            )
        await session.commit()
        return job.id


async def _load(job_id: int) -> DiscJob:
    async with _unit_session_factory() as session:
        return await session.get(DiscJob, job_id)


class _ScannedTitle:
    """Shape of extractor.TitleInfo as reconcile_titles reads it.

    ``disc_title`` is MakeMKV's suggested output filename (TINFO attr 27); it is
    what ``output_index`` is derived from, on the disc scan and on the backup
    re-scan alike.
    """

    def __init__(
        self,
        index: int,
        duration: int,
        source_filename: str,
        segment_map: str,
        disc_title: str | None = None,
    ):
        self.index = index
        self.duration_seconds = duration
        self.source_filename = source_filename
        self.segment_map = segment_map
        self.disc_title = f"SHOW_t{index:02d}.mkv" if disc_title is None else disc_title


def _matching_scan():
    return [
        _ScannedTitle(0, 2600, "00000.m2ts", "1"),
        _ScannedTitle(1, 2601, "00001.m2ts", "2"),
    ]


async def _titles(job_id: int) -> list[DiscTitle]:
    async with _unit_session_factory() as session:
        from sqlmodel import select

        rows = (
            (await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id)))
            .scalars()
            .all()
        )
        return sorted(rows, key=lambda r: r.id)


class TestFallbackMatrix:
    """Every backup problem degrades to a direct rip, never to a failed job."""

    @pytest.mark.asyncio
    async def test_no_backup_location_configured(self, spies, monkeypatch):
        monkeypatch.setattr(_JM, "backup_destination", lambda job, config: None)
        job_id = await _seed()

        await job_manager._run_backup(job_id)

        job = await _load(job_id)
        assert job.backup_status == "skipped"
        assert job.backup_status_reason == "no backup location is configured"
        assert job.source_spec is None
        assert job.state == JobState.RIPPING
        spies.release.assert_not_called()
        spies.backup_disc.assert_not_called()

    @pytest.mark.asyncio
    async def test_insufficient_space(self, spies, dest, monkeypatch):
        monkeypatch.setattr(_JM, "has_room_for_backup", lambda d, needed: False)
        job_id = await _seed()

        await job_manager._run_backup(job_id)

        job = await _load(job_id)
        assert job.backup_status == "skipped"
        assert "not enough free space" in job.backup_status_reason
        assert job.source_spec is None
        assert job.state == JobState.RIPPING
        spies.release.assert_not_called()
        spies.backup_disc.assert_not_called()

    @pytest.mark.asyncio
    async def test_drive_has_no_resolvable_disc_index(self, spies, dest, monkeypatch):
        monkeypatch.setattr(_JM, "resolve_disc_index", AsyncMock(return_value=None))
        job_id = await _seed()

        await job_manager._run_backup(job_id)

        job = await _load(job_id)
        assert job.backup_status == "skipped"
        assert "could not address drive E:" in job.backup_status_reason
        assert job.source_spec is None
        assert job.state == JobState.RIPPING
        spies.release.assert_not_called()
        spies.backup_disc.assert_not_called()

    @pytest.mark.asyncio
    async def test_backup_itself_failed(self, spies, dest):
        spies.backup_disc.return_value = BackupResult(
            success=False, error_message="disc read error at 43%"
        )
        job_id = await _seed()

        await job_manager._run_backup(job_id)

        job = await _load(job_id)
        assert job.backup_status == "failed"
        assert job.backup_status_reason == "disc read error at 43%"
        # The copy is unusable, so extraction must still read the disc.
        assert job.source_spec is None
        assert job.state == JobState.RIPPING
        spies.release.assert_not_called()

    @pytest.mark.asyncio
    async def test_an_unexpected_error_still_degrades(self, spies, dest):
        spies.backup_disc.side_effect = RuntimeError("kaboom")
        job_id = await _seed()

        await job_manager._run_backup(job_id)

        job = await _load(job_id)
        assert job.backup_status == "failed"
        assert "kaboom" in job.backup_status_reason
        assert job.state == JobState.RIPPING
        spies.release.assert_not_called()


class TestSuccess:
    @pytest.mark.asyncio
    async def test_success_redirects_extraction_and_releases_the_drive(self, spies, dest):
        spies.backup_disc.return_value = BackupResult(success=True, dest=dest)
        spies.scan_disc.return_value = (_matching_scan(), "")
        job_id = await _seed()

        await job_manager._run_backup(job_id)

        job = await _load(job_id)
        assert job.backup_status == "completed"
        assert job.backup_status_reason is None
        assert job.backup_path == str(dest)
        assert job.source_spec == f"file:{dest}"
        assert job.state == JobState.RIPPING

        spies.release.assert_awaited_once()
        assert spies.release.await_args.args == (job_id, "E:", "Backed up")

    @pytest.mark.asyncio
    async def test_the_backup_is_addressed_by_disc_index_but_locks_as_the_drive(self, spies, dest):
        spies.scan_disc.return_value = (_matching_scan(), "")
        job_id = await _seed()

        await job_manager._run_backup(job_id)

        source = spies.backup_disc.await_args.args[0]
        assert source.makemkv_arg == "disc:0"
        # The lock that matters is the physical drive's, not the index's.
        from app.core.disc_source import DiscSource

        assert source.lock_key == DiscSource.parse("E:").lock_key

    @pytest.mark.asyncio
    async def test_an_existing_backup_is_reused_not_recopied(self, spies, dest):
        dest.mkdir(parents=True)
        (dest / "BDMV").mkdir()
        spies.scan_disc.return_value = (_matching_scan(), "")
        job_id = await _seed()

        await job_manager._run_backup(job_id)

        spies.backup_disc.assert_not_called()
        # Nothing is about to be written, so the (potentially slow, potentially
        # networked) free-space probe must not run either.
        spies.has_room.assert_not_called()
        job = await _load(job_id)
        assert job.backup_status == "completed"
        assert job.source_spec == f"file:{dest}"
        assert job.state == JobState.RIPPING
        spies.release.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_an_empty_destination_directory_is_not_a_backup(self, spies, dest):
        dest.mkdir(parents=True)
        spies.scan_disc.return_value = (_matching_scan(), "")
        job_id = await _seed()

        await job_manager._run_backup(job_id)

        spies.backup_disc.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_remapped_scan_rewrites_the_stored_indices(self, spies, dest):
        # The backup enumerated the same two titles in the other order.
        spies.scan_disc.return_value = (
            [
                _ScannedTitle(7, 2600, "00000.m2ts", "1"),
                _ScannedTitle(9, 2601, "00001.m2ts", "2"),
            ],
            "",
        )
        job_id = await _seed()

        await job_manager._run_backup(job_id)

        async with _unit_session_factory() as session:
            from sqlmodel import select

            rows = (
                (await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id)))
                .scalars()
                .all()
            )
        assert sorted(r.title_index for r in rows) == [7, 9]
        job = await _load(job_id)
        assert job.state == JobState.RIPPING


class TestRemapRewritesTheRipTarget:
    """A remap must move ``output_index`` too, or files land on the wrong rows.

    ``ripping_helpers.expected_native_index`` PREFERS ``output_index`` over
    ``title_index``, and every site that maps a produced ``..._tNN.mkv`` back
    onto a ``DiscTitle`` goes through it. So these assertions are deliberately
    on ``expected_native_index``, not on ``title_index``: rewriting only the
    latter leaves the resolution path reading the stale disc-scan number, which
    is the silent mis-file this whole module exists to prevent.
    """

    @pytest.mark.asyncio
    async def test_a_swapped_pair_does_not_invert_onto_each_other(self, spies, dest):
        # The backup enumerated the two titles in the opposite order: the
        # content of scan-index 0 is the backup's title 1 and vice versa.
        spies.scan_disc.return_value = (
            [
                _ScannedTitle(0, 2601, "00001.m2ts", "2"),
                _ScannedTitle(1, 2600, "00000.m2ts", "1"),
            ],
            "",
        )
        job_id = await _seed()

        await job_manager._run_backup(job_id)

        rows = await _titles(job_id)
        by_source = {r.source_filename: r for r in rows}
        a, b = by_source["00000.m2ts"], by_source["00001.m2ts"]
        # Row A's content is title 1 in the backup, row B's is title 0.
        assert (a.title_index, b.title_index) == (1, 0)
        # Without the output_index rewrite these would still read (0, 1), and
        # LABEL_t01.mkv (A's content) would resolve onto row B.
        assert expected_native_index(a) == 1
        assert expected_native_index(b) == 0

    @pytest.mark.asyncio
    async def test_a_scan_with_no_suggested_filename_falls_back_to_title_index(self, spies, dest):
        spies.scan_disc.return_value = (
            [
                _ScannedTitle(7, 2600, "00000.m2ts", "1", disc_title=""),
                _ScannedTitle(9, 2601, "00001.m2ts", "2", disc_title=""),
            ],
            "",
        )
        job_id = await _seed()

        await job_manager._run_backup(job_id)

        rows = await _titles(job_id)
        assert [r.output_index for r in rows] == [None, None]
        # None is the legitimate fallback case, not a stale number.
        assert sorted(expected_native_index(r) for r in rows) == [7, 9]

    @pytest.mark.asyncio
    async def test_an_identical_scan_still_refreshes_the_native_numbers(self, spies, dest):
        # Same order, same durations, same disc structure: reconcile_titles
        # reports IDENTICAL. But the backup suggests different _tNN numbers,
        # and _is_identical deliberately does not compare disc_title, so the
        # refresh has to happen unconditionally at the caller.
        spies.scan_disc.return_value = (
            [
                _ScannedTitle(0, 2600, "00000.m2ts", "1", disc_title="SHOW_t04.mkv"),
                _ScannedTitle(1, 2601, "00001.m2ts", "2", disc_title="SHOW_t05.mkv"),
            ],
            "",
        )
        job_id = await _seed()

        await job_manager._run_backup(job_id)

        rows = await _titles(job_id)
        assert [r.title_index for r in rows] == [0, 1]
        assert [expected_native_index(r) for r in rows] == [4, 5]

    @pytest.mark.asyncio
    async def test_an_offset_numbered_backup_keeps_its_native_numbers(self, spies, dest):
        # Issue #517: a disc whose native _tNN does not equal its scan index.
        # Clearing output_index instead of recomputing it would fall back to
        # title_index and look for the wrong files.
        spies.scan_disc.return_value = (
            [
                _ScannedTitle(0, 2600, "00000.m2ts", "1", disc_title="SHOW_t01.mkv"),
                _ScannedTitle(1, 2601, "00001.m2ts", "2", disc_title="SHOW_t02.mkv"),
            ],
            "",
        )
        job_id = await _seed()

        await job_manager._run_backup(job_id)

        rows = await _titles(job_id)
        assert [expected_native_index(r) for r in rows] == [1, 2]


class TestUnreconcilableBackup:
    """A good copy that cannot be lined up parks for review, never guesses."""

    @pytest.mark.asyncio
    async def test_empty_scan_parks_for_review(self, spies, dest):
        spies.scan_disc.return_value = ([], "")
        job_id = await _seed()

        await job_manager._run_backup(job_id)

        job = await _load(job_id)
        assert job.state == JobState.REVIEW_NEEDED
        assert str(dest) in job.review_reason
        assert "safe" in job.review_reason
        # The backup succeeded, so the source is still the copy and the drive
        # was still released.
        assert job.source_spec == f"file:{dest}"
        spies.release.assert_awaited_once()
        spies.run_ripping.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_raising_scan_is_treated_as_an_empty_one(self, spies, dest):
        spies.scan_disc.side_effect = OSError("backup folder vanished")
        job_id = await _seed()

        await job_manager._run_backup(job_id)

        job = await _load(job_id)
        assert job.state == JobState.REVIEW_NEEDED
        spies.run_ripping.assert_not_called()


class TestProgressThrottling:
    @pytest.mark.asyncio
    async def test_sub_percent_callbacks_are_collapsed(self, spies, dest):
        percentages = [0.1, 0.2, 0.9, 1.0, 1.4, 1.9, 2.0, 2.5, 3.0]

        async def _fake_backup(source, target, progress_callback=None, log_dir=None, job_id=0):
            for pct in percentages:
                progress_callback(pct)
            return BackupResult(success=True, dest=target)

        spies.backup_disc.side_effect = _fake_backup
        spies.scan_disc.return_value = (_matching_scan(), "")
        job_id = await _seed()

        await job_manager._run_backup(job_id)

        # One message per whole percent crossed: 0, 1, 2, 3.
        assert spies.broadcast.call_count == 4

    @pytest.mark.asyncio
    async def test_a_repeated_or_backwards_percentage_says_nothing_new(self, spies, dest):
        # MakeMKV re-reports the current percent, and a multi-pass copy can even
        # step backwards. Neither is news, so neither reaches the wire.
        async def _fake_backup(source, target, progress_callback=None, log_dir=None, job_id=0):
            for pct in (5.0, 5.0, 5.7, 3.0, 0.0, 5.9):
                progress_callback(pct)
            return BackupResult(success=True, dest=target)

        spies.backup_disc.side_effect = _fake_backup
        spies.scan_disc.return_value = (_matching_scan(), "")
        job_id = await _seed()

        await job_manager._run_backup(job_id)

        assert spies.broadcast.call_count == 1

    @pytest.mark.asyncio
    async def test_progress_is_reported_in_bytes_against_the_disc_estimate(self, spies, dest):
        async def _fake_backup(source, target, progress_callback=None, log_dir=None, job_id=0):
            progress_callback(50.0)
            return BackupResult(success=True, dest=target)

        spies.backup_disc.side_effect = _fake_backup
        spies.scan_disc.return_value = (_matching_scan(), "")
        job_id = await _seed()

        await job_manager._run_backup(job_id)

        total = 2 * 5 * 1024**3
        assert spies.broadcast.call_args.args == (job_id, total // 2, total)


class TestExistingBackupProbe:
    def test_a_missing_path_is_not_a_backup(self, tmp_path):
        assert _JM._has_existing_backup(tmp_path / "nope") is False

    def test_a_file_is_not_a_backup(self, tmp_path):
        f = tmp_path / "x"
        f.write_text("hi")
        assert _JM._has_existing_backup(f) is False

    def test_a_non_empty_directory_is_a_backup(self, tmp_path):
        d = tmp_path / "d"
        (d / "BDMV").mkdir(parents=True)
        assert _JM._has_existing_backup(d) is True

    def test_an_unreadable_destination_answers_false(self, monkeypatch, tmp_path):
        d = tmp_path / "d"
        d.mkdir()

        def _boom(self):
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "iterdir", _boom)
        assert _JM._has_existing_backup(d) is False


class TestHoldsDiscInDrive:
    """Whether a job in a disc-required state really has the disc loaded.

    This is the decision the disc-insert guard consumes to decide if a new disc
    is refused. It matters because the backup phase releases the drive the
    moment the copy finishes, so a RIPPING job reading the copy must stop
    blocking the tray: otherwise the feature's headline benefit (swap discs in
    minutes rather than hours) does not happen.
    """

    def test_a_rip_from_the_drive_holds_it(self):
        job = DiscJob(drive_id="E:", state=JobState.RIPPING)
        assert JobManager._holds_disc_in_drive(job) is True

    def test_a_rip_from_a_backup_does_not_hold_it(self):
        job = DiscJob(drive_id="E:", state=JobState.RIPPING, source_spec="file:/b/Inception (2010)")
        assert JobManager._holds_disc_in_drive(job) is False

    def test_a_rip_from_an_iso_does_not_hold_it(self):
        job = DiscJob(drive_id="import", state=JobState.RIPPING, source_spec="iso:/b/x.iso")
        assert JobManager._holds_disc_in_drive(job) is False

    def test_a_job_still_copying_holds_it(self):
        # source_spec is only pointed at the copy once the copy has COMPLETED,
        # so a BACKING_UP job reports the drive it is reading and still blocks.
        job = DiscJob(drive_id="E:", state=JobState.BACKING_UP)
        assert JobManager._holds_disc_in_drive(job) is True

    def test_an_unresolvable_source_is_assumed_to_hold_it(self):
        # Refusing a second job is the safe side of the guess.
        job = DiscJob(drive_id="", state=JobState.RIPPING)
        assert JobManager._holds_disc_in_drive(job) is True


class TestEjectDuringBackup:
    """A user must be able to get their disc back out of a long copy.

    A full-disc backup can run for hours. Before this, the Eject button was
    offered during BACKING_UP but the backend rejected the state, so the button
    was dead for the whole phase.
    """

    @pytest.mark.asyncio
    async def test_ejecting_during_a_backup_cancels_the_job(self, monkeypatch):
        job_id = await _seed()
        cancelled = []

        monkeypatch.setattr(sys.modules["app.core.sentinel"], "eject_disc", lambda drive: True)
        monkeypatch.setattr(
            job_manager, "cancel_job", AsyncMock(side_effect=lambda jid: cancelled.append(jid))
        )
        monkeypatch.setattr(job_manager._drive_monitor, "notify_ejected", MagicMock())

        result = await job_manager.eject_disc_for_job(job_id)

        assert result == {"ejected": True, "action": "job_cancelled"}
        assert cancelled == [job_id]

    @pytest.mark.asyncio
    async def test_a_backup_sourced_rip_has_no_disc_to_eject(self, monkeypatch):
        # The disc came out when the copy finished. Ejecting would open an empty
        # tray and send a second "ripped" notification for one disc.
        job_id = await _seed()
        async with _unit_session_factory() as session:
            job = await session.get(DiscJob, job_id)
            job.state = JobState.RIPPING
            job.source_spec = "file:/b/Show/Season 01/S01D01"
            await session.commit()

        release = AsyncMock()
        monkeypatch.setattr(job_manager, "_release_drive", release)

        with pytest.raises(ValueError, match="already released"):
            await job_manager.eject_disc_for_job(job_id)

        release.assert_not_awaited()


class TestStalledBackupDegrades:
    """A stalled copy falls back to a direct rip instead of failing the job.

    This is the feature's governing rule and it bites hardest here: the disc
    most likely to stall a copy is the scratched one the feature exists for, so
    failing the job would make enabling the setting actively worse than leaving
    it off. backup_disc has no per-operation stall watchdog and defers to this
    job-level one, so this branch is the only fallback the stall case has.
    """

    @pytest.mark.asyncio
    async def test_a_stalled_backup_ends_in_ripping_not_failed(self, monkeypatch):
        job_id = await _seed()
        enter_ripping = AsyncMock()
        monkeypatch.setattr(job_manager, "_enter_ripping", enter_ripping)
        cancel = MagicMock()
        monkeypatch.setattr(job_manager._extractor, "cancel", cancel)

        config = SimpleNamespace(
            timeout_identifying_seconds=0,
            timeout_backing_up_seconds=60,
            timeout_ripping_seconds=0,
            timeout_matching_seconds=0,
            timeout_organizing_seconds=0,
        )
        async with _unit_session_factory() as session:
            job = await session.get(DiscJob, job_id)
            # Idle well past the ceiling.
            job_manager._last_activity[job_id] = 0.0
            await job_manager._watchdog_check_job(job, config, now=10_000.0)

        async with _unit_session_factory() as session:
            job = await session.get(DiscJob, job_id)

        assert job.state is not JobState.FAILED
        assert job.backup_status == "failed"
        assert "stalled" in (job.backup_status_reason or "")
        enter_ripping.assert_awaited_once_with(job_id)
        # The copy must be stopped before the rip starts, or two makemkvcon
        # processes fight over one drive.
        cancel.assert_called_once_with(job_id)
