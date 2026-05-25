"""Tests for the threaded multi-provider scheduler."""

import time
from collections import deque
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.matcher.provider_scheduler import EpisodeJob, _CircuitBreaker, run_jobs


def _make_job(
    episode: int = 1,
    providers: list[str] | None = None,
    srt_target: Path | None = None,
) -> EpisodeJob:
    return EpisodeJob(
        tmdb_id=1396,
        show_name="Breaking Bad",
        season=1,
        episode=episode,
        episode_code=f"S01E{episode:02d}",
        srt_target=srt_target or Path("/dev/null"),
        pending_providers=deque(providers or ["addic7ed"]),
    )


_VALID_SRT = (
    "1\n00:00:00,000 --> 00:00:02,000\nHello, world. This is a subtitle.\n\n"
    "2\n00:00:02,000 --> 00:00:04,000\nA second cue follows for padding.\n"
)


def _writing_client(content: str = _VALID_SRT) -> Mock:
    """Mock client that 'downloads' a real SRT file so is_valid_srt_file
    passes — the scheduler validates downloads before marking success.
    is_valid_srt_file requires >= 50 bytes AND a ``-->`` marker."""
    client = Mock()
    client.get_best_subtitle.return_value = Mock()

    def download(_entry, save_path):
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(content)
        return save_path

    client.download_subtitle.side_effect = download
    return client


def _missing_client() -> Mock:
    """Mock client that always reports 'not found'."""
    client = Mock()
    client.get_best_subtitle.return_value = None
    return client


def _failing_client(exc: Exception) -> Mock:
    """Mock client whose search raises — exercises the scheduler's
    exception path."""
    client = Mock()
    client.get_best_subtitle.side_effect = exc
    return client


@pytest.mark.unit
class TestSingleProvider:
    def test_one_job_one_provider_success(self, tmp_path):
        job = _make_job(srt_target=tmp_path / "ep.srt")
        client = _writing_client()
        results = run_jobs([job], workers={"addic7ed": client}, timeout=5)
        assert results["S01E01"]["status"] == "downloaded"
        assert results["S01E01"]["source"] == "addic7ed"
        assert Path(results["S01E01"]["path"]).read_text().startswith("1\n")

    def test_one_job_one_provider_not_found(self, tmp_path):
        job = _make_job(srt_target=tmp_path / "ep.srt")
        results = run_jobs([job], workers={"addic7ed": _missing_client()}, timeout=5)
        assert results["S01E01"]["status"] == "not_found"
        assert results["S01E01"]["source"] is None


@pytest.mark.unit
class TestProviderCascade:
    def test_first_provider_misses_second_succeeds(self, tmp_path):
        job = _make_job(
            providers=["addic7ed", "podnapisi"],
            srt_target=tmp_path / "ep.srt",
        )
        workers = {"addic7ed": _missing_client(), "podnapisi": _writing_client()}
        results = run_jobs([job], workers=workers, timeout=5)
        assert results["S01E01"]["status"] == "downloaded"
        assert results["S01E01"]["source"] == "podnapisi"

    def test_all_providers_fail_yields_not_found(self, tmp_path):
        job = _make_job(
            providers=["addic7ed", "podnapisi", "tvsubtitles"],
            srt_target=tmp_path / "ep.srt",
        )
        workers = {
            "addic7ed": _missing_client(),
            "podnapisi": _missing_client(),
            "tvsubtitles": _missing_client(),
        }
        results = run_jobs([job], workers=workers, timeout=5)
        assert results["S01E01"]["status"] == "not_found"

    def test_provider_exception_advances_to_next(self, tmp_path):
        """The scheduler must treat a provider that raised the same as one
        that returned None — pop it and try the next, rather than poisoning
        the job."""
        job = _make_job(
            providers=["addic7ed", "podnapisi"],
            srt_target=tmp_path / "ep.srt",
        )
        workers = {
            "addic7ed": _failing_client(RuntimeError("simulated 503")),
            "podnapisi": _writing_client(),
        }
        results = run_jobs([job], workers=workers, timeout=5)
        assert results["S01E01"]["status"] == "downloaded"
        assert results["S01E01"]["source"] == "podnapisi"

    def test_invalid_srt_is_treated_as_miss(self, tmp_path):
        """If a provider 'downloads' something is_valid_srt_file rejects
        (e.g. HTML), the scheduler should advance to the next provider —
        not silently mark it as a successful download."""
        # Client writes an HTML blob — is_valid_srt_file will reject it.
        client_bad = Mock()
        client_bad.get_best_subtitle.return_value = Mock()

        def bad_download(_entry, save_path):
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text("<html><body>not a subtitle</body></html>")
            return save_path

        client_bad.download_subtitle.side_effect = bad_download

        job = _make_job(
            providers=["addic7ed", "podnapisi"],
            srt_target=tmp_path / "ep.srt",
        )
        workers = {"addic7ed": client_bad, "podnapisi": _writing_client()}
        results = run_jobs([job], workers=workers, timeout=5)
        assert results["S01E01"]["status"] == "downloaded"
        assert results["S01E01"]["source"] == "podnapisi"


