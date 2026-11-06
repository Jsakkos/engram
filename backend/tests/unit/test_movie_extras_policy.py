"""``extras_policy`` on a movie disc's organize and review (#773).

The resolver half (which titles become extras, and in which state) lives in
``tests/integration/test_movie_feature_resolution.py``. These pin what happens to
those extras afterwards: "skip" keeps them out of ``Extras/``, and "ask" files
the ones the reviewer kept while deleting the ones they discarded.
"""

import importlib
from pathlib import Path
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
def policy(monkeypatch):
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


def _organize_ok(monkeypatch, main: Path, mapping: dict | None = None) -> Mock:
    m = Mock(
        return_value={
            "success": True,
            "main_file": main,
            "extras": list((mapping or {}).values()),
            "extras_mapping": mapping or {},
            "error": None,
        }
    )
    monkeypatch.setattr(organizer_module.movie_organizer, "organize", m)
    return m


async def _seed(tmp_path, *, state: JobState, specs) -> tuple[int, list[int]]:
    """specs: (index, minutes, title_state, is_extra, is_selected)."""
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="/dev/sr0",
            volume_label="HAIRSPRAY_DISC1",
            content_type=ContentType.MOVIE,
            state=state,
            detected_title="Hairspray",
            tmdb_name="Hairspray",
            tmdb_year=2007,
            staging_path=str(tmp_path),
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        titles = []
        for index, minutes, title_state, is_extra, is_selected in specs:
            f = tmp_path / f"title_t{index:02d}.mkv"
            f.write_bytes(b"x")
            t = DiscTitle(
                job_id=job.id,
                title_index=index,
                duration_seconds=int(minutes * 60),
                file_size_bytes=int(minutes * 60 * 4_000_000),
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


class TestRipEndOrganize:
    async def _finalize(self, jm, job_id, tmp_path):
        jm.job_manager._matching.get_discdb_mappings = lambda _job_id: []
        await jm.job_manager._finalize_ripped_movie(
            job_id, tmp_path, "HAIRSPRAY_DISC1", "Hairspray"
        )

    async def test_skip_leaves_extras_out_of_the_library(
        self,
        tmp_path,
        monkeypatch,
        policy,
        job_manager_module,  # noqa: F811
    ):
        policy.extras_policy = "skip"
        organize = _organize_ok(monkeypatch, tmp_path / "lib" / "Hairspray (2007).mkv")
        job_id, ids = await _seed(
            tmp_path,
            state=JobState.RIPPING,
            specs=[
                (0, 117, TitleState.MATCHED, False, True),
                (1, 15, TitleState.MATCHED, False, True),
            ],
        )

        await self._finalize(job_manager_module, job_id, tmp_path)

        assert organize.call_args.kwargs["move_extras"] is False
        feature, extra = await _titles(ids)
        assert feature.state == TitleState.COMPLETED
        assert extra.state == TitleState.COMPLETED and extra.is_extra
        assert extra.organized_to is None
        assert (await _job(job_id)).state == JobState.COMPLETED

    async def test_keep_still_files_extras(
        self,
        tmp_path,
        monkeypatch,
        policy,
        job_manager_module,  # noqa: F811
    ):
        organize = _organize_ok(monkeypatch, tmp_path / "lib" / "Hairspray (2007).mkv")
        job_id, _ = await _seed(
            tmp_path,
            state=JobState.RIPPING,
            specs=[
                (0, 117, TitleState.MATCHED, False, True),
                (1, 15, TitleState.MATCHED, False, True),
            ],
        )

        await self._finalize(job_manager_module, job_id, tmp_path)

        assert organize.call_args.kwargs["move_extras"] is True

    async def test_keep_files_extras_already_completed_by_an_earlier_pass(
        self,
        tmp_path,
        monkeypatch,
        policy,
        job_manager_module,  # noqa: F811
    ):
        """Only the skip policy's own marker turns extras filing off: an extra left
        COMPLETED by an earlier pass is not a skip decision."""
        organize = _organize_ok(monkeypatch, tmp_path / "lib" / "Hairspray (2007).mkv")
        job_id, _ = await _seed(
            tmp_path,
            state=JobState.RIPPING,
            specs=[
                (0, 117, TitleState.MATCHED, False, True),
                (1, 15, TitleState.COMPLETED, True, True),
            ],
        )

        await self._finalize(job_manager_module, job_id, tmp_path)

        assert organize.call_args.kwargs["move_extras"] is True

    async def test_ask_parks_the_disc_for_review(
        self,
        tmp_path,
        monkeypatch,
        policy,
        job_manager_module,  # noqa: F811
    ):
        policy.extras_policy = "ask"
        organize = _organize_ok(monkeypatch, tmp_path / "lib" / "Hairspray (2007).mkv")
        job_id, ids = await _seed(
            tmp_path,
            state=JobState.RIPPING,
            specs=[
                (0, 117, TitleState.MATCHED, False, True),
                (1, 15, TitleState.MATCHED, False, True),
            ],
        )

        await self._finalize(job_manager_module, job_id, tmp_path)

        organize.assert_not_called()
        assert (await _job(job_id)).state == JobState.REVIEW_NEEDED
        assert (await _titles(ids))[1].state == TitleState.REVIEW


class TestAskReview:
    async def test_kept_extras_are_filed_and_discarded_ones_deleted(
        self, tmp_path, monkeypatch, policy
    ):
        policy.extras_policy = "ask"
        job_id, ids = await _seed(
            tmp_path,
            state=JobState.REVIEW_NEEDED,
            specs=[
                (0, 117, TitleState.MATCHED, False, True),
                (1, 15, TitleState.REVIEW, True, False),
                (2, 4, TitleState.REVIEW, True, False),
            ],
        )
        feature_id, kept_id, discarded_id = ids
        kept_file = tmp_path / "title_t01.mkv"
        discarded_file = tmp_path / "title_t02.mkv"
        kept_dest = tmp_path / "lib" / "Extras" / "Extra 1.mkv"
        seen_at_organize: dict[str, bool] = {}

        def fake_organize(*args, **kwargs):
            seen_at_organize["kept"] = kept_file.exists()
            seen_at_organize["discarded"] = discarded_file.exists()
            return {
                "success": True,
                "main_file": tmp_path / "lib" / "Hairspray (2007).mkv",
                "extras": [kept_dest],
                "extras_mapping": {kept_file.name: kept_dest},
                "error": None,
            }

        monkeypatch.setattr(organizer_module.movie_organizer, "organize", fake_organize)
        coord = _make_coord()

        await coord.apply_review(job_id, discarded_id, episode_code="skip")
        assert (await _job(job_id)).state == JobState.REVIEW_NEEDED

        await coord.apply_review(job_id, feature_id)

        assert seen_at_organize == {"kept": True, "discarded": False}
        _, kept, discarded = await _titles(ids)
        assert kept.state == TitleState.COMPLETED
        assert kept.organized_to == str(kept_dest)
        assert discarded.state == TitleState.FAILED
        assert (await _job(job_id)).state == JobState.COMPLETED
