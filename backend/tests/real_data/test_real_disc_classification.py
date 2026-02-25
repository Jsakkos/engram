"""Real-data tests for disc classification.

Feeds real MKV file durations through the DiscAnalyst to verify
correct TV/Movie classification on actual discs.

These tests require actual ripped files on disk — skipped in CI.
Run locally with:
    uv run pytest tests/real_data/ -v -m real_data
"""

from pathlib import Path

import pytest

from app.core.analyst import DiscAnalyst, TitleInfo
from app.models.app_config import AppConfig
from app.models.disc_job import ContentType


def _scan_mkv_durations(staging_path: Path) -> list[TitleInfo]:
    """Scan MKV files in a staging directory and extract durations.

    Uses ffprobe if available, otherwise uses file size as a rough proxy.
    """
    import subprocess

    titles = []
    mkv_files = sorted(staging_path.glob("*.mkv"))

    for i, mkv in enumerate(mkv_files):
        duration = 0
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "csv=p=0",
                    str(mkv),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            duration = int(float(result.stdout.strip()))
        except (subprocess.SubprocessError, ValueError, FileNotFoundError):
            # Fallback: estimate duration from file size (~5 Mbps average)
            size_bytes = mkv.stat().st_size
            duration = int(size_bytes / (5 * 1024 * 1024 / 8))

        titles.append(
            TitleInfo(
                index=i,
                duration_seconds=duration,
                size_bytes=mkv.stat().st_size,
                chapter_count=0,
                name=mkv.name,
            )
        )

    return titles


@pytest.mark.real_data
class TestRealDiscClassification:
    """Test classification with real disc rips."""

    @pytest.mark.parametrize(
        "real_staging_path",
        ["C:/Video/ARRESTED_Development_S1D1"],
        indirect=True,
    )
    def test_classify_real_tv_disc(self, real_staging_path):
        """Real MKV durations → correct TV classification."""
        config = AppConfig()
        analyst = DiscAnalyst(config=config)
        titles = _scan_mkv_durations(real_staging_path)

        assert len(titles) > 0, f"No MKV files in {real_staging_path}"

        result = analyst.analyze(titles, volume_label=real_staging_path.name)
        assert result.content_type == ContentType.TV
        assert result.confidence >= 0.7

    @pytest.mark.parametrize(
        "real_staging_path",
        ["C:/Video/INCEPTION_2010"],
        indirect=True,
    )
    def test_classify_real_movie_disc(self, real_staging_path):
        """Real MKV durations → correct Movie classification."""
        config = AppConfig()
        analyst = DiscAnalyst(config=config)
        titles = _scan_mkv_durations(real_staging_path)

        assert len(titles) > 0, f"No MKV files in {real_staging_path}"

        result = analyst.analyze(titles, volume_label=real_staging_path.name)
        assert result.content_type == ContentType.MOVIE
        assert result.confidence >= 0.7

    @pytest.mark.parametrize(
        "real_staging_path",
        ["C:/Video/STAR TREK PICARD S1D3"],
        indirect=True,
    )
    def test_classify_real_picard_disc(self, real_staging_path):
        """Real Picard disc → TV classification with Play All detected."""
        config = AppConfig()
        analyst = DiscAnalyst(config=config)
        titles = _scan_mkv_durations(real_staging_path)

        assert len(titles) > 0, f"No MKV files in {real_staging_path}"

        result = analyst.analyze(titles, volume_label=real_staging_path.name)
        assert result.content_type == ContentType.TV
        assert result.detected_season == 1
        assert len(result.play_all_title_indices) > 0

    @pytest.mark.parametrize(
        "real_staging_path",
        ["C:/Video/THE TERMINATOR"],
        indirect=True,
    )
    def test_classify_real_terminator_disc(self, real_staging_path):
        """Real Terminator disc → Movie with ambiguous features needing review."""
        config = AppConfig()
        analyst = DiscAnalyst(config=config)
        titles = _scan_mkv_durations(real_staging_path)

        assert len(titles) > 0, f"No MKV files in {real_staging_path}"

        result = analyst.analyze(titles, volume_label=real_staging_path.name)
        assert result.content_type == ContentType.MOVIE
        assert result.needs_review is True

    @pytest.mark.parametrize(
        "real_staging_path",
        ["C:/Video/LOGICAL_VOLUME_ID"],
        indirect=True,
    )
    def test_classify_real_generic_label_disc(self, real_staging_path):
        """Generic label disc → Movie, detected_name is None."""
        config = AppConfig()
        analyst = DiscAnalyst(config=config)
        titles = _scan_mkv_durations(real_staging_path)

        assert len(titles) > 0, f"No MKV files in {real_staging_path}"

        result = analyst.analyze(titles, volume_label=real_staging_path.name)
        assert result.content_type == ContentType.MOVIE
        assert result.detected_name is None
