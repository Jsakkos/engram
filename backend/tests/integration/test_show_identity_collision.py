import pytest
from sqlalchemy import text

from app.core.analyst import DiscAnalysisResult, DiscAnalyst
from app.core.tmdb_classifier import TmdbSignal
from app.database import async_session, init_db
from app.models.disc_job import ContentType, DiscJob, JobState


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()
    async with async_session() as session:
        await session.execute(text("DELETE FROM disc_titles"))
        await session.execute(text("DELETE FROM disc_jobs"))
        await session.commit()


def test_ambiguous_signal_produces_review_result_without_id():
    """The analyst seam: an ambiguous TV signal yields needs_review + no tmdb_id."""
    analyst = DiscAnalyst()
    result = DiscAnalysisResult(content_type=ContentType.TV, confidence=0.85)
    sig = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.6,
        tmdb_id=37854,
        tmdb_name="One Piece",
        ambiguous_identity=True,
        candidates=[
            {"tmdb_id": 37854, "name": "One Piece", "year": "1999", "popularity": 60.0},
            {"tmdb_id": 111110, "name": "One Piece", "year": "2023", "popularity": 38.3},
        ],
    )
    out = analyst._apply_tmdb_signal(result, sig)
    assert out.needs_review is True
    assert out.tmdb_id is None
    assert "One Piece" in out.review_reason


async def test_match_single_file_forwards_tmdb_id(monkeypatch):
    """The curator seam: a known tmdb_id reaches _ensure_initialized."""
    from app.core.curator import EpisodeCurator

    cur = EpisodeCurator()
    seen = {}

    def fake_ensure(show_name, tmdb_id=None):
        seen["show_name"] = show_name
        seen["tmdb_id"] = tmdb_id
        return False  # matcher unavailable -> fallback path, no real matching

    monkeypatch.setattr(cur, "_ensure_initialized", fake_ensure)
    from pathlib import Path

    await cur.match_single_file(Path("nonexistent.mkv"), "Frasier", 1, tmdb_id=195241)
    assert seen == {"show_name": "Frasier", "tmdb_id": 195241}


async def test_ambiguous_disc_rips_first_with_reidentify_prompt(monkeypatch):
    """Coordinator seam: an ambiguous-identity analysis must NOT be intercepted by the
    generic 'words merged' TMDB-lookup-failed guard; it must fall through to the
    needs_review branch and convert to rip-first (walk-away B2) — RIPPING with a
    kind=reidentify prompt carrying the candidate-naming reason verbatim.
    """
    from app.core.analyst import TitleInfo
    from app.services.job_manager import job_manager

    coord = job_manager._identification

    # Seed a job in IDENTIFYING with a drive id.
    async with async_session() as session:
        job = DiscJob(
            volume_label="FRASIER_S1",
            drive_id="E:",
            state=JobState.IDENTIFYING,
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    # Fake disc scan: return one plausible title so identify_disc doesn't bail early.
    async def fake_scan_disc(drive, log_dir=None, *, job_id=0):
        return (
            [TitleInfo(index=0, duration_seconds=1440, size_bytes=2_000_000_000, chapter_count=7)],
            "FRASIER_S1",
        )

    monkeypatch.setattr(coord._extractor, "scan_disc", fake_scan_disc)

    # Craft an ambiguous analysis: TV, no tmdb_id, needs_review, candidate-naming reason.
    candidate_reason = (
        'Multiple shows match "Frasier" on TMDB: '
        "Frasier (1993, #3452); Frasier (2023, #195241). Pick the correct one."
    )
    analysis = DiscAnalysisResult(content_type=ContentType.TV, confidence=0.6)
    analysis.detected_name = "Frasier"
    analysis.detected_season = 1
    analysis.needs_review = True
    analysis.tmdb_id = None
    analysis.review_reason = candidate_reason
    twins = [
        {"tmdb_id": 3452, "name": "Frasier", "year": "1993", "popularity": 75.6},
        {"tmdb_id": 195241, "name": "Frasier", "year": "2023", "popularity": 5.7},
    ]
    analysis._tmdb_signal = TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.6,
        tmdb_id=None,
        tmdb_name="Frasier",
        ambiguous_identity=True,
        candidates=twins,
        all_candidates=twins,
    )
    analysis._discdb_signal = None

    async def fake_run_classification(*args, **kwargs):
        return analysis

    monkeypatch.setattr(coord, "_run_classification", fake_run_classification)

    ran_ripping = []

    async def fake_run_ripping(jid):
        ran_ripping.append(jid)

    monkeypatch.setattr(coord, "_run_ripping", fake_run_ripping)

    # Capture WebSocket job updates so we can assert the prompt (with the candidate
    # reason the ReIdentifyModal banner relies on) reaches the client, not just the DB.
    import app.services.identification_coordinator as idc

    broadcasts: list[tuple[str, dict]] = []

    async def record_broadcast(job_id_arg, state, **kwargs):
        broadcasts.append((state, kwargs))

    monkeypatch.setattr(idc.ws_manager, "broadcast_job_update", record_broadcast)

    # Drive the real identify_disc flow.
    await coord.identify_disc(job_id)

    # Reload from DB and assert correct routing: rip-first with a reidentify prompt.
    import json as _json

    async with async_session() as session:
        refreshed = await session.get(DiscJob, job_id)
        assert refreshed.state == JobState.RIPPING, f"Expected RIPPING, got {refreshed.state}"
        assert refreshed.review_reason is None
        prompt = _json.loads(refreshed.identity_prompt_json)
        assert prompt["kind"] == "reidentify"
        assert "Multiple shows match" in prompt["reason"], (
            f"Candidate reason not found in prompt: {prompt['reason']!r}"
        )
        assert "words merged" not in prompt["reason"], (
            f"Generic 'words merged' message incorrectly set: {prompt['reason']!r}"
        )
        # The twins survive on the job for the ReIdentifyModal quick-pick.
        assert "195241" in (refreshed.candidates_json or "")

    assert ran_ripping == [job_id]

    # The WS broadcast for the rip must carry the prompt with the candidate reason.
    ripping_prompts = [
        kwargs.get("identity_prompt_json")
        for state, kwargs in broadcasts
        if state == JobState.RIPPING.value
    ]
    assert any("Multiple shows match" in (p or "") for p in ripping_prompts), (
        f"No RIPPING broadcast carried the reidentify prompt; saw: {ripping_prompts!r}"
    )
    # And nothing broadcast a pre-rip review park.
    assert not any(state == JobState.REVIEW_NEEDED.value for state, _ in broadcasts)


