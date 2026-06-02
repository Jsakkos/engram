"""Unit tests for FinalizationCoordinator's conflict resolution + completion routing.

The escalation ladder and conflict helpers are covered by
test_auto_conflict_escalation.py; this file targets finalize_disc_job's
ranking/reassignment loop and organize routing, plus check_job_completion's
decision branches. The organizer and websocket layers are stubbed.
"""

import json
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from sqlmodel import select

from app.api.websocket import manager as ws_manager
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.finalization_coordinator import (
    FinalizationCoordinator,
    _detect_wrong_show,
)
from app.services.job_state_machine import JobStateMachine
from tests.unit.conftest import _unit_session_factory

FRASIER_CANDS = json.dumps(
    [
        {"tmdb_id": 3452, "name": "Frasier", "year": "1993", "popularity": 75.6},
        {"tmdb_id": 195241, "name": "Frasier", "year": "2023", "popularity": 5.7},
    ]
)


@pytest.fixture(autouse=True)
def _patch_session_and_ws(monkeypatch):
    # finalize_disc_job opens its own session; point it at the test DB.
    monkeypatch.setattr(
        "app.services.finalization_coordinator.async_session", _unit_session_factory
    )

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ws_manager, "broadcast_job_update", _noop)
    monkeypatch.setattr(ws_manager, "broadcast_title_update", _noop)


@pytest.fixture
def mock_organize(monkeypatch):
    """Stub tv_organizer.organize to a success result by default."""
    import app.core.organizer as org

    m = Mock(return_value={"success": True, "final_path": "/lib/tv/Show/ep.mkv"})
    monkeypatch.setattr(org.tv_organizer, "organize", m)
    return m


def _make_coord() -> FinalizationCoordinator:
    broadcaster = MagicMock()
    broadcaster.broadcast_job_completed = AsyncMock()
    broadcaster.broadcast_job_failed = AsyncMock()
    broadcaster.broadcast_job_state_changed = AsyncMock()
    return FinalizationCoordinator(broadcaster, JobStateMachine(broadcaster))


async def _seed_job(
    titles,
    staging,
    *,
    content_type=ContentType.TV,
    state=JobState.MATCHING,
    match_details_by_idx=None,
    tmdb_id=None,
    candidates_json=None,
) -> int:
    """Seed a job with the given (title_index, episode, output_filename, title_state) titles."""
    md = match_details_by_idx or {}
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="SHOW_S1D1",
            content_type=content_type,
            state=state,
            detected_title="Some Show",
            detected_season=1,
            staging_path=staging,
            tmdb_id=tmdb_id,
            candidates_json=candidates_json,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        for idx, ep, outfn, tstate in titles:
            session.add(
                DiscTitle(
                    job_id=job.id,
                    title_index=idx,
                    duration_seconds=1380,
                    matched_episode=ep,
                    match_confidence=0.8,
                    state=tstate,
                    output_filename=outfn,
                    match_details=md.get(idx),
                )
            )
        await session.commit()
        return job.id


async def _load(job_id):
    async with _unit_session_factory() as session:
        job = await session.get(DiscJob, job_id)
        titles = (
            (await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id)))
            .scalars()
            .all()
        )
        return job, {t.title_index: t for t in titles}


