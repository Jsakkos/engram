"""Unit tests for the TranscriptionPrewarmer background service.

The matcher and Whisper model are faked; the transcript store is the real
module redirected to a tmp SQLite file by the global conftest fixture
(``_isolate_transcript_store``), so coverage checks and write-through exercise
the production key shapes.
"""

import asyncio
import importlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.matcher import transcript_store
from app.matcher.asr_models import model_output_key
from app.matcher.episode_identification import EpisodeMatcher, canonical_scan_points
from app.models import DiscJob, JobState
from app.models.disc_job import ContentType, DiscTitle, TitleState
from app.services.finalization_coordinator import FinalizationCoordinator
from app.services.job_state_machine import JobStateMachine
from app.services.transcription_prewarm import TranscriptionPrewarmer
from tests.unit.conftest import _unit_session_factory

# Import via importlib: app/services/__init__.py re-exports the ``job_manager``
# INSTANCE, shadowing the submodule on plain ``import app.services.job_manager``.
jm = importlib.import_module("app.services.job_manager")

DURATION = 600
CHUNK_LEN = 30
MODEL_CONFIG = {"type": "whisper", "name": "small", "device": "cpu", "requested_workers": 1}
CONFIG_KEY = model_output_key(MODEL_CONFIG)
OFFSETS = canonical_scan_points(DURATION, skip_initial=90, chunk_len=CHUNK_LEN, num_points=10)


class FakeMatcher:
    """Just enough EpisodeMatcher surface for the prewarmer."""

    chunk_duration = CHUNK_LEN
    skip_initial_duration = 90

    def __init__(self):
        self.audio_chunks = {}
        self.transcribe_calls: list[tuple[int, int, str]] = []
        self.honest_key: str | None = None  # None -> same as the config-derived key

    def _model_config(self):
        return dict(MODEL_CONFIG)

    def _model_key_for(self, model):
        return self.honest_key or CONFIG_KEY

    @staticmethod
    def _resolve_source(mkv_file):
        return str(Path(mkv_file).resolve())

    def transcribe_chunk_cached(
        self, video_file, start, length, model, *, file_key=None, model_key=None, temp_files=None
    ):
        self.transcribe_calls.append((int(start), int(length), model_key))
        transcript_store.put(file_key, start, length, model_key, f"text-{start}")
        return f"text-{start}"


class CountingSemaphore:
    """Async-context-manager stub counting per-chunk acquire/release."""

    def __init__(self):
        self.acquires = 0
        self.releases = 0
        self.held = 0
        self.max_held = 0

    async def __aenter__(self):
        self.acquires += 1
        self.held += 1
        self.max_held = max(self.max_held, self.held)

    async def __aexit__(self, *exc):
        self.releases += 1
        self.held -= 1
        return False


class BlockingSemaphore(CountingSemaphore):
    """Lets the first acquire through, blocks the second forever (cancel target)."""

    def __init__(self):
        super().__init__()
        self.blocked = asyncio.Event()
        self._proceed = asyncio.Event()  # never set

    async def __aenter__(self):
        await super().__aenter__()
        if self.acquires > 1:
            self.blocked.set()
            await self._proceed.wait()


@pytest.fixture
def config_flags(monkeypatch):
    """Stub config_service.get_config with mutable prewarm flags."""
    flags = SimpleNamespace(
        enable_background_pretranscription=True,
        pretranscribe_full_file=False,
    )

    async def _get_config():
        return flags

    monkeypatch.setattr("app.services.config_service.get_config", _get_config)
    return flags


@pytest.fixture
def model_loader(monkeypatch):
    """Stub asr_models.get_cached_model, recording every load."""
    calls = []

    def _get_cached_model(cfg):
        calls.append(cfg)
        return SimpleNamespace(name="fake-model")

    monkeypatch.setattr("app.matcher.asr_models.get_cached_model", _get_cached_model)
    return calls


