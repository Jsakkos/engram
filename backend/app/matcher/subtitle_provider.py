import abc
import re
from pathlib import Path

from app.matcher.models import EpisodeInfo, SubtitleFile
from app.matcher.subtitle_utils import sanitize_filename


def parse_season_episode(filename: str) -> EpisodeInfo | None:
    """Parse season and episode from filename using regex."""
    # S01E01
    match = re.search(r"[Ss](\d{1,2})[Ee](\d{1,2})", filename)
    if match:
        return EpisodeInfo(
            series_name="",  # Placeholder
            season=int(match.group(1)),
            episode=int(match.group(2)),
        )
    # 1x01
    match = re.search(r"(\d{1,2})x(\d{1,2})", filename)
    if match:
        return EpisodeInfo(series_name="", season=int(match.group(1)), episode=int(match.group(2)))
    return None


class SubtitleProvider(abc.ABC):
    @abc.abstractmethod
    def get_subtitles(
        self,
        show_name: str,
        season: int,
        video_files: list[Path] = None,
        tmdb_id: int | None = None,
    ) -> list[SubtitleFile]:
        pass


class LocalSubtitleProvider(SubtitleProvider):
    """Provider that scans a local directory for subtitle files."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir / "data"

    def get_subtitles(
        self,
        show_name: str,
        season: int,
        video_files: list[Path] = None,
        tmdb_id: int | None = None,
    ) -> list[SubtitleFile]:
        """Get all subtitle files for a specific show and season."""
        show_dir = self.cache_dir / sanitize_filename(show_name)
        if not show_dir.exists():
            return []

        subtitles = []
        # Case insensitive glob
        files = list(show_dir.glob("*.srt")) + list(show_dir.glob("*.SRT"))

        for f in files:
            info = parse_season_episode(f.name)
            if info:
                if info.season == season:
                    info.series_name = show_name
                    subtitles.append(SubtitleFile(path=f, episode_info=info))

        # Deduplicate by path
        seen = set()
        unique_subs = []
        for sub in subtitles:
            if sub.path not in seen:
                seen.add(sub.path)
                unique_subs.append(sub)

        return unique_subs
