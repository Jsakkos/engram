"""Models for the chromaprint fingerprint contribution queue (Phase 1: local-only)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Index, text
from sqlmodel import Field, SQLModel


class FingerprintContribution(SQLModel, table=True):
    """Local-only queue row.

    Phase 1: rows are appended on successful match. They never leave the local
    machine. Phase 2 adds a ContributionUploader service that drains this table
    over HTTPS to the fingerprint network server.
    """

    __tablename__ = "fingerprint_contributions"

    id: int | None = Field(default=None, primary_key=True)
    queued_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column_kwargs={"server_default": text("(datetime('now'))")},
    )

    # Nullable so bootstrap rows (no DiscTitle row) can also be queued.
    title_id: int | None = Field(default=None, foreign_key="disc_titles.id", index=True)
    chromaprint_blob: bytes

    # Episode identity (the payload-bearing fields per the Phase 2 design)
    tmdb_id: int
    season: int | None = None
    episode: int | None = None

    # Human-readable show name for local display/diagnostics (logs, future queue
    # UI). Not part of the upload identity — the server keys off tmdb_id+season+
    # episode. Nullable: older rows and disc-flow rows may leave it unset.
    show_title: str | None = None

    # Provenance for trust-tier promotion
    match_confidence: float
    match_source: str  # 'engram_asr' | 'engram_discdb' | 'bootstrap' | 'user_review'

    # Identifies a *disc release*, not the user's file (m2ts size MD5 from TheDiscDB)
    disc_content_hash: bytes | None = None

    pseudonym: str

    # Phase 2 uploader state
    uploaded_at: datetime | None = None
    upload_attempts: int = Field(default=0)  # cumulative lifetime attempts (diagnostic)
    # None=pending OR transiently failed (5xx/network/429) and awaiting retry on a
    # later drain — distinguish via upload_attempts/upload_error_msg; "success";
    # "failed"=PERMANENT only (4xx or blob-decode). A sustained transient outage
    # never reaches "failed" — that is what keeps a 503 storm from burning rows.
    upload_status: str | None = Field(default=None)
    upload_error_msg: str | None = Field(default=None)  # last error text for UI


class DiscContribution(SQLModel, table=True):
    """Local-only queue row for a whole-disc layout contribution (Phase C).

    Appended when a disc job reaches COMPLETED. Captures the disc's content hash
    plus its full title→assignment mapping (``titles_json``) so the fingerprint
    network can promote a disc once enough independent users contribute the same
    disc with the same mapping — letting future inserts skip audio matching.

    Mirrors ``FingerprintContribution``'s upload-state fields. A separate uploader
    (Phase C-B2) drains this table over HTTPS; this model is append-only.
    """

    __tablename__ = "disc_contributions"
    # Composite index for the enqueue dedup probe, which filters on
    # (pseudonym, disc_content_hash) (then titles_json) before every insert.
    # Declared on the MODEL — not only the migration — because frozen builds
    # skip Alembic and create this table via create_all over the metadata, so
    # an index on the migration alone would never reach end users.
    __table_args__ = (Index("ix_disc_contributions_dedup", "pseudonym", "disc_content_hash"),)

    id: int | None = Field(default=None, primary_key=True)
    queued_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column_kwargs={"server_default": text("(datetime('now'))")},
    )

    # Identifies a *disc release* (m2ts size MD5 from TheDiscDB) — stored as raw
    # bytes, like FingerprintContribution.disc_content_hash.
    disc_content_hash: bytes

    # Disc identity
    tmdb_id: int
    content_type: str  # "tv" | "movie"
    season: int | None = None  # TV disc season (None for movies)

    # Full per-title layout (JSON list of title rows). Each row carries the
    # title index, duration, size, its assignment ("episode"|"main_movie"|
    # "extra"|"discarded"), season/episode, confidence and mapped match source.
    titles_json: str

    pseudonym: str

    # Uploader state (mirrors FingerprintContribution; client_version is added at
    # UPLOAD time, not stored here).
    uploaded_at: datetime | None = None
    upload_attempts: int = Field(default=0)
    upload_status: str | None = Field(default=None)  # None=pending; "success"; "failed"
    upload_error_msg: str | None = Field(default=None)
