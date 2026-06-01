"""Application configuration stored in SQLite.

This model stores user-configurable settings that persist across restarts
and can be modified via the UI.
"""

from datetime import datetime

from sqlalchemy import text
from sqlmodel import Field, SQLModel

# Base origin of the fingerprint network — WITHOUT a /v1 suffix. The uploader
# appends /v1/contribute and the forget endpoint appends /v1/forget, so a stored
# /v1 here would double to /v1/v1/... and 404. Used as the effective default at
# point-of-use (see ContributionUploader / forget endpoint) so EXISTING installs
# — whose app_config row predates this column and stores NULL — still engage the
# network. The opt-out toggle (enable_fingerprint_contributions), not a blank
# URL, is the single source of truth for "do I contribute".
DEFAULT_FINGERPRINT_SERVER_URL = "https://engram-fp-prod.jonathansakkos.workers.dev"


class AppConfig(SQLModel, table=True):
    """User-configurable application settings stored in database."""

    __tablename__ = "app_config"

    id: int | None = Field(default=None, primary_key=True)

    # MakeMKV Configuration
    makemkv_path: str = ""  # Auto-detected on startup
    makemkv_key: str = ""  # License key

    # Paths - User's media library locations
    staging_path: str = ""  # Platform-aware default set on first run
    library_movies_path: str = ""
    library_tv_path: str = ""

    # Episode Matcher Settings
    subtitles_cache_path: str = "~/.engram/cache"
    matcher_min_confidence: float = 0.6

    # Precomputed subtitle-vector cache (downloaded from GitHub Releases on first run).
    # server_default="1" so the column is added enabled for pre-existing databases.
    precomputed_cache_enabled: bool = Field(
        default=True, sa_column_kwargs={"server_default": text("1")}
    )
    precomputed_cache_version: str = ""  # content version of the installed cache

    # TMDB API (for show metadata)
    tmdb_api_key: str = ""

    # Matching concurrency (limits parallel Whisper ASR tasks to avoid GPU OOM)
    max_concurrent_matches: int = 2

    # FFmpeg path (empty string = use PATH)
    ffmpeg_path: str = ""

    # Default conflict resolution behavior
    conflict_resolution_default: str = "ask"  # Options: "ask", "overwrite", "rename", "skip"

    # Analyst Classification Thresholds
    analyst_movie_min_duration: int = 80 * 60  # 80 minutes in seconds
    analyst_tv_duration_variance: int = 2 * 60  # ±2 minutes cluster tolerance
    analyst_tv_min_cluster_size: int = 3  # Minimum titles to form TV cluster
    analyst_tv_min_duration: int = 18 * 60  # 18 minutes minimum for TV episodes
    analyst_tv_max_duration: int = 70 * 60  # 70 minutes maximum for TV episodes
    analyst_movie_dominance_threshold: float = 0.6  # 60% threshold for movie detection

    # Ripping Coordination
    ripping_file_poll_interval: float = 5.0  # Seconds between file readiness checks
    ripping_stability_checks: int = 3  # Consecutive checks before file is ready
    ripping_file_ready_timeout: float = 600.0  # 10 minutes max wait for file
    ripping_stall_timeout: float = (
        120.0  # Seconds of no file growth before skipping track (0=disabled)
    )

    # Sentinel Drive Monitoring
    sentinel_poll_interval: float = 2.0  # Seconds between drive polls

    # Stale-job watchdog — auto-advances jobs that stop making progress.
    # Per-phase "no activity" ceilings (seconds). server_default carries the same
    # value so pre-existing databases get a sane timeout, not the _add_missing_columns
    # int fallback of 0 (which would fire the watchdog instantly).
    watchdog_enabled: bool = Field(default=True, sa_column_kwargs={"server_default": text("1")})
    watchdog_poll_seconds: int = Field(default=60, sa_column_kwargs={"server_default": text("60")})
    timeout_identifying_seconds: int = Field(
        default=600, sa_column_kwargs={"server_default": text("600")}
    )
    timeout_ripping_seconds: int = Field(
        default=1200, sa_column_kwargs={"server_default": text("1200")}
    )
    timeout_matching_seconds: int = Field(
        default=1800, sa_column_kwargs={"server_default": text("1800")}
    )
    timeout_organizing_seconds: int = Field(
        default=600, sa_column_kwargs={"server_default": text("600")}
    )

    # Staging Cleanup
    staging_cleanup_policy: str = (
        "on_success"  # "on_success" | "on_completion" | "manual" | "after_days"
    )
    staging_cleanup_days: int = 7  # Only used when policy is "after_days"

    # Extras handling
    extras_policy: str = "keep"  # "keep" | "skip" | "ask"

    # Naming conventions (Python format strings)
    naming_season_format: str = "Season {season:02d}"
    naming_episode_format: str = "{show} - S{season:02d}E{episode:02d}"
    naming_movie_format: str = "{title} ({year})"

    # Episode ordering (#200) — global default output ordering for TV libraries.
    # "aired" keeps TMDB's canonical numbering (== the fingerprint-network key,
    # == current behavior); per-show overrides live in show_ordering_preferences.
    # server_default 'aired' so pre-existing rows upgrade to a no-op default
    # rather than the _add_missing_columns String fallback of ''.
    episode_ordering_preference: str = Field(
        default="aired", sa_column_kwargs={"server_default": text("'aired'")}
    )

    # AI-powered disc identification
    ai_identification_enabled: bool = False  # Enable AI-powered title resolution
    ai_provider: str = "anthropic"  # "anthropic" | "openai" | "openrouter" | "gemini"
    ai_api_key: str = ""  # API key for the selected provider
    ai_episode_matching_enabled: bool = (
        False  # Enable LLM-based episode identification fallback (uses ai_provider/ai_api_key)
    )
    # Staging auto-import watcher
    staging_watch_enabled: bool = False  # Auto-import MKV folders from staging directory

    # Import watch folder (for ARM / external ripper ingestion)
    import_watch_path: str | None = Field(default=None)
    import_destination_mode: str = Field(
        default="library", sa_column_kwargs={"server_default": text("'library'")}
    )

    # TheDiscDB
    discdb_enabled: bool = True  # Enable TheDiscDB lookups for disc identification

    # TheDiscDB Contributions
    discdb_contributions_enabled: bool = False  # Opt-in to export disc data
    discdb_contribution_tier: int = 2  # 1=don't share, 2=auto, 3=full (with UPC/images)
    discdb_export_path: str = ""  # Override export directory (default: ~/.engram/discdb-exports)
    discdb_api_key: str = ""  # API key for TheDiscDB submission
    discdb_api_url: str = "https://thediscdb.com"  # TheDiscDB API base URL

    # OpenSubtitles.com REST API (for subtitle downloads)
    opensubtitles_api_key: str = ""
    opensubtitles_username: str = ""
    opensubtitles_password: str = ""

    # Network access
    # When True, the server binds 0.0.0.0 (reachable on the LAN) instead of localhost.
    # Read at startup before uvicorn binds; an explicit HOST env var takes precedence.
    allow_lan_access: bool = Field(default=False, sa_column_kwargs={"server_default": text("0")})

    # Onboarding
    setup_complete: bool = False  # Set True after user completes setup wizard

    # Auto-update preferences
    skipped_update_version: str | None = None  # e.g. "0.8.2" — user dismissed this version
    last_update_check: datetime | None = None  # informational timestamp

    # Chromaprint / fingerprint contributions
    fpcalc_path: str | None = Field(default=None)
    contribution_pseudonym: str | None = Field(default=None)
    enable_fingerprint_contributions: bool = Field(
        default=True, sa_column_kwargs={"server_default": text("1")}
    )
    # Stored override for the fingerprint network base origin. NULL/blank means
    # "use DEFAULT_FINGERPRINT_SERVER_URL" — the runtime resolves it at point of
    # use, so existing rows that predate this column (stored NULL) still work.
    # To stop contributing, untick enable_fingerprint_contributions; clearing
    # this field does NOT disable uploads.
    fingerprint_server_url: str | None = Field(default=DEFAULT_FINGERPRINT_SERVER_URL)
    fingerprint_disclosure_accepted: bool = Field(
        default=False, sa_column_kwargs={"server_default": text("0")}
    )
    fingerprint_disclosure_accepted_at: datetime | None = Field(default=None)

    # Phase 3: chromaprint identification (default OFF until the catalog is seeded).
    enable_fingerprint_identification: bool = Field(
        default=False, sa_column_kwargs={"server_default": text("0")}
    )
