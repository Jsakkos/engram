"""Background transcript prewarmer for review-parked jobs.

When a job parks in REVIEW_NEEDED its unresolved tracks are very likely to be
re-matched soon (the user reassigns an episode, re-identifies the show, or hits
"Re-match all"). Re-matching re-transcribes the same canonical scan-grid chunks
Whisper already saw — unless they are sitting in the persistent transcript
store (``app/matcher/transcript_store.py``, the L2 cache under
``transcribe_chunk_cached``). This service fills that store in the background
while the job idles in review, so the eventual re-match is near-instant.

Design notes
------------
* **One dedicated EpisodeMatcher**, built lazily in a thread on first use.
  It is constructed with the SAME model-affecting args production matching
  uses (see ``EpisodeCurator._ensure_initialized``): default model name,
  startup-pinned device, ``requested_workers`` from config — so its
  ``model_key`` (and even its ``get_cached_model`` cache slot) match what live
  matching produces and looks up. ``show_name`` does not affect transcription;
  a sentinel is used.
* **Coverage check without a model load**: the model-free
  ``model_output_key(matcher._model_config())`` plus ``transcript_store.get``
  per offset decide whether anything is missing. A fully-cached file never
  touches the Whisper loader.
* **Honest-key recheck**: after the model loads, ``_model_key_for(model)``
  may differ from the config-derived key (CUDA→CPU fallback). When it does,
  coverage is re-checked under the honest key before transcribing.
* **Semaphore per chunk**: each chunk acquires the matching semaphore and
  releases it before the next one, so a live match preempts the prewarmer
  between chunks instead of queueing behind a whole file.
* **Fail-soft everywhere**: a chunk or file failure logs and continues; the
  background task can never propagate into job state.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from pathlib import Path

from sqlmodel import select

from app.core.log_context import with_job_log_context
from app.database import async_session
from app.models import DiscJob
from app.models.disc_job import DiscTitle, TitleState

logger = logging.getLogger(__name__)

# The default (shallowest) scan-lattice depth — the offsets every normal match
# visits first. Deeper re-scans are strict supersets (nested lattice), so
# warming level 0 always pays off regardless of later scan depth.
DEFAULT_SCAN_POINTS = 10

# Title states whose files can never benefit from prewarming: COMPLETED is
# already organized (the file has moved out of staging), FAILED is discarded,
# RIPPING is still being written. Everything else with a real file on disk
# (PENDING/QUEUED/MATCHING/MATCHED/REVIEW) is a re-match candidate.
_SKIP_STATES = {TitleState.COMPLETED, TitleState.FAILED, TitleState.RIPPING}

# A span is (start_s, duration_s) — the transcript-store key minus file/model.
_Span = tuple[int, int]


class TranscriptionPrewarmer:
    """Pre-fills the persistent ASR transcript cache for review-parked jobs."""

    def __init__(
        self,
        semaphore_provider: Callable[[], asyncio.Semaphore | None] | None = None,
    ) -> None:
        """``semaphore_provider`` returns the live match semaphore (or None).

        A callable rather than the semaphore itself because the semaphore is
        created at ``job_manager.start()`` (after ASR capacity is resolved),
        long after this service is constructed.
        """
        self._semaphore_provider = semaphore_provider
        self._tasks: dict[int, asyncio.Task] = {}
        # Strong refs to the short-lived kickoff tasks (asyncio keeps only weak
        # refs to tasks; an unreferenced task can be garbage-collected mid-run).
        self._kickoffs: set[asyncio.Task] = set()
        self._matcher = None
        # asyncio.Lock binds to the loop lazily (py3.11), so constructing it
        # here (no running loop) is safe — same pattern as matching_coordinator.
        self._matcher_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def kickoff(self, job_id: int) -> None:
        """Fire-and-forget ``start_for_job`` — sync seam for state-machine callbacks."""
        task = asyncio.create_task(self.start_for_job(job_id))
        self._kickoffs.add(task)
        task.add_done_callback(self._kickoffs.discard)

    async def start_for_job(self, job_id: int) -> None:
        """Start a background prewarm task for ``job_id`` (idempotent, fail-soft)."""
        try:
            existing = self._tasks.get(job_id)
            if existing is not None and not existing.done():
                return

            from app.services.config_service import get_config

            config = await get_config()
            if not config.enable_background_pretranscription:
                logger.debug(f"Job {job_id}: background pre-transcription disabled; skipping")
                return
            full_file = bool(config.pretranscribe_full_file)

            # Re-check after the await: a concurrent start could have spawned.
            existing = self._tasks.get(job_id)
            if existing is not None and not existing.done():
                return

            task = asyncio.create_task(
                with_job_log_context(job_id, self._prewarm_job(job_id, full_file=full_file))
            )
            self._tasks[job_id] = task
            task.add_done_callback(lambda t, jid=job_id: self._on_task_done(t, jid))
            logger.info(f"Job {job_id}: transcript prewarm task started (full_file={full_file})")
        except Exception as e:  # never let prewarm startup ripple into the caller
            logger.warning(f"Job {job_id}: failed to start transcript prewarm: {e}", exc_info=True)

    def cancel_for_job(self, job_id: int) -> None:
        """Cancel and forget the prewarm task for ``job_id``. Safe when absent.

        Cancellation prevents *future* chunks from being dispatched.  The
        in-flight thread (if any) finishes its current chunk — up to ~60 s —
        before the asyncio.CancelledError propagates; callers must not assume
        the GPU or the file handle is freed instantly.
        """
        task = self._tasks.pop(job_id, None)
        if task is not None and not task.done():
            task.cancel()
            logger.info(
                f"Job {job_id}: transcript prewarm cancelled (in-flight thread finishes current chunk)"
            )

    async def on_job_terminal(self, job_id: int, _state) -> None:
        """JobStateMachine ``on_terminal_state`` hook: stop warming a finished job."""
        self.cancel_for_job(job_id)

    def cancel_all(self) -> None:
        """Cancel every prewarm task (shutdown seam)."""
        for task in self._kickoffs:
            task.cancel()
        self._kickoffs.clear()
        for job_id in list(self._tasks):
            self.cancel_for_job(job_id)

    # ------------------------------------------------------------------
    # Per-job task
    # ------------------------------------------------------------------

    def _on_task_done(self, task: asyncio.Task, job_id: int) -> None:
        if self._tasks.get(job_id) is task:
            del self._tasks[job_id]
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning(f"Job {job_id}: transcript prewarm task died: {exc}", exc_info=exc)

    async def _prewarm_job(self, job_id: int, *, full_file: bool) -> None:
        """Warm the transcript store for every candidate file of one job."""
        try:
            files = await self._load_candidate_files(job_id)
            if not files:
                logger.debug(f"Job {job_id}: no prewarm candidates (no files on disk)")
                return

            matcher = await self._get_matcher()
            if matcher is None:
                return

            for file_path in files:
                try:
                    await self._prewarm_file(matcher, file_path, full_file=full_file)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(
                        f"Job {job_id}: prewarm failed for {file_path}: {e}", exc_info=True
                    )
            logger.info(f"Job {job_id}: transcript prewarm finished ({len(files)} file(s))")
        except asyncio.CancelledError:
            logger.debug(f"Job {job_id}: transcript prewarm cancelled mid-run")
            raise
        except Exception as e:
            logger.warning(f"Job {job_id}: transcript prewarm aborted: {e}", exc_info=True)

    async def _load_candidate_files(self, job_id: int) -> list[Path]:
        """Files worth warming: a real ``output_filename`` on disk, not yet organized.

        Mirrors the file resolution in ``JobManager._rerun_matching`` (absolute
        path first, then basename under the job's staging dir).
        """
        async with async_session() as session:
            job = await session.get(DiscJob, job_id)
            if job is None:
                return []
            staging = Path(job.staging_path) if job.staging_path else None
            result = await session.execute(select(DiscTitle).where(DiscTitle.job_id == job_id))
            titles = result.scalars().all()

        files: list[Path] = []
        seen: set[str] = set()
        for title in titles:
            if not title.output_filename or title.state in _SKIP_STATES:
                continue
            path = Path(title.output_filename)
            if not path.exists() and staging is not None:
                path = staging / path.name
            if not path.exists():
                continue
            key = str(path)
            if key not in seen:
                seen.add(key)
                files.append(path)
        return files

    async def _prewarm_file(self, matcher, file_path: Path, *, full_file: bool) -> None:
        """Warm all missing scan-grid spans (and optionally the full file) for one file."""
        from app.matcher import transcript_store
        from app.matcher.asr_models import get_cached_model, model_output_key
        from app.matcher.episode_identification import (
            canonical_scan_points,
            get_video_duration,  # int (np.ceil) — same helper transcribe_full keys its
            # L2 row with. Do NOT swap in srt_utils.get_video_duration (returns float):
            # a fractional duration would fork the (0, duration) full-file cache key.
        )

        duration = await asyncio.to_thread(get_video_duration, str(file_path))
        chunk_len = matcher.chunk_duration
        offsets = canonical_scan_points(
            duration,
            skip_initial=matcher.skip_initial_duration,
            chunk_len=chunk_len,
            num_points=DEFAULT_SCAN_POINTS,
        )

        file_key = transcript_store.file_key_for(file_path)
        if file_key is None:
            logger.debug(f"Prewarm: cannot derive file_key for {file_path}; skipping")
            return

        # Grid first, then the full-file span — same order they are warmed in,
        # so the cheap chunks land before the expensive full transcription.
        # (start=0, duration) is exactly how transcribe_full keys its L2 entry.
        wanted: list[_Span] = [(int(off), int(chunk_len)) for off in offsets]
        if full_file:
            wanted.append((0, int(duration)))

        # Coverage check WITHOUT loading the model: the config-derived key.
        config_key = model_output_key(matcher._model_config())
        missing = await asyncio.to_thread(self._missing_spans, file_key, wanted, config_key)
        if not missing:
            logger.debug(f"Prewarm: {file_path.name} fully cached ({len(wanted)} spans); skipping")
            return

        # Something is missing — now load the model (thread; shares the live
        # matcher's cache slot) and re-derive the honest post-load key.
        model = await asyncio.to_thread(get_cached_model, matcher._model_config())
        model_key = matcher._model_key_for(model)
        if model_key != config_key:
            # Post-load device differs from the config-derived assumption
            # (e.g. CUDA→CPU fallback). Re-check before transcribing anything.
            missing = await asyncio.to_thread(self._missing_spans, file_key, wanted, model_key)
            if not missing:
                logger.debug(f"Prewarm: {file_path.name} cached under honest key; skipping")
                return

        logger.info(
            f"Prewarm: transcribing {len(missing)}/{len(wanted)} span(s) for {file_path.name}"
        )
        for start, length in missing:
            # Guard: the file may have been organized (moved) between the
            # coverage check above and this chunk — break cleanly rather than
            # emitting a cascade of WARNINGs and ERRORs from ffprobe.
            if not file_path.exists():
                logger.info(f"Prewarm: {file_path.name} no longer on disk; stopping")
                break
            semaphore = self._semaphore_provider() if self._semaphore_provider else None
            # Acquire/release PER CHUNK so live matches preempt between chunks.
            # nullcontext() is a no-op async CM on py3.11 when no semaphore is set.
            ctx: contextlib.AbstractAsyncContextManager = (
                semaphore if semaphore is not None else contextlib.nullcontext()
            )
            try:
                async with ctx:
                    await asyncio.to_thread(
                        self._transcribe_span,
                        matcher,
                        file_path,
                        start,
                        length,
                        model,
                        file_key,
                        model_key,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Prewarm: chunk {start}s of {file_path.name} failed: {e}")

    # ------------------------------------------------------------------
    # Sync helpers (run inside asyncio.to_thread)
    # ------------------------------------------------------------------

    @staticmethod
    def _missing_spans(file_key: str, spans: list[_Span], model_key: str) -> list[_Span]:
        """Spans with no transcript-store entry. ``""`` is a hit; only None misses."""
        from app.matcher import transcript_store

        return [
            (start, length)
            for start, length in spans
            if transcript_store.get(file_key, start, length, model_key) is None
        ]

    @staticmethod
    def _transcribe_span(matcher, file_path, start, length, model, file_key, model_key) -> None:
        """Transcribe one span through the matcher's write-through cache, then clean up.

        ``transcribe_chunk_cached`` appends the extracted wav to ``temp_files``
        before transcribing, so cleanup happens even if Whisper raises. The
        matcher's per-file audio-chunk memo is also dropped — the prewarmer has
        no later use for the wav path it caches.
        """
        temp_files: list = []
        try:
            matcher.transcribe_chunk_cached(
                file_path,
                start,
                length,
                model,
                file_key=file_key,
                model_key=model_key,
                temp_files=temp_files,
            )
        finally:
            for p in temp_files:
                try:
                    Path(p).unlink(missing_ok=True)
                except OSError:
                    pass
            matcher.audio_chunks.pop((matcher._resolve_source(file_path), start, length), None)

    # ------------------------------------------------------------------
    # Lazy matcher construction
    # ------------------------------------------------------------------

    async def _get_matcher(self):
        """Return the dedicated EpisodeMatcher, building it once in a thread."""
        if self._matcher is not None:
            return self._matcher
        async with self._matcher_lock:
            if self._matcher is None:
                try:
                    self._matcher = await asyncio.to_thread(self._build_matcher)
                except Exception as e:
                    logger.warning(f"Prewarm: matcher unavailable: {e}", exc_info=True)
                    return None
        return self._matcher

    @staticmethod
    def _build_matcher():
        """Construct the prewarmer's EpisodeMatcher (heavy import — thread only).

        Model-affecting args mirror ``EpisodeCurator._ensure_initialized``:
        default ``model_name`` ("small"), default device (the startup-pinned
        ``detect_asr_device()``), and ``requested_workers`` from config — so
        ``_model_config()`` / ``_model_key_for`` agree with live matching and
        ``get_cached_model`` resolves to the same shared WhisperModel.
        ``show_name`` is irrelevant to transcription; a sentinel is passed.

        The matcher's ``temp_dir`` is redirected to a prewarm-only subdirectory
        so that cancelled tasks can't leave half-written wavs in the shared
        ``whisper_chunks/`` namespace that live matchers read via the bare
        ``exists()`` check in ``extract_audio_chunk``.
        """
        import tempfile

        from app.matcher.episode_identification import EpisodeMatcher
        from app.services.config_service import get_config_sync

        config = get_config_sync()
        if config and config.subtitles_cache_path:
            cache_dir = Path(config.subtitles_cache_path).expanduser()
        else:
            cache_dir = Path.home() / ".engram" / "cache"

        matcher = EpisodeMatcher(
            cache_dir=cache_dir,
            show_name="__prewarm__",
            requested_workers=(config.max_concurrent_matches if config else 1),
        )
        # Override the default whisper_chunks/ namespace so a cancelled prewarm
        # thread can't poison the shared chunk cache with a half-written wav.
        # Mirror EpisodeMatcher.__init__: assign then mkdir.
        matcher.temp_dir = Path(tempfile.gettempdir()) / "whisper_chunks_prewarm"
        matcher.temp_dir.mkdir(exist_ok=True)
        return matcher