@pytest.fixture
def duration_stub(monkeypatch):
    monkeypatch.setattr("app.matcher.episode_identification.get_video_duration", lambda f: DURATION)


@pytest.fixture
def fake_matcher():
    return FakeMatcher()


@pytest.fixture
def prewarmer(fake_matcher):
    sem = CountingSemaphore()
    p = TranscriptionPrewarmer(semaphore_provider=lambda: sem)
    p._matcher = fake_matcher  # skip the lazy heavy build
    return p, sem


async def _seed_review_job(tmp_path, *, title_state=TitleState.REVIEW) -> tuple[int, Path]:
    """Job parked in review with one title whose output file exists on disk."""
    f = tmp_path / "show_t00.mkv"
    f.write_bytes(b"\x00" * 2048)
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="E:",
            volume_label="SHOW_S1D1",
            content_type=ContentType.TV,
            state=JobState.REVIEW_NEEDED,
            detected_title="Some Show",
            detected_season=1,
            staging_path=str(tmp_path),
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        session.add(
            DiscTitle(
                job_id=job.id,
                title_index=0,
                duration_seconds=DURATION,
                state=title_state,
                output_filename=str(f),
            )
        )
        await session.commit()
        return job.id, f


async def _run_to_completion(p: TranscriptionPrewarmer, job_id: int) -> None:
    """start_for_job + await the spawned task + let done-callbacks run."""
    await p.start_for_job(job_id)
    task = p._tasks.get(job_id)
    if task is not None:
        await task
    await asyncio.sleep(0)  # let add_done_callback pop the dict entry


class TestStartForJob:
    async def test_disabled_flag_is_a_no_op(self, config_flags, prewarmer, model_loader):
        config_flags.enable_background_pretranscription = False
        p, _sem = prewarmer

        await p.start_for_job(123)

        assert p._tasks == {}
        assert model_loader == []

    async def test_double_start_is_idempotent(self, config_flags):
        p = TranscriptionPrewarmer()
        starts = 0
        release = asyncio.Event()

        async def fake_job(job_id, *, full_file):
            nonlocal starts
            starts += 1
            await release.wait()

        p._prewarm_job = fake_job

        await p.start_for_job(7)
        first_task = p._tasks[7]
        await p.start_for_job(7)

        assert p._tasks[7] is first_task
        await asyncio.sleep(0)
        assert starts == 1

        release.set()
        await first_task
        await asyncio.sleep(0)
        assert p._tasks == {}


