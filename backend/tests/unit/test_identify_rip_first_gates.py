"""Walk-away B2: the four pre-rip identity gates become non-blocking prompts.

``identify_disc`` no longer parks (A) unreadable-label, (B) TV-without-TMDB,
(C) same-name-collision, or (D) unknown-season discs in REVIEW_NEEDED — each
ships to RIPPING carrying an ``identity_prompt_json`` CTA whose reason text is
VERBATIM today's review_reason (the literals are frontend contracts, and B4's
rip-end convergence replays them as ``review_reason``). Every OTHER review
path still parks before ripping.

Gate C's collision seam is pinned end-to-end in
tests/integration/test_show_identity_collision.py; this file covers gates
A/B/D, the permissive title-selection helper, the still-parks pin, and the
full identify→rip→converge walk-away chain for an unreadable disc.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

import app.services.identification_coordinator as idc
from app.core.analyst import DiscAnalysisResult, TitleInfo
from app.core.extractor import RipResult
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.identification_coordinator import (
    IdentificationCoordinator,
    apply_permissive_title_selection,
)
from app.services.job_manager import job_manager
from app.services.job_state_machine import JobStateMachine
from tests.unit.conftest import _unit_session_factory

UNREADABLE_REASON = "Disc label unreadable. Please enter the title to continue."


@pytest.mark.unit
class TestApplyPermissiveTitleSelection:
    """Identity-unknown discs rip permissively: every title >= 900 s, else the
    single longest title; empty input is a no-op."""

    @staticmethod
    def _titles(*durations):
        return [
            DiscTitle(job_id=1, title_index=i, duration_seconds=d) for i, d in enumerate(durations)
        ]

    def test_mixed_durations_selects_only_long_titles(self):
        titles = self._titles(1500, 120, 900, 899)
        apply_permissive_title_selection(titles)
        assert [t.is_selected for t in titles] == [True, False, True, False]

    def test_all_short_disc_selects_only_the_longest(self):
        titles = self._titles(120, 600, 45)
        apply_permissive_title_selection(titles)
        assert [t.is_selected for t in titles] == [False, True, False]

    def test_none_duration_treated_as_zero(self):
        titles = [
            DiscTitle(job_id=1, title_index=0, duration_seconds=None),
            DiscTitle(job_id=1, title_index=1, duration_seconds=1500),
        ]
        apply_permissive_title_selection(titles)
        assert [t.is_selected for t in titles] == [False, True]

    def test_empty_list_is_noop(self):
        apply_permissive_title_selection([])  # must not raise

    def test_finalized_play_all_not_reselected(self):
        """Regression: a long COMPLETED+is_extra play-all must stay deselected
        even when it is the longest title on the disc (B2 review regression)."""
        # Play-all concat row: longest title, already finalized before rip.
        play_all = DiscTitle(
            job_id=1,
            title_index=0,
            duration_seconds=5000,
            state=TitleState.COMPLETED,
            is_extra=True,
            is_selected=False,
        )
        # Episode-length PENDING titles that should be selected.
        ep1 = DiscTitle(job_id=1, title_index=1, duration_seconds=1320, state=TitleState.PENDING)
        ep2 = DiscTitle(job_id=1, title_index=2, duration_seconds=1380, state=TitleState.PENDING)
        # Short PENDING title that falls below the 900-s floor.
        short = DiscTitle(job_id=1, title_index=3, duration_seconds=300, state=TitleState.PENDING)

        apply_permissive_title_selection([play_all, ep1, ep2, short])

        # Finalized play-all: must remain deselected and COMPLETED.
        assert play_all.is_selected is False
        assert play_all.state == TitleState.COMPLETED
        # Episode titles above floor: selected.
        assert ep1.is_selected is True
        assert ep2.is_selected is True
        # Short title: deselected.
        assert short.is_selected is False

    def test_all_ineligible_is_noop(self):
        """When every title is finalized/extra the helper must not select anything."""
        play_all = DiscTitle(
            job_id=1,
            title_index=0,
            duration_seconds=5000,
            state=TitleState.COMPLETED,
            is_extra=True,
            is_selected=False,
        )
        apply_permissive_title_selection([play_all])

        # Nothing should be selected.
        assert play_all.is_selected is False
        assert play_all.state == TitleState.COMPLETED


def _make_analysis(
    content_type,
    name,
    *,
    season=1,
    confidence=0.85,
    needs_review=False,
    review_reason=None,
    tmdb_id=None,
    signal=None,
):
    analysis = DiscAnalysisResult(content_type=content_type, confidence=confidence)
    analysis.detected_name = name
    analysis.detected_season = season
    analysis.needs_review = needs_review
    analysis.review_reason = review_reason
    analysis.tmdb_id = tmdb_id
    analysis.tmdb_name = name
    analysis._tmdb_signal = signal
    analysis._discdb_signal = None
    return analysis


def _bare_coord(analysis, titles, label, *, seasons=None):
    """Bare coordinator (skip __init__ wiring) with everything identify_disc
    touches stubbed except the code under test."""
    coord = IdentificationCoordinator.__new__(IdentificationCoordinator)
    coord._extractor = SimpleNamespace(scan_disc=AsyncMock(return_value=(titles, label)))
    broadcaster = MagicMock()
    broadcaster.broadcast_job_state_changed = AsyncMock()
    broadcaster.broadcast_job_completed = AsyncMock()
    broadcaster.broadcast_job_failed = AsyncMock()
    coord._broadcaster = broadcaster
    coord._state_machine = JobStateMachine(broadcaster)
    coord._get_discdb_mappings = lambda job_id: []
    coord._run_classification = AsyncMock(return_value=analysis)
    coord._run_ripping = AsyncMock()
    coord._start_subtitle_download = Mock()
    coord._start_subtitle_download_all_seasons = Mock()

    async def fake_resolve_seasons(title, tmdb_id=None):
        return seasons if seasons is not None else []

    coord._resolve_all_season_numbers = fake_resolve_seasons
    return coord


async def _seed_identifying_job(label):
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label=label,
            state=JobState.IDENTIFYING,
            staging_path="/tmp/staging",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


async def _reload_job(job_id):
    async with _unit_session_factory() as session:
        return await session.get(DiscJob, job_id)


async def _job_titles(job_id):
    from sqlmodel import select

    async with _unit_session_factory() as session:
        result = await session.execute(
            select(DiscTitle).where(DiscTitle.job_id == job_id).order_by(DiscTitle.title_index)
        )
        return result.scalars().all()


@pytest.fixture
def gate_env(monkeypatch):
    """Patch the identify_disc environment: in-memory DB, no snapshot writes,
    no TMDB year lookup, captured WS job updates."""
    monkeypatch.setattr(idc, "async_session", _unit_session_factory)

    import app.core.snapshot as snapshot_mod

    monkeypatch.setattr(snapshot_mod, "save_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(idc, "_resolve_show_year", lambda tmdb_id, signal=None: None)

    broadcasts: list[tuple[str, dict]] = []

    async def record_job_update(job_id, state, **kwargs):
        broadcasts.append((state, kwargs))

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(idc.ws_manager, "broadcast_job_update", record_job_update)
    monkeypatch.setattr(idc.ws_manager, "broadcast_titles_discovered", _noop)
    return broadcasts


_GATE_TITLES = [
    TitleInfo(index=0, duration_seconds=1500, size_bytes=2_000_000_000, chapter_count=7),
    TitleInfo(index=1, duration_seconds=120, size_bytes=50_000_000, chapter_count=1),
    TitleInfo(index=2, duration_seconds=2700, size_bytes=4_000_000_000, chapter_count=12),
]


@pytest.mark.unit
class TestGateAUnreadableLabel:
    async def test_rips_first_with_name_prompt_and_permissive_selection(self, gate_env):
        job_id = await _seed_identifying_job("DVD_VIDEO")
        analysis = _make_analysis(
            ContentType.UNKNOWN,
            None,
            season=None,
            needs_review=True,
            review_reason="Could not classify disc content",
        )
        coord = _bare_coord(analysis, _GATE_TITLES, "DVD_VIDEO")

        await coord.identify_disc(job_id)

        job = await _reload_job(job_id)
        assert job.state == JobState.RIPPING
        assert job.review_reason is None
        prompt = json.loads(job.identity_prompt_json)
        assert prompt == {"kind": "name", "reason": UNREADABLE_REASON}
        coord._run_ripping.assert_awaited_once_with(job_id)

        # Permissive selection: >= 900 s titles selected, the 2-minute one not.
        titles = await _job_titles(job_id)
        assert [t.is_selected for t in titles] == [True, False, True]

        # No prefetch possible (no identity at all).
        coord._start_subtitle_download.assert_not_called()
        coord._start_subtitle_download_all_seasons.assert_not_called()

        # The RIPPING broadcast carries the prompt for the dashboard CTA.
        ripping = [kw for state, kw in gate_env if state == JobState.RIPPING.value]
        assert any(
            json.loads(kw.get("identity_prompt_json") or "{}").get("kind") == "name"
            for kw in ripping
        )
        assert not any(state == JobState.REVIEW_NEEDED.value for state, _ in gate_env)


@pytest.mark.unit
class TestGateBTvWithoutTmdb:
    async def test_rips_first_with_name_prompt_and_no_prefetch(self, gate_env):
        job_id = await _seed_identifying_job("MERGEDWORDS_S2")
        analysis = _make_analysis(ContentType.TV, "Mergedwords", season=2, tmdb_id=None)
        coord = _bare_coord(analysis, _GATE_TITLES, "MERGEDWORDS_S2")

        await coord.identify_disc(job_id)

        job = await _reload_job(job_id)
        assert job.state == JobState.RIPPING
        assert job.review_reason is None
        prompt = json.loads(job.identity_prompt_json)
        assert prompt["kind"] == "name"
        # Verbatim literal — the NamePromptModal keys on this substring.
        assert "merged without separators" in prompt["reason"]
        assert 'Could not find "Mergedwords" on TMDB' in prompt["reason"]
        coord._run_ripping.assert_awaited_once_with(job_id)

        # No tmdb_id → prefetch must be skipped (a name-keyed download would
        # fetch the wrong show).
        coord._start_subtitle_download.assert_not_called()
        coord._start_subtitle_download_all_seasons.assert_not_called()
        assert not any(state == JobState.REVIEW_NEEDED.value for state, _ in gate_env)


@pytest.mark.unit
class TestGateDUnknownSeason:
    async def test_multi_season_rips_with_season_prompt_and_all_seasons_prefetch(self, gate_env):
        job_id = await _seed_identifying_job("EUREKA_D3")
        analysis = _make_analysis(ContentType.TV, "Eureka", season=None, tmdb_id=4620)
        coord = _bare_coord(analysis, _GATE_TITLES, "EUREKA_D3", seasons=[1, 2, 3, 4, 5])

        await coord.identify_disc(job_id)

        job = await _reload_job(job_id)
        assert job.state == JobState.RIPPING
        assert job.detected_season is None
        assert job.review_reason is None
        prompt = json.loads(job.identity_prompt_json)
        assert prompt["kind"] == "season"
        # "select a season" is the SeasonPromptModal frontend contract.
        assert "select a season" in prompt["reason"]
        coord._run_ripping.assert_awaited_once_with(job_id)

        # The existing detected_season-is-None seam fires the all-seasons prefetch.
        coord._start_subtitle_download_all_seasons.assert_called_once_with(
            job_id, "Eureka", [1, 2, 3, 4, 5], tmdb_id=4620
        )
        coord._start_subtitle_download.assert_not_called()

        # The season prompt rides along on the shared RIPPING broadcast.
        ripping = [kw for state, kw in gate_env if state == JobState.RIPPING.value]
        assert any(
            json.loads(kw.get("identity_prompt_json") or "{}").get("kind") == "season"
            for kw in ripping
        )

    async def test_single_season_auto_pin_unchanged(self, gate_env):
        job_id = await _seed_identifying_job("MINISERIES_D2")
        analysis = _make_analysis(ContentType.TV, "Miniseries", season=None, tmdb_id=777)
        coord = _bare_coord(analysis, _GATE_TITLES, "MINISERIES_D2", seasons=[1])

        await coord.identify_disc(job_id)

        job = await _reload_job(job_id)
        assert job.state == JobState.RIPPING
        assert job.detected_season == 1
        assert job.identity_prompt_json is None  # no prompt needed
        coord._run_ripping.assert_awaited_once_with(job_id)
        coord._start_subtitle_download.assert_called_once_with(job_id, "Miniseries", 1, 777)
        coord._start_subtitle_download_all_seasons.assert_not_called()


@pytest.mark.unit
class TestOtherReviewPathsStillPark:
    async def test_type_conflict_review_still_parks_before_ripping(self, gate_env):
        """A non-collision needs_review analysis (TMDB/heuristic content-type
        conflict) keeps today's pre-rip park — only the four gates convert."""
        job_id = await _seed_identifying_job("EUREKA_S2D1")
        analysis = _make_analysis(
            ContentType.TV,
            "Eureka",
            season=2,
            tmdb_id=4620,
            needs_review=True,
            review_reason="TMDB suggests movie but heuristics strongly suggest tv. Please verify.",
        )
        coord = _bare_coord(analysis, _GATE_TITLES, "EUREKA_S2D1")

        await coord.identify_disc(job_id)

        job = await _reload_job(job_id)
        assert job.state == JobState.REVIEW_NEEDED
        assert job.review_reason == analysis.review_reason
        assert job.identity_prompt_json is None
        coord._run_ripping.assert_not_called()

    async def test_park_with_unknown_season_clears_the_season_prompt(self, gate_env):
        """Gate D's shortcut CTA must not survive onto a blocking review: a job
        that parks for another reason after the season prompt was set clears it."""
        job_id = await _seed_identifying_job("EUREKA_D3")
        analysis = _make_analysis(
            ContentType.TV,
            "Eureka",
            season=None,
            tmdb_id=4620,
            needs_review=True,
            review_reason="TMDB suggests movie but heuristics strongly suggest tv. Please verify.",
        )
        coord = _bare_coord(analysis, _GATE_TITLES, "EUREKA_D3", seasons=[1, 2, 3])

        await coord.identify_disc(job_id)

        job = await _reload_job(job_id)
        assert job.state == JobState.REVIEW_NEEDED
        assert job.identity_prompt_json is None  # cleared on the park
        coord._run_ripping.assert_not_called()


