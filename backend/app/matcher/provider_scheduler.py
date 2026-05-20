"""Threaded multi-provider scheduler for subtitle downloads.

The single-threaded loop in ``testing_service.download_subtitles`` used to
try Addic7ed for every episode, then the OpenSubtitles.org scraper for
every miss — both blocking on per-provider rate-limit sleeps (3s and 6s
respectively). While provider A was sleeping, provider B sat idle.

This module replaces that loop with one worker thread per provider, each
holding its own client and rate-limit state. Episode jobs flow through a
provider-priority list: when provider A fails, the job is re-queued at
provider B; meanwhile provider A's worker is already serving a different
episode. The total wall-time for a season approaches
``max(per_provider_time)`` rather than ``sum(per_provider_time)``.

Public surface:
- ``EpisodeJob`` — dataclass describing one episode's lifecycle.
- ``ProviderWorker`` — thread that pulls jobs for one provider.
- ``Scheduler`` — wires workers together and waits for completion.
- ``run_jobs(jobs, workers)`` — convenience entry point.

Worker contract (any provider):
- ``client.get_best_subtitle(show, season, episode) -> entry | None``
- ``client.download_subtitle(entry, save_path) -> Path | None``

This matches ``Addic7edClient`` and ``TVSubtitlesClient``.
"""

from __future__ import annotations

import queue
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from loguru import logger

from app.matcher.subtitle_utils import is_valid_srt_file


class SubtitleProviderClient(Protocol):
    """Structural contract every provider client must satisfy."""

    def get_best_subtitle(self, show_name: str, season: int, episode: int) -> Any: ...

    def download_subtitle(self, subtitle: Any, save_path: Path) -> Path | None: ...


@dataclass
class EpisodeJob:
    """One episode's pending state. ``pending_providers`` is consumed
    from the left as providers fail; when empty, the job finalises as
    ``not_found``."""

    tmdb_id: int
    show_name: str
    season: int
    episode: int
    episode_code: str
    srt_target: Path
    pending_providers: deque[str] = field(default_factory=deque)
    result: dict[str, Any] | None = None


class ProviderWorker(threading.Thread):
    """Thread bound to one provider client. Pulls from its private
    ``queue.Queue``; the scheduler dispatches jobs whose
    ``pending_providers[0]`` matches this worker's ``name``."""

    def __init__(self, name: str, client: SubtitleProviderClient, scheduler: Scheduler):
        super().__init__(daemon=True, name=f"provider-{name}")
        self.provider_name = name
        self.client = client
        self.scheduler = scheduler
        self.queue: queue.Queue[EpisodeJob | None] = queue.Queue()

    def run(self) -> None:
        while True:
            job = self.queue.get()
            try:
                if job is None:  # shutdown sentinel
                    return
                self._process_job(job)
            finally:
                self.queue.task_done()

    def _process_job(self, job: EpisodeJob) -> None:
        try:
            entry = self.client.get_best_subtitle(job.show_name, job.season, job.episode)
            if entry is None:
                logger.debug(
                    f"{self.provider_name} miss for {job.show_name} S{job.season:02d}"
                    f"E{job.episode:02d}"
                )
                self.scheduler.advance_or_fail(job)
                return

            result = self.client.download_subtitle(entry, job.srt_target)
            if result is None:
                self.scheduler.advance_or_fail(job)
                return

            if not is_valid_srt_file(Path(result)):
                logger.warning(
                    f"{self.provider_name} returned an invalid SRT for "
                    f"{job.episode_code}; deleting and trying next provider"
                )
                Path(result).unlink(missing_ok=True)
                self.scheduler.advance_or_fail(job)
                return

            job.result = {
                "code": job.episode_code,
                "status": "downloaded",
                "path": str(result),
                "source": self.provider_name,
            }
            self.scheduler.mark_complete(job)
        except Exception as e:
            logger.warning(
                f"{self.provider_name} raised on {job.episode_code}: {e}",
                exc_info=True,
            )
            self.scheduler.advance_or_fail(job)

    def stop(self) -> None:
        self.queue.put(None)


class Scheduler:
    """Coordinates workers. Submit a batch of jobs, wait for completion,
    collect results keyed by ``episode_code``.

    Lifecycle: ``run(jobs)`` is a one-shot — it starts workers (idempotent
    if already started), submits jobs, blocks on completion, and returns
    results. Call ``shutdown()`` when done to drain the worker threads.
    """

    def __init__(self):
        self.workers: dict[str, ProviderWorker] = {}
        self._pending_count = 0
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._results: dict[str, dict[str, Any]] = {}

    def register(self, name: str, client: SubtitleProviderClient) -> None:
        if name in self.workers:
            return
        self.workers[name] = ProviderWorker(name, client, self)

    def run(
        self, jobs: list[EpisodeJob], timeout: float | None = None
    ) -> dict[str, dict[str, Any]]:
        """Execute every job to completion (success or not_found). Returns
        a dict keyed by episode_code — ordering is the caller's responsibility."""
        # Fresh state for this batch.
        with self._lock:
            self._results = {}
            self._pending_count = len(jobs)
            if self._pending_count == 0:
                return {}
            self._done.clear()

        # Start any worker we haven't started yet (idempotent).
        for worker in self.workers.values():
            if not worker.is_alive():
                worker.start()

        for job in jobs:
            self._enqueue_next(job)

        if not self._done.wait(timeout=timeout):
            logger.error(
                f"Scheduler timed out after {timeout}s with {self._pending_count} jobs unfinished"
            )

        with self._lock:
            return dict(self._results)

    def advance_or_fail(self, job: EpisodeJob) -> None:
        """Worker callback: current provider failed; pop it and try the
        next one, or finalise as not_found if exhausted."""
        if job.pending_providers:
            job.pending_providers.popleft()
        self._enqueue_next(job)

    def _enqueue_next(self, job: EpisodeJob) -> None:
        # Skip any pending providers we don't have a registered worker for
        # — a config drift that would otherwise hang the job forever.
        while job.pending_providers and job.pending_providers[0] not in self.workers:
            skipped = job.pending_providers.popleft()
            logger.debug(f"No worker registered for {skipped!r}; skipping")

        if not job.pending_providers:
            self._mark_not_found(job)
            return

        provider = job.pending_providers[0]
        self.workers[provider].queue.put(job)

    def _mark_not_found(self, job: EpisodeJob) -> None:
        job.result = {
            "code": job.episode_code,
            "status": "not_found",
            "path": None,
            "source": None,
        }
        self.mark_complete(job)

    def mark_complete(self, job: EpisodeJob) -> None:
        with self._lock:
            assert job.result is not None
            self._results[job.episode_code] = job.result
            self._pending_count -= 1
            if self._pending_count <= 0:
                self._done.set()

    def shutdown(self) -> None:
        for worker in self.workers.values():
            if worker.is_alive():
                worker.stop()
        for worker in self.workers.values():
            if worker.is_alive():
                worker.join(timeout=5)


def run_jobs(
    jobs: list[EpisodeJob],
    workers: dict[str, SubtitleProviderClient],
    timeout: float | None = None,
) -> dict[str, dict[str, Any]]:
    """One-shot helper: create a scheduler, register workers, run, shut
    down. Suitable when the caller (e.g. ``download_subtitles``) handles
    one season at a time and doesn't need to reuse worker state."""
    scheduler = Scheduler()
    for name, client in workers.items():
        scheduler.register(name, client)
    try:
        return scheduler.run(jobs, timeout=timeout)
    finally:
        scheduler.shutdown()
