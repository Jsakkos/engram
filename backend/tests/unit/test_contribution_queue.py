"""Tests for the local FingerprintContribution queue."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.fingerprint import FingerprintContribution
from app.services.contribution_queue import ContributionQueue


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


@pytest.mark.asyncio
async def test_enqueue_persists_row():
    """enqueue() inserts a FingerprintContribution row with the supplied fields."""
    session = AsyncMock()
    session.add = MagicMock()
    queue = ContributionQueue()
    await queue.enqueue(
        session=session,
        title_id=42,
        chromaprint_blob=b"\xde\xad",
        tmdb_id=1399,
        season=1,
        episode=1,
        match_confidence=0.91,
        match_source="engram_asr",
        disc_content_hash=b"\x12\x34",
        pseudonym="11111111-1111-4111-8111-111111111111",
    )
    session.add.assert_called_once()
    added = session.add.call_args[0][0]
    assert added.title_id == 42
    assert added.match_source == "engram_asr"


@pytest.mark.asyncio
async def test_enqueue_respects_opt_out():
    """If contributions_enabled=False, enqueue is a no-op."""
    session = AsyncMock()
    session.add = MagicMock()
    queue = ContributionQueue()
    await queue.enqueue(
        session=session,
        title_id=1,
        chromaprint_blob=b"x",
        tmdb_id=1,
        season=1,
        episode=1,
        match_confidence=0.9,
        match_source="engram_asr",
        disc_content_hash=None,
        pseudonym="11111111-1111-4111-8111-111111111111",
        contributions_enabled=False,
    )
    session.add.assert_not_called()
