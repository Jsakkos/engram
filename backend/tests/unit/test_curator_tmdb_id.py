from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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


def _chromaprint_cfg() -> MagicMock:
    """A config that lets _chromaprint_prepass run to the show-id resolution point."""
    cfg = MagicMock()
    cfg.enable_fingerprint_identification = True
    cfg.fpcalc_path = "/fake/fpcalc"  # set → skips detect_fpcalc()
    cfg.ffmpeg_path = "/fake/ffmpeg"  # set → skips detect_ffmpeg()
    cfg.fingerprint_server_url = "http://fp.test"
    return cfg


async def test_chromaprint_prepass_uses_known_id_and_skips_fetch_show_id():
    """The Phase-3 fingerprint prepass must fetch the pack for the KNOWN tmdb_id,
    not re-resolve by name — otherwise a same-name collision (Frasier 1993 vs the
    2023 revival) pulls the wrong show's fingerprint pack."""
    cur = EpisodeCurator()
    cur._matcher = object()  # must be non-None to proceed

    captured = {}

    class FakeChromaprintMatcher:
        def __init__(self, tmdb_id, server_url, pack_cache=None):
            captured["tmdb_id"] = tmdb_id

    fake_fetch_id = MagicMock(side_effect=AssertionError("fetch_show_id must not be called"))
    sentinel = {"season": 2, "episode": 5, "confidence": 0.95, "tier": "canonical"}

    with (
        patch("app.services.config_service.get_config", AsyncMock(return_value=_chromaprint_cfg())),
        patch("app.matcher.tmdb_client.fetch_show_id", fake_fetch_id),
        patch("app.matcher.chromaprint_matcher.ChromaprintMatcher", FakeChromaprintMatcher),
        patch(
            "app.matcher.chromaprint_matcher.identify_episode_chromaprint",
            AsyncMock(return_value=sentinel),
        ),
        patch("app.matcher.chromaprint_extractor.ChromaprintExtractor", MagicMock()),
        patch("app.matcher.episode_identification.get_video_duration", return_value=42.0),
    ):
        result = await cur._chromaprint_prepass(
            file_path=Path("ep.mkv"),
            series_name="Frasier",
            season=2,
            tmdb_id=195241,
        )

    assert result is sentinel
    assert captured["tmdb_id"] == 195241  # the known id flowed into the matcher
    fake_fetch_id.assert_not_called()


async def test_maybe_add_llm_suggestion_uses_known_id_and_skips_fetch_show_id():
    """The LLM fallback must build its TMDB context from the KNOWN tmdb_id, not
    re-resolve by name — same collision hazard as the chromaprint path."""
    cur = EpisodeCurator()
    cur._matcher = object()

    config = MagicMock()
    config.ai_episode_matching_enabled = True
    config.ai_api_key = "ai-key"
    config.ai_provider = "anthropic"
    config.tmdb_api_key = "tmdb-key"

    fake_fetch_id = MagicMock(side_effect=AssertionError("fetch_show_id must not be called"))

    suggestion = MagicMock()
    suggestion.episode = 5
    suggestion.confidence = 0.8
    suggestion.reasoning = "dialogue references the season-2 finale"
    suggestion.runner_up = None
    suggestion.model = "claude"
    fake_llm = AsyncMock(return_value=suggestion)

    with (
        patch("app.services.config_service.get_config", AsyncMock(return_value=config)),
        patch("app.matcher.tmdb_client.fetch_show_id", fake_fetch_id),
        patch("app.core.curator.match_episode_via_llm", fake_llm),
    ):
        result = await cur._maybe_add_llm_suggestion(
            file_path=Path("ep.mkv"),
            series_name="Frasier",
            season=2,
            match_details={},
            existing_transcript="hello world",  # set → skips transcribe_full
            tmdb_id=195241,
        )

    assert result is not None
    assert result["llm_suggestion"]["episode"] == 5
    fake_fetch_id.assert_not_called()
    # The known id flows through to the LLM matcher as a string.
    assert fake_llm.call_args.kwargs["tmdb_show_id"] == "195241"


async def test_chromaprint_prepass_falls_back_to_fetch_show_id_without_tmdb_id():
    """Without a known id, the prepass must still resolve by name — the legacy path
    used for the common non-collision case must keep working."""
    cur = EpisodeCurator()
    cur._matcher = object()

    captured = {}

    class FakeChromaprintMatcher:
        def __init__(self, tmdb_id, server_url, pack_cache=None):
            captured["tmdb_id"] = tmdb_id

    fake_fetch_id = MagicMock(return_value=3452)
    sentinel = {"season": 1, "episode": 1, "confidence": 0.95, "tier": "canonical"}

    with (
        patch("app.services.config_service.get_config", AsyncMock(return_value=_chromaprint_cfg())),
        patch("app.matcher.tmdb_client.fetch_show_id", fake_fetch_id),
        patch("app.matcher.chromaprint_matcher.ChromaprintMatcher", FakeChromaprintMatcher),
        patch(
            "app.matcher.chromaprint_matcher.identify_episode_chromaprint",
            AsyncMock(return_value=sentinel),
        ),
        patch("app.matcher.chromaprint_extractor.ChromaprintExtractor", MagicMock()),
        patch("app.matcher.episode_identification.get_video_duration", return_value=42.0),
    ):
        result = await cur._chromaprint_prepass(
            file_path=Path("ep.mkv"),
            series_name="Frasier",
            season=1,
        )

    assert result is sentinel
    fake_fetch_id.assert_called_once_with("Frasier")
    assert captured["tmdb_id"] == 3452


async def test_maybe_add_llm_suggestion_falls_back_to_fetch_show_id_without_tmdb_id():
    """Without a known id, the LLM fallback must still resolve by name."""
    cur = EpisodeCurator()
    cur._matcher = object()

    config = MagicMock()
    config.ai_episode_matching_enabled = True
    config.ai_api_key = "ai-key"
    config.ai_provider = "anthropic"
    config.tmdb_api_key = "tmdb-key"

    fake_fetch_id = MagicMock(return_value=3452)

    suggestion = MagicMock()
    suggestion.episode = 1
    suggestion.confidence = 0.8
    suggestion.reasoning = "r"
    suggestion.runner_up = None
    suggestion.model = "claude"
    fake_llm = AsyncMock(return_value=suggestion)

    with (
        patch("app.services.config_service.get_config", AsyncMock(return_value=config)),
        patch("app.matcher.tmdb_client.fetch_show_id", fake_fetch_id),
        patch("app.core.curator.match_episode_via_llm", fake_llm),
    ):
        result = await cur._maybe_add_llm_suggestion(
            file_path=Path("ep.mkv"),
            series_name="Frasier",
            season=1,
            match_details={},
            existing_transcript="hello world",
        )

    assert result is not None
    fake_fetch_id.assert_called_once_with("Frasier")
    assert fake_llm.call_args.kwargs["tmdb_show_id"] == "3452"
