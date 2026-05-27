"""Tests for chromaprint extraction and storage."""

from app.models.app_config import AppConfig
from app.models.disc_job import DiscTitle


def test_disc_title_has_chromaprint_fields():
    """DiscTitle model exposes chromaprint storage fields."""
    fields = DiscTitle.model_fields
    assert "chromaprint_blob" in fields, "DiscTitle is missing chromaprint_blob"
    assert "chromaprint_extracted_at" in fields, "DiscTitle is missing chromaprint_extracted_at"


def test_app_config_has_fingerprint_fields():
    """AppConfig exposes fingerprint extraction settings."""
    fields = AppConfig.model_fields
    assert "fpcalc_path" in fields
    assert "contribution_pseudonym" in fields
    assert "enable_fingerprint_contributions" in fields


def test_enable_fingerprint_contributions_defaults_true():
    """Opt-out default: contributions enabled unless explicitly disabled."""
    cfg = AppConfig()
    assert cfg.enable_fingerprint_contributions is True
