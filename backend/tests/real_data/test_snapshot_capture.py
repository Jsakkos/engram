"""Utility to capture disc metadata snapshots for pipeline tests.

Supports two capture modes:
  Mode A: ffprobe on already-ripped MKV folders
  Mode B: makemkvcon info scan on physical disc (no ripping)

Run locally to generate JSON fixtures:
    # Mode A: from ripped folders
    uv run pytest tests/real_data/test_snapshot_capture.py -v -m real_data -k "capture_from_folder" -s

    # Mode B: from physical disc in drive E:
    uv run pytest tests/real_data/test_snapshot_capture.py -v -m real_data -k "capture_from_disc" -s

Output goes to tests/fixtures/disc_snapshots/*.json
Manually annotate expected_* fields after capture.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

SNAPSHOT_DIR = Path(__file__).parent.parent / "fixtures" / "disc_snapshots"


def _probe_duration(mkv_path: Path) -> int:
    """Get duration in seconds via ffprobe."""
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
                str(mkv_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return int(float(result.stdout.strip()))
    except (subprocess.SubprocessError, ValueError, FileNotFoundError):
        return 0


def _probe_resolution(mkv_path: Path) -> str:
    """Get video resolution via ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=height",
                "-of",
                "csv=p=0",
                str(mkv_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        height = int(result.stdout.strip().split("\n")[0])
        if height >= 2160:
            return "4K"
        if height >= 1080:
            return "1080p"
        if height >= 720:
            return "720p"
        return "480p"
    except (subprocess.SubprocessError, ValueError, FileNotFoundError, IndexError):
        return "unknown"


def _probe_chapters(mkv_path: Path) -> int:
    """Count chapters via ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-show_chapters",
                "-of",
                "json",
                str(mkv_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        data = json.loads(result.stdout)
        return len(data.get("chapters", []))
    except (subprocess.SubprocessError, ValueError, FileNotFoundError, json.JSONDecodeError):
        return 0


def _extract_title_index(filename: str) -> int:
    """Extract title index from MakeMKV filename (e.g., B1_t03.mkv -> 3)."""
    idx_match = re.search(r"t(\d+)\.mkv$", filename, re.IGNORECASE)
    if idx_match:
        return int(idx_match.group(1))
    return -1


def _build_skeleton_snapshot(volume_label: str, staging_path: str, tracks: list) -> dict:
    """Build a skeleton snapshot dict with expected fields left blank."""
    return {
        "volume_label": volume_label,
        "staging_path": staging_path,
        "tracks": tracks,
        "expected_content_type": "",
        "expected_detected_name": None,
        "expected_season": None,
        "expected_play_all_indices": [],
        "expected_needs_review": False,
        "expected_review_reason": None,
        "tmdb_signal": None,
        "notes": "AUTO-GENERATED — fill in expected_* fields manually",
    }


def _safe_filename(label: str) -> str:
    """Convert a volume label to a safe filename."""
    return re.sub(r"[^\w]", "_", label).strip("_").lower()


@pytest.mark.real_data
class TestSnapshotCaptureFromFolder:
    """Mode A: Capture disc metadata from ripped MKV folders via ffprobe."""

    @pytest.mark.parametrize(
        "staging_path,volume_label",
        [
            ("C:/Video/LOGICAL_VOLUME_ID", "LOGICAL_VOLUME_ID"),
            ("C:/Video/THE TERMINATOR", "THE TERMINATOR"),
            ("C:/Video/STAR TREK PICARD S1D3", "STAR TREK PICARD S1D3"),
            ("C:/Video/ARRESTED_Development_S1D1", "ARRESTED_Development_S1D1"),
        ],
    )
    def test_capture_from_folder(self, staging_path, volume_label):
        """Scan ripped MKV dir and emit a JSON snapshot."""
        path = Path(staging_path)
        if not path.exists():
            pytest.skip(f"Not available: {path}")

        mkv_files = sorted(path.glob("*.mkv"))
        assert mkv_files, f"No MKV files in {path}"

        tracks = []
        for mkv in mkv_files:
            index = _extract_title_index(mkv.name)
            if index == -1:
                index = len(tracks)

            duration = _probe_duration(mkv)
            print(f"  {mkv.name}: {duration}s, {mkv.stat().st_size} bytes")

            tracks.append(
                {
                    "index": index,
                    "filename": mkv.name,
                    "duration_seconds": duration,
                    "size_bytes": mkv.stat().st_size,
                    "chapter_count": _probe_chapters(mkv),
                    "video_resolution": _probe_resolution(mkv),
                    "expected_episode": None,
                }
            )

        snapshot = _build_skeleton_snapshot(volume_label, staging_path, tracks)

        safe_name = _safe_filename(volume_label)
        out_path = SNAPSHOT_DIR / f"{safe_name}.json"
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(snapshot, indent=2))
        print(f"\nSnapshot written to: {out_path}")
        print(f"Tracks captured: {len(tracks)}")


@pytest.mark.real_data
class TestSnapshotCaptureFromDisc:
    """Mode B: Capture disc metadata from physical disc via makemkvcon scan."""

    @pytest.mark.parametrize("drive", ["E:"])
    def test_capture_from_disc(self, drive):
        """Scan physical disc with makemkvcon and emit a JSON snapshot.

        Insert a disc before running. The scan takes ~30 seconds, no ripping occurs.
        """
        from app.core.extractor import MakeMKVExtractor

        # Find makemkvcon
        from app.services.config_service import get_config_sync

        try:
            config = get_config_sync()
            makemkv_path = config.makemkv_path
        except Exception:
            makemkv_path = "makemkvcon64.exe"

        drive_spec = f"dev:{drive}"
        cmd = [str(makemkv_path), "-r", "info", drive_spec]

        print(f"\nScanning disc in {drive} ...")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except FileNotFoundError:
            pytest.skip("makemkvcon not found on PATH")
        except subprocess.TimeoutExpired:
            pytest.skip("makemkvcon scan timed out (no disc?)")

        if result.returncode != 0:
            pytest.skip(f"makemkvcon scan failed: {result.stderr[:200]}")

        # Parse TINFO lines using Extractor's parser
        extractor = MakeMKVExtractor(makemkv_path=makemkv_path)
        titles = extractor._parse_disc_info(result.stdout or "")
        assert titles, "No titles found on disc"

        # Parse CINFO lines for volume label
        # CINFO:1,1,0,"disc_type"  CINFO:2,1,0,"name"
        volume_label = ""
        for line in (result.stdout or "").split("\n"):
            line = line.strip()
            if line.startswith("CINFO:"):
                match = re.match(r'CINFO:(\d+),\d+,\d+,"(.*)"', line)
                if match:
                    attr_id = int(match.group(1))
                    if attr_id == 2:  # Disc name
                        volume_label = match.group(2)

        if not volume_label:
            volume_label = f"DISC_{drive.replace(':', '')}"
            print(f"Warning: No volume label found, using '{volume_label}'")

        print(f"Volume label: {volume_label}")
        print(f"Titles found: {len(titles)}")

        tracks = []
        for t in titles:
            print(
                f"  Title {t.index}: {t.duration_seconds}s, "
                f"{t.size_bytes} bytes, {t.chapter_count} chapters, "
                f"{t.video_resolution}"
            )
            tracks.append(
                {
                    "index": t.index,
                    "filename": f"t{t.index:02d}.mkv",
                    "duration_seconds": t.duration_seconds,
                    "size_bytes": t.size_bytes,
                    "chapter_count": t.chapter_count,
                    "video_resolution": t.video_resolution,
                    "expected_episode": None,
                }
            )

        snapshot = _build_skeleton_snapshot(volume_label, f"dev:{drive}", tracks)

        safe_name = _safe_filename(volume_label)
        out_path = SNAPSHOT_DIR / f"{safe_name}.json"
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(snapshot, indent=2))
        print(f"\nSnapshot written to: {out_path}")