class TestCoverageAndTranscription:
    async def test_fully_cached_file_never_loads_model(
        self, tmp_path, config_flags, prewarmer, model_loader, duration_stub
    ):
        p, sem = prewarmer
        job_id, f = await _seed_review_job(tmp_path)
        file_key = transcript_store.file_key_for(f)
        for off in OFFSETS:
            transcript_store.put(file_key, off, CHUNK_LEN, CONFIG_KEY, "cached")

        await _run_to_completion(p, job_id)

        assert model_loader == []
        assert p._matcher.transcribe_calls == []
        assert sem.acquires == 0
        assert p._tasks == {}

    async def test_partial_coverage_transcribes_only_missing(
        self, tmp_path, config_flags, prewarmer, model_loader, duration_stub
    ):
        p, sem = prewarmer
        job_id, f = await _seed_review_job(tmp_path)
        file_key = transcript_store.file_key_for(f)
        for off in OFFSETS[:4]:
            transcript_store.put(file_key, off, CHUNK_LEN, CONFIG_KEY, "cached")

        await _run_to_completion(p, job_id)

        assert len(model_loader) == 1
        expected = [(off, CHUNK_LEN, CONFIG_KEY) for off in OFFSETS[4:]]
        assert p._matcher.transcribe_calls == expected
        # Semaphore taken and released once PER CHUNK, never held across chunks.
        assert sem.acquires == len(expected)
        assert sem.releases == len(expected)
        assert sem.max_held == 1
        # Write-through landed in the store under the honest key.
        for off in OFFSETS:
            assert transcript_store.get(file_key, off, CHUNK_LEN, CONFIG_KEY) is not None

    async def test_empty_string_transcript_counts_as_cached(
        self, tmp_path, config_flags, prewarmer, model_loader, duration_stub
    ):
        """A cached "" (silent audio) is a hit, not a miss."""
        p, _sem = prewarmer
        job_id, f = await _seed_review_job(tmp_path)
        file_key = transcript_store.file_key_for(f)
        for off in OFFSETS:
            transcript_store.put(file_key, off, CHUNK_LEN, CONFIG_KEY, "")

        await _run_to_completion(p, job_id)

        assert model_loader == []
        assert p._matcher.transcribe_calls == []

    async def test_full_file_flag_warms_full_span(
        self, tmp_path, config_flags, prewarmer, model_loader, duration_stub
    ):
        config_flags.pretranscribe_full_file = True
        p, _sem = prewarmer
        job_id, f = await _seed_review_job(tmp_path)
        file_key = transcript_store.file_key_for(f)
        for off in OFFSETS:
            transcript_store.put(file_key, off, CHUNK_LEN, CONFIG_KEY, "cached")

        await _run_to_completion(p, job_id)

        # Grid fully cached -> only the (0, duration) full-file span is warmed,
        # keyed exactly as transcribe_full's L2 entry.
        assert p._matcher.transcribe_calls == [(0, DURATION, CONFIG_KEY)]
        assert transcript_store.get(file_key, 0, DURATION, CONFIG_KEY) is not None

    async def test_full_file_off_does_not_warm_full_span(
        self, tmp_path, config_flags, prewarmer, model_loader, duration_stub
    ):
        p, _sem = prewarmer
        job_id, f = await _seed_review_job(tmp_path)

        await _run_to_completion(p, job_id)

        starts = [c[0] for c in p._matcher.transcribe_calls]
        assert starts == list(OFFSETS)  # grid only, no (0, DURATION) span
        file_key = transcript_store.file_key_for(f)
        assert transcript_store.get(file_key, 0, DURATION, CONFIG_KEY) is None

    async def test_honest_key_recheck_skips_transcription(
        self, tmp_path, config_flags, prewarmer, model_loader, duration_stub
    ):
        """Config key misses, but the post-load (honest) key is fully cached."""
        p, _sem = prewarmer
        p._matcher.honest_key = "whisper_small_cuda_float16"
        job_id, f = await _seed_review_job(tmp_path)
        file_key = transcript_store.file_key_for(f)
        for off in OFFSETS:
            transcript_store.put(file_key, off, CHUNK_LEN, p._matcher.honest_key, "cached")

        await _run_to_completion(p, job_id)

        # The model HAD to load (config-derived key looked uncovered), but the
        # honest-key recheck found full coverage -> zero transcriptions.
        assert len(model_loader) == 1
        assert p._matcher.transcribe_calls == []

    async def test_no_candidate_files_skips_matcher_build(
        self, tmp_path, config_flags, model_loader
    ):
        """Organized/failed titles and missing files never touch the matcher."""
        p = TranscriptionPrewarmer()
        built = MagicMock()
        p._build_matcher = built
        job_id, f = await _seed_review_job(tmp_path, title_state=TitleState.COMPLETED)

        await _run_to_completion(p, job_id)

        built.assert_not_called()
        assert model_loader == []