@pytest.mark.unit
class TestMissingWorker:
    def test_pending_provider_with_no_registered_worker_is_skipped(self, tmp_path):
        """If pending_providers names a worker that isn't registered, the
        scheduler must skip it instead of hanging the job forever."""
        job = _make_job(
            providers=["does_not_exist", "addic7ed"],
            srt_target=tmp_path / "ep.srt",
        )
        results = run_jobs([job], workers={"addic7ed": _writing_client()}, timeout=5)
        assert results["S01E01"]["status"] == "downloaded"
        assert results["S01E01"]["source"] == "addic7ed"

    def test_only_unregistered_providers_yields_not_found(self, tmp_path):
        job = _make_job(
            providers=["does_not_exist"],
            srt_target=tmp_path / "ep.srt",
        )
        results = run_jobs([job], workers={"addic7ed": _writing_client()}, timeout=5)
        assert results["S01E01"]["status"] == "not_found"


@pytest.mark.unit
class TestConcurrency:
    def test_jobs_distributed_across_providers(self, tmp_path):
        """Two providers, three jobs each going to a different provider's
        head — every job should complete and be sourced from the right
        provider. This validates the per-provider-queue routing."""
        jobs = [
            _make_job(episode=1, providers=["addic7ed"], srt_target=tmp_path / "1.srt"),
            _make_job(episode=2, providers=["podnapisi"], srt_target=tmp_path / "2.srt"),
            _make_job(episode=3, providers=["addic7ed"], srt_target=tmp_path / "3.srt"),
        ]
        addic7ed = _writing_client(
            "1\n00:00:00,000 --> 00:00:02,000\nfrom A, padded to satisfy validator.\n"
        )
        podnapisi = _writing_client(
            "1\n00:00:00,000 --> 00:00:02,000\nfrom P, padded to satisfy validator.\n"
        )
        results = run_jobs(jobs, workers={"addic7ed": addic7ed, "podnapisi": podnapisi}, timeout=5)
        assert results["S01E01"]["source"] == "addic7ed"
        assert results["S01E02"]["source"] == "podnapisi"
        assert results["S01E03"]["source"] == "addic7ed"

    def test_providers_overlap_in_wall_time(self, tmp_path):
        """Each provider has its own thread, so 3 slow jobs split across
        3 providers should take roughly the per-job time of one provider,
        not 3x. We use a 200ms gate; serial would be ~600ms, parallel ~200ms.
        Asserts < 500ms to allow plenty of headroom for CI flakiness."""

        def slow_client(label: str) -> Mock:
            client = Mock()
            client.get_best_subtitle.return_value = Mock()

            def slow_download(_entry, save_path):
                time.sleep(0.2)
                save_path.parent.mkdir(parents=True, exist_ok=True)
                save_path.write_text(
                    "1\n00:00:00,000 --> 00:00:02,000\nfrom "
                    + label
                    + ", padded to satisfy validator.\n"
                )
                return save_path

            client.download_subtitle.side_effect = slow_download
            return client

        jobs = [
            _make_job(episode=1, providers=["a"], srt_target=tmp_path / "1.srt"),
            _make_job(episode=2, providers=["b"], srt_target=tmp_path / "2.srt"),
            _make_job(episode=3, providers=["c"], srt_target=tmp_path / "3.srt"),
        ]
        workers = {"a": slow_client("A"), "b": slow_client("B"), "c": slow_client("C")}

        start = time.monotonic()
        results = run_jobs(jobs, workers=workers, timeout=5)
        elapsed = time.monotonic() - start

        assert len(results) == 3
        assert all(r["status"] == "downloaded" for r in results.values())
        # Serial would be ~600ms; parallel should be ~200ms.
        assert elapsed < 0.5, f"expected parallel execution, took {elapsed:.3f}s"


