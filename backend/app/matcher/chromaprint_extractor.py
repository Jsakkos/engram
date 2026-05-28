"""Chromaprint fingerprint extraction.

Wraps the fpcalc CLI (bundled with libchromaprint) to produce a chromaprint hash
stream for an MKV/MP4/audio file. Phase 1 stores the full fingerprint per title;
windowed querying lives in Phase 3.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass


@dataclass
class ChromaprintResult:
    """The full chromaprint hash stream for one media file."""

    hashes: list[int]
    duration_seconds: float
    fpcalc_version: str

    def to_blob(self) -> bytes:
        """Serialize to gzip-compressed JSON for DB storage."""
        payload = {
            "v": 1,
            "duration": self.duration_seconds,
            "fpcalc": self.fpcalc_version,
            "hashes": self.hashes,
        }
        return gzip.compress(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"),
            mtime=0,
        )

    @classmethod
    def from_blob(cls, blob: bytes) -> ChromaprintResult:
        payload = json.loads(gzip.decompress(blob).decode("utf-8"))
        if payload.get("v") != 1:
            raise ValueError(f"Unknown chromaprint blob version: {payload.get('v')}")
        return cls(
            hashes=list(payload["hashes"]),
            duration_seconds=float(payload["duration"]),
            fpcalc_version=str(payload.get("fpcalc", "")),
        )


class ChromaprintExtractor:
    """Subprocess-based chromaprint fingerprint extractor."""

    def __init__(self, fpcalc_path: str) -> None:
        self.fpcalc_path = fpcalc_path

    async def extract(self, media_path: str) -> ChromaprintResult:
        raise NotImplementedError("extract() lands in Task C2")