class TestCancellation:
    async def test_cancel_mid_fill_stops_promptly(
        self, tmp_path, config_flags, fake_matcher, model_loader, duration_stub
    ):
        sem = BlockingSemaphore()
        p = TranscriptionPrewarmer(semaphore_provider=lambda: sem)
        p._matcher = fake_matcher
        job_id, _f = await _seed_review_job(tmp_path)

        await p.start_for_job(job_id)
        task = p._tasks[job_id]
        # First chunk transcribes; the second acquire blocks -> cancel there.
        await asyncio.wait_for(sem.blocked.wait(), timeout=5)
        assert len(fake_matcher.transcribe_calls) == 1

        p.cancel_for_job(job_id)
        with pytest.raises(asyncio.CancelledError):
            await task

        assert len(fake_matcher.transcribe_calls) == 1  # nothing after cancel
        assert p._tasks == {}

    async def test_cancel_for_job_absent_is_safe(self):
        TranscriptionPrewarmer().cancel_for_job(424242)  # must not raise

    async def test_on_job_terminal_cancels_task(self):
        p = TranscriptionPrewarmer()
        fake_task = MagicMock()
        fake_task.done.return_value = False
        p._tasks[5] = fake_task

        await p.on_job_terminal(5, JobState.COMPLETED)

        fake_task.cancel.assert_called_once()
        assert 5 not in p._tasks

    async def test_cancel_all_sweeps_every_task(self):
        p = TranscriptionPrewarmer()
        tasks = {}
        for jid in (1, 2):
            t = MagicMock()
            t.done.return_value = False
            tasks[jid] = t
            p._tasks[jid] = t

        p.cancel_all()

        for t in tasks.values():
            t.cancel.assert_called_once()
        assert p._tasks == {}


