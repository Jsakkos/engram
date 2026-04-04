"""DiscJob model - the core state machine for disc processing."""

from datetime import UTC, datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel


class JobState(StrEnum):
    """States in the disc processing lifecycle."""

    IDLE = "idle"
    IDENTIFYING = "identifying"  # Scanning disc structure
    REVIEW_NEEDED = "review_needed"  # Human-in-the-Loop trigger
    RIPPING = "ripping"  # Active extraction
    MATCHING = "matching"  # Audio fingerprinting
    ORGANIZING = "organizing"  # Moving files to library
    COMPLETED = "completed"
    FAILED = "failed"


class ContentType(StrEnum):
    """Type of content on the disc."""

    TV = "tv"
    MOVIE = "movie"
    UNKNOWN = "unknown"


class TitleState(StrEnum):
    """State of an individual title."""

    PENDING = "pending"
    RIPPING = "ripping"
    MATCHING = "matching"
    MATCHED = "matched"  # Intermediate state: matched but not yet organized
    REVIEW = "review"  # Ripped successfully but needs human review for episode assignment
    COMPLETED = "completed"
    FAILED = "failed"


class DiscJob(SQLModel, table=True):
    """Represents a disc ripping job with full state tracking."""

    __tablename__ = "disc_jobs"

    id: int | None = Field(default=None, primary_key=True)
    drive_id: str = Field(index=True)  # e.g., "E:" or "/dev/sr0"
    volume_label: str = ""  # e.g., "THE_OFFICE_S1"

    # Classification
    content_type: ContentType = ContentType.UNKNOWN
    detected_title: str | None = None  # e.g., "The Office"
    detected_season: int | None = None
    is_transcoding_enabled: bool = False

    # Classification metadata (persisted from DiscAnalysisResult)
    classification_confidence: float = Field(
        default=0.0, sa_column_kwargs={"server_default": "0.0"}
    )
    classification_source: str = Field(
        default="heuristic", sa_column_kwargs={"server_default": "'heuristic'"}
    )
    tmdb_id: int | None = Field(default=None)
    tmdb_name: str | None = Field(default=None)
    play_all_indices_json: str | None = Field(default=None)
    is_ambiguous_movie: bool = Field(default=False, sa_column_kwargs={"server_default": "0"})

    # Paths
    staging_path: str | None = None
    final_path: str | None = None

    # Progress Tracking
    state: JobState = JobState.IDLE
    current_speed: str = "0.0x"
    eta_seconds: int = 0
    progress_percent: float = 0.0
    current_title: int = 0
    total_titles: int = 0

    # Subtitle tracking
    subtitle_status: str | None = None  # "downloading", "completed", "partial", "failed", None
    subtitles_downloaded: int = 0
    subtitles_total: int = 0
    subtitles_failed: int = 0

    # Metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = Field(default=None)  # When job reached terminal state
    cleared_at: datetime | None = Field(default=None)  # Soft-delete: hidden from dashboard
    error_message: str | None = None
    review_reason: str | None = None  # Human-readable reason why review is needed

    # Title information (JSON stored as string for simplicity)
    titles_json: str | None = None  # List of titles with durations

    # Disc metadata for multi-disc sets
    disc_number: int = 1  # For multi-disc sets, default to 1

    # TheDiscDB metadata
    content_hash: str | None = Field(default=None)  # MakeMKV disc fingerprint (MD5)
    discdb_slug: str | None = Field(default=None)  # e.g., "band-of-brothers-2001"
    discdb_disc_slug: str | None = Field(default=None)  # e.g., "S01D01"
    discdb_mappings_json: str | None = Field(
        default=None
    )  # JSON-serialized DiscDbTitleMapping list

    # TheDiscDB contribution tracking
    exported_at: datetime | None = Field(default=None)  # When disc data was exported
    upc_code: str | None = Field(default=None)  # UPC barcode for full contributions


class DiscTitle(SQLModel, table=True):
    """Individual title (track) on a disc."""

    __tablename__ = "disc_titles"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="disc_jobs.id", index=True)
    title_index: int  # MakeMKV title index
    duration_seconds: int
    file_size_bytes: int = 0
    chapter_count: int = 0

    # Selection
    is_selected: bool = True
    output_filename: str | None = None

    # Version/Quality info
    video_resolution: str | None = None  # e.g., "4K", "1080p", "480p"
    edition: str | None = None  # e.g., "Extended", "Director's Cut", "Theatrical"

    # Matching results
    matched_episode: str | None = None  # e.g., "S01E01"
    match_confidence: float = 0.0
    match_details: str | None = None  # JSON string with score breakdown

    # Progress
    state: TitleState = TitleState.PENDING

    # Conflict resolution for organization
    conflict_resolution: str | None = None  # User's choice for specific conflict
    existing_file_path: str | None = None  # Path to existing file causing conflict

    # MakeMKV track metadata (for TheDiscDB contributions)
    source_filename: str | None = None  # e.g., "00001.m2ts"
    segment_count: int = 0
    segment_map: str | None = None  # e.g., "1,2,3"

    # Organization tracking
    organized_from: str | None = None  # Source filename
    organized_to: str | None = None  # Destination path
    is_extra: bool = False  # True if organized as extra content
