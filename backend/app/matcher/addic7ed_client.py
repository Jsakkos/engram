"""Addic7ed.com subtitle scraper.

Scrapes subtitles from Addic7ed.com for TV shows.
Replaces OpenSubtitles as the default subtitle provider.
"""

import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup
from loguru import logger


@dataclass
class SubtitleEntry:
    """Represents a subtitle entry from Addic7ed."""

    language: str
    version: str  # Release group/version info
    downloads: int
    download_url: str
    uploader: str = ""
    is_hearing_impaired: bool = False


# Known show name aliases: maps local/TMDB names to Addic7ed names
# Add entries here when shows don't match
SHOW_NAME_ALIASES = {
    # Star Trek shows - Addic7ed uses " - " separator
    "Star Trek - The Next Generation": "Star Trek - The Next Generation",  # Already correct
    "Star Trek The Next Generation": "Star Trek - The Next Generation",
    "Star Trek: The Next Generation": "Star Trek - The Next Generation",
    "Star Trek: TNG": "Star Trek - The Next Generation",
    # Dexter shows - use colon
    "Dexter - New Blood (2021)": "Dexter: New Blood",
    "Dexter New Blood": "Dexter: New Blood",
    # The Office variants
    "The Office": "The Office (US)",
    "The Office US": "The Office (US)",
    # Rings of Power - try without "The Lord of the Rings"
    "The Lord of the Rings The Rings of Power": "The Rings of Power",
    "Rings of Power": "The Rings of Power",
    # Stargate - use colon
    "Stargate Atlantis": "Stargate: Atlantis",
}


def normalize_show_name(show_name: str) -> str:
    """Normalize show name for Addic7ed URL.

    Checks aliases first, then applies transformations.
    """
    # Check direct alias match
    if show_name in SHOW_NAME_ALIASES:
        return SHOW_NAME_ALIASES[show_name]

    # Try case-insensitive match
    for key, value in SHOW_NAME_ALIASES.items():
        if key.lower() == show_name.lower():
            return value

    # Remove common suffixes like year in parentheses
    normalized = re.sub(r"\s*\(\d{4}\)\s*$", "", show_name)

    # Replace " - " with ": " (common pattern)
    normalized = normalized.replace(" - ", ": ")

    return normalized