class TestJobManagerWiring:
    def test_review_trigger_and_terminal_hook_registered(self):
        assert jm.job_manager._start_prewarm_on_review in jm.state_machine._on_transition_callbacks
        assert jm.job_manager._prewarmer.on_job_terminal in jm.state_machine._on_terminal_callbacks

    async def test_review_transition_kicks_off_prewarm(self, monkeypatch):
        mock_prewarmer = MagicMock()
        monkeypatch.setattr(jm.job_manager, "_prewarmer", mock_prewarmer)

        jm.job_manager._start_prewarm_on_review(7, JobState.REVIEW_NEEDED)
        mock_prewarmer.kickoff.assert_called_once_with(7)

        jm.job_manager._start_prewarm_on_review(7, JobState.MATCHING)
        mock_prewarmer.kickoff.assert_called_once()  # unchanged: review-only

    async def test_finalization_review_parking_triggers_prewarm(self, tmp_path, monkeypatch):
        """check_job_completion parking a TV job in review flows through the
        on_transition chokepoint into prewarmer.kickoff."""
        broadcaster = MagicMock()
        broadcaster.broadcast_job_completed = AsyncMock()
        broadcaster.broadcast_job_failed = AsyncMock()
        broadcaster.broadcast_job_state_changed = AsyncMock()
        sm = JobStateMachine(broadcaster)
        coord = FinalizationCoordinator(broadcaster, sm)
        coord.finalize_disc_job = AsyncMock()

        mock_prewarmer = MagicMock()
        monkeypatch.setattr(jm.job_manager, "_prewarmer", mock_prewarmer)
        sm.on_transition(jm.job_manager._start_prewarm_on_review)

        async with _unit_session_factory() as session:
            job = DiscJob(
                drive_id="E:",
                volume_label="SHOW_S1D1",
                content_type=ContentType.TV,
                state=JobState.MATCHING,
                detected_title="Some Show",
                detected_season=1,
                staging_path=str(tmp_path),
                subtitle_status="completed",
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            job_id = job.id
            session.add(
                DiscTitle(
                    job_id=job_id,
                    title_index=0,
                    duration_seconds=DURATION,
                    matched_episode="S01E01",
                    match_confidence=0.8,
                    state=TitleState.MATCHED,
                )
            )
            session.add(
                DiscTitle(
                    job_id=job_id,
                    title_index=1,
                    duration_seconds=DURATION,
                    state=TitleState.REVIEW,
                )
            )
            await session.commit()

        async with _unit_session_factory() as session:
            await coord.check_job_completion(session, job_id)

        mock_prewarmer.kickoff.assert_called_once_with(job_id)

    async def test_set_name_and_resume_cancels_prewarm(self, monkeypatch):
        mock_prewarmer = MagicMock()
        monkeypatch.setattr(jm.job_manager, "_prewarmer", mock_prewarmer)
        monkeypatch.setattr(jm.job_manager._identification, "set_name_and_resume", AsyncMock())
        run_ripping = AsyncMock()
        monkeypatch.setattr(jm.job_manager, "_run_ripping", run_ripping)

        await jm.job_manager.set_name_and_resume(11, "Show", "tv")

        mock_prewarmer.cancel_for_job.assert_called_once_with(11)
        await jm.job_manager._active_jobs.pop(11)

    async def test_re_identify_job_cancels_prewarm(self, monkeypatch):
        mock_prewarmer = MagicMock()
        monkeypatch.setattr(jm.job_manager, "_prewarmer", mock_prewarmer)
        monkeypatch.setattr(
            jm.job_manager._identification,
            "re_identify",
            AsyncMock(return_value={"has_ripped": True}),
        )
        monkeypatch.setattr(jm.job_manager, "_rerun_matching", AsyncMock())

        await jm.job_manager.re_identify_job(12, "Show", "tv")

        mock_prewarmer.cancel_for_job.assert_called_once_with(12)
        await jm.job_manager._active_jobs.pop(12)

    async def test_rerun_matching_cancels_prewarm(self, monkeypatch):
        mock_prewarmer = MagicMock()
        monkeypatch.setattr(jm.job_manager, "_prewarmer", mock_prewarmer)

        # Nonexistent job: _rerun_matching returns right after the cancel seam.
        await jm.job_manager._rerun_matching(999_999)

        mock_prewarmer.cancel_for_job.assert_called_once_with(999_999)

    async def test_rematch_single_title_cancels_prewarm(self, monkeypatch):
        mock_prewarmer = MagicMock()
        monkeypatch.setattr(jm.job_manager, "_prewarmer", mock_prewarmer)
        monkeypatch.setattr(jm.job_manager._matching, "rematch_single_title", AsyncMock())

        await jm.job_manager.rematch_single_title(13, 1)

        mock_prewarmer.cancel_for_job.assert_called_once_with(13)

    async def test_apply_review_cancels_prewarm(self, monkeypatch):
        mock_prewarmer = MagicMock()
        monkeypatch.setattr(jm.job_manager, "_prewarmer", mock_prewarmer)
        monkeypatch.setattr(jm.job_manager._finalization, "apply_review", AsyncMock())

        await jm.job_manager.apply_review(14, 100, episode_code="S01E03")

        mock_prewarmer.cancel_for_job.assert_called_once_with(14)

    async def test_apply_review_batch_cancels_prewarm(self, monkeypatch):
        mock_prewarmer = MagicMock()
        monkeypatch.setattr(jm.job_manager, "_prewarmer", mock_prewarmer)
        monkeypatch.setattr(jm.job_manager._finalization, "apply_review_batch", AsyncMock())

        await jm.job_manager.apply_review_batch(15, [{"title_id": 1, "episode_code": "S01E01"}])

        mock_prewarmer.cancel_for_job.assert_called_once_with(15)


class TestPrewarmerTempNamespace:
    def test_prewarm_matcher_uses_isolated_temp_dir(self, monkeypatch):
        """_build_matcher must give the prewarm matcher its own temp dir so that a
        cancelled thread can't poison the shared whisper_chunks/ namespace."""
        import tempfile

        from app.services.transcription_prewarm import TranscriptionPrewarmer

        # Stub out the heavy EpisodeMatcher constructor.
        fake_matcher = FakeMatcher()
        fake_matcher.temp_dir = None  # will be overwritten by _build_matcher

        monkeypatch.setattr(
            "app.matcher.episode_identification.EpisodeMatcher",
            lambda **kwargs: fake_matcher,
        )
        monkeypatch.setattr(
            "app.services.config_service.get_config_sync",
            lambda: None,
        )

        matcher = TranscriptionPrewarmer._build_matcher()

        default_dir = str(Path(tempfile.gettempdir()) / "whisper_chunks")
        prewarm_dir = str(Path(tempfile.gettempdir()) / "whisper_chunks_prewarm")
        assert str(matcher.temp_dir) == prewarm_dir, (
            f"Expected prewarm namespace {prewarm_dir!r}, got {matcher.temp_dir!r}"
        )
        assert str(matcher.temp_dir) != default_dir, (
            "Prewarm temp_dir must differ from the shared whisper_chunks/ namespace"
        )


class TestWalkAwayRematch:
    """End-to-end Phase A guarantee, across a simulated restart.

    A review-parked job's files are prewarmed by a REAL ``_prewarm_job`` run
    (real EpisodeMatcher, real transcript_store redirected to tmp_path by the
    conftest fixture; only Whisper and ffmpeg are faked). The store must then
    hold exactly the 10 lattice rows per file — and a FRESH EpisodeMatcher
    (= new process) must re-match every offset from the persistent cache with
    ZERO ASR calls and ZERO audio extractions, even with a model that raises.
    """

    class _CountingWhisper:
        device = "cpu"  # honest post-load device -> model_key matches CONFIG_KEY

        def __init__(self):
            self.calls = 0

        def transcribe(self, audio_path):
            self.calls += 1
            return {"text": f"prewarmed speech from {Path(audio_path).stem} again and again"}

    class _PoisonModel:
        device = "cpu"

        def transcribe(self, audio_path):
            raise AssertionError("restart re-match must be served from the persistent cache")

    @staticmethod
    def _fake_extract(tmp_path):
        """Per-(file, offset) wav path so transcripts are distinct per chunk."""

        def _extract(mkv_file, start_time, duration=None):
            return str(tmp_path / f"chunk_{Path(mkv_file).stem}_{start_time}.wav")

        return _extract

    def _expected_text(self, f: Path, off: int) -> str:
        return f"prewarmed speech from chunk_{f.stem}_{off} again and again"

    async def _seed_two_title_review_job(self, tmp_path) -> tuple[int, list[Path]]:
        """Review-parked job with TWO titles whose output files exist on disk."""
        files = []
        for i in range(2):
            f = tmp_path / f"show_t0{i}.mkv"
            f.write_bytes(b"\x00" * 2048)
            files.append(f)
        async with _unit_session_factory() as session:
            job = DiscJob(
                drive_id="E:",
                volume_label="SHOW_S1D1",
                content_type=ContentType.TV,
                state=JobState.REVIEW_NEEDED,
                detected_title="Some Show",
                detected_season=1,
                staging_path=str(tmp_path),
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            for i, f in enumerate(files):
                session.add(
                    DiscTitle(
                        job_id=job.id,
                        title_index=i,
                        duration_seconds=DURATION,
                        state=TitleState.REVIEW,
                        output_filename=str(f),
                    )
                )
            await session.commit()
            return job.id, files

    async def test_prewarm_then_fresh_matcher_rematches_with_zero_asr(
        self, tmp_path, duration_stub
    ):
        job_id, files = await self._seed_two_title_review_job(tmp_path)

        # --- Leg 1: real prewarm run against the review-parked job. ---------
        # Real EpisodeMatcher (default model_name="small", requested_workers=1,
        # explicit device="cpu") so _model_config()/_model_key_for produce the
        # SAME keys live matching uses — i.e. CONFIG_KEY.
        prewarm_matcher = EpisodeMatcher(cache_dir=tmp_path, show_name="__prewarm__", device="cpu")
        whisper = self._CountingWhisper()
        p = TranscriptionPrewarmer()  # no semaphore provider -> nullcontext per chunk
        p._matcher = prewarm_matcher

        with (
            patch.object(prewarm_matcher, "extract_audio_chunk", self._fake_extract(tmp_path)),
            patch("app.matcher.asr_models.get_cached_model", return_value=whisper),
        ):
            await p._prewarm_job(job_id, full_file=False)

        assert whisper.calls == len(files) * len(OFFSETS)

        # The store holds EXACTLY the 10 lattice rows per file, keyed under the
        # production file_key/model_key — nothing extra (no full-file span, no
        # rows under a drifted model identity).
        expected_rows = set()
        for f in files:
            file_key = transcript_store.file_key_for(f)
            assert file_key is not None
            for off in OFFSETS:
                assert transcript_store.get(file_key, off, CHUNK_LEN, CONFIG_KEY) is not None
                expected_rows.add((file_key, int(off), CHUNK_LEN, CONFIG_KEY))
        conn = sqlite3.connect(transcript_store.CACHE_DB_PATH)
        try:
            rows = set(
                conn.execute(
                    "SELECT file_key, start_s, duration_s, model_key FROM transcripts"
                ).fetchall()
            )
        finally:
            conn.close()
        assert rows == expected_rows

        # --- Leg 2: simulated restart — fresh matcher, poison model. ---------
        # New EpisodeMatcher instance = empty L1; the poison model raises on any
        # transcribe and extraction is forbidden, so every transcript MUST come
        # from the persistent L2 store.
        fresh_matcher = EpisodeMatcher(cache_dir=tmp_path, show_name="Some Show", device="cpu")
        no_extract = MagicMock(side_effect=AssertionError("L2 hit must not extract a wav"))
        with patch.object(fresh_matcher, "extract_audio_chunk", no_extract):
            for f in files:
                for off in OFFSETS:
                    text = fresh_matcher.transcribe_chunk_cached(
                        f, off, CHUNK_LEN, self._PoisonModel()
                    )
                    assert text == self._expected_text(f, int(off))
        no_extract.assert_not_called()


class TestFailSoftGranularity:
    async def test_single_chunk_failure_continues_remaining(
        self, tmp_path, config_flags, model_loader, duration_stub
    ):
        """A failure in chunk N must not prevent chunks N+1 .. end from warming."""
        fail_at_start = int(OFFSETS[2])  # chunk 3 (0-indexed: 2) raises

        class PartialFailMatcher(FakeMatcher):
            def transcribe_chunk_cached(
                self,
                video_file,
                start,
                length,
                model,
                *,
                file_key=None,
                model_key=None,
                temp_files=None,
            ):
                if int(start) == fail_at_start:
                    raise RuntimeError("simulated transcription failure")
                return super().transcribe_chunk_cached(
                    video_file,
                    start,
                    length,
                    model,
                    file_key=file_key,
                    model_key=model_key,
                    temp_files=temp_files,
                )

        fm = PartialFailMatcher()
        p = TranscriptionPrewarmer(semaphore_provider=lambda: CountingSemaphore())
        p._matcher = fm

        job_id, f = await _seed_review_job(tmp_path)
        await _run_to_completion(p, job_id)

        succeeded = [s for s, _l, _k in fm.transcribe_calls]
        # Every offset except the failing one should have been attempted.
        expected_ok = [int(off) for off in OFFSETS if int(off) != fail_at_start]
        assert succeeded == expected_ok, f"Expected {expected_ok}, got {succeeded}"
        # The file_key entry for the failing chunk must be absent; all others present.
        file_key = transcript_store.file_key_for(f)
        for off in OFFSETS:
            if int(off) == fail_at_start:
                assert transcript_store.get(file_key, off, CHUNK_LEN, CONFIG_KEY) is None
            else:
                assert transcript_store.get(file_key, off, CHUNK_LEN, CONFIG_KEY) is not None
