"""REST API endpoints for standalone testing of subtitle download, transcription, and matching."""

import asyncio

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.guards import require_debug, require_localhost_or_lan

# These are developer tools, not product surface: nothing in the frontend or the
# E2E suite calls them, and every one of them hands a client-supplied path to
# ffmpeg and Whisper, or spends real time scraping subtitle providers. Ungated
# they let anyone who can reach the port transcribe an arbitrary file on the
# host and read the text back, so they carry the same DEBUG gate as
# /api/simulate/*, plus the origin gate the other filesystem-touching endpoints
# use. Both are needed: DEBUG=true is the documented way to run the dev and E2E
# backend, so on its own it would leave the surface open on a LAN-exposed dev
# box, and the origin gate alone would leave it in release builds.
#
# There is deliberately no is_within_configured_roots check on video_path: the
# whole point of the harness is pointing the matcher at sample media that lives
# outside the library roots. Reachability is the control here, not the path.
test_router = APIRouter(
    prefix="/api/test",
    tags=["testing"],
    dependencies=[Depends(require_debug), Depends(require_localhost_or_lan)],
)


class SubtitleRequest(BaseModel):
    show_name: str
    season: int


class TranscribeRequest(BaseModel):
    video_path: str
    start_time: float | None = None
    duration: float = 30


class MatchRequest(BaseModel):
    video_path: str
    show_name: str
    season: int


@test_router.post("/subtitles")
async def test_subtitles(req: SubtitleRequest) -> dict:
    """Download subtitles for a TV show season via Addic7ed + TMDB.

    This is a slow operation (~3s per episode due to rate limiting).
    """
    from app.matcher.testing_service import download_subtitles

    return await asyncio.to_thread(download_subtitles, req.show_name, req.season)


@test_router.post("/transcribe")
async def test_transcribe(req: TranscribeRequest) -> dict:
    """Transcribe an audio chunk from a video file using Whisper ASR.

    CPU/GPU-bound operation. First call has cold-start cost for model loading.
    """
    from app.matcher.testing_service import transcribe_chunk

    return await asyncio.to_thread(transcribe_chunk, req.video_path, req.start_time, req.duration)


@test_router.post("/match")
async def test_match(req: MatchRequest) -> dict:
    """Match an MKV file against cached subtitles to identify the episode.

    Requires subtitles to already be cached (run /api/test/subtitles first).
    """
    from pathlib import Path

    from app.matcher.testing_service import match_episodes

    # Support single file or directory
    path = Path(req.video_path)
    if path.is_dir():
        video_paths = sorted(path.glob("*.mkv"))
    else:
        video_paths = [path]

    results = await asyncio.to_thread(match_episodes, video_paths, req.show_name, req.season)
    return {"results": results}