class Addic7edClient:
    """Client for scraping subtitles from Addic7ed.com.

    Uses web scraping since Addic7ed doesn't have a public API.
    Implements rate limiting to be respectful to the server.
    """

    BASE_URL = "https://www.addic7ed.com"

    # Rate limiting: max requests per minute
    REQUESTS_PER_MINUTE = 20
    MIN_REQUEST_INTERVAL = 60.0 / REQUESTS_PER_MINUTE

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Referer": self.BASE_URL,
            }
        )
        self._last_request_time = 0.0

    def _rate_limit(self):
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.MIN_REQUEST_INTERVAL:
            sleep_time = self.MIN_REQUEST_INTERVAL - elapsed
            logger.debug(f"Rate limiting: sleeping {sleep_time:.2f}s")
            time.sleep(sleep_time)
        self._last_request_time = time.time()

    def _get(self, url: str, **kwargs) -> requests.Response:
        """Make a rate-limited GET request."""
        self._rate_limit()
        logger.debug(f"GET {url}")
        response = self.session.get(url, timeout=30, **kwargs)
        response.raise_for_status()
        return response

    def _sanitize_show_name_for_url(self, show_name: str) -> str:
        """Sanitize show name for use in Addic7ed URLs.

        Addic7ed uses URL-encoded show names with spaces preserved.
        """
        # Replace special characters but keep spaces for URL encoding
        sanitized = re.sub(r"[^\w\s\-']", "", show_name)
        return quote(sanitized, safe="")

    def search_show(self, show_name: str) -> list[dict]:
        """Search for a TV show by name.

        Args:
            show_name: Name of the show to search for

        Returns:
            List of matching shows with 'name', 'id', and 'url' keys
        """
        # Addic7ed doesn't have a search API, but we can try direct URL access
        # Most shows follow the pattern /show/{id} or /serie/{Show_Name}/...
        # For now, we'll try the direct serie URL approach
        logger.info(f"Searching for show: {show_name}")

        # We can also try to find the show from the shows.php page
        # but that requires login. Let's just try the direct approach.
        return [{"name": show_name, "url_name": show_name}]

    def get_episode_subtitles(
        self, show_name: str, season: int, episode: int, language: str = "English"
    ) -> list[SubtitleEntry]:
        """Get available subtitles for a specific episode.

        Args:
            show_name: Name of the TV show
            season: Season number
            episode: Episode number
            language: Language to filter by (default: English)

        Returns:
            List of SubtitleEntry objects sorted by download count (highest first)
        """
        # Normalize show name for Addic7ed (handle aliases and formatting)
        normalized_name = normalize_show_name(show_name)

        # Build the episode URL - Addic7ed uses format:
        # /serie/{Show_Name}/{season}/{episode}/{filter}
        # where filter can be a number (1 for English) or episode title
        url_show_name = self._sanitize_show_name_for_url(normalized_name)

        # Try with language filter - "1" typically means sorted/English
        episode_url = f"{self.BASE_URL}/serie/{url_show_name}/{season}/{episode}/1"

        logger.info(f"Fetching subtitles from: {episode_url}")

        try:
            response = self._get(episode_url)
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                logger.warning(f"Episode page not found: {episode_url}")
                return []
            raise

        soup = BeautifulSoup(response.text, "html.parser")
        # Use episode URL as referer for downloads (required by Addic7ed)
        referer = f"{self.BASE_URL}/serie/{url_show_name}/{season}/{episode}/addic7ed"
        subtitles = self._parse_subtitle_table(soup, language, referer)

        # Sort by download count (highest first)
        subtitles.sort(key=lambda x: x.downloads, reverse=True)

        logger.info(
            f"Found {len(subtitles)} {language} subtitles for {show_name} S{season:02d}E{episode:02d}"
        )
        return subtitles

    def _parse_subtitle_table(
        self, soup: BeautifulSoup, language: str, referer: str
    ) -> list[SubtitleEntry]:
        """Parse the subtitle tables from an episode page.

        Based on pogman-code/addic7ed parser - uses tabel95 class tables.

        Args:
            soup: BeautifulSoup object of the episode page
            language: Language to filter by
            referer: Referer URL for downloads

        Returns:
            List of SubtitleEntry objects
        """
        subtitles = []

        # Find all subtitle tables - they use class "tabel95"
        # Each subtitle version is in a nested tabel95 table
        tables = soup.find_all("table", attrs={"class": "tabel95"})

        for table in tables:
            # Look for nested table which contains the actual subtitle info
            inner_table = table.find("table", attrs={"class": "tabel95"})
            if not inner_table:
                continue

            try:
                subtitle = self._parse_single_subtitle(inner_table, language, referer)
                if subtitle:
                    subtitles.append(subtitle)
            except Exception as e:
                logger.debug(f"Error parsing subtitle entry: {e}")
                continue

        return subtitles

    def _parse_single_subtitle(
        self, table: BeautifulSoup, language: str, referer: str
    ) -> SubtitleEntry | None:
        """Parse a single subtitle entry from its table.

        Args:
            table: BeautifulSoup table element containing subtitle info
            language: Language to filter by
            referer: Referer URL for downloads

        Returns:
            SubtitleEntry or None if not matching language
        """
        # Extract language from the table
        lang_cell = table.find("td", attrs={"class": "language"})
        if not lang_cell:
            return None

        sub_language = lang_cell.get_text(strip=True)
        if language.lower() not in sub_language.lower():
            return None

        # Extract version/release from NewsTitle class
        version = "Unknown"
        news_title = table.find("td", attrs={"class": "NewsTitle"})
        if news_title:
            version_text = news_title.get_text(strip=True)
            # Format is like "Version KILLERS, 0.00 MBs"
            if "," in version_text:
                version = version_text.split(",")[0].replace("Version ", "").strip()
            else:
                version = version_text.replace("Version ", "").strip()

        # Extract download count from newsDate class
        downloads = 0
        news_dates = table.find_all("td", attrs={"class": "newsDate"})
        for td in news_dates:
            text = td.get_text(strip=True).replace("Â", "")  # Handle encoding issue
            match = re.search(r"(\d+)\s*Downloads?", text, re.IGNORECASE)
            if match:
                downloads = int(match.group(1))
                break

        # Extract download link from buttonDownload class or face-button class
        download_link = table.find_all("a", attrs={"class": "buttonDownload"})
        if not download_link:
            download_link = table.find_all("a", attrs={"class": "face-button"})

        if not download_link:
            return None

        # Get the last buttonDownload link (usually the download one, not the updated one)
        href = download_link[-1].get("href", "")
        if not href:
            return None

        download_url = urljoin(self.BASE_URL, href)

        # Extract uploader if available
        uploader = ""
        user_link = table.find("a", href=re.compile(r"/user/\d+"))
        if user_link:
            uploader = user_link.get_text(strip=True)

        return SubtitleEntry(
            language=sub_language,
            version=version,
            downloads=downloads,
            download_url=download_url,
            uploader=uploader,
        )

    def _extract_download_count(self, parent_element) -> int:
        """Extract download count from parent element (legacy method)."""
        if parent_element is None:
            return 0

        text = parent_element.get_text()
        match = re.search(r"(\d+)\s*(?:downloads?|times?)", text, re.IGNORECASE)
        if match:
            return int(match.group(1))

        return 0

    def _extract_version_info(self, parent_element, link) -> str:
        """Extract version/release group info (legacy method)."""
        if parent_element is None:
            return "Unknown"

        text = parent_element.get_text()
        groups = [
            "LOL",
            "DIMENSION",
            "KILLERS",
            "FoV",
            "IMMERSE",
            "AVS",
            "2HD",
            "ASAP",
            "EXCELLENCE",
            "FLEET",
            "TLA",
            "PROPER",
            "REPACK",
        ]

        for group in groups:
            if group in text.upper():
                return group

        return "Original"

    def _extract_uploader(self, parent_element) -> str:
        """Extract uploader username (legacy method)."""
        if parent_element is None:
            return ""

        user_link = parent_element.find("a", href=re.compile(r"/user/\d+"))
        if user_link:
            return user_link.get_text(strip=True)

        return ""

    def download_subtitle(self, subtitle: SubtitleEntry, save_path: Path) -> Path | None:
        """Download a subtitle file.

        Args:
            subtitle: SubtitleEntry to download
            save_path: Path where to save the .srt file

        Returns:
            Path to saved file, or None if download failed
        """
        logger.info(f"Downloading subtitle from: {subtitle.download_url}")

        try:
            response = self._get(subtitle.download_url)

            # Check if we got an actual subtitle file
            content_type = response.headers.get("content-type", "")
            if "text" not in content_type and "application" not in content_type:
                logger.warning(f"Unexpected content type: {content_type}")

            # Ensure parent directory exists
            save_path.parent.mkdir(parents=True, exist_ok=True)

            # Save the file
            save_path.write_bytes(response.content)
            logger.info(f"Saved subtitle to: {save_path}")

            return save_path

        except Exception as e:
            logger.error(f"Failed to download subtitle: {e}")
            return None

    def get_best_subtitle(
        self, show_name: str, season: int, episode: int, language: str = "English"
    ) -> SubtitleEntry | None:
        """Get the best (most downloaded) subtitle for an episode.

        Args:
            show_name: Name of the TV show
            season: Season number
            episode: Episode number
            language: Language preference (default: English)

        Returns:
            Best SubtitleEntry or None if not found
        """
        subtitles = self.get_episode_subtitles(show_name, season, episode, language)

        if not subtitles:
            return None

        # Return the one with most downloads (list is already sorted)
        return subtitles[0]


