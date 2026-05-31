from unittest.mock import MagicMock, Mock, patch

import pytest

import app.matcher.testing_service as ts

# download_subtitles does `from app.services.config_service import get_config_sync`
# at call time, so patching the source module (not `ts.get_config_sync`) is what
# the function actually resolves.


def _mock_config(tmp_path):
    cfg = Mock()
    cfg.subtitles_cache_path = str(tmp_path)
    cfg.opensubtitles_api_key = None
    cfg.opensubtitles_username = None
    cfg.opensubtitles_password = None
    return cfg


def test_download_subtitles_uses_known_id_and_skips_fetch_show_id(tmp_path):
    """When tmdb_id is supplied, fetch_show_id is never called; the id is used directly."""
    fake_fetch_show_id = MagicMock(side_effect=AssertionError("fetch_show_id must not be called"))
    with (
        patch("app.services.config_service.get_config_sync", return_value=_mock_config(tmp_path)),
        patch.object(ts, "fetch_show_id", fake_fetch_show_id),
        patch.object(ts, "fetch_show_details", return_value={"name": "Frasier"}),
        patch.object(ts, "fetch_season_details", return_value=0) as season,
        patch.object(ts, "_precomputed_skip_result", return_value=None),
    ):
        # season count 0 -> raises ValueError AFTER id resolution, which is fine:
        # we only assert that fetch_show_id was bypassed and the known id was used.
        with pytest.raises(ValueError):
            ts.download_subtitles("Frasier", 1, tmdb_id=195241)
    fake_fetch_show_id.assert_not_called()
    season.assert_called_once_with("195241", 1)


def test_download_subtitles_without_id_still_resolves_by_name(tmp_path):
    with (
        patch("app.services.config_service.get_config_sync", return_value=_mock_config(tmp_path)),
        patch.object(ts, "fetch_show_id", return_value="3452") as fid,
        patch.object(ts, "fetch_show_details", return_value={"name": "Frasier"}),
        patch.object(ts, "fetch_season_details", return_value=0),
        patch.object(ts, "_precomputed_skip_result", return_value=None),
    ):
        with pytest.raises(ValueError):
            ts.download_subtitles("Frasier", 1)
    fid.assert_called_once_with("Frasier")
