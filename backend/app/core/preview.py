"""Browser-playable preview clips for a ripped track.

Review is a question about *content* — "which episode is this?" — and the fastest
way to answer it is to watch a few seconds. The obstacle is that a rip is not
something a browser can play: a DVD track is MPEG-2 video with AC3 audio in an
MKV, and no browser decodes either. Repackaging (remuxing) the streams into MP4
therefore does NOT help; the picture has to be re-encoded.

So this module transcodes short clips on demand and streams them straight from
ffmpeg's stdout. Nothing is written to disk — no temp files to clean up, no cache
to invalidate, and no window in which a half-written file can be served. A 20s
360p clip from an SD source costs well under a second, which is what makes the
per-clip approach preferable to preparing a whole playable rendition.

Chapters come from the same source file and make the natural seek points: MakeMKV
preserves the disc's chapter marks, and on a segment-format show they land exactly
on the segment boundaries — so "chapter 3" is usually "the second episode in this
track".
"""

import asyncio
import contextlib
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Clip bounds. The default is enough to recognise a scene; the cap keeps a
# hand-edited URL from turning a preview into a full-length transcode.
DEFAULT_CLIP_SECONDS = 20
MAX_CLIP_SECONDS = 120

# Preview quality. Deliberately modest: this is an identification aid, not a
# viewing experience, and every second saved is a second the reviewer isn't
# waiting. 360p is legible for "which episode is this" on any source.
PREVIEW_HEIGHT = 360
PREVIEW_CRF = 30
PREVIEW_PRESET = "veryfast"

# Codecs a browser can play as-is. When a rip already uses them (Blu-ray H.264
# sources), the clip is copied rather than re-encoded — near-instant.
BROWSER_VIDEO_CODECS = {"h264"}
BROWSER_AUDIO_CODECS = {"aac", "mp3"}


@dataclass
class Chapter:
    """One chapter mark, in seconds from the start of the track."""

    index: int
    start: float
    end: float
    title: str


@dataclass
class MediaProbe:
    """What a preview needs to know about a source file."""

    duration: float | None
    video_codec: str | None
    audio_codec: str | None
    chapters: list[Chapter]

    @property
    def can_copy(self) -> bool:
        """True when the streams can be repackaged instead of re-encoded."""
        return self.video_codec in BROWSER_VIDEO_CODECS and self.audio_codec in BROWSER_AUDIO_CODECS


def resolve_ffmpeg_binary(configured: str | None, name: str = "ffmpeg") -> str | None:
    """Path to ffmpeg/ffprobe: the configured one, its sibling, or one on PATH.

    Engram already depends on ffmpeg (it backs the chromaprint pre-decode), and
    users who set an explicit path usually point at a folder holding both
    binaries — so a configured ``ffmpeg`` also tells us where ``ffprobe`` lives.
    """
    if configured:
        configured_path = Path(configured)
        if configured_path.exists():
            if name == "ffmpeg":
                return str(configured_path)
            sibling = configured_path.with_name(name + configured_path.suffix)
            if sibling.exists():
                return str(sibling)
    return shutil.which(name)


async def probe_media(path: Path, ffprobe: str) -> MediaProbe:
    """Duration, codecs and chapter marks for a source file.

    Never raises: a preview is an aid, and a probe failure should degrade to
    "no chapters, re-encode to be safe" rather than break the review page.
    """
    proc = await asyncio.create_subprocess_exec(
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-show_streams",
        "-show_chapters",
        "-of",
        "json",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.warning("ffprobe failed for preview (%s): %s", path.name, stderr.decode()[:200])
        return MediaProbe(duration=None, video_codec=None, audio_codec=None, chapters=[])

    try:
        data = json.loads(stdout or b"{}")
    except json.JSONDecodeError:
        logger.warning("ffprobe returned unparseable JSON for %s", path.name)
        return MediaProbe(duration=None, video_codec=None, audio_codec=None, chapters=[])

    duration = None
    with contextlib.suppress(TypeError, ValueError):
        duration = float(data.get("format", {}).get("duration"))

    video_codec = next(
        (s.get("codec_name") for s in data.get("streams", []) if s.get("codec_type") == "video"),
        None,
    )
    audio_codec = next(
        (s.get("codec_name") for s in data.get("streams", []) if s.get("codec_type") == "audio"),
        None,
    )

    chapters: list[Chapter] = []
    for i, raw in enumerate(data.get("chapters", [])):
        try:
            start = float(raw["start_time"])
            end = float(raw["end_time"])
        except (KeyError, TypeError, ValueError):
            continue
        chapters.append(
            Chapter(
                index=i + 1,
                start=start,
                end=end,
                title=(raw.get("tags") or {}).get("title") or f"Chapter {i + 1:02d}",
            )
        )

    return MediaProbe(
        duration=duration, video_codec=video_codec, audio_codec=audio_codec, chapters=chapters
    )


def build_preview_command(
    ffmpeg: str, path: Path, start: float, duration: float, *, copy_streams: bool
) -> list[str]:
    """The ffmpeg invocation for one preview clip, writing fragmented MP4 to stdout.

    ``-ss`` precedes ``-i`` so ffmpeg seeks by index instead of decoding up to the
    start point — the difference between a preview that appears at once and one
    that takes as long as the offset. The fragmented-MP4 flags are what make the
    output streamable at all: a normal MP4 puts its index at the end, which a pipe
    cannot go back and write.
    """
    cmd = [
        ffmpeg,
        "-v",
        "error",
        "-ss",
        f"{max(start, 0):.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(path),
    ]
    if copy_streams:
        cmd += ["-c", "copy"]
    else:
        cmd += [
            "-c:v",
            "libx264",
            "-preset",
            PREVIEW_PRESET,
            "-crf",
            str(PREVIEW_CRF),
            "-vf",
            f"scale=-2:{PREVIEW_HEIGHT}",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-ac",
            "2",
        ]
    # Only the first video/audio stream: a DVD rip carries several audio tracks
    # and bitmap subtitles that MP4 cannot hold at all.
    cmd += [
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-sn",
        "-movflags",
        "frag_keyframe+empty_moov+default_base_moof",
        "-f",
        "mp4",
        "pipe:1",
    ]
    return cmd


async def stream_preview(cmd: list[str], chunk_size: int = 64 * 1024):
    """Yield the clip as ffmpeg produces it, killing ffmpeg if the client leaves.

    The kill matters: a reviewer clicking through tracks abandons requests
    constantly, and without it each abandoned preview would run to completion
    against the (often network-mounted) library.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        while True:
            chunk = await proc.stdout.read(chunk_size)
            if not chunk:
                break
            yield chunk
        stderr = await proc.stderr.read()
        if proc.returncode not in (0, None) and stderr:
            logger.warning("ffmpeg preview exited %s: %s", proc.returncode, stderr.decode()[:200])
    finally:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