def get_subtitles_addic7ed(
    show_name: str, seasons: set[int], cache_dir: Path, max_retries: int = 3
) -> dict[str, Path]:
    """Download subtitles for a TV show from Addic7ed.

    Args:
        show_name: Name of the TV show
        seasons: Set of season numbers to download
        cache_dir: Directory to cache downloaded subtitles
        max_retries: Number of retry attempts per episode

    Returns:
        Dict mapping "S{season:02d}E{episode:02d}" to subtitle file paths
    """
    from app.matcher.subtitle_utils import sanitize_filename
    from app.matcher.tmdb_client import fetch_season_details, fetch_show_id

    client = Addic7edClient()
    downloaded = {}

    # Get TMDB show ID to fetch episode counts
    show_id = fetch_show_id(show_name)
    if not show_id:
        logger.error(f"Could not find show '{show_name}' on TMDB")
        return downloaded

    # Sanitize show name for filenames
    safe_show_name = sanitize_filename(show_name)
    series_cache_dir = cache_dir / "data" / safe_show_name
    series_cache_dir.mkdir(parents=True, exist_ok=True)

    for season in sorted(seasons):
        # Get episode count from TMDB
        episode_count = fetch_season_details(show_id, season)
        if episode_count == 0:
            logger.warning(f"No episodes found for {show_name} Season {season}")
            continue

        logger.info(
            f"Downloading subtitles for {show_name} Season {season} ({episode_count} episodes)"
        )

        for episode in range(1, episode_count + 1):
            episode_code = f"S{season:02d}E{episode:02d}"
            srt_path = series_cache_dir / f"{safe_show_name} - {episode_code}.srt"

            # Skip if already exists
            if srt_path.exists():
                logger.debug(f"Subtitle already exists: {srt_path.name}")
                downloaded[episode_code] = srt_path
                continue

            # Try to download
            for attempt in range(max_retries):
                try:
                    best_sub = client.get_best_subtitle(show_name, season, episode)

                    if best_sub is None:
                        logger.warning(f"No subtitles found for {show_name} {episode_code}")
                        break

                    result = client.download_subtitle(best_sub, srt_path)
                    if result:
                        downloaded[episode_code] = result
                        logger.info(f"Downloaded: {episode_code} ({best_sub.downloads} downloads)")
                        break

                except Exception as e:
                    logger.warning(
                        f"Attempt {attempt + 1}/{max_retries} failed for {episode_code}: {e}"
                    )
                    if attempt < max_retries - 1:
                        time.sleep(2**attempt)  # Exponential backoff

    logger.info(f"Downloaded {len(downloaded)} subtitles for {show_name}")
    return downloaded
