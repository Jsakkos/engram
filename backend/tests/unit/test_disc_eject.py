"""Unit tests for the mid-rip disc eject feature."""

from app.core.extractor import MakeMKVExtractor, RipResult


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
