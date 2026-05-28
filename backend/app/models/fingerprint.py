"""Models for the chromaprint fingerprint contribution queue (Phase 1: local-only)."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel


class FingerprintContribution(SQLModel, table=True):
    """Local-only queue row.

    Phase 1: rows are appended on successful match. They never leave the local
    machine. Phase 2 adds a ContributionUploader service that drains this table
    over HTTPS to the fingerprint network server.
    """

    __tablename__ = "fingerprint_contributions"

    id: int | None = Field(default=None, primary_key=True)
    queued_at: datetime = Field(default_factory=datetime.utcnow)

    # Nullable so bootstrap rows (no DiscTitle row) can also be queued.
    title_id: int | None = Field(default=None, foreign_key="disc_titles.id", index=True)
    chromaprint_blob: bytes

    # Episode identity (the payload-bearing fields per the Phase 2 design)
    tmdb_id: int
    season: int | None = None
    episode: int | None = None

    # Provenance for trust-tier promotion
    match_confidence: float
    match_source: str  # 'engram_asr' | 'engram_discdb' | 'bootstrap' | 'user_review'

    # Identifies a *disc release*, not the user's file (m2ts size MD5 from TheDiscDB)
    disc_content_hash: bytes | None = None

    pseudonym: str

    # Set when Phase 2 uploader runs
    uploaded_at: datetime | None = None
    upload_attempts: int = Field(default=0)
