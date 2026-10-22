"""A movie bonus disc: no title is the feature (#812, #813, #814).

The shape behind all three reports: a disc identified as the movie (same TMDB id)
that carries only bonus content, longest title ~47 min against a ~152 min film.
"""

import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from app.core.analyst import TitleInfo, select_movie_main_feature
from app.core.organizer import organize_movie, organize_movie_extras
from app.models import DiscJob, JobState
from app.models.app_config import AppConfig
from app.models.disc_job import ContentType, DiscTitle, TitleState
from tests.unit.conftest import _unit_session_factory
from tests.unit.test_movie_review_conflict import (
    _make_coord,
    _patch_session_and_ws,  # noqa: F401 (autouse fixture)
    job_manager_module,  # noqa: F401 (pytest fixture)
)

organizer_module = importlib.import_module("app.core.organizer")
config_service_module = importlib.import_module("app.services.config_service")


@pytest.fixture
def cfg(monkeypatch):
    cfg = SimpleNamespace(
        conflict_resolution_default="ask",
        extras_policy="keep",
        analyst_movie_min_duration=4800,
        tmdb_api_key="",
    )

    async def _cfg():
        return cfg

    monkeypatch.setattr(config_service_module, "get_config", _cfg)
    return cfg


async def _seed(tmp_path, *, state: JobState, specs) -> tuple[int, list[int]]:
    """specs: (index, minutes, size_bytes, title_state, is_extra, is_selected)."""
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="/dev/sr0",
            volume_label="THE DARK KNIGHT BONUS DISC",
            content_type=ContentType.MOVIE,
            state=state,
            detected_title="The Dark Knight",
            tmdb_name="The Dark Knight",
            tmdb_id=155,
            tmdb_year=2008,
            staging_path=str(tmp_path),
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        titles = []
        for index, minutes, size, title_state, is_extra, is_selected in specs:
            f = tmp_path / f"BONUS_t{index:02d}.mkv"
            f.write_bytes(b"x" * size)
            t = DiscTitle(
                job_id=job.id,
                title_index=index,
                duration_seconds=int(minutes * 60),
                file_size_bytes=size,
                is_selected=is_selected,
                is_extra=is_extra,
                output_filename=str(f),
                state=title_state,
            )
            session.add(t)
            titles.append(t)
        await session.commit()
        for t in titles:
            await session.refresh(t)
        return job.id, [t.id for t in titles]


async def _titles(ids):
    async with _unit_session_factory() as session:
        return [await session.get(DiscTitle, i) for i in ids]


async def _job(job_id):
    async with _unit_session_factory() as session:
        return await session.get(DiscJob, job_id)