@pytest.mark.unit
class TestEmptyInput:
    def test_no_jobs_returns_empty(self):
        results = run_jobs([], workers={"addic7ed": _missing_client()})
        assert results == {}


@pytest.mark.unit
class TestCircuitBreaker:
    """Deterministic tests of the breaker state machine. ``now`` is injected
    so cooldown transitions don't depend on wall-clock sleeps."""

    def test_closed_allows_by_default(self):
        cb = _CircuitBreaker("addic7ed", failure_threshold=3)
        assert cb.allow(now=0.0) is True

    def test_trips_after_threshold_consecutive_failures(self):
        cb = _CircuitBreaker("addic7ed", failure_threshold=3, base_cooldown=30.0)
        cb.record_failure(now=0.0)
        cb.record_failure(now=0.0)
        assert cb.allow(now=0.0) is True  # 2 failures: still closed
        cb.record_failure(now=0.0)
        assert cb.allow(now=0.0) is False  # 3rd consecutive failure trips it

    def test_success_resets_failure_count(self):
        cb = _CircuitBreaker("addic7ed", failure_threshold=3)
        cb.record_failure(now=0.0)
        cb.record_failure(now=0.0)
        cb.record_success()  # reachable again → counter resets
        cb.record_failure(now=0.0)
        cb.record_failure(now=0.0)
        assert cb.allow(now=0.0) is True  # only 2 failures since reset → still closed

    def test_open_blocks_until_cooldown_then_half_open_probe(self):
        cb = _CircuitBreaker("addic7ed", failure_threshold=1, base_cooldown=30.0)
        cb.record_failure(now=0.0)  # threshold 1 → trips immediately
        assert cb.allow(now=10.0) is False  # within cooldown window
        assert cb.allow(now=31.0) is True  # cooldown elapsed → one probe allowed
        assert cb.allow(now=31.0) is False  # probe already in flight → others blocked

    def test_probe_success_closes_circuit(self):
        cb = _CircuitBreaker("addic7ed", failure_threshold=1, base_cooldown=30.0)
        cb.record_failure(now=0.0)
        assert cb.allow(now=31.0) is True  # probe
        cb.record_success()  # probe succeeded → circuit closes
        assert cb.allow(now=31.0) is True

    def test_probe_failure_reopens_with_doubled_cooldown(self):
        cb = _CircuitBreaker(
            "addic7ed", failure_threshold=1, base_cooldown=30.0, max_cooldown=300.0
        )
        cb.record_failure(now=0.0)  # open, cooldown 30
        assert cb.allow(now=31.0) is True  # probe
        cb.record_failure(now=31.0)  # probe failed → cooldown doubles to 60
        assert cb.allow(now=61.0) is False  # 31 + 60 = 91 not yet reached
        assert cb.allow(now=92.0) is True  # 91 elapsed → probe again


@pytest.mark.unit
class TestCircuitBreakerIntegration:
    def test_tripped_provider_skipped_for_remaining_jobs(self, tmp_path):
        """Once a provider fails enough consecutive times, the scheduler stops
        calling it for the rest of the batch and routes straight to the next
        provider — so a dead provider costs at most `threshold` attempts, not
        one per episode (the whole point: no 8s timeout per job)."""
        addic7ed = _failing_client(RuntimeError("connect timeout"))
        podnapisi = _writing_client()
        jobs = [
            _make_job(
                episode=i,
                providers=["addic7ed", "podnapisi"],
                srt_target=tmp_path / f"{i}.srt",
            )
            for i in range(1, 7)  # 6 jobs; default threshold is 3
        ]
        results = run_jobs(jobs, workers={"addic7ed": addic7ed, "podnapisi": podnapisi}, timeout=5)
        # Every job still resolved via the healthy fallback.
        assert all(results[f"S01E{i:02d}"]["source"] == "podnapisi" for i in range(1, 7))
        # But the dead provider was hit only `threshold` (3) times — jobs 4-6
        # skipped it entirely instead of each paying a failed call.
        assert addic7ed.get_best_subtitle.call_count == 3