@pytest.mark.unit
class TestFinalizeDiscJob:
    async def test_no_conflict_organizes_all_and_completes(self, tmp_path, mock_organize):
        f0 = tmp_path / "show_t00.mkv"
        f1 = tmp_path / "show_t01.mkv"
        f0.write_text("")
        f1.write_text("")
        job_id = await _seed_job(
            [
                (0, "S01E01", str(f0), TitleState.MATCHED),
                (1, "S01E02", str(f1), TitleState.MATCHED),
            ],
            staging=str(tmp_path),
        )

        await _make_coord().finalize_disc_job(job_id)

        job, titles = await _load(job_id)
        assert job.state == JobState.COMPLETED
        assert all(t.state == TitleState.COMPLETED for t in titles.values())
        assert mock_organize.call_count == 2

    async def test_conflict_reassigns_loser_via_runner_up(self, tmp_path, mock_organize):
        f0 = tmp_path / "show_t00.mkv"
        f1 = tmp_path / "show_t01.mkv"
        f0.write_text("")
        f1.write_text("")
        job_id = await _seed_job(
            [
                (0, "S01E05", str(f0), TitleState.MATCHED),
                (1, "S01E05", str(f1), TitleState.MATCHED),
            ],
            staging=str(tmp_path),
            match_details_by_idx={
                0: json.dumps({"score": 0.9, "vote_count": 10, "file_cov": 0.9, "runner_ups": []}),
                1: json.dumps(
                    {
                        "score": 0.5,
                        "vote_count": 2,
                        "file_cov": 0.5,
                        "runner_ups": [{"episode": "S01E06", "score": 0.8, "confidence": 0.85}],
                    }
                ),
            },
        )

        await _make_coord().finalize_disc_job(job_id)

        job, titles = await _load(job_id)
        # Lower-voted title is bumped to its runner-up episode and both organize.
        assert titles[0].matched_episode == "S01E05"
        assert titles[1].matched_episode == "S01E06"
        assert titles[1].match_confidence == 0.85
        assert all(t.state == TitleState.COMPLETED for t in titles.values())
        assert job.state == JobState.COMPLETED

    async def test_conflict_without_runner_up_defers_to_review(self, tmp_path, mock_organize):
        f0 = tmp_path / "show_t00.mkv"
        f1 = tmp_path / "show_t01.mkv"
        f0.write_text("")
        f1.write_text("")
        job_id = await _seed_job(
            [
                (0, "S01E05", str(f0), TitleState.MATCHED),
                (1, "S01E05", str(f1), TitleState.MATCHED),
            ],
            staging=str(tmp_path),
            match_details_by_idx={
                0: json.dumps({"vote_count": 10, "score": 0.9, "runner_ups": []}),
                1: json.dumps({"vote_count": 2, "score": 0.5, "runner_ups": []}),
            },
        )

        await _make_coord().finalize_disc_job(job_id)

        job, titles = await _load(job_id)
        assert titles[1].state == TitleState.REVIEW
        # Winner is held (not organized) while the disc has an unresolved title.
        assert titles[0].state == TitleState.MATCHED
        assert job.state == JobState.REVIEW_NEEDED
        mock_organize.assert_not_called()

    async def test_missing_source_file_marks_review(self, tmp_path, mock_organize):
        job_id = await _seed_job(
            [(0, "S01E01", None, TitleState.MATCHED)],
            staging=str(tmp_path),  # empty dir, no glob match
        )

        await _make_coord().finalize_disc_job(job_id)

        job, titles = await _load(job_id)
        assert titles[0].state == TitleState.REVIEW
        assert job.state == JobState.REVIEW_NEEDED
        mock_organize.assert_not_called()

    async def test_organize_failure_marks_review(self, tmp_path, monkeypatch):
        import app.core.organizer as org

        monkeypatch.setattr(
            org.tv_organizer,
            "organize",
            Mock(return_value={"success": False, "error": "disk full"}),
        )
        f0 = tmp_path / "show_t00.mkv"
        f0.write_text("")
        job_id = await _seed_job(
            [(0, "S01E01", str(f0), TitleState.MATCHED)], staging=str(tmp_path)
        )

        await _make_coord().finalize_disc_job(job_id)

        job, titles = await _load(job_id)
        assert titles[0].state == TitleState.REVIEW
        assert job.state == JobState.REVIEW_NEEDED

    async def test_extra_title_routes_to_extras_folder(self, tmp_path, monkeypatch):
        """A MATCHED title tagged ``matched_episode == "extra"`` must land in the
        season's Extras/ folder (via organize_tv_extras) with is_extra=True — the
        same handling as the review path, not the regular-episode path.

        Mirrors test_review_batch.py::test_batch_review_organizes_extras_without_collision
        by redirecting the TV library to a tmp dir through get_config_sync.
        """
        from app.models import AppConfig

        tv_lib = tmp_path / "TV"
        tv_lib.mkdir()
        # destination_mode defaults to "library", so _library_path_for_job returns
        # None and organize_tv_extras falls back to get_config_sync().library_tv_path.
        fake_config = AppConfig(library_tv_path=str(tv_lib))
        monkeypatch.setattr("app.services.config_service.get_config_sync", lambda: fake_config)

        f0 = tmp_path / "show_t03.mkv"
        f0.write_text("")
        job_id = await _seed_job(
            [(3, "extra", str(f0), TitleState.MATCHED)],
            staging=str(tmp_path),
        )

        await _make_coord().finalize_disc_job(job_id)

        job, titles = await _load(job_id)
        assert titles[3].state == TitleState.COMPLETED
        assert titles[3].is_extra is True
        extras_dir = tv_lib / "Some Show" / "Season 01" / "Extras"
        organized = list(extras_dir.glob("*.mkv"))
        assert len(organized) == 1, f"expected the extra under Extras/, got {organized}"
        assert job.state == JobState.COMPLETED

    async def test_multiple_extras_organize_without_false_conflict(self, tmp_path, monkeypatch):
        """Several ``"extra"``-tagged MATCHED titles share the synthetic "extra"
        code but are NOT an episode collision: each must organize to a distinct
        Extras/ file and none may be bounced to review by conflict resolution.
        """
        from app.models import AppConfig

        tv_lib = tmp_path / "TV"
        tv_lib.mkdir()
        fake_config = AppConfig(library_tv_path=str(tv_lib))
        monkeypatch.setattr("app.services.config_service.get_config_sync", lambda: fake_config)

        f0 = tmp_path / "show_t03.mkv"
        f1 = tmp_path / "show_t04.mkv"
        f0.write_text("")
        f1.write_text("")
        job_id = await _seed_job(
            [
                (3, "extra", str(f0), TitleState.MATCHED),
                (4, "extra", str(f1), TitleState.MATCHED),
            ],
            staging=str(tmp_path),
        )

        await _make_coord().finalize_disc_job(job_id)

        job, titles = await _load(job_id)
        assert all(t.state == TitleState.COMPLETED for t in titles.values())
        assert all(t.is_extra is True for t in titles.values())
        extras_dir = tv_lib / "Some Show" / "Season 01" / "Extras"
        organized = sorted(p.name for p in extras_dir.glob("*.mkv"))
        assert len(organized) == 2, f"expected 2 distinct extras, got {organized}"
        assert len(set(organized)) == 2
        assert job.state == JobState.COMPLETED

    async def test_extra_organize_failure_keeps_is_extra(self, tmp_path, monkeypatch):
        """When organize_tv_extras fails (e.g. destination already exists), the
        extra is sent to REVIEW but must keep is_extra=True so the episode
        re-match loop skips it. _is_rematchable_review guards on is_extra; leaving
        it False would feed the extra into audio re-match — wasted passes that
        can't yield a valid episode code for an extra.
        """
        from app.models import AppConfig
        from app.services.finalization_coordinator import _is_rematchable_review

        tv_lib = tmp_path / "TV"
        tv_lib.mkdir()
        fake_config = AppConfig(library_tv_path=str(tv_lib))
        monkeypatch.setattr("app.services.config_service.get_config_sync", lambda: fake_config)

        # Pre-create the exact destination so organize_tv_extras returns FILE_EXISTS.
        dest_dir = tv_lib / "Some Show" / "Season 01" / "Extras"
        dest_dir.mkdir(parents=True)
        (dest_dir / "Some Show Disc 1 Extra t03.mkv").write_text("existing")

        f0 = tmp_path / "show_t03.mkv"
        f0.write_text("")
        job_id = await _seed_job(
            [(3, "extra", str(f0), TitleState.MATCHED)],
            staging=str(tmp_path),
        )

        await _make_coord().finalize_disc_job(job_id)

        job, titles = await _load(job_id)
        assert titles[3].state == TitleState.REVIEW
        assert titles[3].is_extra is True
        assert _is_rematchable_review(titles[3]) is False
        assert job.state == JobState.REVIEW_NEEDED


