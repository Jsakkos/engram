"""REST API endpoints for standalone testing of subtitle download, transcription, and matching."""

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel

test_router = APIRouter(prefix="/api/test", tags=["testing"])


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
