"""Regression tests for #676: an imported movie must actually be organized.

``identify_from_staging`` used to hand a movie import to ``finalize_disc_job``, the
TV finalizer, which only organizes titles carrying an episode code. The movie was
never moved yet the job reported COMPLETED. The existing staging-import tests
mocked the finalizer away, so these drive the REAL movie tail and the REAL
organizer against a temp library, asserting where the file lands on disk.
"""

import importlib
import json
from unittest.mock import patch

import pytest
from sqlmodel import select

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.models.app_config import AppConfig
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.job_manager import job_manager
from tests.unit.conftest import _unit_session_factory

jm_mod = importlib.import_module("app.services.job_manager")

MOVIE = "A Study in Scarlet"
FOLDER = f"{MOVIE} (1933) tmdbid-38585"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ws_manager, "broadcast_title_update", _noop)
    monkeypatch.setattr(ws_manager, "broadcast_job_update", _noop)
    # No staging cleanup / Discord on COMPLETED: irrelevant here and they would
    # otherwise run against the user's "source folder".
    monkeypatch.setattr(jm_mod.state_machine, "_on_terminal_callbacks", [])
    # REVIEW_NEEDED notifies via a background task that would outlive the test.
    monkeypatch.setattr(job_manager, "_send_discord_notification_for_state", _noop)


def _cfg(library):
    return patch(
        "app.services.config_service.get_config_sync",
        return_value=AppConfig(
            library_movies_path=str(library),
            naming_movie_format="{title} ({year}) tmdbid-{tmdb_id}",
        ),
    )


def _mkv(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 64)
    return path


async def _seed_import(source_dir, files, *, destination_mode="library", manifest=None):
    """An import job as identify_from_staging leaves it: ORGANIZING, titles MATCHED."""
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="import",
            volume_label="A_STUDY_IN_SCARLET_(1933)",
            staging_path=str(source_dir),
            state=JobState.ORGANIZING,
            content_type=ContentType.MOVIE,
            detected_title=MOVIE,
            tmdb_name=MOVIE,
            tmdb_id="38585",
            tmdb_year=1933,
            destination_mode=destination_mode,
            import_manifest_json=json.dumps(manifest) if manifest else None,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        for i, f in enumerate(files):
            session.add(
                DiscTitle(
                    job_id=job.id,
                    title_index=i,
                    duration_seconds=4260,
                    state=TitleState.MATCHED,
                    output_filename=str(f),
                )
            )
        await session.commit()
        return job.id


async def _finalize(job_id, source_dir):
    await job_manager._finalize_ripped_movie(
        job_id, source_dir, "A_STUDY_IN_SCARLET_(1933)", MOVIE, import_files=True
    )
    async with _unit_session_factory() as session:
        return await session.get(DiscJob, job_id)


@pytest.mark.unit
class TestMovieImportOrganize:
    async def test_library_import_moves_file_into_movie_library(self, tmp_path):
        """The exact scenario from #676."""
        source_dir = tmp_path / "engram_import" / f"{MOVIE} (1933)"
        src = _mkv(source_dir / f"{MOVIE} (1933).mkv")
        library = tmp_path / "Movies"
        job_id = await _seed_import(source_dir, [src])

        with _cfg(library):
            job = await _finalize(job_id, source_dir)

        dest = library / FOLDER / f"{FOLDER}.mkv"
        assert dest.exists()
        assert not src.exists()
        assert job.state == JobState.COMPLETED
        assert job.final_path == str(dest)

    async def test_in_place_import_lands_beside_picked_folder(self, tmp_path):
        """destination_mode=in_place must not fall back to the global library."""
        root = tmp_path / "collection"
        source_dir = root / f"{MOVIE} (1933)"
        src = _mkv(source_dir / f"{MOVIE} (1933).mkv")
        library = tmp_path / "Movies"
        job_id = await _seed_import(
            source_dir,
            [src],
            destination_mode="in_place",
            manifest={"root": str(source_dir), "picked_is_show": True, "files": [str(src)]},
        )

        with _cfg(library):
            job = await _finalize(job_id, source_dir)

        assert (root / FOLDER / f"{FOLDER}.mkv").exists()
        assert not library.exists()
        assert job.state == JobState.COMPLETED

    async def test_unlisted_mkv_in_source_folder_is_left_alone(self, tmp_path):
        """Only the job's own files move. A sibling MKV the import never listed is
        the user's, and must not be swept into the movie's Extras/ folder."""
        source_dir = tmp_path / "engram_import" / f"{MOVIE} (1933)"
        src = _mkv(source_dir / f"{MOVIE} (1933).mkv")
        bystander = _mkv(source_dir / "unrelated.mkv")
        library = tmp_path / "Movies"
        job_id = await _seed_import(source_dir, [src])

        with _cfg(library):
            job = await _finalize(job_id, source_dir)

        assert job.state == JobState.COMPLETED
        assert bystander.exists()
        assert not (library / FOLDER / "Extras").exists()

    async def test_listed_extra_is_organized_with_the_movie(self, tmp_path):
        """Files the import DID list still travel as extras."""
        source_dir = tmp_path / "engram_import" / f"{MOVIE} (1933)"
        src = _mkv(source_dir / f"{MOVIE} (1933).mkv")
        extra = _mkv(source_dir / "trailer.mkv")
        library = tmp_path / "Movies"
        job_id = await _seed_import(source_dir, [src, extra])
        # Skip TMDB-runtime feature selection: title 0 is the feature.
        with patch.object(job_manager, "_resolve_multi_title_movie", return_value=False):
            with _cfg(library):
                job = await _finalize(job_id, source_dir)

        assert job.state == JobState.COMPLETED
        assert (library / FOLDER / f"{FOLDER}.mkv").exists()
        assert (library / FOLDER / "Extras" / "Extra 1.mkv").exists()
        assert not extra.exists()


