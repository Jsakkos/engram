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
from unittest.mock import AsyncMock

import pytest

from app.core.extractor import BackupResult
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.job_manager import job_manager
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
    monkeypatch.setattr(_JM, "has_room_for_backup", lambda dest, needed: True)
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
        config=config,
    )


@pytest.fixture
def dest(monkeypatch, tmp_path, spies):
    """The computed backup destination, patched to a tmp path."""
    target = tmp_path / "backups" / "TV" / "Show" / "Disc 1"
    monkeypatch.setattr(_JM, "backup_destination", lambda job, config: target)
    spies.backup_disc.return_value = BackupResult(success=True, dest=target)
    return target


async def _seed(*, indices=(0, 1), durations=(2600, 2601)) -> int:
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
        for idx, dur in zip(indices, durations, strict=True):
            session.add(
                DiscTitle(
                    job_id=job.id,
                    title_index=idx,
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
    """Shape of extractor.TitleInfo as reconcile_titles reads it."""

    def __init__(self, index: int, duration: int, source_filename: str, segment_map: str):
        self.index = index
        self.duration_seconds = duration
        self.source_filename = source_filename
        self.segment_map = segment_map


def _matching_scan():
    return [
        _ScannedTitle(0, 2600, "00000.m2ts", "1"),
        _ScannedTitle(1, 2601, "00001.m2ts", "2"),
    ]


class TestFallbackMatrix:
    """Every backup problem degrades to a direct rip, never to a failed job."""

    @pytest.mark.asyncio
    async def test_no_backup_location_configured(self, spies, monkeypatch):
        monkeypatch.setattr(_JM, "backup_destination", lambda job, config: None)
        job_id = await _seed()

        await job_manager._run_backup(job_id)

        job = await _load(job_id)
        assert job.backup_status == "skipped"
        assert job.backup_status_reason == "not_configured"
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
        assert job.backup_status_reason == "insufficient_space"
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
        assert job.backup_status_reason == "no_disc_index"
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