class TestOrganizerFilesTheChosenFeature:
    """#814: the rip-end organize files the title the job chose, not the biggest file."""

    async def test_rip_end_hands_the_selected_title_to_the_organizer(
        self,
        tmp_path,
        monkeypatch,
        cfg,
        job_manager_module,  # noqa: F811
    ):
        # Title 4 is the longest (the feature pick); title 0 is shorter but the
        # bigger file. A folder scan for "the largest MKV" would file title 0.
        job_id, ids = await _seed(
            tmp_path,
            state=JobState.RIPPING,
            specs=[
                (0, 46.0, 3000, TitleState.MATCHED, False, True),
                (4, 46.7, 2000, TitleState.MATCHED, False, True),
            ],
        )
        main = tmp_path / "lib" / "The Dark Knight (2008).mkv"
        extra_dest = tmp_path / "lib" / "Extras" / "Extra 1.mkv"
        organize = Mock(
            return_value={
                "success": True,
                "main_file": main,
                "extras": [extra_dest],
                "extras_mapping": {"BONUS_t00.mkv": extra_dest},
                "error": None,
            }
        )
        monkeypatch.setattr(organizer_module.movie_organizer, "organize", organize)
        job_manager_module.job_manager._matching.get_discdb_mappings = lambda _job_id: []

        await job_manager_module.job_manager._finalize_ripped_movie(
            job_id, tmp_path, "THE DARK KNIGHT BONUS DISC", "The Dark Knight"
        )

        assert organize.call_args.args[0] == tmp_path / "BONUS_t04.mkv"
        extra, feature = await _titles(ids)
        assert feature.organized_to == str(main) and not feature.is_extra
        assert extra.organized_to == str(extra_dest) and extra.is_extra

    async def test_a_title_the_organizer_did_not_move_is_not_recorded_as_the_feature(
        self,
        tmp_path,
        monkeypatch,
        cfg,
        job_manager_module,  # noqa: F811
    ):
        """Only the chosen title gets the feature path; anything left unmapped is
        not silently recorded as having been filed there."""
        job_id, ids = await _seed(
            tmp_path,
            state=JobState.RIPPING,
            specs=[
                (0, 46.0, 3000, TitleState.MATCHED, False, True),
                (4, 46.7, 2000, TitleState.MATCHED, False, True),
            ],
        )
        main = tmp_path / "lib" / "The Dark Knight (2008).mkv"
        organize = Mock(
            return_value={
                "success": True,
                "main_file": main,
                "extras": [],
                "extras_mapping": {},
                "error": None,
            }
        )
        monkeypatch.setattr(organizer_module.movie_organizer, "organize", organize)
        job_manager_module.job_manager._matching.get_discdb_mappings = lambda _job_id: []

        await job_manager_module.job_manager._finalize_ripped_movie(
            job_id, tmp_path, "THE DARK KNIGHT BONUS DISC", "The Dark Knight"
        )

        extra, feature = await _titles(ids)
        assert feature.organized_to == str(main)
        assert extra.organized_to != str(main)


class TestSelectingAnExtraAsTheFeature:
    """#814: Select on a title tagged as an extra makes it the feature, flags and all."""

    async def test_select_clears_is_extra_and_moves_the_selection(self, tmp_path, monkeypatch, cfg):
        job_id, ids = await _seed(
            tmp_path,
            state=JobState.REVIEW_NEEDED,
            specs=[
                (0, 46.0, 3000, TitleState.MATCHED, True, False),
                (4, 46.7, 2000, TitleState.MATCHED, False, True),
            ],
        )
        main = tmp_path / "lib" / "The Dark Knight (2008).mkv"
        monkeypatch.setattr(
            organizer_module.movie_organizer,
            "organize",
            Mock(
                return_value={
                    "success": True,
                    "main_file": main,
                    "extras": [],
                    "extras_mapping": {},
                    "error": None,
                }
            ),
        )

        await _make_coord().apply_review(job_id, ids[0])

        chosen, other = await _titles(ids)
        assert chosen.is_extra is False and chosen.is_selected is True
        assert chosen.organized_to == str(main)
        assert other.is_selected is False


class TestSelectAfterCleanup:
    """#812: a title whose file the Select cleanup deleted can't be the feature."""

    async def test_select_on_a_removed_title_is_refused_without_a_rerip(
        self, tmp_path, monkeypatch, cfg
    ):
        job_id, ids = await _seed(
            tmp_path,
            state=JobState.REVIEW_NEEDED,
            specs=[
                (0, 46.0, 3000, TitleState.REVIEW, True, False),
                (4, 46.7, 2000, TitleState.FAILED, False, True),
            ],
        )
        removed = tmp_path / "BONUS_t04.mkv"
        removed.unlink()
        async with _unit_session_factory() as session:
            t = await session.get(DiscTitle, ids[1])
            t.match_details = '{"reason": "Unselected by user"}'
            session.add(t)
            await session.commit()
        coord = _make_coord()
        rip = Mock()
        monkeypatch.setattr(coord, "_run_ripping", rip, raising=False)

        with pytest.raises(ValueError, match="removed"):
            await coord.apply_review(job_id, ids[1])

        rip.assert_not_called()
        assert (await _job(job_id)).state == JobState.REVIEW_NEEDED
        assert (await _titles(ids))[1].state == TitleState.FAILED