@pytest.mark.unit
class TestImportLibraryConflict:
    """The import branch organizes through organize_movie, not movie_organizer, so
    the #685 conflict strategy has to reach it too (it merged in beside #676)."""

    async def test_existing_library_file_parks_for_review_and_touches_nothing(self, tmp_path):
        source_dir = tmp_path / "engram_import" / f"{MOVIE} (1933)"
        src = _mkv(source_dir / f"{MOVIE} (1933).mkv")
        library = tmp_path / "Movies"
        existing = _mkv(library / FOLDER / f"{FOLDER}.mkv")
        existing.write_bytes(b"library copy")
        job_id = await _seed_import(source_dir, [src])

        with _cfg(library):
            job = await _finalize(job_id, source_dir)

        assert job.state == JobState.REVIEW_NEEDED
        assert src.exists()
        assert existing.read_bytes() == b"library copy"
        async with _unit_session_factory() as session:
            title = (
                await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id))
            ).scalar_one()
        assert title.state == TitleState.REVIEW
        assert json.loads(title.match_details)["error"] == "file_exists"

    async def test_recorded_rename_files_the_import_beside_the_existing_copy(self, tmp_path):
        source_dir = tmp_path / "engram_import" / f"{MOVIE} (1933)"
        src = _mkv(source_dir / f"{MOVIE} (1933).mkv")
        library = tmp_path / "Movies"
        existing = _mkv(library / FOLDER / f"{FOLDER}.mkv")
        job_id = await _seed_import(source_dir, [src])
        async with _unit_session_factory() as session:
            title = (
                await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id))
            ).scalar_one()
            title.conflict_resolution = "rename"
            session.add(title)
            await session.commit()

        with _cfg(library):
            job = await _finalize(job_id, source_dir)

        renamed = library / FOLDER / f"{FOLDER} (v2).mkv"
        assert job.state == JobState.COMPLETED
        assert renamed.exists()
        assert existing.exists()
        assert job.final_path == str(renamed)


@pytest.fixture
def _fc_session(monkeypatch):
    monkeypatch.setattr(
        "app.services.finalization_coordinator.async_session", _unit_session_factory
    )


@pytest.mark.unit
@pytest.mark.usefixtures("_fc_session")
class TestImportReviewKeepsUserFiles:
    async def test_choosing_a_cut_leaves_the_other_file_in_place(self, tmp_path):
        """Picking one of two imported cuts in review must not delete the other.
        For a disc rip the unselected file is a regenerable rip; for an import it
        is the user's original."""
        source_dir = tmp_path / "engram_import" / f"{MOVIE} (1933)"
        chosen = _mkv(source_dir / "theatrical.mkv")
        other = _mkv(source_dir / "extended.mkv")
        library = tmp_path / "Movies"
        job_id = await _seed_import(source_dir, [chosen, other])
        async with _unit_session_factory() as session:
            job = await session.get(DiscJob, job_id)
            job.state = JobState.REVIEW_NEEDED
            session.add(job)
            await session.commit()
            titles = (
                (await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id)))
                .scalars()
                .all()
            )
        chosen_id = next(t.id for t in titles if t.output_filename == str(chosen))

        with _cfg(library):
            await job_manager._finalization.apply_review(job_id, chosen_id)

        assert (library / FOLDER / f"{FOLDER}.mkv").exists()
        assert other.exists()
        assert not (library / FOLDER / "Extras").exists()


@pytest.mark.unit
@pytest.mark.usefixtures("_fc_session")
class TestFinalizeDiscJobBackstop:
    async def test_matched_titles_with_nothing_organized_do_not_complete(self, tmp_path):
        """finalize_disc_job only organizes titles with an episode code. If it is
        handed MATCHED titles without one, it must not report COMPLETED over files
        it never moved: that silent success is what hid #676."""
        source_dir = tmp_path / "src"
        src = _mkv(source_dir / "movie.mkv")
        job_id = await _seed_import(source_dir, [src])

        await job_manager._finalization.finalize_disc_job(job_id)

        async with _unit_session_factory() as session:
            job = await session.get(DiscJob, job_id)
        assert job.state == JobState.REVIEW_NEEDED
        assert src.exists()
