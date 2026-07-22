"""End-to-end proof that identify_disc honors an armed manual identity (#520).

Replaces three tautological tests (deleted from
tests/unit/test_manual_identify_path.py) that each re-implemented a
production boolean guard as a local function and asserted against that
local copy — worthless as a regression guard, since removing
``and not _is_manual`` from identification_coordinator.identify_disc would
not move those tests at all.

This test drives the REAL ``IdentificationCoordinator.identify_disc`` (the
production instance wired by the ``job_manager`` singleton) with a manual
identity crafted to be the worst case for all three absence-triggered gates
at once:

- content_type="tv" with tmdb_id=None  -> would trip Gate B (TV w/o tmdb_id)
- season=None                          -> would trip Gate D (unknown season)
- a catalog-number-shaped volume label -> would trip Gate E (catalog-number
  title clearing)

Network/filesystem boundaries are stubbed at the extractor (MakeMKV scan),
the classification pipeline, ripping, and the TV subtitle prefetch —
everything identify_disc calls out to before the gates run. The gate logic
inside identify_disc itself is NOT stubbed; it runs unmodified.

The classification stub deliberately returns a WRONG guess (a movie named
"Wrong Guess") rather than a network-boundary stub of TMDB/DiscDB: the
job's real config (backend/engram.db) may or may not have live API keys, so
stubbing the network boundary would make this test's determinism depend on
whatever is configured in this developer's database. Returning a wrong
guess and asserting the manual identity wins is also a stronger proof of
the override than an empty/neutral classification would be. This test uses
a private in-memory SQLite database (see ``isolated_db`` below) so it can
never touch backend/engram.db regardless.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import app.services.identification_coordinator as idc
from app.core.analyst import DiscAnalysisResult, TitleInfo
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType
from app.services.job_manager import job_manager
from app.services.manual_identity import ManualIdentity

_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_session_factory = sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def isolated_db(monkeypatch):
    """Private in-memory DB for this file, mirroring tests/unit/conftest.py's
    isolate_database pattern (not reused directly to avoid a cross-directory
    coupling between tests/integration and tests/unit).

    identify_disc reads/writes sessions via the module-level ``async_session``
    name it imported into identification_coordinator, so patching that name
    is sufficient — it never touches backend/engram.db in this test.
    """
    async with _engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    monkeypatch.setattr(idc, "async_session", _session_factory)

    # save_snapshot writes a debug JSON to the real ~/.engram/snapshots on
    # disk; identify_disc imports it locally per-call, so patching the
    # source module's attribute (not idc's) is what actually takes effect.
    import app.core.snapshot as snapshot_mod

    monkeypatch.setattr(snapshot_mod, "save_snapshot", lambda *a, **k: None)

    yield

    async with _engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)


_TITLES = [
    TitleInfo(index=0, duration_seconds=1380, size_bytes=2_100_000_000, chapter_count=6),
    TitleInfo(index=1, duration_seconds=1420, size_bytes=2_200_000_000, chapter_count=6),
]


def _wrong_guess() -> DiscAnalysisResult:
    """A classification result that confidently guessed wrong.

    Manual identity must override this, not merely supplement it — proving
    the guard beats a plausible-looking wrong answer is a stronger test
    than proving it beats an empty one.
    """
    result = DiscAnalysisResult(content_type=ContentType.MOVIE)
    result.detected_name = "Wrong Guess"
    result.detected_season = None
    result.tmdb_id = 999999
    result.tmdb_name = "Wrong Guess"
    result.confidence = 0.35
    result.classification_source = "heuristic"
    result.needs_review = True
    result.review_reason = "Could not confirm identity"
    result.is_ambiguous_movie = False
    result.identity_unconfirmed = True
    return result


async def _seed_identifying_job(volume_label: str) -> int:
    async with _session_factory() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label=volume_label,
            state=JobState.IDENTIFYING,
            staging_path="/tmp/staging",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


async def _reload_job(job_id: int) -> DiscJob:
    async with _session_factory() as session:
        return await session.get(DiscJob, job_id)


@pytest.mark.integration
async def test_manual_identity_survives_gates_b_d_and_e_simultaneously():
    """One manual identity trips Gate B, D, and E's absence conditions at
    once; identify_disc must suppress all three and rip unattended."""
    # Catalog-number-shaped label (e.g. a real FHE Blu-ray SKU) — without the
    # Gate E guard this alone would erase detected_title after the override
    # nulls both external signals.
    job_id = await _seed_identifying_job("FHED3456")

    manual = ManualIdentity(
        title="My Home Movies",
        content_type="tv",
        season=None,  # Gate D bait: unknown season
        tmdb_id=None,  # Gate B bait: TV without a tmdb_id
        disc_number=None,
    )

    coord = job_manager._identification
    with (
        patch.object(coord._extractor, "scan_disc", AsyncMock(return_value=(_TITLES, "FHE Disc"))),
        patch.object(coord, "_run_classification", AsyncMock(return_value=_wrong_guess())),
        patch.object(coord, "_run_ripping", AsyncMock()) as run_ripping,
        patch.object(coord, "_start_tv_subtitle_prefetch", AsyncMock()) as prefetch,
    ):
        await coord.identify_disc(job_id, manual_identity=manual)

    job = await _reload_job(job_id)

    # The manual identity won, not the wrong guess.
    assert job.content_type == ContentType.TV
    assert job.detected_title == "My Home Movies"
    assert job.classification_source == "manual"

    # Gate B/D: no blocking or shortcut prompt was raised.
    assert not job.identity_prompt_json

    # The job rips unattended — never parks.
    assert job.state != JobState.REVIEW_NEEDED
    assert job.state == JobState.RIPPING
    run_ripping.assert_awaited_once_with(job_id)

    # Gate D confirmed to have actually been in play: season genuinely
    # stayed unresolved (no auto-pin happened), yet no prompt was set for it.
    assert job.detected_season is None

    # Gate B confirmed to have actually been in play: tmdb_id genuinely
    # stayed absent, yet no prompt was set for it.
    assert job.tmdb_id is None

    # The subtitle prefetch still runs for a manual TV disc (unknown season
    # routes it to the all-seasons path) — confirms we reached that branch
    # rather than short-circuiting somewhere earlier.
    prefetch.assert_awaited_once()


@pytest.mark.integration
async def test_re_identify_records_manual_correction_provenance(monkeypatch):
    """Drives the REAL IdentificationCoordinator.re_identify (#520 Task 6),
    not the mocked API endpoint, so it actually proves classification_source
    gets set to "manual_correction" rather than asserting a copy of the
    assignment.

    Boundary stubs, and why each is needed:
    - ``tmdb_id`` is passed explicitly so ``re_identify`` takes the
      ``if tmdb_id is not None`` branch and skips its own
      ``get_config()``/``classify_from_tmdb`` re-lookup entirely — avoids
      needing to also patch config_service's ``async_session`` (a second
      module-level name distinct from identification_coordinator's) to keep
      this test off both the real DB and the network.
    - ``_resolve_show_year`` is still reached (tmdb_id is now truthy) and
      falls through to a live ``fetch_show_details`` network call unless
      stubbed — patched to a fixed year.
    - ``_restart_subtitle_download`` is wired at JobManager construction
      time (not just at ``start()``), so the production
      ``job_manager._identification`` singleton already has a real,
      non-None callback that would kick off actual subtitle downloading;
      stubbed to an AsyncMock.
    - The job is seeded with no ``staging_path``, so ``has_ripped`` is
      False and the pre-rip branch runs, meaning ``re_identify`` itself
      never calls ``_run_ripping`` or matching dispatch — nothing else to
      stub for this branch.
    """
    monkeypatch.setattr(idc, "_resolve_show_year", lambda *a, **k: 2005)

    async with _session_factory() as session:
        # REVIEW_NEEDED, not IDENTIFYING: re-identify deliberately rejects
        # IDENTIFYING (#520) because the identify_disc task is still in flight
        # then. A parked (REVIEW_NEEDED) disc with no ripped files hits the same
        # pre-rip "start_rip" branch this test exercises.
        job = DiscJob(
            drive_id="E:",
            volume_label="X",
            state=JobState.REVIEW_NEEDED,
            staging_path=None,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    coord = job_manager._identification
    with patch.object(coord, "_restart_subtitle_download", AsyncMock()) as restart_mock:
        result = await coord.re_identify(job_id, "The Office", "tv", season=2, tmdb_id=2316)

    job = await _reload_job(job_id)

    assert job.classification_source == "manual_correction"
    assert job.detected_title == "The Office"
    assert job.content_type == ContentType.TV
    assert job.detected_season == 2
    assert job.tmdb_id == 2316

    # Parked disc, no ripped files -> pre-rip resume path (start a fresh rip).
    assert job.state == JobState.RIPPING
    assert result["resume_action"] == "start_rip"

    # TV + known season + no ripped files -> the corrected-title subtitle
    # restart fires (stubbed, so no real network/download happens).
    restart_mock.assert_awaited_once_with(job_id, "The Office", 2, 2316)
