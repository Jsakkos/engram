"""Core pytest fixtures for UMA subtitle workflow tests."""

from unittest.mock import Mock

import pytest


@pytest.fixture
def temp_cache_dir(tmp_path):
    """Isolated cache directory for each test."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "data").mkdir()
    return cache_dir


@pytest.fixture
def populated_cache_dir(temp_cache_dir):
    """Cache with sample subtitle files for cache hit tests."""
    show_dir = temp_cache_dir / "data" / "Breaking_Bad"
    show_dir.mkdir(parents=True)
    for ep in range(1, 4):
        subtitle_file = show_dir / f"Breaking_Bad - S01E{ep:02d}.srt"
        subtitle_file.write_text(
            f"1\n00:00:00,000 --> 00:00:02,000\nSubtitle content for episode {ep}\n"
        )
    return temp_cache_dir


@pytest.fixture
def mock_tmdb_responses():
    """Pre-built TMDB API response data."""
    return {
        "arrested_development": {"results": [{"id": 4589, "name": "Arrested Development"}]},
        "the_office": {"results": [{"id": 2316, "name": "The Office"}]},
        "season_details": {
            "season_number": 1,
            "episodes": [
                {"episode_number": 1, "name": "Pilot"},
                {"episode_number": 2, "name": "Top Banana"},
                {"episode_number": 3, "name": "Bringing Up Buster"},
            ],
        },
        "empty": {"results": []},
    }


@pytest.fixture
def mock_subtitle():
    """Mock subtitle object for testing."""
    subtitle = Mock()
    subtitle.language = "English"
    subtitle.version = "WEB"
    subtitle.download_url = "http://example.com/subtitle.srt"
    subtitle.downloads = 1000
    return subtitle


@pytest.fixture
def mock_addic7ed_html():
    """Sample Addic7ed HTML page for parsing tests."""
    return """
    <html>
    <body>
        <table class="tabel95">
            <tr>
                <td class="language">English</td>
                <td>WEB</td>
                <td><a href="/subtitle/123">Download</a></td>
                <td>1000 Downloads</td>
            </tr>
        </table>
    </body>
    </html>
    """