@pytest.mark.unit
class TestApplyReviewDecisionMovie:
    """Test the post-rip movie review path in apply_review."""

    async def test_organized_fields_set_on_movie_review_success(self, tmp_path, monkeypatch):
        """title.organized_from/organized_to are persisted and broadcast after a movie review."""
        import app.core.organizer as org

        source = tmp_path / "INCEPTION_2010_t00.mkv"
        source.write_bytes(b"x" * 1024)
        dest = tmp_path / "movies" / "Inception (2010)" / "Inception (2010).mkv"

        monkeypatch.setattr(
            org.movie_organizer,
            "organize",
            Mock(return_value={"success": True, "main_file": dest}),
        )

        job_id = await _seed_job(
            [(0, None, str(source), TitleState.REVIEW)],
            staging=str(tmp_path),
            content_type=ContentType.MOVIE,
            state=JobState.REVIEW_NEEDED,
        )
        _, titles = await _load(job_id)
        title_id = titles[0].id

        broadcast_spy = AsyncMock()
        monkeypatch.setattr(ws_manager, "broadcast_title_update", broadcast_spy)

        await _make_coord().apply_review(job_id, title_id)

        _, titles = await _load(job_id)
        t = titles[0]
        assert t.organized_from == source.name
        assert t.organized_to == str(dest)

        broadcast_spy.assert_called_once_with(
            job_id,
            title_id,
            TitleState.COMPLETED.value,
            organized_from=source.name,
            organized_to=str(dest),
            output_filename=str(source),
        )


