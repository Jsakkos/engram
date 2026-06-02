"""Unit tests for testing service orchestration."""

import json
from unittest.mock import Mock, patch

import pytest

# Use the module-level import (`testing_service.X`) for everything in this
# file. Mixing `import as` with `from … import …` of the same module
# triggers CodeQL's duplicate-import-style warning and makes refactors that
# rename symbols harder to follow.
from app.matcher import testing_service
from app.matcher.subtitle_utils import sanitize_filename
from app.matcher.testing_service import download_subtitles
from app.matcher.vectorizer_config import CACHE_FORMAT_VERSION, vectorizer_config_hash


def _write_precomputed_cache(cache_dir, show_name, season=1, episode_codes=None):
    """Write a valid precomputed manifest + placeholder vector files.

    The skip gate only checks manifest validity and file existence, so the
    .npz/.index.json contents are irrelevant here.
    """
    episode_codes = episode_codes or ["S01E01", "S01E02", "S01E03"]
    precomputed = cache_dir / "precomputed"
    show_dir = precomputed / sanitize_filename(show_name)
    show_dir.mkdir(parents=True, exist_ok=True)
    (precomputed / "idf.npy").write_bytes(b"")
    (show_dir / f"S{season:02d}.npz").write_bytes(b"")
    (show_dir / f"S{season:02d}.index.json").write_text(json.dumps(episode_codes))
    (precomputed / "manifest.json").write_text(
        json.dumps(
            {
                "cache_format_version": CACHE_FORMAT_VERSION,
                "vectorizer_config_hash": vectorizer_config_hash(),
                "content_version": "test",
                "shows": {show_name: {"tmdb_id": 1, "name": show_name, "seasons": [season]}},
            }
        )
    )


@pytest.fixture(autouse=True)
def _stub_extra_providers():
    """Stub the TVsubtitles worker to return None for every lookup.
    Tests in this file are written around the Addic7ed path (historical
    behaviour) and explicitly assert ``not_found`` when the primary
    scraper misses — without this stub the real TVsubtitles client would
    make live HTTP calls and hang the test."""
    with patch("app.matcher.testing_service.TVSubtitlesClient") as tvsub:
        instance = Mock()
        instance.get_best_subtitle.return_value = None
        instance.download_subtitle.return_value = None
        tvsub.return_value = instance
        yield


