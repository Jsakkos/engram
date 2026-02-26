from pathlib import Path

from pydantic import BaseModel


class EpisodeInfo(BaseModel):
    """Data model for episode information."""

    series_name: str
    season: int
    episode: int
    title: str | None = None

    @property
    def s_e_format(self) -> str:
        return f"S{self.season:02d}E{self.episode:02d}"


class SubtitleFile(BaseModel):
    """Data model for a subtitle file."""

    path: Path
    language: str = "en"
    episode_info: EpisodeInfo | None = None
    content: str | None = None  # Loaded content (optional)


class AudioChunk(BaseModel):
    """Data model for an extracted audio chunk."""

    path: Path
    start_time: float
    duration: float


class MatchResult(BaseModel):
    """Data model for a matching result."""

    episode_info: EpisodeInfo
    confidence: float
    matched_file: Path
    matched_time: float
    chunk_index: int = 0
    model_name: str
    match_details: dict | None = None
    original_file: Path | None = None  # Store original filename for display


class FailedMatch(BaseModel):
    """Data model for a failed match."""

    original_file: Path
    reason: str
    confidence: float = 0.0
    series_name: str | None = None
    season: int | None = None


class MatchCandidate(BaseModel):
    """A candidate match from a single chunk."""

    episode_info: EpisodeInfo
    confidence: float
    reference_file: Path