@pytest.mark.unit
class TestCheckJobCompletion:
    async def test_active_title_returns_without_finalizing(self, tmp_path):
        job_id = await _seed_job([(0, "S01E01", None, TitleState.RIPPING)], staging=str(tmp_path))
        coord = _make_coord()
        coord.finalize_disc_job = AsyncMock()

        async with _unit_session_factory() as session:
            await coord.check_job_completion(session, job_id)

        job, _ = await _load(job_id)
        assert job.state == JobState.MATCHING  # unchanged
        coord.finalize_disc_job.assert_not_called()

    async def test_review_title_transitions_to_review(self, tmp_path):
        job_id = await _seed_job(
            [
                (0, "S01E01", None, TitleState.MATCHED),
                (1, None, None, TitleState.REVIEW),
            ],
            staging=str(tmp_path),
        )
        coord = _make_coord()
        coord.finalize_disc_job = AsyncMock()

        async with _unit_session_factory() as session:
            await coord.check_job_completion(session, job_id)

        job, _ = await _load(job_id)
        assert job.state == JobState.REVIEW_NEEDED
        coord.finalize_disc_job.assert_not_called()

    async def test_all_matched_invokes_finalize(self, tmp_path):
        job_id = await _seed_job([(0, "S01E01", None, TitleState.MATCHED)], staging=str(tmp_path))
        coord = _make_coord()
        coord.finalize_disc_job = AsyncMock()

        async with _unit_session_factory() as session:
            await coord.check_job_completion(session, job_id)

        coord.finalize_disc_job.assert_awaited_once_with(job_id)

    async def test_all_failed_transitions_to_failed(self, tmp_path):
        job_id = await _seed_job(
            [
                (0, None, None, TitleState.FAILED),
                (1, None, None, TitleState.FAILED),
            ],
            staging=str(tmp_path),
        )
        coord = _make_coord()
        coord.finalize_disc_job = AsyncMock()

        async with _unit_session_factory() as session:
            await coord.check_job_completion(session, job_id)

        job, _ = await _load(job_id)
        assert job.state == JobState.FAILED
        coord.finalize_disc_job.assert_not_called()

    async def test_conflict_escalation_short_circuits_finalize(self, tmp_path):
        job_id = await _seed_job(
            [
                (0, "S01E05", None, TitleState.MATCHED),
                (1, "S01E05", None, TitleState.MATCHED),
            ],
            staging=str(tmp_path),
        )
        coord = _make_coord()
        coord.finalize_disc_job = AsyncMock()

        async def fake_rematch(jid, ep, num_points=None, min_vote_count=None):
            return {"dispatched": [1], "skipped": []}

        coord._rematch_conflict = fake_rematch

        async with _unit_session_factory() as session:
            await coord.check_job_completion(session, job_id)

        # Escalation dispatched a re-match, so finalization is deferred.
        coord.finalize_disc_job.assert_not_called()
        assert coord._conflict_passes.get(job_id) == 25


