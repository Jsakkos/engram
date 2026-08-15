"""Unit tests for the mid-rip disc eject feature."""

from pathlib import Path

from app.core.extractor import MakeMKVExtractor, RipResult, _delete_partials_at_abort


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
    )

    assert (tmp_path / "title_t00.mkv").exists(), "finished title must be kept"
    assert not (tmp_path / "title_t01.mkv").exists()
    assert not (tmp_path / "title_t02.mkv").exists()


def test_delete_partials_tolerates_missing_files(tmp_path: Path):
    """A file already gone must not raise (MakeMKV may have cleaned it up)."""
    _delete_partials_at_abort(
        tmp_path,
        _FakeCompletion(["gone.mkv"]),
        extra_stub=None,
    )
