"""Tests for the local FingerprintContribution queue."""

from app.models.fingerprint import FingerprintContribution


def test_fingerprint_contribution_has_required_fields():
    fields = FingerprintContribution.model_fields
    for required in (
        "id",
        "queued_at",
        "title_id",
        "chromaprint_blob",
        "tmdb_id",
        "season",
        "episode",
        "match_confidence",
        "match_source",
        "disc_content_hash",
        "pseudonym",
        "uploaded_at",
        "upload_attempts",
    ):
        assert required in fields, f"FingerprintContribution missing field: {required}"


def test_fingerprint_contribution_construction():
    c = FingerprintContribution(
        title_id=1,
        chromaprint_blob=b"\x00\x01",
        tmdb_id=12345,
        season=1,
        episode=7,
        match_confidence=0.92,
        match_source="engram_asr",
        disc_content_hash=b"\xab\xcd",
        pseudonym="00000000-0000-4000-8000-000000000000",
    )
    assert c.uploaded_at is None
    assert c.upload_attempts == 0


def test_fingerprint_contribution_title_id_nullable():
    """Bootstrap contributions don't have a corresponding DiscTitle row."""
    c = FingerprintContribution(
        title_id=None,
        chromaprint_blob=b"x",
        tmdb_id=1,
        season=1,
        episode=1,
        match_confidence=1.0,
        match_source="bootstrap",
        pseudonym="00000000-0000-4000-8000-000000000000",
    )
    assert c.title_id is None
