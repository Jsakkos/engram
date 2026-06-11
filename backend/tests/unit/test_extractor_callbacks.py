"""Tests for extractor 'file created' handling.

Verifies that the MakeMKV 'created' message seeds the completion detector (so
the file is tracked) **without** firing ``title_complete_callback`` — MakeMKV
emits 'created' when it *opens* a file for writing, not when it finishes.
Completion is decided later by :class:`TitleCompletionDetector` from stable,
superseded file sizes.

The stable-size completion logic itself now lives in
``test_title_completion_detector.py``, which exercises
:class:`TitleCompletionDetector` directly. This file used to hand-mirror that
closure logic, which drifted from production and encoded the issue #381 false
positive (a lone stable file was reported complete); the mirror is gone.
"""

from pathlib import Path

from app.core.extractor import TitleCompletionDetector, _extract_created_mkv


class TestCreatedMessageSeedsWithoutCompleting:
    """The 'created' message tracks the file but must not complete it.

    Firing the callback at file-open time would cause premature
    ``_on_title_ripped`` calls for titles MakeMKV is still writing.
    """

    def test_created_message_seeds_detector_without_callback(self):
        detector = TitleCompletionDetector()

        line = "MSG:5011,0,0,\"File '/output/title00.mkv' created successfully.\""
        output_dir = Path("/output")

        filepath = _extract_created_mkv(line, output_dir)
        assert filepath is not None
        if not detector.is_known(filepath.name):
            detector.seed(filepath.name)

        # File is tracked, but nothing has completed.
        assert detector.is_known("title00.mkv")
        assert not detector.is_completed("title00.mkv")
        assert detector.completed_count == 0

    def test_non_created_mkv_line_is_ignored(self):
        # A progress line that happens to mention a file but not "created".
        assert _extract_created_mkv("PRGV:1000,2000,65536", Path("/output")) is None

    def test_seed_is_idempotent_and_preserves_size(self):
        detector = TitleCompletionDetector()
        detector.seed("title00.mkv")

        # A real size poll establishes the file's size.
        detector.poll({"title00.mkv": 500_000})
        # A duplicate 'created' message must not reset tracking back to 0.
        detector.seed("title00.mkv")

        # Growth is still tracked correctly and nothing completed spuriously.
        detector.poll({"title00.mkv": 1_000_000})
        assert not detector.is_completed("title00.mkv")
