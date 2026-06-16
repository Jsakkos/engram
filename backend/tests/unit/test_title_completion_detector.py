"""Tests for ``TitleCompletionDetector`` — the in-flight rip-completion logic.

This replaces the hand-mirrored ``_check_for_completed_files`` simulation in
``test_extractor_callbacks.py`` with tests that exercise the real production
class, so the two can no longer drift.

Regression focus (issue #381): a MakeMKV title that *pauses* mid-rip (slow or
dirty disc) holds its output-file size constant for several polls. Size
stability **alone** must not be read as "finished writing" — doing so hands a
still-ripping file to the matcher, and the job UI then flickers between the red
RIPPING state and the green matched/idle state for the rest of the rip. A title
is only complete once MakeMKV has demonstrably moved on (the process exited, or
a *different* output file is now the one growing).
"""

from app.core.extractor import STABLE_CHECKS_REQUIRED, TitleCompletionDetector


def _drain(detector: TitleCompletionDetector, sizes, *, force=False):
    """Run one poll and return the list of newly-completed filenames."""
    return [fname for fname, _ordinal in detector.poll(dict(sizes), force=force)]


class TestNoFalsePositiveOnMidRipPause:
    """The core #381 regression: a lone, still-active title that pauses writing
    must never be reported complete, no matter how many stable polls elapse."""

    def test_paused_active_title_never_completes_in_flight(self):
        d = TitleCompletionDetector(STABLE_CHECKS_REQUIRED)

        # The single title grows for a couple of polls (it is the active file).
        assert _drain(d, {"S1D1_t00.mkv": 100_000_000}) == []  # baseline
        assert _drain(d, {"S1D1_t00.mkv": 200_000_000}) == []  # grew

        # MakeMKV pauses writes (bad sector). The size is now stable for far
        # more than STABLE_CHECKS_REQUIRED polls — but nothing else is growing,
        # so the active title cannot be "done".
        for _ in range(STABLE_CHECKS_REQUIRED + 3):
            assert _drain(d, {"S1D1_t00.mkv": 200_000_000}) == []

        # Writing resumes — proving the earlier "completion" would have been a
        # false positive.
        assert _drain(d, {"S1D1_t00.mkv": 300_000_000}) == []

    def test_completes_only_once_a_later_title_starts_growing(self):
        d = TitleCompletionDetector(STABLE_CHECKS_REQUIRED)

        # t00 rips, then goes stable (it has actually finished).
        _drain(d, {"disc_t00.mkv": 100})
        _drain(d, {"disc_t00.mkv": 500})
        for _ in range(STABLE_CHECKS_REQUIRED):
            assert _drain(d, {"disc_t00.mkv": 500}) == []  # no successor yet

        # MakeMKV opens t01 and starts writing it → t00 is now provably done.
        completed = _drain(d, {"disc_t00.mkv": 500, "disc_t01.mkv": 50})
        assert completed == ["disc_t00.mkv"]
        assert d.is_completed("disc_t00.mkv")
        assert not d.is_completed("disc_t01.mkv")


class TestForceCompletion:
    """force=True (process exit) finalizes every non-empty, not-yet-done file."""

    def test_force_completes_active_title_at_process_exit(self):
        d = TitleCompletionDetector(STABLE_CHECKS_REQUIRED)
        _drain(d, {"only_t00.mkv": 100})
        _drain(d, {"only_t00.mkv": 900})
        # In-flight it never fired (no successor); the exit force-check does.
        assert _drain(d, {"only_t00.mkv": 900}, force=True) == ["only_t00.mkv"]

    def test_force_does_not_double_fire(self):
        d = TitleCompletionDetector(STABLE_CHECKS_REQUIRED)
        _drain(d, {"t00.mkv": 100})
        assert _drain(d, {"t00.mkv": 100}, force=True) == ["t00.mkv"]
        assert _drain(d, {"t00.mkv": 100}, force=True) == []

    def test_force_ignores_zero_byte_files(self):
        d = TitleCompletionDetector(STABLE_CHECKS_REQUIRED)
        d.seed("t00.mkv")  # MakeMKV announced 'created' but never wrote bytes
        assert _drain(d, {"t00.mkv": 0}, force=True) == []
        assert not d.is_completed("t00.mkv")

    def test_force_batch_assigns_sequential_ordinals(self):
        # When a single force poll finalizes several titles at once (process
        # exit with more than one undetected title), each must get a distinct
        # 1-based ordinal — not all share the final total. The ordinal feeds the
        # callback's sequential title-resolution fallback.
        d = TitleCompletionDetector(STABLE_CHECKS_REQUIRED)
        d.poll({"t00.mkv": 100, "t01.mkv": 200})  # baseline (prev=None both)
        completed = d.poll({"t00.mkv": 100, "t01.mkv": 200}, force=True)
        assert completed == [("t00.mkv", 1), ("t01.mkv", 2)]

    def test_force_batch_via_seed_completes_all_with_distinct_ordinals(self):
        # Realistic process-exit batch: both files were announced via the
        # 'created' message (seeded at 0) but in-flight detection never fired
        # (e.g. neither was superseded), so the post-process force poll finalizes
        # both at once. Each must complete with a distinct sequential ordinal.
        d = TitleCompletionDetector(STABLE_CHECKS_REQUIRED)
        d.seed("t00.mkv")
        d.seed("t01.mkv")
        completed = d.poll({"t00.mkv": 500, "t01.mkv": 300}, force=True)
        assert completed == [("t00.mkv", 1), ("t01.mkv", 2)]
        assert d.completed_count == 2