@pytest.mark.unit
class TestDownloadSubtitles:
    """Tests for subtitle download orchestration."""

    @patch("app.matcher.testing_service.Addic7edClient")
    @patch("app.matcher.testing_service.fetch_show_details")
    @patch("app.matcher.testing_service.fetch_season_details")
    @patch("app.matcher.testing_service.fetch_show_id")
    @patch("app.services.config_service.get_config_sync")
    def test_successful_download(
        self,
        mock_config_sync,
        mock_show_id,
        mock_season,
        mock_show_details,
        mock_addic7ed,
        tmp_path,
    ):
        """Test complete download workflow with all mocks."""
        # Mock config
        mock_config = Mock()
        mock_config.subtitles_cache_path = str(tmp_path)
        mock_config_sync.return_value = mock_config

        # Mock TMDB
        mock_show_id.return_value = "4589"
        mock_show_details.return_value = {"name": "Arrested Development"}
        mock_season.return_value = 3  # 3 episodes

        # Mock Addic7ed client (primary scraper)
        addic7ed_client = Mock()
        mock_addic7ed.return_value = addic7ed_client

        mock_subtitle = Mock()
        mock_subtitle.language = "English"
        mock_subtitle.version = "WEB"
        addic7ed_client.get_best_subtitle.return_value = mock_subtitle

        def download_side_effect(subtitle, save_path):
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text(
                f"1\n00:00:00,000 --> 00:00:02,000\nSubtitle for {save_path.name}\n"
            )
            return save_path

        addic7ed_client.download_subtitle.side_effect = download_side_effect

        # Execute
        result = download_subtitles("Arrested Development", 1)

        # Verify
        assert result["show_name"] == "Arrested Development"
        assert result["season"] == 1
        assert result["total_episodes"] == 3
        assert len(result["episodes"]) == 3
        assert all(ep["status"] == "downloaded" for ep in result["episodes"])
        assert all(ep["source"] == "addic7ed" for ep in result["episodes"])

        # Verify files were created
        cache_path = tmp_path / "data" / "Arrested Development"
        assert cache_path.exists()
        assert (cache_path / "Arrested Development - S01E01.srt").exists()
        assert (cache_path / "Arrested Development - S01E02.srt").exists()
        assert (cache_path / "Arrested Development - S01E03.srt").exists()

    @patch("app.matcher.testing_service.Addic7edClient")
    @patch("app.matcher.testing_service.fetch_show_details")
    @patch("app.matcher.testing_service.fetch_season_details")
    @patch("app.matcher.testing_service.fetch_show_id")
    @patch("app.services.config_service.get_config_sync")
    def test_skips_download_when_precomputed_covers_season(
        self,
        mock_config_sync,
        mock_show_id,
        mock_season,
        mock_show_details,
        mock_addic7ed,
        tmp_path,
    ):
        """When the precomputed cache covers the season, no providers are hit."""
        mock_config = Mock()
        mock_config.subtitles_cache_path = str(tmp_path)
        mock_config_sync.return_value = mock_config

        mock_show_id.return_value = "4589"
        mock_show_details.return_value = {"name": "Arrested Development"}
        mock_season.return_value = 3

        _write_precomputed_cache(tmp_path, "Arrested Development", season=1)

        addic7ed_client = Mock()
        mock_addic7ed.return_value = addic7ed_client

        result = download_subtitles("Arrested Development", 1)

        assert result["total_episodes"] == 3
        assert len(result["episodes"]) == 3
        assert all(ep["status"] == "precomputed" for ep in result["episodes"])
        assert all(ep["source"] == "precomputed" for ep in result["episodes"])

        # No provider calls and nothing written to the raw SRT cache.
        addic7ed_client.get_best_subtitle.assert_not_called()
        addic7ed_client.download_subtitle.assert_not_called()
        assert not list((tmp_path / "data" / "Arrested Development").glob("*.srt"))

    @patch("app.matcher.testing_service.Addic7edClient")
    @patch("app.matcher.testing_service.fetch_show_details")
    @patch("app.matcher.testing_service.fetch_season_details")
    @patch("app.matcher.testing_service.fetch_show_id")
    @patch("app.services.config_service.get_config_sync")
    def test_use_precomputed_false_still_downloads(
        self,
        mock_config_sync,
        mock_show_id,
        mock_season,
        mock_show_details,
        mock_addic7ed,
        tmp_path,
    ):
        """The builder opt-out (use_precomputed=False) downloads even when covered."""
        mock_config = Mock()
        mock_config.subtitles_cache_path = str(tmp_path)
        mock_config_sync.return_value = mock_config

        mock_show_id.return_value = "4589"
        mock_show_details.return_value = {"name": "Arrested Development"}
        mock_season.return_value = 1

        _write_precomputed_cache(tmp_path, "Arrested Development", season=1)

        addic7ed_client = Mock()
        mock_addic7ed.return_value = addic7ed_client
        addic7ed_client.get_best_subtitle.return_value = Mock(language="English", version="WEB")

        def download_side_effect(subtitle, save_path):
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text("1\n00:00:00,000 --> 00:00:02,000\nhi\n")
            return save_path

        addic7ed_client.download_subtitle.side_effect = download_side_effect

        result = download_subtitles("Arrested Development", 1, use_precomputed=False)

        assert all(ep["status"] != "precomputed" for ep in result["episodes"])
        addic7ed_client.get_best_subtitle.assert_called()

    @patch("app.matcher.testing_service.Addic7edClient")
    @patch("app.matcher.testing_service.fetch_show_details")
    @patch("app.matcher.testing_service.fetch_season_details")
    @patch("app.matcher.testing_service.fetch_show_id")
    @patch("app.services.config_service.get_config_sync")
    def test_precomputed_skip_does_not_touch_tmdb(
        self,
        mock_config_sync,
        mock_show_id,
        mock_season,
        mock_show_details,
        mock_addic7ed,
        tmp_path,
    ):
        """The precomputed fast path must work without any TMDB call (offline)."""
        mock_config = Mock()
        mock_config.subtitles_cache_path = str(tmp_path)
        mock_config_sync.return_value = mock_config

        # Any TMDB access would blow up — proving the fast path is network-free.
        boom = AssertionError("TMDB must not be called on the precomputed fast path")
        mock_show_id.side_effect = boom
        mock_show_details.side_effect = boom
        mock_season.side_effect = boom

        _write_precomputed_cache(tmp_path, "Arrested Development", season=1)

        result = download_subtitles("Arrested Development", 1)

        assert result["total_episodes"] == 3
        assert all(ep["status"] == "precomputed" for ep in result["episodes"])
        mock_show_id.assert_not_called()

    @patch("app.matcher.testing_service.fetch_show_id")
    def test_tmdb_show_not_found_raises_error(self, mock_show_id):
        """Test that ValueError is raised when show not found on TMDB."""
        mock_show_id.return_value = None

        with pytest.raises(ValueError, match="Could not find show"):
            download_subtitles("Nonexistent Show", 1)

    @patch("app.matcher.testing_service.fetch_season_details")
    @patch("app.matcher.testing_service.fetch_show_id")
    def test_no_episodes_found_raises_error(self, mock_show_id, mock_season):
        """Test that ValueError is raised when no episodes found."""
        mock_show_id.return_value = "123"
        mock_season.return_value = 0  # No episodes

        with pytest.raises(ValueError, match="No episodes found"):
            download_subtitles("Test Show", 1)

    @patch("app.matcher.testing_service.Addic7edClient")
    @patch("app.matcher.testing_service.fetch_show_details")
    @patch("app.matcher.testing_service.fetch_season_details")
    @patch("app.matcher.testing_service.fetch_show_id")
    @patch("app.services.config_service.get_config_sync")
    def test_cached_subtitles_not_redownloaded(
        self,
        mock_config_sync,
        mock_show_id,
        mock_season,
        mock_show_details,
        mock_addic7ed,
        tmp_path,
    ):
        """Test that cached files aren't re-downloaded."""
        # Setup cache with existing files (valid SRT content with --> markers)
        cache_dir = tmp_path / "data" / "Test Show"
        cache_dir.mkdir(parents=True)
        (cache_dir / "Test Show - S01E01.srt").write_text(
            "1\n00:00:00,000 --> 00:00:02,000\nCached subtitle 1\n"
        )
        (cache_dir / "Test Show - S01E02.srt").write_text(
            "1\n00:00:00,000 --> 00:00:02,000\nCached subtitle 2\n"
        )

        mock_config = Mock()
        mock_config.subtitles_cache_path = str(tmp_path)
        mock_config_sync.return_value = mock_config

        mock_show_id.return_value = "123"
        mock_show_details.return_value = {"name": "Test Show"}
        mock_season.return_value = 2  # 2 episodes

        addic7ed_client = Mock()
        mock_addic7ed.return_value = addic7ed_client

        # Execute
        result = download_subtitles("Test Show", 1)

        # Verify no downloads were attempted
        assert addic7ed_client.get_best_subtitle.call_count == 0
        assert all(ep["status"] == "cached" for ep in result["episodes"])
        assert all(ep["source"] == "cache" for ep in result["episodes"])

    @patch("app.matcher.testing_service.Addic7edClient")
    @patch("app.matcher.testing_service.fetch_show_details")
    @patch("app.matcher.testing_service.fetch_season_details")
    @patch("app.matcher.testing_service.fetch_show_id")
    @patch("app.services.config_service.get_config_sync")
    def test_partial_cache_downloads_missing(
        self,
        mock_config_sync,
        mock_show_id,
        mock_season,
        mock_show_details,
        mock_addic7ed,
        tmp_path,
    ):
        """Test that only missing episodes are downloaded."""
        # Setup cache with partial files (valid SRT content with --> markers)
        cache_dir = tmp_path / "data" / "Test Show"
        cache_dir.mkdir(parents=True)
        (cache_dir / "Test Show - S01E01.srt").write_text(
            "1\n00:00:00,000 --> 00:00:02,000\nCached subtitle 1\n"
        )

        mock_config = Mock()
        mock_config.subtitles_cache_path = str(tmp_path)
        mock_config_sync.return_value = mock_config

        mock_show_id.return_value = "123"
        mock_show_details.return_value = {"name": "Test Show"}
        mock_season.return_value = 3  # 3 episodes total

        addic7ed_client = Mock()
        mock_addic7ed.return_value = addic7ed_client

        mock_subtitle = Mock()
        addic7ed_client.get_best_subtitle.return_value = mock_subtitle

        def download_side_effect(subtitle, save_path):
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text("1\n00:00:00,000 --> 00:00:02,000\nDownloaded subtitle\n")
            return save_path

        addic7ed_client.download_subtitle.side_effect = download_side_effect

        # Execute
        result = download_subtitles("Test Show", 1)

        # Verify: 1 cached, 2 downloaded
        statuses = [ep["status"] for ep in result["episodes"]]
        assert statuses.count("cached") == 1
        assert statuses.count("downloaded") == 2
        assert addic7ed_client.get_best_subtitle.call_count == 2  # Only for missing episodes

    @patch("app.matcher.testing_service.Addic7edClient")
    @patch("app.matcher.testing_service.fetch_show_details")
    @patch("app.matcher.testing_service.fetch_season_details")
    @patch("app.matcher.testing_service.fetch_show_id")
    @patch("app.services.config_service.get_config_sync")
    def test_subtitle_not_found_on_addic7ed(
        self,
        mock_config_sync,
        mock_show_id,
        mock_season,
        mock_show_details,
        mock_addic7ed,
        tmp_path,
    ):
        """Test handling when subtitle not found on both scrapers."""
        mock_config = Mock()
        mock_config.subtitles_cache_path = str(tmp_path)
        mock_config_sync.return_value = mock_config

        mock_show_id.return_value = "123"
        mock_show_details.return_value = {"name": "Test Show"}
        mock_season.return_value = 2  # 2 episodes

        addic7ed_client = Mock()
        mock_addic7ed.return_value = addic7ed_client
        addic7ed_client.get_best_subtitle.return_value = None  # Not found

        # Execute
        result = download_subtitles("Test Show", 1)

        # Verify Addic7ed was tried and returned nothing
        assert all(ep["status"] == "not_found" for ep in result["episodes"])
        assert all(ep["path"] is None for ep in result["episodes"])
        assert all(ep["source"] is None for ep in result["episodes"])

    @patch("app.matcher.testing_service.Addic7edClient")
    @patch("app.matcher.testing_service.fetch_show_details")
    @patch("app.matcher.testing_service.fetch_season_details")
    @patch("app.matcher.testing_service.fetch_show_id")
    @patch("app.services.config_service.get_config_sync")
    def test_download_failure_marked_as_failed(
        self,
        mock_config_sync,
        mock_show_id,
        mock_season,
        mock_show_details,
        mock_addic7ed,
        tmp_path,
    ):
        """Test handling when both downloads fail but subtitle entries exist."""
        mock_config = Mock()
        mock_config.subtitles_cache_path = str(tmp_path)
        mock_config_sync.return_value = mock_config

        mock_show_id.return_value = "123"
        mock_show_details.return_value = {"name": "Test Show"}
        mock_season.return_value = 1  # 1 episode

        addic7ed_client = Mock()
        mock_addic7ed.return_value = addic7ed_client

        mock_subtitle = Mock()
        addic7ed_client.get_best_subtitle.return_value = mock_subtitle
        addic7ed_client.download_subtitle.return_value = None  # Download failed

        # Execute
        result = download_subtitles("Test Show", 1)

        # Verify the failed download is marked not_found
        assert result["episodes"][0]["status"] == "not_found"

    @patch("app.matcher.testing_service.Addic7edClient")
    @patch("app.matcher.testing_service.fetch_show_details")
    @patch("app.matcher.testing_service.fetch_season_details")
    @patch("app.matcher.testing_service.fetch_show_id")
    @patch("app.services.config_service.get_config_sync")
    def test_exception_during_download_marked_as_failed(
        self,
        mock_config_sync,
        mock_show_id,
        mock_season,
        mock_show_details,
        mock_addic7ed,
        tmp_path,
    ):
        """Test that exceptions during download are caught and both scrapers tried."""
        mock_config = Mock()
        mock_config.subtitles_cache_path = str(tmp_path)
        mock_config_sync.return_value = mock_config

        mock_show_id.return_value = "123"
        mock_show_details.return_value = {"name": "Test Show"}
        mock_season.return_value = 1

        addic7ed_client = Mock()
        mock_addic7ed.return_value = addic7ed_client
        addic7ed_client.get_best_subtitle.side_effect = Exception("Network error")

        # Execute
        result = download_subtitles("Test Show", 1)

        # Verify
        assert result["episodes"][0]["status"] == "not_found"