@pytest.mark.unit
class TestDetectWrongShow:
    """The pure wrong-show signal: a whole TV disc that matched NOTHING against
    its reference, plus a same-name twin, means the disc was identified as the
    wrong same-named show (e.g. Frasier 1993 corpus vs a 2023-revival disc)."""

    def _job(self, **kw):
        base = dict(
            drive_id="E:",
            volume_label="FRASIER_S1D1",
            content_type=ContentType.TV,
            tmdb_id=3452,
            tmdb_name="Frasier",
            candidates_json=FRASIER_CANDS,
        )
        base.update(kw)
        return DiscJob(**base)

    def _ttl(self, idx, matched_episode=None, is_extra=False, is_selected=True):
        return DiscTitle(
            job_id=1,
            title_index=idx,
            duration_seconds=1380,
            matched_episode=matched_episode,
            is_extra=is_extra,
            is_selected=is_selected,
            state=TitleState.REVIEW,
        )

    def test_all_zero_match_with_twin_detects_and_names_twin(self):
        titles = [self._ttl(0), self._ttl(1), self._ttl(2)]
        res = _detect_wrong_show(self._job(), titles)
        assert res is not None
        assert res["twin"]["tmdb_id"] == 195241
        assert res["twin"]["year"] == "2023"
        assert res["unmatched"] == 3

    def test_none_when_any_title_matched(self):
        titles = [self._ttl(0), self._ttl(1, matched_episode="S01E02"), self._ttl(2)]
        assert _detect_wrong_show(self._job(), titles) is None

    def test_none_without_persisted_twin(self):
        titles = [self._ttl(0), self._ttl(1)]
        assert _detect_wrong_show(self._job(candidates_json=None), titles) is None

    def test_none_when_only_one_episode_candidate(self):
        titles = [self._ttl(0)]
        assert _detect_wrong_show(self._job(), titles) is None

    def test_excludes_extras_from_candidate_count(self):
        # 1 real episode candidate + 2 extras -> below the >=2 episode floor.
        titles = [self._ttl(0), self._ttl(1, is_extra=True), self._ttl(2, is_extra=True)]
        assert _detect_wrong_show(self._job(), titles) is None

    def test_none_for_movie(self):
        titles = [self._ttl(0), self._ttl(1)]
        assert _detect_wrong_show(self._job(content_type=ContentType.MOVIE), titles) is None


@pytest.mark.unit
class TestWrongShowRoutingInCompletion:
    """check_job_completion must surface the wrong-show review (naming the twin)
    instead of the generic "needs manual episode assignment" message."""

    async def test_all_zero_match_with_twin_routes_to_wrong_show_review(self, tmp_path):
        job_id = await _seed_job(
            [
                (0, None, None, TitleState.REVIEW),
                (1, None, None, TitleState.REVIEW),
                (2, None, None, TitleState.REVIEW),
            ],
            staging=str(tmp_path),
            tmdb_id=3452,
            candidates_json=FRASIER_CANDS,
        )
        coord = _make_coord()
        coord.finalize_disc_job = AsyncMock()

        async with _unit_session_factory() as session:
            await coord.check_job_completion(session, job_id)

        job, _ = await _load(job_id)
        assert job.state == JobState.REVIEW_NEEDED
        assert "Frasier" in job.review_reason
        assert "2023" in job.review_reason
        assert "re-identify" in job.review_reason.lower()
        coord.finalize_disc_job.assert_not_called()

    async def test_wrong_show_clears_review_pass_counter(self, tmp_path):
        # The wrong-show branch fires only after the review-escalation ladder is
        # EXHAUSTED, where _maybe_escalate_reviews intentionally leaves
        # _review_passes pinned at max (so re-entries bail). REVIEW_NEEDED isn't
        # terminal, so reset_conflict_passes never fires — the wrong-show block
        # must clear it, else a re-identify to the right show skips deep re-match.
        job_id = await _seed_job(
            [
                (0, None, None, TitleState.REVIEW),
                (1, None, None, TitleState.REVIEW),
            ],
            staging=str(tmp_path),
            tmdb_id=3452,
            candidates_json=FRASIER_CANDS,
        )
        coord = _make_coord()
        coord.finalize_disc_job = AsyncMock()
        # A non-None rematch callback so _maybe_escalate_reviews doesn't take its
        # early "no callback" branch (which clears the counter itself); a pinned
        # counter forces the exhausted-ladder branch that LEAVES it set.
        coord._rematch_title = AsyncMock()
        coord._review_passes[job_id] = 999

        async with _unit_session_factory() as session:
            await coord.check_job_completion(session, job_id)

        job, _ = await _load(job_id)
        assert job.state == JobState.REVIEW_NEEDED
        coord._rematch_title.assert_not_awaited()  # ladder exhausted, no dispatch
        assert job_id not in coord._review_passes

    async def test_partial_match_keeps_generic_review_reason(self, tmp_path):
        # One title matched -> not a wrong-show disc; keep the generic message.
        job_id = await _seed_job(
            [
                (0, "S01E01", None, TitleState.MATCHED),
                (1, None, None, TitleState.REVIEW),
            ],
            staging=str(tmp_path),
            tmdb_id=3452,
            candidates_json=FRASIER_CANDS,
        )
        coord = _make_coord()
        coord.finalize_disc_job = AsyncMock()

        async with _unit_session_factory() as session:
            await coord.check_job_completion(session, job_id)

        job, _ = await _load(job_id)
        assert job.state == JobState.REVIEW_NEEDED
        assert "manual episode assignment" in job.review_reason


