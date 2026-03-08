"""Disc scan snapshot capture for debugging and test fixture generation."""

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from app.core.analyst import DiscAnalysisResult

logger = logging.getLogger(__name__)

SNAPSHOTS_DIR = Path.home() / ".engram" / "snapshots"


def _safe_filename(label: str) -> str:
    """Convert a volume label to a safe filename."""
    return re.sub(r"[^\w]", "_", label).strip("_").lower()


def save_snapshot(volume_label: str, analysis: DiscAnalysisResult) -> Path | None:
    """Save a JSON snapshot of disc analysis results.

    Args:
        volume_label: The disc's volume label
        analysis: Classification results from DiscAnalyst

    Returns:
        Path to the saved snapshot file, or None on failure
    """
    try:
        SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        safe_label = _safe_filename(volume_label) or "unknown"
        filename = f"{safe_label}_{timestamp}.json"

        snapshot = {
            "volume_label": volume_label,
            "timestamp": datetime.now(UTC).isoformat(),
            "classification": {
                "content_type": analysis.content_type.value,
                "confidence": analysis.confidence,
                "source": analysis.classification_source,
                "detected_name": analysis.detected_name,
                "detected_season": analysis.detected_season,
                "needs_review": analysis.needs_review,
                "review_reason": analysis.review_reason,
                "is_ambiguous_movie": analysis.is_ambiguous_movie,
            },
            "tmdb": {
                "id": analysis.tmdb_id,
                "name": analysis.tmdb_name,
            },
            "tracks": [
                {
                    "index": t.index,
                    "duration_seconds": t.duration_seconds,
                    "size_bytes": t.size_bytes,
                    "chapter_count": t.chapter_count,
                    "video_resolution": t.video_resolution,
                }
                for t in analysis.titles
            ],
            "play_all_indices": analysis.play_all_title_indices,
        }

        path = SNAPSHOTS_DIR / filename
        path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        logger.info(f"Saved disc snapshot: {path}")
        return path

    except Exception:
        logger.warning("Failed to save disc snapshot", exc_info=True)
        return None
