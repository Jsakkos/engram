"""The curator forwards config.max_concurrent_matches as the matcher's requested_workers."""

from unittest.mock import MagicMock, patch


def test_ensure_initialized_passes_concurrency_as_workers():
    from app.core.curator import EpisodeCurator

    fake_config = MagicMock()
    fake_config.subtitles_cache_path = None
    fake_config.max_concurrent_matches = 7

    with (
        patch("app.matcher.episode_identification.EpisodeMatcher") as MockMatcher,
        patch("app.services.config_service.get_config_sync", return_value=fake_config),
        patch("app.matcher.tmdb_client.fetch_show_id", return_value=None),
        patch("app.matcher.tmdb_client.fetch_show_details", return_value=None),
    ):
        curator = EpisodeCurator()
        curator._ensure_initialized("Test Show")

    assert MockMatcher.call_args.kwargs["requested_workers"] == 7