class TestIgnoresPreExistingFiles:
    """A single-track re-rip writes into a staging dir that still holds the
    disc's other, already-finished titles. A fresh detector has no memory of
    them, so without an ignore-list it would (a) re-report each pre-existing
    file as freshly "completed" — mis-attributing it to the title being
    re-ripped — and (b) on a stall, delete it as "incomplete", wiping good
    episodes. Files for a title we ARE (re-)ripping must NOT be ignored.
    """

    def test_ignored_file_never_reported_complete(self):
        # B1_t00 (a prior title's finished file) sits in the dir as the re-rip
        # of E1_t03 begins. Only the real re-ripped file may ever fire.
        d = TitleCompletionDetector(STABLE_CHECKS_REQUIRED, ignore={"B1_t00.mkv"})

        _drain(d, {"B1_t00.mkv": 1_000_000, "E1_t03.mkv": 10})  # baseline
        _drain(d, {"B1_t00.mkv": 1_000_000, "E1_t03.mkv": 20})  # E1 grows

        # B1_t00 is stable far beyond the threshold while E1_t03 is the active
        # (growing) file — the exact shape that would falsely complete it.
        for _ in range(STABLE_CHECKS_REQUIRED + 2):
            assert _drain(d, {"B1_t00.mkv": 1_000_000, "E1_t03.mkv": 20}) == []

        # Process exit force-finalizes the real re-ripped file only.
        assert _drain(d, {"B1_t00.mkv": 1_000_000, "E1_t03.mkv": 20}, force=True) == ["E1_t03.mkv"]
        assert not d.is_completed("B1_t00.mkv")

    def test_should_preserve_shields_ignored_and_completed_files(self):
        # The stall-cleanup loop deletes any *.mkv for which should_preserve is
        # False. A pre-existing good title is shielded; a partial re-rip is not.
        d = TitleCompletionDetector(STABLE_CHECKS_REQUIRED, ignore={"B1_t00.mkv"})
        assert d.should_preserve("B1_t00.mkv") is True
        assert d.should_preserve("E1_t03.mkv") is False

        # Once the real re-ripped file completes, it too is preserved.
        _drain(d, {"E1_t03.mkv": 100})
        _drain(d, {"E1_t03.mkv": 200}, force=True)
        assert d.should_preserve("E1_t03.mkv") is True

    def test_ignored_file_does_not_inflate_completed_count(self):
        # Ordinals/counts must reflect titles WE produced, not pre-existing ones.
        d = TitleCompletionDetector(STABLE_CHECKS_REQUIRED, ignore={"B1_t00.mkv"})
        _drain(d, {"B1_t00.mkv": 1_000, "E1_t03.mkv": 100})
        d.poll({"B1_t00.mkv": 1_000, "E1_t03.mkv": 200}, force=True)
        assert d.completed_count == 1


class TestInvariants:
    """General invariants preserved from the original stable-size detector."""

    def test_growing_file_does_not_complete(self):
        d = TitleCompletionDetector(STABLE_CHECKS_REQUIRED)
        _drain(d, {"t00.mkv": 500_000})
        assert _drain(d, {"t00.mkv": 1_000_000}) == []

    def test_zero_size_file_does_not_complete(self):
        d = TitleCompletionDetector(STABLE_CHECKS_REQUIRED)
        _drain(d, {"t00.mkv": 0})
        assert _drain(d, {"t00.mkv": 0}) == []

    def test_seed_marks_file_known_without_completing(self):
        d = TitleCompletionDetector(STABLE_CHECKS_REQUIRED)
        d.seed("t00.mkv")
        assert d.is_known("t00.mkv")
        assert not d.is_completed("t00.mkv")

    def test_completed_count_tracks_finished_titles(self):
        d = TitleCompletionDetector(STABLE_CHECKS_REQUIRED)
        # Two titles finish in sequence, each superseded by the next.
        _drain(d, {"t00.mkv": 100})
        _drain(d, {"t00.mkv": 400})
        for _ in range(STABLE_CHECKS_REQUIRED):
            _drain(d, {"t00.mkv": 400})
        _drain(d, {"t00.mkv": 400, "t01.mkv": 50})  # t00 done
        _drain(d, {"t00.mkv": 400, "t01.mkv": 400})
        for _ in range(STABLE_CHECKS_REQUIRED):
            _drain(d, {"t00.mkv": 400, "t01.mkv": 400})
        _drain(d, {"t00.mkv": 400, "t01.mkv": 400, "t02.mkv": 10})  # t01 done
        assert d.completed_count == 2
