"""Re-map DiscTitle.title_index onto a re-scan of the backup."""

from app.core.extractor import TitleInfo
from app.models import DiscTitle
from app.services.backup_reconcile import ReconcileOutcome, reconcile_titles


def _title(index: int, duration: int, source: str = "", segments: str = "") -> DiscTitle:
    return DiscTitle(
        job_id=1,
        title_index=index,
        duration_seconds=duration,
        source_filename=source,
        segment_map=segments,
    )


def _scanned(index: int, duration: int, source: str = "", segments: str = "") -> TitleInfo:
    return TitleInfo(
        index=index,
        duration_seconds=duration,
        source_filename=source,
        segment_map=segments,
        size_bytes=0,
        chapter_count=0,
    )


class TestIdenticalEnumeration:
    def test_same_order_and_durations_keeps_every_index(self):
        db = [_title(0, 2600), _title(1, 2610)]
        scan = [_scanned(0, 2600), _scanned(1, 2610)]
        result = reconcile_titles(db, scan)
        assert result.outcome is ReconcileOutcome.IDENTICAL
        assert result.remap == {}

    def test_small_duration_drift_still_counts_as_identical(self):
        # MakeMKV rounds; a one-second difference is not a different disc.
        db = [_title(0, 2600)]
        scan = [_scanned(0, 2601)]
        assert reconcile_titles(db, scan).outcome is ReconcileOutcome.IDENTICAL

    def test_extra_title_in_the_backup_is_not_identical(self):
        # Same first title, but the backup enumerates one more. Falls through to
        # the fingerprint pass, which can still line the known title up.
        db = [_title(0, 2600, "00001.m2ts")]
        scan = [_scanned(0, 2600, "00001.m2ts"), _scanned(1, 900, "00002.m2ts")]
        result = reconcile_titles(db, scan)
        assert result.outcome is ReconcileOutcome.REMAPPED
        assert result.remap == {0: 0}


class TestRemapped:
    def test_shifted_indices_remap_by_source_filename(self):
        db = [_title(0, 2600, "00001.m2ts"), _title(1, 2610, "00002.m2ts")]
        scan = [_scanned(5, 2600, "00001.m2ts"), _scanned(6, 2610, "00002.m2ts")]
        result = reconcile_titles(db, scan)
        assert result.outcome is ReconcileOutcome.REMAPPED
        assert result.remap == {0: 5, 1: 6}

    def test_segment_map_breaks_a_source_filename_tie(self):
        db = [_title(0, 2600, "00001.m2ts", "1,2"), _title(1, 2600, "00001.m2ts", "3,4")]
        scan = [
            _scanned(9, 2600, "00001.m2ts", "3,4"),
            _scanned(8, 2600, "00001.m2ts", "1,2"),
        ]
        result = reconcile_titles(db, scan)
        assert result.outcome is ReconcileOutcome.REMAPPED
        assert result.remap == {0: 8, 1: 9}

    def test_duration_drift_survives_a_shifted_index(self):
        # Regression guard against bucketing the duration: 2600 and 2601 are the
        # same title, but any integer bucket has a boundary somewhere and two
        # values within tolerance can straddle it.
        db = [_title(0, 2600, "00001.m2ts"), _title(1, 3599, "00002.m2ts")]
        scan = [_scanned(5, 2601, "00001.m2ts"), _scanned(6, 3600, "00002.m2ts")]
        result = reconcile_titles(db, scan)
        assert result.outcome is ReconcileOutcome.REMAPPED
        assert result.remap == {0: 5, 1: 6}

    def test_titles_without_source_metadata_remap_on_duration_alone(self):
        # DVDs often carry no m2ts source filename or segment map. Those rows must
        # not all collapse into one bucket and read as ambiguous when their
        # durations tell them apart perfectly well.
        db = [_title(0, 2600), _title(1, 3600), _title(2, 480)]
        scan = [_scanned(6, 3600), _scanned(5, 2600), _scanned(7, 480)]
        result = reconcile_titles(db, scan)
        assert result.outcome is ReconcileOutcome.REMAPPED
        assert result.remap == {0: 5, 1: 6, 2: 7}


class TestAmbiguous:
    def test_missing_title_is_ambiguous(self):
        db = [_title(0, 2600, "00001.m2ts"), _title(1, 2610, "00002.m2ts")]
        scan = [_scanned(0, 2600, "00001.m2ts")]
        assert reconcile_titles(db, scan).outcome is ReconcileOutcome.AMBIGUOUS

    def test_indistinguishable_titles_are_ambiguous(self):
        db = [_title(0, 2600), _title(1, 2600)]
        scan = [_scanned(7, 2600), _scanned(8, 2600)]
        result = reconcile_titles(db, scan)
        assert result.outcome is ReconcileOutcome.AMBIGUOUS
        assert result.reason

    def test_empty_scan_is_ambiguous(self):
        assert reconcile_titles([_title(0, 2600)], []).outcome is ReconcileOutcome.AMBIGUOUS

    def test_two_stored_titles_claiming_one_scanned_title_is_ambiguous(self):
        # Both rows sit inside the tolerance of the single scanned title, so each
        # one alone looks unambiguous. Only the collision check catches it.
        db = [_title(0, 2600, "00001.m2ts"), _title(1, 2601, "00001.m2ts")]
        scan = [_scanned(5, 2600, "00001.m2ts")]
        result = reconcile_titles(db, scan)
        assert result.outcome is ReconcileOutcome.AMBIGUOUS
        assert result.reason

    def test_duration_beyond_tolerance_is_ambiguous(self):
        db = [_title(0, 2600, "00001.m2ts")]
        scan = [_scanned(5, 2650, "00001.m2ts")]
        assert reconcile_titles(db, scan).outcome is ReconcileOutcome.AMBIGUOUS

    def test_no_stored_titles_is_ambiguous(self):
        assert reconcile_titles([], [_scanned(0, 2600)]).outcome is ReconcileOutcome.AMBIGUOUS
