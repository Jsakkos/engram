"""Unit tests for staging-file location and title resolution."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState
from app.services.ripping_helpers import find_staging_file, resolve_title_from_filename
from tests.unit.conftest import _unit_session_factory


@pytest.mark.unit
def test_disc_title_output_index_defaults_to_none():
    """New column: the disc-native _tNN number captured at scan time.

    Must default to None so legacy rows (created before this migration, or by
    call sites that never populate it) fall back to title_index-based matching.
    """
    t = DiscTitle(job_id=1, title_index=0, duration_seconds=100)
    assert t.output_index is None


@pytest.mark.unit
def test_find_staging_file_prefers_output_filename(tmp_path):
    f = tmp_path / "Show_t07.mkv"
    f.write_bytes(b"x")
    job = SimpleNamespace(staging_path=str(tmp_path))
    title = SimpleNamespace(output_filename=str(f), title_index=7, organized_to=None)
    assert find_staging_file(job, title) == f


@pytest.mark.unit
def test_find_staging_file_falls_back_to_organized_to(tmp_path):
    """When the staging file is gone, re-match can read the organized library file."""
    organized = tmp_path / "library" / "Show - S01E14.mkv"
    organized.parent.mkdir()
    organized.write_bytes(b"x")
    job = SimpleNamespace(staging_path=str(tmp_path / "empty_staging"))
    title = SimpleNamespace(
        output_filename=str(tmp_path / "gone.mkv"),  # no longer exists
        title_index=7,
        organized_to=str(organized),
    )
    assert find_staging_file(job, title) == organized


@pytest.mark.unit
def test_find_staging_file_returns_none_when_nothing_found(tmp_path):
    job = SimpleNamespace(staging_path=str(tmp_path / "empty"))
    title = SimpleNamespace(output_filename=None, title_index=3, organized_to=None)
    assert find_staging_file(job, title) is None


# --- resolve_title_from_filename ------------------------------------------
#
# Regression focus (single-track re-rip): a re-rip passes ONLY the re-ripped
# title(s) as ``sorted_titles``. A pre-existing file for a *different* title
# (e.g. ``B1_t00.mkv`` for title 0, sitting in the staging dir) must never be
# positionally mis-mapped onto the lone subset title — its filename carries a
# real title index that isn't in the candidate set, so it is foreign and
# unresolved. Mis-mapping it stamped the re-ripped title with the first track's
# filename and produced a duplicate-episode conflict.


async def _seed(
    indices: list[int], output_indices: list[int | None] | None = None
) -> tuple[int, list[DiscTitle]]:
    """Persist a job with titles at the given title_index values (detached).

    ``output_indices``, when given, must be the same length as ``indices`` and
    sets each title's ``output_index`` (the disc-native "_tNN" number). Defaults
    to all-None (legacy rows with no recorded native number).
    """
    if output_indices is None:
        output_indices = [None] * len(indices)
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="F:",
            volume_label="SHOW_S3D4",
            content_type=ContentType.TV,
            state=JobState.RIPPING,
            staging_path="/tmp/staging",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        titles = []
        for idx, out_idx in zip(indices, output_indices, strict=True):
            t = DiscTitle(
                job_id=job.id,
                title_index=idx,
                output_index=out_idx,
                duration_seconds=2700,
                state=TitleState.RIPPING,
            )
            session.add(t)
            await session.commit()
            await session.refresh(t)
            titles.append(t)
        for t in titles:
            session.expunge(t)
        return job.id, titles


@pytest.mark.unit
async def test_resolve_by_filename_index():
    job_id, titles = await _seed([0, 1, 2, 3])
    async with _unit_session_factory() as session:
        t = await resolve_title_from_filename(Path("E1_t03.mkv"), titles, 4, job_id, session)
    assert t is not None
    assert t.title_index == 3


@pytest.mark.unit
async def test_resolve_foreign_filename_index_not_in_subset_returns_none():
    # Single-track re-rip: subset is only the re-ripped title (index 3). The
    # pre-existing B1_t00.mkv (title 0's file) must NOT resolve to it.
    job_id, titles = await _seed([3])
    async with _unit_session_factory() as session:
        t = await resolve_title_from_filename(Path("B1_t00.mkv"), titles, 1, job_id, session)
    assert t is None


@pytest.mark.unit
async def test_resolve_unparseable_filename_falls_back_positionally():
    # A disc whose output names carry no t<NN> index still resolves via the
    # sequential rip_index fallback (unchanged behavior).
    job_id, titles = await _seed([0, 1])
    async with _unit_session_factory() as session:
        t = await resolve_title_from_filename(Path("weird_name.mkv"), titles, 2, job_id, session)
    assert t is not None
    assert t.title_index == 1


@pytest.mark.unit
async def test_resolve_by_output_index_when_native_numbering_offset():
    """Issue #517: disc has no "t00" — MakeMKV's native numbering starts at 1.

    Scan-order title_index=0 has output_index=1 (from its suggested filename
    "..._t01.mkv"). The ripped file for that title is literally named
    "..._t01.mkv". It must resolve to title_index=0, not to whatever row (if
    any) happens to have title_index==1.
    """
    job_id, titles = await _seed([0, 1], output_indices=[1, 2])
    async with _unit_session_factory() as session:
        t = await resolve_title_from_filename(Path("C1title_t01.mkv"), titles, 1, job_id, session)
    assert t is not None
    assert t.title_index == 0


@pytest.mark.unit
async def test_resolve_falls_back_to_title_index_when_output_index_unset():
    """Legacy rows (output_index=None) keep the old title_index-based matching."""
    job_id, titles = await _seed([0, 1, 2, 3])  # output_index defaults to None
    async with _unit_session_factory() as session:
        t = await resolve_title_from_filename(Path("E1_t03.mkv"), titles, 4, job_id, session)
    assert t is not None
    assert t.title_index == 3