@pytest.mark.unit
class TestUnreadableDiscWalkAwayChain:
    """ONE end-to-end chain for gate A: identify (prompt + permissive selection
    + RIPPING) → real _run_ripping with a mocked extractor (titles park QUEUED
    under the blocking name prompt — B3) → rip-end convergence to pooled review
    with the verbatim reason (B4). Proves the full walk-away path for an
    unreadable disc with nobody answering."""

    async def test_identify_rip_converge(self, tmp_path, monkeypatch):
        import importlib

        import app.core.discdb_exporter as exporter_mod
        import app.core.sentinel as sentinel_mod
        import app.core.snapshot as snapshot_mod

        # Resolve the actual module — the JobManager singleton shadows the
        # submodule name in the app.services package namespace.
        jm_mod = importlib.import_module("app.services.job_manager")

        coord = job_manager._identification

        # identify-side env: in-memory DB, silenced snapshot/WS, mocked scan +
        # classification (UNKNOWN type, no detected name → gate A).
        monkeypatch.setattr(idc, "async_session", _unit_session_factory)
        monkeypatch.setattr(snapshot_mod, "save_snapshot", lambda *a, **k: None)

        async def _noop(*a, **k):
            return None

        broadcasts: list[tuple[str, dict]] = []

        async def record_job_update(job_id, state, **kwargs):
            broadcasts.append((state, kwargs))

        monkeypatch.setattr(idc.ws_manager, "broadcast_job_update", record_job_update)
        monkeypatch.setattr(idc.ws_manager, "broadcast_titles_discovered", _noop)
        monkeypatch.setattr(idc.ws_manager, "broadcast_title_update", _noop)

        scan_titles = [
            TitleInfo(index=0, duration_seconds=1500, size_bytes=2_000_000_000, chapter_count=7),
            TitleInfo(index=1, duration_seconds=120, size_bytes=50_000_000, chapter_count=1),
        ]
        monkeypatch.setattr(
            coord._extractor, "scan_disc", AsyncMock(return_value=(scan_titles, "DVD_VIDEO"))
        )
        analysis = _make_analysis(
            ContentType.UNKNOWN,
            None,
            season=None,
            needs_review=True,
            review_reason="Could not classify disc content",
        )
        monkeypatch.setattr(coord, "_run_classification", AsyncMock(return_value=analysis))

        # rip-side env (mirrors test_job_manager's rip_env): no eject, no real
        # makemkv log dir, no terminal-state side effects, no backfill.
        monkeypatch.setattr(sentinel_mod, "eject_disc", lambda drive_id: None)
        monkeypatch.setattr(exporter_mod, "get_makemkv_log_dir", lambda job_id: tmp_path)
        monkeypatch.setattr(jm_mod.state_machine, "_on_terminal_callbacks", [])
        monkeypatch.setattr(job_manager, "_backfill_unmatched_titles", AsyncMock())

        staging = tmp_path / "staging"
        staging.mkdir()

        async def fake_rip(*args, **kwargs):
            # The "rip" produces the selected title's file but never fires the
            # per-title callback — reconcile_stuck_titles must recover it into
            # QUEUED (parked: the name prompt blocks) rather than MATCHED.
            (staging / "disc_t00.mkv").write_bytes(b"data")
            return RipResult(success=True, output_files=[])

        monkeypatch.setattr(job_manager._extractor, "rip_titles", AsyncMock(side_effect=fake_rip))

        async with _unit_session_factory() as session:
            db_job = DiscJob(
                drive_id="E:",
                volume_label="DVD_VIDEO",
                state=JobState.IDENTIFYING,
                staging_path=str(staging),
            )
            session.add(db_job)
            await session.commit()
            await session.refresh(db_job)
            job_id = db_job.id

        # identify_disc drives the whole chain: prompt → RIPPING → rip →
        # reconcile → convergence (run_ripping is awaited inline).
        await coord.identify_disc(job_id)

        job = await _reload_job(job_id)
        assert job.state == JobState.REVIEW_NEEDED
        assert job.review_reason == UNREADABLE_REASON  # verbatim — frontend contract
        assert job.identity_prompt_json is None  # converted, then cleared

        titles = await _job_titles(job_id)
        assert len(titles) == 2
        long_title, short_title = titles
        # The selected title was recovered into QUEUED and STAYS queued (B3
        # gate under the blocking prompt; the review flow owns it now).
        assert long_title.is_selected is True
        assert long_title.state == TitleState.QUEUED
        assert long_title.output_filename == str(staging / "disc_t00.mkv")
        # The short title was deselected by permissive selection and finalized
        # by the deselection safety net.
        assert short_title.is_selected is False
        assert short_title.state == TitleState.COMPLETED
        assert short_title.is_extra is True

        # State sequence reached the dashboard: RIPPING with the prompt, then
        # one pooled review with the same reason and the prompt cleared ("").
        ripping = [kw for state, kw in broadcasts if state == JobState.RIPPING.value]
        assert any(
            json.loads(kw.get("identity_prompt_json") or "{}").get("kind") == "name"
            for kw in ripping
        )
        review = [kw for state, kw in broadcasts if state == JobState.REVIEW_NEEDED.value]
        assert len(review) == 1
        assert review[0]["review_reason"] == UNREADABLE_REASON
        assert review[0]["identity_prompt_json"] == ""
