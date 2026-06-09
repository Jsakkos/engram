"""Unit tests for the job poster endpoint (poster-by-tmdb_id)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routes import get_job_poster
from app.models.disc_job import ContentType, DiscJob


@pytest.mark.asyncio
async def test_poster_uses_tmdb_id_directly():
    """With tmdb_id set, fetch /tv/{id} directly and ignore a garbled detected_title."""
    job = DiscJob(drive_id="E:", volume_label="BREAKINGBADS2", content_type=ContentType.TV)
    job.tmdb_id = 1396
    job.detected_title = "Breakingbad"  # garbled — must NOT be used

    cfg = MagicMock()
    cfg.tmdb_api_key = "fake-key"

    captured = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["url"] = url
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"poster_path": "/poster.jpg"}
        return resp

    with (
        patch("app.services.config_service.get_config", new=AsyncMock(return_value=cfg)),
        patch("requests.get", side_effect=fake_get),
    ):
        result = await get_job_poster(job=job)

    assert captured["url"] == "https://api.themoviedb.org/3/tv/1396"
    assert result["poster_url"] == "https://image.tmdb.org/t/p/original/poster.jpg"


@pytest.mark.asyncio
async def test_poster_falls_back_to_name_search_without_tmdb_id():
    """Without a tmdb_id, fall back to the name search on detected_title."""
    job = DiscJob(drive_id="E:", volume_label="SOME_MOVIE", content_type=ContentType.MOVIE)
    job.tmdb_id = None
    job.detected_title = "Some Movie"

    cfg = MagicMock()
    cfg.tmdb_api_key = "fake-key"

    captured = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"results": [{"poster_path": "/m.jpg"}]}
        return resp

    with (
        patch("app.services.config_service.get_config", new=AsyncMock(return_value=cfg)),
        patch("requests.get", side_effect=fake_get),
    ):
        result = await get_job_poster(job=job)

    assert captured["url"] == "https://api.themoviedb.org/3/search/movie"
    assert captured["params"]["query"] == "Some Movie"
    assert result["poster_url"] == "https://image.tmdb.org/t/p/original/m.jpg"
