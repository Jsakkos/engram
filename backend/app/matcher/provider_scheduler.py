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
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from loguru import logger

from app.matcher.subtitle_utils import is_valid_srt_file


class SubtitleProviderClient(Protocol):
    """Structural contract every provider client must satisfy.

    Protocol method bodies use docstrings rather than the ``...`` ellipsis
    convention because CodeQL's ``py/ineffectual-statement`` check
    flags bare ``...`` as a no-effect statement. Docstrings are
    functionally identical for protocol declaration and document the
    expected return contract at the same time.
    """

    def get_best_subtitle(self, show_name: str, season: int, episode: int) -> Any:
        """Search the provider for the best subtitle for one episode.

        Return a provider-specific entry object on hit, or ``None`` on
        miss. May raise on transport errors — the scheduler treats
        exceptions as a miss and advances to the next provider."""

    def download_subtitle(self, subtitle: Any, save_path: Path) -> Path | None:
        """Persist ``subtitle`` to ``save_path``.

        Return ``save_path`` on success, ``None`` on download failure."""


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


class _CircuitBreaker:
    """Per-provider circuit breaker.

    A provider that's *down* (connect timeouts, 5xx) otherwise costs one
    failed attempt — and its full timeout — for EVERY episode in the batch,
    because residual jobs are all queued at the same head provider upfront.
    This breaker trips after ``failure_threshold`` consecutive transport
    failures, after which the worker fast-skips its remaining jobs to the
    next provider without calling the dead one. After ``cooldown`` seconds it
    half-opens and lets a single probe job through: success closes it, failure
    re-opens it with a doubled cooldown (capped at ``max_cooldown``).

    Only transport failures (exceptions) count. A clean miss or an unusable
    download means the provider *responded* — it's reachable — so those reset
    the counter. ``now`` is injectable purely so the state machine is testable
    without wall-clock sleeps; production passes ``time.monotonic()``.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        base_cooldown: float = 30.0,
        max_cooldown: float = 300.0,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.base_cooldown = base_cooldown
        self.max_cooldown = max_cooldown
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._open = False
        self._opened_at = 0.0
        self._cooldown = base_cooldown
        self._probe_in_flight = False

    def allow(self, now: float | None = None) -> bool:
        """True if a job may be sent to this provider right now."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if not self._open:
                return True
            if now - self._opened_at < self._cooldown:
                return False
            # Cooldown elapsed → half-open. Let exactly ONE probe through; its
            # outcome (record_success / record_failure) decides what happens
            # next. Concurrent callers see probe_in_flight and stay blocked.
            if self._probe_in_flight:
                return False
            self._probe_in_flight = True
            logger.info(f"{self.name} circuit half-open: probing recovery")
            return True

    def record_success(self) -> None:
        """The provider responded (hit, miss, or unusable download). Reset."""
        with self._lock:
            if self._open:
                logger.info(f"{self.name} circuit closed (recovered)")
            self._consecutive_failures = 0
            self._open = False
            self._probe_in_flight = False
            self._cooldown = self.base_cooldown

    def record_failure(self, now: float | None = None) -> None:
        """A transport-level failure (exception) talking to the provider."""
        now = time.monotonic() if now is None else now
        with self._lock:
            was_probe = self._probe_in_flight
            self._probe_in_flight = False
            if self._open:
                # A probe failed → stay open and back off further. Stragglers
                # (jobs dispatched just before the trip) failing while already
                # open don't move the window.
                if was_probe:
                    self._cooldown = min(self._cooldown * 2, self.max_cooldown)
                    self._opened_at = now
                    logger.warning(
                        f"{self.name} circuit re-opened after failed probe; "
                        f"cooldown now {self._cooldown:.0f}s"
                    )
                return
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.failure_threshold:
                self._open = True
                self._opened_at = now
                self._cooldown = self.base_cooldown
                logger.warning(
                    f"{self.name} circuit opened after {self._consecutive_failures} "
                    f"consecutive failures; skipping it for {self._cooldown:.0f}s"
                )


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
        # Skip without touching the network if this provider's breaker is open.
        # This is what makes a dead provider cheap: the residual jobs were all
        # queued at the head provider upfront, so once it trips the worker
        # fast-advances its backlog instead of paying a timeout per job.
        if not self.scheduler.provider_allows(self.provider_name):
            logger.debug(f"{self.provider_name} circuit open; skipping {job.episode_code}")
            self.scheduler.advance_or_fail(job)
            return

        try:
            outcome = self._attempt(job)
        except Exception as e:
            # Transport-level failure — count it toward the breaker so a
            # consistently-down provider trips and stops costing every episode.
            logger.warning(
                f"{self.provider_name} raised on {job.episode_code}: {e}",
                exc_info=True,
            )
            self.scheduler.record_provider_failure(self.provider_name)
            self.scheduler.advance_or_fail(job)
            return

        # No exception → the provider responded (hit, miss, or unusable
        # download). It's reachable, so reset the breaker either way.
        self.scheduler.record_provider_success(self.provider_name)
        if outcome is None:
            self.scheduler.advance_or_fail(job)
        else:
            job.result = outcome
            self.scheduler.mark_complete(job)

    def _attempt(self, job: EpisodeJob) -> dict[str, Any] | None:
        """One provider attempt. Returns the result dict on a usable download,
        or ``None`` for a miss / unusable download (both "reachable"). Raises
        on transport errors so the caller can record a breaker failure."""
        entry = self.client.get_best_subtitle(job.show_name, job.season, job.episode)
        if entry is None:
            logger.debug(f"{self.provider_name} miss for {job.episode_code}")
            return None

        result = self.client.download_subtitle(entry, job.srt_target)
        if result is None:
            return None

        if not is_valid_srt_file(Path(result)):
            logger.warning(
                f"{self.provider_name} returned an invalid SRT for "
                f"{job.episode_code}; deleting and trying next provider"
            )
            Path(result).unlink(missing_ok=True)
            return None

        logger.info(f"{self.provider_name} downloaded {job.episode_code}")
        return {
            "code": job.episode_code,
            "status": "downloaded",
            "path": str(result),
            "source": self.provider_name,
        }

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
        self._breakers: dict[str, _CircuitBreaker] = {}
        self._pending_count = 0
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._results: dict[str, dict[str, Any]] = {}

    def register(self, name: str, client: SubtitleProviderClient) -> None:
        if name in self.workers:
            return
        self.workers[name] = ProviderWorker(name, client, self)
        self._breakers[name] = _CircuitBreaker(name)

    def provider_allows(self, name: str) -> bool:
        """Worker hook: may a job be sent to ``name`` right now? False when its
        circuit breaker is open (and still cooling down)."""
        breaker = self._breakers.get(name)
        return breaker is None or breaker.allow()

    def record_provider_failure(self, name: str) -> None:
        breaker = self._breakers.get(name)
        if breaker is not None:
            breaker.record_failure()

    def record_provider_success(self, name: str) -> None:
        breaker = self._breakers.get(name)
        if breaker is not None:
            breaker.record_success()

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
        # Explicit None-check rather than ``assert`` — ``assert`` is stripped
        # when Python runs under ``-O``, and a None result silently stored
        # in self._results would poison the caller's response dict with no
        # diagnostic.
        if job.result is None:
            raise RuntimeError(
                f"mark_complete called with result=None for {job.episode_code}; "
                "every job-completion path must set job.result first"
            )
        with self._lock:
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
