"""Chromaprint fingerprint extraction.

Wraps the fpcalc CLI (bundled with libchromaprint) to produce a chromaprint hash
stream for an MKV/MP4/audio file. Phase 1 stores the full fingerprint per title;
windowed querying lives in Phase 3.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass

from loguru import logger

# fpcalc's bundled FFmpeg (Chromaprint 1.5.1) ships a limited decoder set and
# fails on common codecs like DTS/TrueHD/FLAC/E-AC-3 with this stderr signature.
# When seen — and an ffmpeg binary is available — we re-decode through ffmpeg
# (full codec support) before fingerprinting. Matched case-insensitively.
_DECODE_FAILURE_MARKERS = ("decoder not found",)


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

    # Class-level dedupe flag for fpcalc -version failures. Each extractor
    # instance is short-lived (one per title in the matching pipeline), so a
    # misconfigured fpcalc binary would otherwise log on every extraction.
    # Tracking on the class — not the instance — gives us once-per-process
    # logging without resorting to `global`.
    _version_failure_logged: bool = False

    def __init__(
        self,
        fpcalc_path: str,
        ffmpeg_path: str | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.fpcalc_path = fpcalc_path
        # Optional: enables the ffmpeg pre-decode fallback for codecs fpcalc's
        # bundled FFmpeg can't decode. None disables the fallback (legacy behavior).
        self.ffmpeg_path = ffmpeg_path
        self.timeout_seconds = timeout_seconds
        self._version_cache: str | None = None

    async def extract(self, media_path: str) -> ChromaprintResult:
        """Extract the full chromaprint hash stream from a media file.

        Returns a `ChromaprintResult` on success. Raises `RuntimeError` on any
        fpcalc-side failure — the caller decides whether the matching pipeline
        should continue without a fingerprint.

        If fpcalc reports a missing decoder (a known limitation of its bundled
        FFmpeg) and an ffmpeg binary is configured, the audio is re-decoded via
        ffmpeg and fingerprinted from that — see `_extract_via_ffmpeg`.
        """
        proc = await self._run_fpcalc(media_path)
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            if self.ffmpeg_path and self._is_decode_failure(stderr):
                logger.info(
                    f"fpcalc could not decode {media_path} ({stderr}); "
                    "retrying via ffmpeg pre-decode"
                )
                return await self._extract_via_ffmpeg(media_path)
            raise RuntimeError(f"fpcalc exited {proc.returncode} on {media_path}: {stderr}")
        return await self._build_result(proc.stdout, media_path)

    async def _run_fpcalc(
        self, path: str, *, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run `fpcalc -raw` on a file.

        Raises RuntimeError on timeout or if the binary cannot be launched (e.g.
        a wrong/removed fpcalc path) — so callers only ever see RuntimeError.
        """
        deadline = timeout if timeout is not None else self.timeout_seconds

        def _run() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [self.fpcalc_path, "-raw", "-length", "99999", path],
                capture_output=True,
                text=True,
                timeout=deadline,
            )

        try:
            return await asyncio.to_thread(_run)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"fpcalc timed out after {deadline}s on {path}") from e
        except OSError as e:
            raise RuntimeError(f"fpcalc could not be launched ({self.fpcalc_path}): {e}") from e

    async def _build_result(self, stdout: str, media_path: str) -> ChromaprintResult:
        """Parse fpcalc `-raw` stdout into a ChromaprintResult."""
        duration: float | None = None
        hashes: list[int] = []
        for line in stdout.splitlines():
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

    @staticmethod
    def _is_decode_failure(stderr: str) -> bool:
        low = stderr.lower()
        return any(marker in low for marker in _DECODE_FAILURE_MARKERS)

    async def _extract_via_ffmpeg(self, media_path: str) -> ChromaprintResult:
        """Decode audio with ffmpeg (full codec support), then fingerprint that.

        ffmpeg decodes the best audio stream straight to the PCM shape Chromaprint
        targets internally — mono, 11025 Hz, signed 16-bit — into a temp WAV that
        fpcalc can always read. Replicating fpcalc's own downmix/resample target
        keeps the temp small and the fingerprint equivalent to a direct decode;
        Chromaprint's acoustic matching tolerates the remaining decoder differences.
        """
        # Full-file demux over a (possibly network) share is I/O-bound, so allow
        # more time than the local fpcalc-on-WAV pass that follows.
        ffmpeg_timeout = max(self.timeout_seconds, 300.0)
        fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="engram_fp_")
        os.close(fd)
        try:

            def _run_ffmpeg() -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [
                        self.ffmpeg_path,
                        "-nostdin",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-i",
                        media_path,
                        "-vn",  # drop video; let ffmpeg auto-select the best audio stream
                        "-ac",
                        "1",
                        "-ar",
                        "11025",
                        "-c:a",
                        "pcm_s16le",
                        "-f",
                        "wav",
                        wav_path,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=ffmpeg_timeout,
                )

            try:
                ff = await asyncio.to_thread(_run_ffmpeg)
            except subprocess.TimeoutExpired as e:
                raise RuntimeError(
                    f"ffmpeg pre-decode timed out after {ffmpeg_timeout}s on {media_path}"
                ) from e
            except OSError as e:
                raise RuntimeError(f"ffmpeg could not be launched ({self.ffmpeg_path}): {e}") from e
            if ff.returncode != 0:
                raise RuntimeError(
                    f"ffmpeg pre-decode failed for {media_path}: {(ff.stderr or '').strip()}"
                )

            proc = await self._run_fpcalc(wav_path)
            if proc.returncode != 0:
                raise RuntimeError(
                    f"fpcalc exited {proc.returncode} on ffmpeg-decoded {media_path}: "
                    f"{(proc.stderr or '').strip()}"
                )
            return await self._build_result(proc.stdout, media_path)
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                pass

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
            # Dedupe failure logging at the class level so a misconfigured
            # fpcalc binary doesn't spam logs once per ripped title.
            if not ChromaprintExtractor._version_failure_logged:
                logger.error(
                    f"fpcalc -version failed at {self.fpcalc_path!r}; "
                    "fpcalc_version will be empty on stored fingerprints. "
                    "Subsequent failures suppressed.",
                    exc_info=True,
                )
                ChromaprintExtractor._version_failure_logged = True
            self._version_cache = ""
        return self._version_cache