def _info(index: int, minutes: float) -> TitleInfo:
    seconds = int(minutes * 60)
    return TitleInfo(
        index=index, duration_seconds=seconds, size_bytes=seconds * 2_000_000, chapter_count=4
    )


class TestNoFeatureOnDisc:
    """#813: a disc with nothing near the movie's runtime is held, not filed as the movie."""

    def test_bonus_disc_needs_review_with_no_feature(self):
        decision = select_movie_main_feature(
            [_info(0, 46.0), _info(4, 46.7), _info(7, 12.0)], runtime_minutes=152
        )
        assert decision.no_feature is True
        assert decision.needs_review is True
        assert decision.feature_index is None
        # Nothing is ruled out: any title may still be the feature, so no extras
        # policy can drop one before the reviewer decides.
        assert decision.extra_indices == []
        assert sorted(decision.candidate_indices) == [0, 4, 7]
        assert "152" in decision.review_reason and "46" in decision.review_reason

    def test_short_film_is_still_its_own_feature(self):
        decision = select_movie_main_feature([_info(0, 28.0), _info(1, 6.0)], runtime_minutes=30)
        assert decision.no_feature is False
        assert decision.feature_index == 0
        assert decision.needs_review is False

    def test_without_a_runtime_the_longest_is_still_picked(self):
        decision = select_movie_main_feature([_info(0, 46.0), _info(4, 46.7)], runtime_minutes=None)
        assert decision.no_feature is False
        assert decision.feature_index == 4

    async def test_rip_end_parks_a_bonus_disc_in_review(
        self,
        tmp_path,
        monkeypatch,
        cfg,
        job_manager_module,  # noqa: F811
    ):
        cfg.tmdb_api_key = "token"
        monkeypatch.setattr(
            importlib.import_module("app.matcher.tmdb_client"),
            "fetch_movie_runtime",
            lambda *_a: 152,
        )
        organize = Mock(side_effect=AssertionError("a bonus disc must not be filed as the movie"))
        monkeypatch.setattr(organizer_module.movie_organizer, "organize", organize)
        job_manager_module.job_manager._matching.get_discdb_mappings = lambda _job_id: []
        job_id, ids = await _seed(
            tmp_path,
            state=JobState.RIPPING,
            specs=[
                (0, 46.0, 3000, TitleState.MATCHED, False, True),
                (4, 46.7, 2000, TitleState.MATCHED, False, True),
            ],
        )

        await job_manager_module.job_manager._finalize_ripped_movie(
            job_id, tmp_path, "THE DARK KNIGHT BONUS DISC", "The Dark Knight"
        )

        job = await _job(job_id)
        assert job.state == JobState.REVIEW_NEEDED
        assert "bonus disc" in job.review_reason
        assert not any(t.is_extra for t in await _titles(ids))


def _library_cfg(library: Path):
    return patch(
        "app.services.config_service.get_config_sync",
        return_value=AppConfig(
            library_movies_path=str(library), naming_movie_format="{title} ({year})"
        ),
    )


