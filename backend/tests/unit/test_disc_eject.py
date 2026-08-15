"""Unit tests for the mid-rip disc eject feature."""

from pathlib import Path

from app.core.extractor import (
    MakeMKVExtractor,
    RipResult,
    TitleCompletionDetector,
    _delete_partials_at_abort,
)


def test_rip_result_defaults_aborted_for_eject_false():
    """A normal RipResult is not an eject abort."""
    result = RipResult(success=True, output_files=[])
    assert result.aborted_for_eject is False


def test_eject_abort_marks_job_ejected_and_cancelled():
    """eject_abort flags the job in both sets so the existing rip loop breaks out.

    _cancelled_jobs is what the reader loop already checks to terminate the
    subprocess; _ejected_jobs is what the return path checks (first) to report
    an eject rather than a cancel.
    """
    extractor = MakeMKVExtractor()
    extractor.eject_abort(42)
    assert 42 in extractor._ejected_jobs
    assert 42 in extractor._cancelled_jobs


def test_eject_abort_is_isolated_per_job():
    """Ejecting one job must not disturb another drive's rip."""
    extractor = MakeMKVExtractor()
    extractor.eject_abort(1)
    assert 2 not in extractor._ejected_jobs
    assert 2 not in extractor._cancelled_jobs


class _FakeCompletion:
    """Stands in for the rip's completion detector.

    files_incomplete_at_abort normally reports files that grew since the last
    poll (still being written). The fake just returns a fixed list.
    """

    def __init__(self, incomplete: list[str]) -> None:
        self._incomplete = incomplete

    def files_incomplete_at_abort(self, sizes: dict[str, int]) -> list[str]:
        return list(self._incomplete)


def test_delete_partials_removes_growing_file_and_stub(tmp_path: Path):
    """The in-flight (growing) file and the named stub are deleted."""
    (tmp_path / "title_t00.mkv").write_bytes(b"complete")
    (tmp_path / "title_t01.mkv").write_bytes(b"partial")
    (tmp_path / "title_t02.mkv").write_bytes(b"stub")

    _delete_partials_at_abort(
        tmp_path,
        _FakeCompletion(["title_t01.mkv"]),
        extra_stub="title_t02.mkv",
        lock=None,
        reason="test",
    )

    assert (tmp_path / "title_t00.mkv").exists(), "finished title must be kept"
    assert not (tmp_path / "title_t01.mkv").exists()
    assert not (tmp_path / "title_t02.mkv").exists()


def test_delete_partials_tolerates_missing_files(tmp_path: Path):
    """A doomed file already gone must not raise or abort the remaining deletions."""
    (tmp_path / "keep.mkv").write_bytes(b"complete")
    (tmp_path / "doomed.mkv").write_bytes(b"partial")

    _delete_partials_at_abort(
        tmp_path,
        _FakeCompletion(["gone.mkv", "doomed.mkv"]),
        extra_stub=None,
        lock=None,
        reason="test",
    )

    assert (tmp_path / "keep.mkv").exists()
    assert not (tmp_path / "doomed.mkv").exists()


def _write(path: Path, size: int) -> None:
    """Create/overwrite an mkv of exactly *size* bytes."""
    path.write_bytes(b"\0" * size)


def _sizes(directory: Path) -> dict[str, int]:
    return {p.name: p.stat().st_size for p in directory.glob("*.mkv")}


def test_eject_cleanup_preserves_title_that_finished_since_last_poll(tmp_path: Path):
    """The eject preparer's fresh poll saves a title that finished mid-window.

    Regression guard for the stale-poll bug: a title can finish writing between
    two routine completion polls, so it has *grown* since the last recorded
    size without being in _completed yet (that needs STABLE_CHECKS_REQUIRED
    polls plus a successor). Without the final poll eject_abort takes before
    terminating, the cleanup condemns it, and after an eject the disc is gone
    so the title cannot be re-ripped.

    Driven through the real TitleCompletionDetector so the actual
    files_incomplete_at_abort comparison is exercised.
    """
    detector = TitleCompletionDetector()
    title = tmp_path / "title_t00.mkv"

    # Routine poll while the title is still being written.
    _write(title, 900)
    detector.poll(_sizes(tmp_path))

    # The title finishes writing. It grew since that poll and is NOT completed.
    _write(title, 1000)
    assert not detector.is_completed(title.name)

    # What eject_abort does before killing MakeMKV: one final poll.
    detector.poll(_sizes(tmp_path))

    _delete_partials_at_abort(tmp_path, detector, None, lock=None, reason="disc eject")

    assert title.exists(), "a title that finished before the eject must be preserved"


def test_delete_partials_condemns_title_grown_since_a_stale_poll(tmp_path: Path):
    """Characterizes WHY the final poll is required (do not delete this test).

    Same scenario as above minus the fresh poll: the detector's sizes are stale,
    the finished title reads as 'still growing', and it is destroyed. This is
    the failure the eject preparer exists to prevent.
    """
    detector = TitleCompletionDetector()
    title = tmp_path / "title_t00.mkv"

    _write(title, 900)
    detector.poll(_sizes(tmp_path))
    _write(title, 1000)  # finished, but never polled again

    _delete_partials_at_abort(tmp_path, detector, None, lock=None, reason="disc eject")

    assert not title.exists()


def test_eject_abort_runs_preparer_before_cancelling(tmp_path: Path):
    """eject_abort polls while MakeMKV still runs, then marks the job."""
    extractor = MakeMKVExtractor()
    calls: list[str] = []

    def _preparer() -> None:
        # The subprocess must still be alive at this point, so neither flag
        # may be set yet.
        assert 7 not in extractor._cancelled_jobs
        assert 7 not in extractor._ejected_jobs
        calls.append("prepared")

    extractor._eject_preparers[7] = _preparer
    extractor.eject_abort(7)

    assert calls == ["prepared"]
    assert 7 in extractor._ejected_jobs
    assert 7 in extractor._cancelled_jobs


def test_eject_abort_without_a_registered_preparer_is_safe():
    """Ejecting a job that is not currently ripping must not raise."""
    extractor = MakeMKVExtractor()
    extractor.eject_abort(99)
    assert 99 in extractor._ejected_jobs
