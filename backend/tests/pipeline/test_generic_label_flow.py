"""Test generic label flow: LOGICAL_VOLUME_ID -> no name -> name prompt -> resume.

Exercises the Analyst's generic label detection and the Organizer's
path generation after the user provides a title manually.
"""

import pytest

from app.core.analyst import DiscAnalyst
from app.core.organizer import clean_movie_name, sanitize_filename
from app.models.disc_job import ContentType

from tests.pipeline.conftest import load_snapshot, snapshot_to_titles


@pytest.mark.pipeline
class TestGenericLabelDetection:
    """Verify that generic disc labels are rejected by the parser."""

    GENERIC_LABELS = [
        "LOGICAL_VOLUME_ID",
        "VIDEO_TS",
        "BDMV",
        "DISC",
        "DVD",
        "BLURAY",
        "BD",
        "NO_LABEL",
        "UNTITLED",
        "VOLUME",
        "NEW_VOLUME",
    ]

    @pytest.mark.parametrize("label", GENERIC_LABELS)
    def test_generic_label_returns_none(self, label):
        name, season, disc = DiscAnalyst._parse_volume_label(label)
        assert name is None
        assert season is None

    def test_non_generic_label_returns_name(self):
        name, season, disc = DiscAnalyst._parse_volume_label("THE_ITALIAN_JOB")
        assert name is not None
        assert "Italian" in name


@pytest.mark.pipeline
class TestGenericLabelNamePromptFlow:
    """Full flow: generic label -> Analyst -> no name -> user provides name."""

    def test_analyst_returns_no_name(self, analyst):
        """Step 1: Analyst correctly returns detected_name=None."""
        snap = load_snapshot("logical_volume_id")
        titles = snapshot_to_titles(snap)
        result = analyst.analyze(titles, "LOGICAL_VOLUME_ID")
        assert result.detected_name is None
        assert result.content_type == ContentType.MOVIE

    def test_job_manager_review_precondition(self, analyst):
        """Step 2: detected_name=None is the precondition for REVIEW_NEEDED.

        JobManager._identify_disc sets job.detected_title = analysis.detected_name,
        then checks `if not job.detected_title:` to trigger the name prompt modal.
        """
        snap = load_snapshot("logical_volume_id")
        titles = snapshot_to_titles(snap)
        result = analyst.analyze(titles, "LOGICAL_VOLUME_ID")
        assert not result.detected_name  # This triggers REVIEW_NEEDED

    def test_user_provided_name_generates_correct_movie_path(self):
        """Step 3: After user provides 'The Italian Job' + year, Organizer paths are correct."""
        user_name = "The Italian Job"
        year = 2003

        clean = clean_movie_name(user_name)
        assert clean == "The Italian Job"

        folder = f"{clean} ({year})"
        folder = sanitize_filename(folder)
        assert folder == "The Italian Job (2003)"

    def test_user_provided_name_with_special_characters(self):
        """Edge case: user provides name with characters that need sanitization."""
        user_name = "The Italian Job: Special Edition"
        year = 2003

        clean = clean_movie_name(user_name)
        folder = f"{clean} ({year})"
        folder = sanitize_filename(folder)
        # Colon should be removed/replaced by sanitize_filename
        assert ":" not in folder
        assert "Italian" in folder