class TestOrganizeMovieExtras:
    def test_files_every_title_under_extras_without_touching_existing_ones(self, tmp_path):
        library = tmp_path / "lib"
        extras_dir = library / "The Dark Knight (2008)" / "Extras"
        extras_dir.mkdir(parents=True)
        (extras_dir / "Extra 1.mkv").write_bytes(b"from the movie disc")
        staged = [tmp_path / "BONUS_t00.mkv", tmp_path / "BONUS_t04.mkv"]
        for f in staged:
            f.write_bytes(b"bonus")

        with _library_cfg(library):
            result = organize_movie_extras(staged, "The Dark Knight", 2008, already_clean=True)

        assert result["success"] is True
        assert (extras_dir / "Extra 1.mkv").read_bytes() == b"from the movie disc"
        assert result["extras_mapping"] == {
            "BONUS_t00.mkv": extras_dir / "Extra 2.mkv",
            "BONUS_t04.mkv": extras_dir / "Extra 3.mkv",
        }
        assert all((extras_dir / n).exists() for n in ("Extra 2.mkv", "Extra 3.mkv"))
        assert result["extras_dir"] == extras_dir
        assert not (library / "The Dark Knight (2008)" / "The Dark Knight (2008).mkv").exists()

    def test_the_feature_path_never_overwrites_an_existing_extra(self, tmp_path):
        """organize_movie numbers extras past ones already filed (a second disc)."""
        library = tmp_path / "lib"
        extras_dir = library / "Hairspray (2007)" / "Extras"
        extras_dir.mkdir(parents=True)
        (extras_dir / "Extra 1.mkv").write_bytes(b"keep me")
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "t00.mkv").write_bytes(b"feature" * 10)
        (staging / "t01.mkv").write_bytes(b"extra")

        with _library_cfg(library):
            result = organize_movie(staging, "Hairspray", 2007, library, already_clean=True)

        assert result["success"] is True
        assert (extras_dir / "Extra 1.mkv").read_bytes() == b"keep me"
        assert result["extras_mapping"] == {"t01.mkv": extras_dir / "Extra 2.mkv"}


class TestFileMovieAsExtras:
    """#813: the review's "no feature on this disc" action."""

    async def test_files_kept_titles_as_extras_and_completes(self, tmp_path, cfg):
        job_id, ids = await _seed(
            tmp_path,
            state=JobState.REVIEW_NEEDED,
            specs=[
                (0, 46.0, 3000, TitleState.REVIEW, False, True),
                (4, 46.7, 2000, TitleState.MATCHED, True, False),
                (7, 12.0, 1000, TitleState.FAILED, True, False),
                (9, 3.0, 500, TitleState.COMPLETED, True, False),
            ],
        )
        async with _unit_session_factory() as session:
            conflict = await session.get(DiscTitle, ids[0])
            conflict.match_details = '{"error": "file_exists", "message": "File already exists"}'
            discarded = await session.get(DiscTitle, ids[2])
            discarded.matched_episode = "skip"
            session.add_all([conflict, discarded])
            await session.commit()
        library = tmp_path / "lib"
        extras_dir = library / "The Dark Knight (2008)" / "Extras"

        with _library_cfg(library):
            count = await _make_coord().file_movie_as_extras(job_id)

        assert count == 2
        first, second, dropped, settled = await _titles(ids)
        for t, name in ((first, "Extra 1.mkv"), (second, "Extra 2.mkv")):
            assert t.state == TitleState.COMPLETED
            assert t.is_extra is True and t.is_selected is False
            assert t.organized_to == str(extras_dir / name)
        assert "file_exists" not in (first.match_details or "")
        assert dropped.state == TitleState.FAILED and dropped.organized_to is None
        assert settled.organized_to is None
        assert (tmp_path / "BONUS_t07.mkv").exists()  # a discarded rip is not filed
        job = await _job(job_id)
        assert job.state == JobState.COMPLETED
        assert job.final_path == str(extras_dir)

    async def test_refused_outside_review(self, tmp_path, cfg):
        job_id, _ = await _seed(
            tmp_path,
            state=JobState.RIPPING,
            specs=[(0, 46.0, 3000, TitleState.MATCHED, False, True)],
        )
        with pytest.raises(ValueError, match="review"):
            await _make_coord().file_movie_as_extras(job_id)

    async def test_refused_when_nothing_is_left_to_file(self, tmp_path, cfg):
        job_id, _ = await _seed(
            tmp_path,
            state=JobState.REVIEW_NEEDED,
            specs=[(0, 46.0, 3000, TitleState.FAILED, True, False)],
        )
        with pytest.raises(ValueError, match="Nothing"):
            await _make_coord().file_movie_as_extras(job_id)
        assert (await _job(job_id)).state == JobState.REVIEW_NEEDED
