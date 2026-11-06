"""#857: one OpenSubtitles result with a dead ``file_id`` must not cost the season.

The search can list an upload whose file the server then refuses to hand out:
``POST /download`` answers ``406 {"message":"Invalid file_id"}``. That is a fact
about that one upload, not about the account or the network, so it must not be
retried (it never succeeds) and must not abort the season's API loop. Before
the fix the first such result raised out of the loop, every remaining episode
dropped to the scrapers, and for a show the scrapers do not carry (the report
was an anime disc) matching had no references at all.

A spent daily quota ALSO answers 406, so the check must key on the message,
not on the status code.
"""

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import requests
from opensubtitlescom import OpenSubtitles

import app.matcher.testing_service as ts
from app.matcher.os_api_retry import is_invalid_file_id_error, os_api_call


def _srt(text: str) -> str:
    return f"1\n00:00:01,000 --> 00:00:03,000\n{text}\n\n"


def _response(status: int, body: dict) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = json.dumps(body).encode()
    resp.url = "https://api.opensubtitles.com/api/v1/download"
    return resp


def _library_error(status: int, body: dict) -> Exception:
    """The exception the real ``opensubtitlescom`` client raises for this reply."""
    client = OpenSubtitles("Engram v0", "key")
    client.user_downloads_remaining = 10  # a logged-in account with quota left
    with patch(
        "opensubtitlescom.opensubtitles.requests.post", return_value=_response(status, body)
    ):
        try:
            client.download("123")
        except Exception as exc:  # noqa: BLE001 - we want whatever the library raises
            return exc
    raise AssertionError("library did not raise")


@pytest.mark.unit
class TestInvalidFileIdClassification:
    def test_invalid_file_id_is_recognised(self):
        assert is_invalid_file_id_error(_library_error(406, {"message": "Invalid file_id"}))

    def test_quota_406_is_not_an_invalid_file_id(self):
        exc = _library_error(
            406,
            {"message": "You have downloaded your allowed 20 subtitles for 24h."},
        )
        assert not is_invalid_file_id_error(exc)

    def test_plain_exception_is_not_an_invalid_file_id(self):
        assert not is_invalid_file_id_error(Exception("boom"))

    def test_os_api_call_does_not_retry_invalid_file_id(self):
        exc = _library_error(406, {"message": "Invalid file_id"})
        callable_ = Mock(side_effect=exc)
        with patch("app.matcher.os_api_retry.time.sleep") as sleep:
            with pytest.raises(type(exc)):
                os_api_call(callable_, max_attempts=4, base_delay=5.0)
        assert callable_.call_count == 1
        sleep.assert_not_called()


def _subtitle(ep: int, file_id: str):
    return SimpleNamespace(
        episode_number=ep, season_number=1, release=None, file_id=file_id, file_name=None
    )


@pytest.mark.unit
class TestSeasonSurvivesOneDeadUpload:
    def test_dead_file_id_skips_only_that_upload(self, tmp_path):
        cfg = Mock()
        cfg.subtitles_cache_path = str(tmp_path)
        cfg.opensubtitles_api_key = "key"
        cfg.opensubtitles_username = "user"
        cfg.opensubtitles_password = "pass"
        cfg.tmdb_api_key = "tmdb-key"

        dead = _library_error(406, {"message": "Invalid file_id"})
        # E01's first upload is dead, its second is fine; E02 is fine.
        results = [_subtitle(1, "dead"), _subtitle(1, "good1"), _subtitle(2, "good2")]

        def download_and_save(subtitle, filename):
            if subtitle.file_id == "dead":
                raise dead
            path = tmp_path / f"{filename}.srt"
            path.write_text(_srt(f"Dialogue served for upload {subtitle.file_id}."))
            return str(path)

        client = Mock()
        client.search.return_value = SimpleNamespace(data=results)
        client.download_and_save.side_effect = download_and_save
        client.user_downloads_remaining = 10

        with (
            patch.object(ts._OS, "failed", False),
            patch("app.services.config_service.get_config_sync", return_value=cfg),
            patch.object(ts, "fetch_show_details", return_value={"name": "Test Show"}),
            patch.object(ts, "fetch_season_details", return_value=2),
            patch.object(ts, "_precomputed_skip_result", return_value=None),
            patch.object(ts, "_os_client_and_reason", return_value=(client, None)),
            patch.object(ts, "_season_episode_titles", return_value={}),
            patch("app.matcher.os_api_retry.time.sleep"),
            patch.object(ts, "run_jobs") as run_jobs,
        ):
            result = ts.download_subtitles("Test Show", 1, tmdb_id=999, use_precomputed=False)

        sources = {ep["code"]: ep["source"] for ep in result["episodes"]}
        assert sources == {"S01E01": "opensubtitles_api", "S01E02": "opensubtitles_api"}
        run_jobs.assert_not_called()
        assert result["degraded"] is False
