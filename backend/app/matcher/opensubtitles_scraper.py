"""OpenSubtitles.org subtitle scraper.

Scrapes subtitles from OpenSubtitles.org for TV shows.
Used as a fallback/alternative to Addic7ed.
"""

import io
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup
from loguru import logger


@dataclass
class SubtitleEntry:
    """Represents a subtitle entry from OpenSubtitles."""
    
    language: str
    show_name: str
    season: int
    episode: int
    downloads: int
    download_url: str
    filename: str = ""
    uploader: str = ""


class OpenSubtitlesClient:
    """Client for scraping subtitles from OpenSubtitles.org.
    
    Uses web scraping since the old API is deprecated.
    Implements rate limiting to be respectful to the server.
    """
    
    BASE_URL = "https://www.opensubtitles.org"
    
    # Rate limiting: max requests per minute
    REQUESTS_PER_MINUTE = 10
    MIN_REQUEST_INTERVAL = 60.0 / REQUESTS_PER_MINUTE
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": self.BASE_URL,
        })
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
    
    def search_subtitles(
        self,
        show_name: str,
        season: int | None = None,
        episode: int | None = None,
        language: str = "eng"
    ) -> list[SubtitleEntry]:
        """Search for subtitles on OpenSubtitles.
        
        Args:
            show_name: Name of the TV show
            season: Optional season number to filter
            episode: Optional episode number to filter  
            language: Language code (default: eng for English)
            
        Returns:
            List of SubtitleEntry objects sorted by download count
        """
        # Build search URL
        # Format: /en/search/sublanguageid-{lang}/searchonlytvseries-on/moviename-{name}
        # With season/episode: /season-{s}/episode-{e}
        encoded_name = quote(show_name.lower().replace(" ", "%20"))
        
        url_parts = [
            f"{self.BASE_URL}/en/search",
            f"sublanguageid-{language}",
            "searchonlytvseries-on",
            f"moviename-{encoded_name}"
        ]
        
        if season is not None:
            url_parts.append(f"season-{season}")
        if episode is not None:
            url_parts.append(f"episode-{episode}")
        
        search_url = "/".join(url_parts)
        logger.info(f"Searching: {search_url}")
        
        try:
            response = self._get(search_url)
        except requests.HTTPError as e:
            logger.warning(f"Search failed: {e}")
            return []
        
        soup = BeautifulSoup(response.text, "html.parser")
        return self._parse_search_results(soup, language)
    
    def _parse_search_results(self, soup: BeautifulSoup, language: str) -> list[SubtitleEntry]:
        """Parse search results from OpenSubtitles page.
        
        Args:
            soup: BeautifulSoup object of the search page
            language: Language code
            
        Returns:
            List of SubtitleEntry objects
        """
        subtitles = []
        
        # Find the results table - usually has id="search_results"
        results_table = soup.find("table", id="search_results")
        if not results_table:
            # Try alternate selectors
            results_table = soup.find("table", class_="results")
        
        if not results_table:
            logger.debug("No results table found")
            return subtitles
        
        # Find all result rows
        rows = results_table.find_all("tr")
        
        for row in rows:
            try:
                subtitle = self._parse_result_row(row, language)
                if subtitle:
                    subtitles.append(subtitle)
            except Exception as e:
                logger.debug(f"Error parsing row: {e}")
                continue
        
        # Sort by downloads (highest first)
        subtitles.sort(key=lambda x: x.downloads, reverse=True)
        
        logger.info(f"Found {len(subtitles)} subtitles")
        return subtitles
    
    def _parse_result_row(self, row, language: str) -> SubtitleEntry | None:
        """Parse a single result row.
        
        Args:
            row: BeautifulSoup row element
            language: Language code
            
        Returns:
            SubtitleEntry or None if not a valid result
        """
        # Skip header rows
        if row.find("th"):
            return None
        
        cells = row.find_all("td")
        if len(cells) < 3:
            return None
        
        # Look for download link
        download_link = row.find("a", href=re.compile(r"/en/subtitleserve/sub/\d+"))
        if not download_link:
            # Try alternate download link pattern
            download_link = row.find("a", href=re.compile(r"/subtitles/\d+"))
        
        if not download_link:
            return None
        
        download_url = urljoin(self.BASE_URL, download_link.get("href", ""))
        
        # Extract show name and episode info from the row
        # Usually in a link with the movie/show title
        title_link = row.find("a", href=re.compile(r"/en/subtitles/"))
        show_name = ""
        if title_link:
            show_name = title_link.get_text(strip=True)
        
        # Try to extract season/episode from text
        season = 0
        episode = 0
        text = row.get_text()
        se_match = re.search(r"S(\d+)E(\d+)", text, re.IGNORECASE)
        if se_match:
            season = int(se_match.group(1))
            episode = int(se_match.group(2))
        else:
            # Try "Season X Episode Y" pattern
            se_match = re.search(r"Season\s*(\d+).*Episode\s*(\d+)", text, re.IGNORECASE)
            if se_match:
                season = int(se_match.group(1))
                episode = int(se_match.group(2))
        
        # Extract download count
        downloads = 0
        # Look for download count in title attribute or text
        for cell in cells:
            cell_text = cell.get_text()
            # Pattern: number followed by "x" or alone
            dl_match = re.search(r"(\d+)x?\s*downloads?", cell_text, re.IGNORECASE)
            if dl_match:
                downloads = int(dl_match.group(1))
                break
            # Or just a number that looks like a count
            title = cell.get("title", "")
            if "download" in title.lower():
                num_match = re.search(r"(\d+)", title)
                if num_match:
                    downloads = int(num_match.group(1))
                    break
        
        return SubtitleEntry(
            language=language,
            show_name=show_name,
            season=season,
            episode=episode,
            downloads=downloads,
            download_url=download_url,
        )
    
    def get_episode_subtitles(
        self,
        show_name: str,
        season: int,
        episode: int,
        language: str = "eng"
    ) -> list[SubtitleEntry]:
        """Get subtitles for a specific episode.
        
        Args:
            show_name: Name of the TV show
            season: Season number
            episode: Episode number
            language: Language code (default: eng for English)
            
        Returns:
            List of SubtitleEntry objects sorted by downloads
        """
        return self.search_subtitles(show_name, season, episode, language)
    
    def download_subtitle(
        self,
        subtitle: SubtitleEntry,
        save_path: Path
    ) -> Path | None:
        """Download a subtitle file.
        
        Args:
            subtitle: SubtitleEntry to download
            save_path: Path where to save the .srt file
            
        Returns:
            Path to saved file, or None if download failed
        """
        logger.info(f"Downloading from: {subtitle.download_url}")
        
        try:
            response = self._get(subtitle.download_url)
            
            # OpenSubtitles often returns a zip file
            content_type = response.headers.get("content-type", "")
            
            save_path.parent.mkdir(parents=True, exist_ok=True)
            
            if "zip" in content_type or response.content[:2] == b"PK":
                # It's a zip file - extract the .srt
                with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
                    for name in zf.namelist():
                        if name.endswith(".srt"):
                            srt_content = zf.read(name)
                            save_path.write_bytes(srt_content)
                            logger.info(f"Extracted and saved: {save_path}")
                            return save_path
                logger.warning("No .srt file found in zip")
                return None
            else:
                # Direct .srt content
                save_path.write_bytes(response.content)
                logger.info(f"Saved: {save_path}")
                return save_path
                
        except Exception as e:
            logger.error(f"Download failed: {e}")
            return None
    
    def get_best_subtitle(
        self,
        show_name: str,
        season: int,
        episode: int,
        language: str = "eng"
    ) -> SubtitleEntry | None:
        """Get the best (most downloaded) subtitle for an episode.
        
        Args:
            show_name: Name of the TV show
            season: Season number
            episode: Episode number
            language: Language code (default: eng)
            
        Returns:
            Best SubtitleEntry or None if not found
        """
        subtitles = self.get_episode_subtitles(show_name, season, episode, language)
        
        if not subtitles:
            return None
        
        return subtitles[0]


def get_subtitles_opensubtitles(
    show_name: str,
    seasons: set[int],
    cache_dir: Path,
    max_retries: int = 3
) -> dict[str, Path]:
    """Download subtitles for a TV show from OpenSubtitles.
    
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
    
    client = OpenSubtitlesClient()
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
        episode_count = fetch_season_details(show_id, season)
        if episode_count == 0:
            logger.warning(f"No episodes found for {show_name} Season {season}")
            continue
        
        logger.info(f"Downloading subtitles for {show_name} Season {season} ({episode_count} episodes)")
        
        for episode in range(1, episode_count + 1):
            episode_code = f"S{season:02d}E{episode:02d}"
            srt_path = series_cache_dir / f"{safe_show_name} - {episode_code}.srt"
            
            if srt_path.exists():
                logger.debug(f"Subtitle already exists: {srt_path.name}")
                downloaded[episode_code] = srt_path
                continue
            
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
                    logger.warning(f"Attempt {attempt + 1}/{max_retries} failed: {e}")
                    if attempt < max_retries - 1:
                        time.sleep(2 ** attempt)
    
    logger.info(f"Downloaded {len(downloaded)} subtitles for {show_name}")
    return downloaded