@pytest.mark.unit
class TestSubtitleFilenameFormat:
    """Tests for subtitle filename format."""

    @patch("app.matcher.testing_service.Addic7edClient")
    @patch("app.matcher.testing_service.fetch_show_details")
    @patch("app.matcher.testing_service.fetch_season_details")
    @patch("app.matcher.testing_service.fetch_show_id")
    @patch("app.services.config_service.get_config_sync")
    def test_filename_format(
        self,
        mock_config_sync,
        mock_show_id,
        mock_season,
        mock_show_details,
        mock_addic7ed,
        tmp_path,
    ):
        """Test that subtitle filenames follow correct format."""
        mock_config = Mock()
        mock_config.subtitles_cache_path = str(tmp_path)
        mock_config_sync.return_value = mock_config

        mock_show_id.return_value = "123"
        mock_show_details.return_value = {"name": "The Office"}
        mock_season.return_value = 2

        addic7ed_client = Mock()
        mock_addic7ed.return_value = addic7ed_client

        mock_subtitle = Mock()
        addic7ed_client.get_best_subtitle.return_value = mock_subtitle

        def download_side_effect(subtitle, save_path):
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text(
                f"1\n00:00:00,000 --> 00:00:02,000\nSubtitle for {save_path.name}\n"
            )
            return save_path

        addic7ed_client.download_subtitle.side_effect = download_side_effect

        # Execute
        download_subtitles("The Office", 1)

        # Verify filename format: Show_Name - S##E##.srt
        cache_path = tmp_path / "data" / "The Office"
        assert (cache_path / "The Office - S01E01.srt").exists()
        assert (cache_path / "The Office - S01E02.srt").exists()


