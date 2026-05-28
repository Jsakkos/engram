"""Tests for chromaprint extraction and storage."""

from app.matcher.chromaprint_extractor import ChromaprintExtractor, ChromaprintResult
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


def test_enable_fingerprint_contributions_has_sql_server_default_true():
    """The column DDL must carry server_default='1' so the frozen-build path
    (_add_missing_columns in database.py) writes the correct default for existing DBs.
    Frozen builds skip Alembic entirely, so the model declaration is the only source
    of truth for that path."""
    column = AppConfig.__table__.columns["enable_fingerprint_contributions"]
    assert column.server_default is not None, (
        "enable_fingerprint_contributions needs sa_column_kwargs={'server_default': text('1')} "
        "so frozen-build users default to opt-in"
    )
    assert "1" in str(column.server_default.arg)


def test_chromaprint_result_serializes_to_bytes():
    """ChromaprintResult.to_blob() returns deterministic compressed bytes."""
    r = ChromaprintResult(
        hashes=[1, 2, 3, 4, 5],
        duration_seconds=42.0,
        fpcalc_version="fpcalc version 1.5.1",
    )
    blob = r.to_blob()
    assert isinstance(blob, bytes)
    assert len(blob) > 0
    assert r.to_blob() == blob  # deterministic


def test_chromaprint_result_roundtrip():
    """to_blob / from_blob is lossless on the hash stream and duration."""
    r = ChromaprintResult(hashes=[100, 200, 300], duration_seconds=12.5, fpcalc_version="test")
    restored = ChromaprintResult.from_blob(r.to_blob())
    assert restored.hashes == [100, 200, 300]
    assert restored.duration_seconds == 12.5


def test_extractor_construction():
    """ChromaprintExtractor takes an fpcalc_path."""
    ex = ChromaprintExtractor(fpcalc_path="/fake/fpcalc")
    assert ex.fpcalc_path == "/fake/fpcalc"
