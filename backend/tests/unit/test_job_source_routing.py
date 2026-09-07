"""Every MakeMKV call site reads the JOB's source, not always the drive.

``DiscJob.source_spec`` records what MakeMKV should be pointed at: ``dev:E:``
for a drive, ``file:<path>`` for a backup folder or an imported disc image,
``iso:<path>`` for an ISO. None means "legacy row, derive from ``drive_id``".

Two failures live here. A completed backup sets ``source_spec`` to the copy and
then EJECTS the disc, so a rip that still targeted ``drive_id`` read an emptied
drive and the whole point of the feature never happened. And a disc-image
import carries ``drive_id == "import"``, which is not a MakeMKV source at all.

The end-of-rip drive release is gated on ``DiscSource.is_physical`` for the
same reason: a non-physical source never held a drive, so ejecting again and
re-sending the RIPPED notification would notify the user twice for one disc.

Everything is patched at the extractor boundary; no makemkvcon ever runs.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

import app.services.identification_coordinator as idc
from app.core.analyst import DiscAnalysisResult, TitleInfo
from app.core.disc_source import DiscSource
from app.core.extractor import RipResult, compute_content_hash
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.job_manager import job_manager
from app.services.job_state_machine import JobStateMachine
from tests.unit.conftest import _unit_session_factory

_SCAN_TITLES = [
    TitleInfo(index=0, duration_seconds=1500, size_bytes=2_000_000_000, chapter_count=7),
    TitleInfo(index=1, duration_seconds=2700, size_bytes=4_000_000_000, chapter_count=12),
]


# --- identification -------------------------------------------------------


def _analysis():
    analysis = DiscAnalysisResult(content_type=ContentType.TV, confidence=0.9)
    analysis.detected_name = "Show"
    analysis.detected_season = 1
    analysis.needs_review = False
    analysis.review_reason = None
    analysis.tmdb_id = 1234
    analysis.tmdb_name = "Show"
    analysis._tmdb_signal = None
    analysis._discdb_signal = None
    return analysis


def _bare_coord():
    """A coordinator with everything but the source resolution stubbed out."""
    coord = idc.IdentificationCoordinator.__new__(idc.IdentificationCoordinator)
    coord._extractor = SimpleNamespace(
        scan_disc=AsyncMock(return_value=(_SCAN_TITLES, "SHOW_S1_D1"))
    )
    broadcaster = MagicMock()
    broadcaster.broadcast_job_state_changed = AsyncMock()
    broadcaster.broadcast_job_completed = AsyncMock()
    broadcaster.broadcast_job_failed = AsyncMock()
    coord._broadcaster = broadcaster
    coord._state_machine = JobStateMachine(broadcaster)
    coord._get_discdb_mappings = lambda job_id: []
    coord._run_classification = AsyncMock(return_value=_analysis())
    coord._run_ripping = AsyncMock()
    coord._run_backup = AsyncMock()
    coord._start_subtitle_download = Mock()
    coord._start_subtitle_download_all_seasons = Mock()

    async def _seasons(title, tmdb_id=None):
        return [1]

    coord._resolve_all_season_numbers = _seasons
    return coord


@pytest.fixture
def identify_env(monkeypatch):
    """The identify_disc environment: in-memory DB, no snapshots, no TMDB."""
    monkeypatch.setattr(idc, "async_session", _unit_session_factory)

    import app.core.snapshot as snapshot_mod

    monkeypatch.setattr(snapshot_mod, "save_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(idc, "_resolve_show_year", lambda tmdb_id, signal=None: None)

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(idc.ws_manager, "broadcast_job_update", _noop)
    monkeypatch.setattr(idc.ws_manager, "broadcast_titles_discovered", _noop)


async def _seed_job(**kwargs) -> int:
    fields = {
        "drive_id": "E:",
        "volume_label": "SHOW_S1_D1",
        "state": JobState.IDENTIFYING,
        "staging_path": "/tmp/staging",
    }
    fields.update(kwargs)
    async with _unit_session_factory() as session:
        job = DiscJob(**fields)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


def _scanned_source(coord) -> DiscSource:
    args, _ = coord._extractor.scan_disc.await_args
    return args[0]


@pytest.mark.unit
class TestIdentifyReadsTheJobSource:
    async def test_backup_source_scans_the_copy(self, identify_env, tmp_path):
        job_id = await _seed_job(source_spec=f"file:{tmp_path}")
        coord = _bare_coord()

        await coord.identify_disc(job_id)

        source = _scanned_source(coord)
        assert isinstance(source, DiscSource)
        assert source.spec == f"file:{tmp_path}"
        assert not source.is_physical

    async def test_legacy_job_still_scans_the_drive(self, identify_env):
        """Regression guard for every disc that predates source_spec."""
        job_id = await _seed_job(drive_id="E:", source_spec=None)
        coord = _bare_coord()

        await coord.identify_disc(job_id)

        assert _scanned_source(coord).spec == "dev:E:"

    async def test_disc_image_import_does_not_raise(self, identify_env, tmp_path):
        """drive_id == "import" is not a MakeMKV source; source_spec is."""
        job_id = await _seed_job(drive_id="import", source_spec=f"file:{tmp_path}")
        coord = _bare_coord()

        await coord.identify_disc(job_id)

        assert _scanned_source(coord).spec == f"file:{tmp_path}"
        async with _unit_session_factory() as session:
            job = await session.get(DiscJob, job_id)
        assert job.state != JobState.FAILED

    async def test_sourceless_job_fails_cleanly(self, identify_env):
        """No drive and no spec: fail with a message, never an unhandled raise."""
        job_id = await _seed_job(drive_id="import", source_spec=None)
        coord = _bare_coord()

        await coord.identify_disc(job_id)

        coord._extractor.scan_disc.assert_not_awaited()
        async with _unit_session_factory() as session:
            job = await session.get(DiscJob, job_id)
        assert job.state == JobState.FAILED


# --- ripping --------------------------------------------------------------


class _RecordingExtractor:
    """Captures the source rip_titles was pointed at; rips nothing."""

    def __init__(self):
        self.sources: list = []

    def skip_title_index(self, job_id, idx):
        pass

    def unskip_title_index(self, job_id, idx):
        pass

    async def rip_titles(self, source, output_dir, **kw):
        self.sources.append(source)
        return RipResult(success=True, output_files=[Path("x.mkv")])


async def _seed_rip_job(staging: Path, **kwargs) -> int:
    fields = {
        "drive_id": "Z:",
        "volume_label": "SHOW_S1_D1",
        "state": JobState.RIPPING,
        "content_type": ContentType.TV,
        "staging_path": str(staging),
        "total_titles": 2,
    }
    fields.update(kwargs)
    async with _unit_session_factory() as session:
        job = DiscJob(**fields)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        for idx in (1, 2):
            session.add(
                DiscTitle(
                    job_id=job.id,
                    title_index=idx,
                    output_index=idx,
                    duration_seconds=1300,
                    file_size_bytes=4096,
                    state=TitleState.PENDING,
                    is_selected=True,
                )
            )
        await session.commit()
        return job.id


@pytest.fixture
def rip_env(monkeypatch, tmp_path):
    """Patched extractor + drive release; the staging dir the rip writes to."""
    staging = tmp_path / "staging"
    staging.mkdir()
    ext = _RecordingExtractor()
    release = AsyncMock(return_value=True)
    monkeypatch.setattr(job_manager, "_extractor", ext)
    monkeypatch.setattr(job_manager, "_release_drive", release)
    # Belt and braces: nothing may reach a real tray, whatever the gate does.
    monkeypatch.setattr("app.core.sentinel.eject_disc", lambda *a, **k: None)
    return SimpleNamespace(staging=staging, ext=ext, release=release)


@pytest.mark.unit
class TestRipReadsTheJobSource:
    async def test_backup_source_rips_the_copy_and_holds_the_tray(self, rip_env, tmp_path):
        backup = tmp_path / "backup"
        backup.mkdir()
        job_id = await _seed_rip_job(rip_env.staging, source_spec=f"file:{backup}")

        await job_manager._run_ripping(job_id)

        assert rip_env.ext.sources, "rip_titles was never called"
        source = rip_env.ext.sources[0]
        assert isinstance(source, DiscSource)
        assert source.spec == f"file:{backup}"
        # The disc left the drive at the end of the backup phase, which already
        # sent its own RIPPED event. Releasing again would double-notify.
        rip_env.release.assert_not_awaited()

    async def test_legacy_job_still_rips_the_drive_and_releases_it(self, rip_env):
        """Regression guard: source_spec is None on every pre-feature row."""
        job_id = await _seed_rip_job(rip_env.staging, drive_id="Z:", source_spec=None)

        await job_manager._run_ripping(job_id)

        assert rip_env.ext.sources[0].spec == "dev:Z:"
        rip_env.release.assert_awaited_once()
        assert rip_env.release.await_args.args[1] == "Z:"

    async def test_disc_image_import_does_not_raise(self, rip_env, tmp_path):
        image = tmp_path / "image"
        image.mkdir()
        job_id = await _seed_rip_job(
            rip_env.staging, drive_id="import", source_spec=f"file:{image}"
        )

        await job_manager._run_ripping(job_id)

        assert rip_env.ext.sources[0].spec == f"file:{image}"
        rip_env.release.assert_not_awaited()


# --- content hash ---------------------------------------------------------


@pytest.mark.unit
class TestContentHashFromABackup:
    """A backup folder holds the same BDMV/STREAM sizes as the disc it copied,
    so it hashes to the same ContentHash and still gets a TheDiscDB lookup."""

    @staticmethod
    def _make_backup(root: Path) -> Path:
        stream = root / "BDMV" / "STREAM"
        stream.mkdir(parents=True)
        (stream / "00000.m2ts").write_bytes(b"a" * 32)
        (stream / "00001.m2ts").write_bytes(b"b" * 64)
        return root

    def test_backup_folder_hashes(self, tmp_path):
        root = self._make_backup(tmp_path / "backup")

        value = compute_content_hash(DiscSource.for_backup(root))

        assert value and len(value) == 32
        assert value == value.upper()

    def test_iso_degrades_to_none(self, tmp_path):
        iso = tmp_path / "disc.iso"
        iso.write_bytes(b"not really an iso")

        assert compute_content_hash(DiscSource.parse(f"iso:{iso}")) is None

    def test_missing_structure_degrades_to_none(self, tmp_path):
        assert compute_content_hash(DiscSource.for_backup(tmp_path)) is None