@pytest.mark.unit
class TestUserAgent:
    """The User-Agent string sent to OpenSubtitles must include the app
    version per their best-practices doc; the prior placeholder ``"Engram"``
    (no version) and ``"Oz 1.0.0"`` (wrong app name) were both
    non-compliant."""

    def test_user_agent_constant_matches_version(self):
        from app import __version__

        assert testing_service._USER_AGENT == f"Engram v{__version__}"
        # Must satisfy the OS "AppName vX.Y.Z" pattern.
        import re

        assert re.match(r"^Engram v\d+\.\d+\.\d+$", testing_service._USER_AGENT)


@pytest.mark.unit
class TestQuotaSnapshot:
    """Reading ``client.user_downloads_remaining`` after API activity is the
    cheapest way to surface the daily quota — no extra /infos/user request."""

    def setup_method(self):
        # Fully replace the dataclass so tests don't bleed into each other.
        # Resetting only `last_quota` + `last_logged_remaining` left
        # `failed` / `client` / `login_time` carrying values from previous
        # tests, silently exercising the wrong code path inside
        # `_get_os_client`'s short-circuit and making `get_last_quota()`
        # return None for the wrong reason.
        testing_service._OS = testing_service._OSState()

    def test_snapshot_records_remaining_from_client_attribute(self):
        client = Mock()
        client.user_downloads_remaining = 87
        testing_service._snapshot_os_quota(client)
        snap = testing_service.get_last_quota()
        assert snap is not None
        assert snap["remaining"] == 87
        assert "as_of" in snap

    def test_snapshot_handles_missing_attribute_gracefully(self):
        """If the library version doesn't expose the attribute, we no-op."""
        client = Mock(spec=[])  # spec=[] → no attributes
        testing_service._snapshot_os_quota(client)
        assert testing_service.get_last_quota() is None

    def test_snapshot_logs_only_on_drop_of_ten_or_more(self):
        """Spammy log noise is the failure mode; one line per season would
        bury everything else. We log on the first read and on big drops only."""
        client = Mock()
        client.user_downloads_remaining = 100

        with patch("app.matcher.testing_service.logger") as log:
            testing_service._snapshot_os_quota(client)  # first read → logs
            assert log.info.call_count == 1

            # Tiny drop (8) → no log.
            client.user_downloads_remaining = 92
            testing_service._snapshot_os_quota(client)
            assert log.info.call_count == 1

            # Bigger drop crossing the 10-unit threshold from the LAST logged
            # value (100) → logs.
            client.user_downloads_remaining = 88
            testing_service._snapshot_os_quota(client)
            assert log.info.call_count == 2

    def test_snapshot_logs_on_quota_refill_at_midnight(self):
        """A 12-hour build crossing midnight would otherwise silence quota
        logs forever: when daily quota resets (e.g. 50 -> 1000), the
        "drop" check sees a negative diff and never fires. The refill
        branch keeps visibility live across the boundary."""
        client = Mock()
        client.user_downloads_remaining = 50

        with patch("app.matcher.testing_service.logger") as log:
            testing_service._snapshot_os_quota(client)  # first read → logs
            assert log.info.call_count == 1

            # Midnight reset: quota jumps from 50 to 1000.
            client.user_downloads_remaining = 1000
            testing_service._snapshot_os_quota(client)
            assert log.info.call_count == 2, "refill must trigger a log"

            # And the next ~50-drop after the refill should still log
            # normally against the new baseline.
            client.user_downloads_remaining = 988
            testing_service._snapshot_os_quota(client)
            assert log.info.call_count == 3, "post-refill drops still log"


