"""Application configuration stored in SQLite.

This model stores user-configurable settings that persist across restarts
and can be modified via the UI.
"""

from sqlmodel import Field, SQLModel


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

    # Feature Flags
    transcoding_enabled: bool = False

    # Episode Matcher Settings
    subtitles_cache_path: str = "~/.engram/cache"
    matcher_min_confidence: float = 0.6

    # OpenSubtitles API (for downloading reference subtitles)
    opensubtitles_username: str = ""
    opensubtitles_password: str = ""
    opensubtitles_api_key: str = ""

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

    # Sentinel Drive Monitoring
    sentinel_poll_interval: float = 2.0  # Seconds between drive polls

    # Onboarding
    setup_complete: bool = False  # Set True after user completes setup wizard
