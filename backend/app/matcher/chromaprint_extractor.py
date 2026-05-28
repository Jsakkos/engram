"""Chromaprint fingerprint extraction.

Wraps the fpcalc CLI (bundled with libchromaprint) to produce a chromaprint hash
stream for an MKV/MP4/audio file. Phase 1 stores the full fingerprint per title;
windowed querying lives in Phase 3.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import subprocess
from dataclasses import dataclass

from loguru import logger


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
        if "v" not in payload:
            raise ValueError("Chromaprint blob is missing version field")
        if payload["v"] != 1:
            raise ValueError(f"Unknown chromaprint blob version: {payload['v']}")
        return cls(
            hashes=list(payload["hashes"]),
            duration_seconds=float(payload["duration"]),
            fpcalc_version=str(payload.get("fpcalc", "")),
        )


class ChromaprintExtractor:
    """Subprocess-based chromaprint fingerprint extractor."""

    def __init__(self, fpcalc_path: str, timeout_seconds: float = 120.0) -> None:
        self.fpcalc_path = fpcalc_path
        self.timeout_seconds = timeout_seconds
        self._version_cache: str | None = None

    async def extract(self, media_path: str) -> ChromaprintResult:
        """Extract the full chromaprint hash stream from a media file.

        Returns a `ChromaprintResult` on success. Raises `RuntimeError` on any
        fpcalc-side failure — the caller decides whether the matching pipeline
        should continue without a fingerprint.
        """

        def _run() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [self.fpcalc_path, "-raw", "-length", "99999", media_path],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )

        try:
            proc = await asyncio.to_thread(_run)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"fpcalc timed out after {self.timeout_seconds}s on {media_path}"
            ) from e

        if proc.returncode != 0:
            raise RuntimeError(
                f"fpcalc exited {proc.returncode} on {media_path}: {proc.stderr.strip()}"
            )

        duration: float | None = None
        hashes: list[int] = []
        for line in proc.stdout.splitlines():
            if line.startswith("DURATION="):
                duration = float(line.removeprefix("DURATION="))
            elif line.startswith("FINGERPRINT="):
                hashes = [int(x) for x in line.removeprefix("FINGERPRINT=").split(",") if x]

        if not hashes:
            raise RuntimeError(f"fpcalc produced no FINGERPRINT line for {media_path}")
        if duration is None:
            duration = 0.0

        version_line = await self._cached_version()
        logger.info(
            f"chromaprint extracted: {len(hashes)} hashes, {duration:.1f}s from {media_path}"
        )
        return ChromaprintResult(
            hashes=hashes, duration_seconds=duration, fpcalc_version=version_line
        )

    async def _cached_version(self) -> str:
        if self._version_cache is not None:
            return self._version_cache

        def _run() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [self.fpcalc_path, "-version"],
                capture_output=True,
                text=True,
                timeout=5,
            )

        try:
            proc = await asyncio.to_thread(_run)
            self._version_cache = (proc.stdout or "").splitlines()[0] if proc.stdout else ""
        except Exception:
            logger.warning("fpcalc -version failed; fpcalc_version will be empty", exc_info=True)
            self._version_cache = ""
        return self._version_cache
