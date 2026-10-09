"""TVDB-numbered references never mix with TMDB ones or the published pack."""

from app.core.episode_namespace import namespace_context
from app.matcher import episode_identification
from app.matcher.subtitle_utils import corpus_dir_name


def test_corpus_dir_name_suffixes_under_tvdb():
    assert corpus_dir_name(1618, "Justice League") == "1618"
    with namespace_context("tvdb"):
        assert corpus_dir_name(1618, "Justice League") == "1618@tvdb"
        assert corpus_dir_name(None, "Justice League") == "Justice League@tvdb"


def test_precomputed_manifest_hidden_under_tvdb(tmp_path):
    (tmp_path / "precomputed").mkdir()
    (tmp_path / "precomputed" / "manifest.json").write_text("{}", "utf-8")
    with namespace_context("tvdb"):
        assert episode_identification.load_precomputed_manifest(tmp_path) is None


def test_matcher_instance_cache_does_not_leak_tmdb_manifest():
    matcher = episode_identification.EpisodeMatcher.__new__(episode_identification.EpisodeMatcher)
    matcher._precomputed_manifest = {"cached": "tmdb-manifest"}
    with namespace_context("tvdb"):
        assert matcher._load_precomputed_manifest() is None
    assert matcher._load_precomputed_manifest() == {"cached": "tmdb-manifest"}


def test_download_uses_namespace_episode_count(monkeypatch):
    from app.matcher import testing_service

    seen = {}

    def fake_count(show_id, season, key):
        seen["count_called"] = True
        return 26

    monkeypatch.setattr(testing_service, "season_episode_count", fake_count)
    monkeypatch.setattr(testing_service, "fetch_season_details", lambda show, season: 24)
    monkeypatch.setattr(
        "app.services.config_service.get_config_sync",
        lambda: type("C", (), {"tmdb_api_key": "k"})(),
    )
    assert testing_service._season_episode_count("1618", 1) == 24  # TMDB path
    with namespace_context("tvdb"):
        assert testing_service._season_episode_count("1618", 1) == 26
    assert seen["count_called"]
