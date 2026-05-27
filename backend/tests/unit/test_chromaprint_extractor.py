"""Tests for chromaprint extraction and storage."""

from app.models.disc_job import DiscTitle


def test_disc_title_has_chromaprint_fields():
    """DiscTitle model exposes chromaprint storage fields."""
    fields = DiscTitle.model_fields
    assert "chromaprint_blob" in fields, "DiscTitle is missing chromaprint_blob"
    assert "chromaprint_extracted_at" in fields, "DiscTitle is missing chromaprint_extracted_at"
