"""A movie bonus disc: no title is the feature (#812, #813, #814).

The shape behind all three reports: a disc identified as the movie (same TMDB id)
that carries only bonus content, longest title ~47 min against a ~152 min film.
"""

import importlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.models import DiscJob, JobState
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