def _frasier_signal(tmdb_id, ambiguous=False):
    return TmdbSignal(
        content_type=ContentType.TV,
        confidence=0.85,
        tmdb_id=tmdb_id,
        tmdb_name="Frasier",
        ambiguous_identity=ambiguous,
        all_candidates=[
            {"tmdb_id": 3452, "name": "Frasier", "year": "1993", "popularity": 75.6},
            {"tmdb_id": 195241, "name": "Frasier", "year": "2023", "popularity": 5.7},
        ],
    )


async def _drive_identify(monkeypatch, *, volume_label, signal):
    """Drive the real identify_disc with a crafted (non-ambiguous) analysis + signal.

    Returns (job_id, started_subtitles: bool, ran_ripping: bool).
    """
    from app.core.analyst import TitleInfo
    from app.services.job_manager import job_manager

    coord = job_manager._identification

    async with async_session() as session:
        job = DiscJob(volume_label=volume_label, drive_id="E:", state=JobState.IDENTIFYING)
        session.add(job)
        await session.commit()
        job_id = job.id

    async def fake_scan_disc(drive, log_dir=None, *, job_id=0):
        return (
            [TitleInfo(index=0, duration_seconds=1440, size_bytes=2_000_000_000, chapter_count=7)],
            volume_label,
        )

    monkeypatch.setattr(coord._extractor, "scan_disc", fake_scan_disc)

    analysis = DiscAnalysisResult(content_type=ContentType.TV, confidence=0.85)
    analysis.detected_name = "Frasier"
    analysis.detected_season = 1
    analysis.needs_review = False
    analysis.tmdb_id = signal.tmdb_id
    analysis.tmdb_name = "Frasier"
    analysis.review_reason = None
    analysis._tmdb_signal = signal
    analysis._discdb_signal = None

    async def fake_run_classification(*args, **kwargs):
        return analysis

    monkeypatch.setattr(coord, "_run_classification", fake_run_classification)

    flags = {"subtitles": False, "ripping": False}

    def fake_subtitles(*a, **k):
        flags["subtitles"] = True

    async def fake_run_ripping(*a, **k):
        flags["ripping"] = True

    monkeypatch.setattr(coord, "_start_subtitle_download", fake_subtitles)
    monkeypatch.setattr(coord, "_run_ripping", fake_run_ripping)

    import app.services.identification_coordinator as idc

    async def noop_broadcast(*a, **k):
        return None

    monkeypatch.setattr(idc.ws_manager, "broadcast_job_update", noop_broadcast)

    await coord.identify_disc(job_id)
    return job_id, flags["subtitles"], flags["ripping"]


async def test_no_year_twin_rips_first_with_reidentify_prompt(monkeypatch):
    """Frasier backstop (reworked for walk-away B2): a no-year disc with a
    same-name twin rips first carrying a kind=reidentify prompt (with the
    no-year candidate reason verbatim) and must NOT download subtitles — even
    though the materiality gate did not fire and a popularity-best tmdb_id is set."""
    import json as _json

    job_id, started_subtitles, ran_ripping = await _drive_identify(
        monkeypatch, volume_label="FRASIER_S1D1", signal=_frasier_signal(3452)
    )

    async with async_session() as session:
        refreshed = await session.get(DiscJob, job_id)
        assert refreshed.state == JobState.RIPPING
        assert refreshed.review_reason is None
        prompt = _json.loads(refreshed.identity_prompt_json)
        assert prompt["kind"] == "reidentify"
        assert "no year" in prompt["reason"].lower()
        assert "195241" in prompt["reason"]
        # The popularity-best guess is kept as the pre-selection; twins are persisted.
        assert refreshed.tmdb_id == 3452
        assert "195241" in (refreshed.candidates_json or "")

    # Identity is uncertain → no prefetch; the rip starts anyway.
    assert started_subtitles is False
    assert ran_ripping is True


async def test_year_in_label_skips_no_year_flag_and_rips(monkeypatch):
    """A year in the label disambiguates twins, so identify proceeds normally
    (no prompt, prefetch + rip as today)."""
    job_id, started_subtitles, ran_ripping = await _drive_identify(
        monkeypatch, volume_label="FRASIER_2023_S1D1", signal=_frasier_signal(195241)
    )

    async with async_session() as session:
        refreshed = await session.get(DiscJob, job_id)
        assert refreshed.state != JobState.REVIEW_NEEDED
        assert refreshed.identity_prompt_json is None

    assert started_subtitles is True
    assert ran_ripping is True