@pytest.mark.unit
class TestGetOsClientQuota:
    """``_get_os_client`` must learn the TRUE remaining download count at
    login. The login response only carries ``allowed_downloads`` (the daily
    CAP), so trusting it makes the build believe quota is full when it is
    actually exhausted — then every per-season download 406s and the run
    silently degrades to slow scrapers while logging "1000 remaining"."""

    def setup_method(self):
        testing_service._OS = testing_service._OSState()

    def _config(self):
        config = Mock()
        config.opensubtitles_api_key = "key"
        config.opensubtitles_username = "user"
        config.opensubtitles_password = "pass"
        return config

    @patch("opensubtitlescom.OpenSubtitles")
    def test_exhausted_quota_skips_opensubtitles(self, mock_os_api):
        """remaining == 0 after the user-info probe → return None, mark the
        process failed so later seasons short-circuit, and never hand back a
        client that would 406 on every download."""
        client = Mock()
        client.login.return_value = {"user": {"allowed_downloads": 1000}}
        # /infos/user reports the real state: cap reached, zero left.
        client.user_downloads_remaining = 0
        mock_os_api.return_value = client

        result = testing_service._get_os_client(self._config())

        assert result is None
        assert testing_service._OS.failed is True
        client.user_info.assert_called_once()
        snap = testing_service.get_last_quota()
        assert snap is not None and snap["remaining"] == 0

    @patch("opensubtitlescom.OpenSubtitles")
    def test_available_quota_returns_client(self, mock_os_api):
        """remaining > 0 → cache and return the client as before."""
        client = Mock()
        client.login.return_value = {"user": {"allowed_downloads": 1000}}
        client.user_downloads_remaining = 950
        mock_os_api.return_value = client

        result = testing_service._get_os_client(self._config())

        assert result is client
        assert testing_service._OS.failed is False
        client.user_info.assert_called_once()
