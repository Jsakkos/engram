from unittest.mock import MagicMock, patch

from app.core.curator import EpisodeCurator


def test_ensure_initialized_uses_known_id_and_skips_fetch_show_id():
    cur = EpisodeCurator()
    captured = {}

    class FakeMatcher:
        def __init__(self, cache_dir, show_name, min_confidence, expected_tmdb_id=None):
            captured["expected_tmdb_id"] = expected_tmdb_id
            captured["show_name"] = show_name

    fake_fetch_id = MagicMock(side_effect=AssertionError("fetch_show_id must not be called"))
    cfg = MagicMock()
    cfg.subtitles_cache_path = None
    with (
        patch("app.matcher.episode_identification.EpisodeMatcher", FakeMatcher),
        patch("app.matcher.tmdb_client.fetch_show_id", fake_fetch_id),
        patch("app.matcher.tmdb_client.fetch_show_details", return_value={"name": "Frasier"}),
        patch("app.services.config_service.get_config_sync", return_value=cfg),
    ):
        ok = cur._ensure_initialized("Frasier", tmdb_id=195241)
    assert ok is True
    assert captured["expected_tmdb_id"] == 195241
    fake_fetch_id.assert_not_called()


def test_ensure_initialized_rebuilds_when_tmdb_id_changes():
    """A changed tmdb_id (e.g. user re-identified the show) must rebuild the matcher
    rather than short-circuit — this is what makes a re-identify actually take effect."""
    cur = EpisodeCurator()
    # Pretend the matcher is already initialized for the ORIGINAL Frasier (#3452).
    cur._initialized = True
    cur._current_show = "Frasier"
    cur._current_tmdb_id = 3452
    cur._matcher = object()

    captured = {}

    class FakeMatcher:
        def __init__(self, cache_dir, show_name, min_confidence, expected_tmdb_id=None):
            captured["expected_tmdb_id"] = expected_tmdb_id

    cfg = MagicMock()
    cfg.subtitles_cache_path = None
    with (
        patch("app.matcher.episode_identification.EpisodeMatcher", FakeMatcher),
        patch("app.matcher.tmdb_client.fetch_show_id", MagicMock()),
        patch("app.matcher.tmdb_client.fetch_show_details", return_value={"name": "Frasier"}),
        patch("app.services.config_service.get_config_sync", return_value=cfg),
    ):
        # Same show name, DIFFERENT id (the 2023 revival) — must not short-circuit.
        ok = cur._ensure_initialized("Frasier", tmdb_id=195241)
    assert ok is True
    assert cur._current_tmdb_id == 195241
    assert captured["expected_tmdb_id"] == 195241