@pytest.mark.unit
class TestEpisodeOrderingProjection:
    """The #200 invariant: output ordering changes the FILENAME on disk only.
    matched_episode (the canonical identity + fingerprint-network key source)
    must stay in TMDB aired order through finalization."""

    async def test_dvd_ordering_projects_file_but_keeps_matched_episode_canonical(
        self, tmp_path, monkeypatch
    ):
        from app.models import AppConfig, ShowOrderingPreference

        tv_lib = tmp_path / "TV"
        tv_lib.mkdir()
        # get_config_sync (sync DB) drives the organizer's library path + naming.
        monkeypatch.setattr(
            "app.services.config_service.get_config_sync",
            lambda: AppConfig(library_tv_path=str(tv_lib), tmdb_api_key="k"),
        )
        # Stand in for the TMDB projection: canonical S01E11 ("Serenity") -> DVD S01E01.
        monkeypatch.setattr(
            "app.core.episode_ordering.project_episode",
            lambda show_id, ordering, s, e, key: (1, 1) if (s, e) == (1, 11) else (s, e),
        )

        f0 = tmp_path / "show_t00.mkv"
        f0.write_text("")
        job_id = await _seed_job(
            [(0, "S01E11", str(f0), TitleState.MATCHED)],
            staging=str(tmp_path),
            tmdb_id=1437,
        )
        # Pin the show to DVD ordering; group pre-resolved so no TMDB call fires.
        async with _unit_session_factory() as session:
            session.add(
                ShowOrderingPreference(tmdb_id=1437, ordering="dvd", episode_group_id="grp_dvd")
            )
            await session.commit()

        await _make_coord().finalize_disc_job(job_id)

        _job, titles = await _load(job_id)
        t = titles[0]
        assert t.state == TitleState.COMPLETED
        # INVARIANT: canonical identity is untouched (fingerprint key stays aired).
        assert t.matched_episode == "S01E11"
        # ...but the file on disk uses the DVD number.
        assert (tv_lib / "Some Show" / "Season 01" / "Some Show - S01E01.mkv").exists()
        assert t.organized_to.endswith("Some Show - S01E01.mkv")
        # Audit records what was applied.
        assert t.episode_ordering == "dvd"
        assert t.episode_group_id == "grp_dvd"

    async def test_aired_default_files_canonically_without_projecting(self, tmp_path, monkeypatch):
        from unittest.mock import Mock

        from app.models import AppConfig

        tv_lib = tmp_path / "TV"
        tv_lib.mkdir()
        monkeypatch.setattr(
            "app.services.config_service.get_config_sync",
            lambda: AppConfig(library_tv_path=str(tv_lib)),
        )
        proj = Mock()
        monkeypatch.setattr("app.core.episode_ordering.project_episode", proj)

        f0 = tmp_path / "show_t00.mkv"
        f0.write_text("")
        # tmdb_id set, but no per-show pref and global default is "aired".
        job_id = await _seed_job(
            [(0, "S01E11", str(f0), TitleState.MATCHED)],
            staging=str(tmp_path),
            tmdb_id=1437,
        )

        await _make_coord().finalize_disc_job(job_id)

        _job, titles = await _load(job_id)
        t = titles[0]
        assert t.matched_episode == "S01E11"
        assert (tv_lib / "Some Show" / "Season 01" / "Some Show - S01E11.mkv").exists()
        assert t.episode_ordering is None
        # aired is the identity path — the projection is never invoked.
        assert proj.call_count == 0
