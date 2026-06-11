"""Unified Media Automator Backend."""

# Keep in sync with pyproject.toml [project].version. Surfaced as the
# User-Agent suffix on outbound API calls (OpenSubtitles, etc.), so a
# stale value here misidentifies the app to upstream services.
__version__ = "0.19.0"
