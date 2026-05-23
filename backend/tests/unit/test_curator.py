"""Unit tests for EpisodeCurator (matcher integration glue).

The audio matcher itself is stubbed; these tests cover the filename-fallback
helpers, confidence-threshold routing, lazy initialization branches, and the
batch driver.
"""

import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from app.core.curator import EpisodeCurator, MatchResult


@pytest.mark.unit
class TestParseEpisodeFromFilename:
    @pytest.mark.parametrize(
        "name, expected",
        [
            ("Show.S01E03.mkv", "S01E03"),
            ("Show S1E3.mkv", "S01E03"),
            ("Show.1x05.mkv", "S01E05"),
            ("Season 2 Episode 4.mkv", "S02E04"),
            ("random_movie.mkv", None),
        ],
    )
    def test_patterns(self, name, expected):
        assert EpisodeCurator()._parse_episode_from_filename(name) == expected


@pytest.mark.unit
class TestFallbackResult:
    def test_parses_filename_with_code(self, tmp_path):
        result = EpisodeCurator()._fallback_result(tmp_path / "Show.S02E05.mkv")
        assert result.episode_code == "S02E05"
        assert result.confidence == 0.3
        assert result.needs_review is True

    def test_no_code_is_zero_confidence(self, tmp_path):
        result = EpisodeCurator()._fallback_result(tmp_path / "movie.mkv")
        assert result.episode_code is None
        assert result.confidence == 0.0

    def test_parse_disabled_passes_details_through(self, tmp_path):
        result = EpisodeCurator()._fallback_result(
            tmp_path / "Show.S02E05.mkv", parse_filename=False, match_details={"k": 1}
        )
        assert result.episode_code is None
        assert result.match_details == {"k": 1}


@pytest.mark.unit
class TestClassifyResults:
    def test_splits_high_confidence_from_review(self, tmp_path):
        hi = MatchResult(tmp_path / "a", "S01E01", None, 0.9, False)
        lo = MatchResult(tmp_path / "b", "S01E02", None, 0.6, True)
        # High score but flagged for review still goes to the review bucket.
        flagged = MatchResult(tmp_path / "c", "S01E03", None, 0.8, True)

        high, review = EpisodeCurator().classify_results([hi, lo, flagged])
        assert high == [hi]
        assert lo in review and flagged in review


@pytest.mark.unit
class TestMatchSingleFile:
    async def test_fallback_when_matcher_unavailable(self, tmp_path, monkeypatch):
        curator = EpisodeCurator()
        f = tmp_path / "Show.S01E04.mkv"
        f.write_text("")
        monkeypatch.setattr(curator, "_ensure_initialized", lambda show: False)

        result = await curator.match_single_file(f, "Show", 1)
        assert result.needs_review is True
        assert result.episode_code == "S01E04"  # recovered from filename

    async def test_fallback_when_no_season(self, tmp_path, monkeypatch):
        curator = EpisodeCurator()
        curator._matcher = Mock()
        f = tmp_path / "Show.S01E04.mkv"
        f.write_text("")
        monkeypatch.setattr(curator, "_ensure_initialized", lambda show: True)

        result = await curator.match_single_file(f, "Show", None)
        assert result.needs_review is True

    async def test_high_confidence_match(self, tmp_path, monkeypatch):
        curator = EpisodeCurator()
        curator._cache_dir = tmp_path
        f = tmp_path / "ep.mkv"
        f.write_text("")
        mock_matcher = Mock()
        mock_matcher.identify_episode.return_value = {
            "season": 1,
            "episode": 3,
            "confidence": 0.95,
            "match_details": {"votes": 10},
            "runner_ups": [{"episode": "S01E04"}],
        }
        curator._matcher = mock_matcher
        monkeypatch.setattr(curator, "_ensure_initialized", lambda show: True)

        result = await curator.match_single_file(f, "Show", 1)
        assert result.episode_code == "S01E03"
        assert result.confidence == 0.95
        assert result.needs_review is False
        assert result.match_details["votes"] == 10
        assert result.match_details["runner_ups"] == [{"episode": "S01E04"}]

    async def test_low_confidence_match_needs_review(self, tmp_path, monkeypatch):
        curator = EpisodeCurator()
        curator._cache_dir = tmp_path
        f = tmp_path / "ep.mkv"
        f.write_text("")
        mock_matcher = Mock()
        mock_matcher.identify_episode.return_value = {
            "season": 1,
            "episode": 2,
            "confidence": 0.6,
        }
        curator._matcher = mock_matcher
        monkeypatch.setattr(curator, "_ensure_initialized", lambda show: True)

        result = await curator.match_single_file(f, "Show", 1)
        assert result.episode_code == "S01E02"
        assert result.needs_review is True

    async def test_no_match_falls_back_preserving_details(self, tmp_path, monkeypatch):
        curator = EpisodeCurator()
        curator._cache_dir = tmp_path
        f = tmp_path / "ep.mkv"
        f.write_text("")
        mock_matcher = Mock()
        mock_matcher.identify_episode.return_value = {
            "episode": None,
            "match_details": {"reason": "no votes"},
        }
        curator._matcher = mock_matcher
        monkeypatch.setattr(curator, "_ensure_initialized", lambda show: True)

        result = await curator.match_single_file(f, "Show", 1)
        assert result.needs_review is True
        assert result.match_details == {"reason": "no votes"}

    async def test_matcher_exception_falls_back(self, tmp_path, monkeypatch):
        curator = EpisodeCurator()
        curator._cache_dir = tmp_path
        f = tmp_path / "Show.S03E07.mkv"
        f.write_text("")
        mock_matcher = Mock()
        mock_matcher.identify_episode.side_effect = RuntimeError("boom")
        curator._matcher = mock_matcher
        monkeypatch.setattr(curator, "_ensure_initialized", lambda show: True)

        result = await curator.match_single_file(f, "Show", 3)
        assert result.needs_review is True
        assert result.episode_code == "S03E07"  # filename fallback


@pytest.mark.unit
class TestMatchFiles:
    async def test_no_series_name_all_fallback(self, tmp_path):
        files = [tmp_path / "Show.S01E01.mkv", tmp_path / "Show.S01E02.mkv"]
        results = await EpisodeCurator().match_files(files, series_name=None)
        assert len(results) == 2
        assert all(r.needs_review for r in results)

    async def test_matcher_unavailable_uses_fallback_and_reports_progress(
        self, tmp_path, monkeypatch
    ):
        curator = EpisodeCurator()
        monkeypatch.setattr(curator, "_ensure_initialized", lambda show: False)
        files = [tmp_path / "a.mkv", tmp_path / "b.mkv"]
        seen: list[tuple[int, int]] = []

        results = await curator.match_files(
            files, series_name="Show", progress_callback=lambda c, t: seen.append((c, t))
        )
        assert len(results) == 2
        assert seen == [(1, 2), (2, 2)]

    async def test_success_path_reports_progress(self, tmp_path, monkeypatch):
        curator = EpisodeCurator()
        monkeypatch.setattr(curator, "_ensure_initialized", lambda show: True)

        async def fake_single(fp, series, season):
            return MatchResult(fp, "S01E01", None, 0.9, False)

        monkeypatch.setattr(curator, "match_single_file", fake_single)
        seen: list[tuple[int, int]] = []

        results = await curator.match_files(
            [tmp_path / "a.mkv"], "Show", 1, progress_callback=lambda c, t: seen.append((c, t))
        )
        assert results[0].confidence == 0.9
        assert seen == [(1, 1)]

    async def test_per_file_exception_falls_back(self, tmp_path, monkeypatch):
        curator = EpisodeCurator()
        monkeypatch.setattr(curator, "_ensure_initialized", lambda show: True)

        async def boom(fp, series, season):
            raise RuntimeError("x")

        monkeypatch.setattr(curator, "match_single_file", boom)
        results = await curator.match_files([tmp_path / "a.mkv"], "Show", 1)
        assert len(results) == 1
        assert results[0].needs_review is True


@pytest.mark.unit
class TestEnsureInitialized:
    def test_short_circuit_when_show_unchanged(self):
        curator = EpisodeCurator()
        curator._initialized = True
        curator._current_show = "Show"
        curator._matcher = object()
        assert curator._ensure_initialized("Show") is True

        curator._matcher = None
        assert curator._ensure_initialized("Show") is False

    def test_init_success_resolves_canonical_name(self, tmp_path):
        curator = EpisodeCurator()
        with (
            patch("app.matcher.episode_identification.EpisodeMatcher") as MockMatcher,
            patch("app.matcher.tmdb_client.fetch_show_id", return_value=123),
            patch(
                "app.matcher.tmdb_client.fetch_show_details",
                return_value={"name": "Canonical Show"},
            ),
            patch(
                "app.services.config_service.get_config_sync",
                return_value=SimpleNamespace(subtitles_cache_path=str(tmp_path / "cache")),
            ),
        ):
            ok = curator._ensure_initialized("Show")

        assert ok is True
        assert curator._matcher is not None
        _, kwargs = MockMatcher.call_args
        assert kwargs["show_name"] == "Canonical Show"

    def test_init_default_cache_when_no_config(self, tmp_path):
        curator = EpisodeCurator()
        with (
            patch("app.matcher.episode_identification.EpisodeMatcher"),
            patch("app.matcher.tmdb_client.fetch_show_id", return_value=None),
            patch("app.services.config_service.get_config_sync", return_value=None),
            patch("app.core.curator.Path.home", return_value=tmp_path),
        ):
            ok = curator._ensure_initialized("Show")

        assert ok is True
        assert curator._cache_dir == tmp_path / ".engram" / "cache"

    def test_init_failure_returns_false(self, tmp_path):
        curator = EpisodeCurator()
        with (
            patch(
                "app.matcher.episode_identification.EpisodeMatcher",
                side_effect=RuntimeError("boom"),
            ),
            patch("app.matcher.tmdb_client.fetch_show_id", return_value=None),
            patch(
                "app.services.config_service.get_config_sync",
                return_value=SimpleNamespace(subtitles_cache_path=str(tmp_path)),
            ),
        ):
            ok = curator._ensure_initialized("Show")

        assert ok is False
        assert curator._matcher is None

    def test_import_error_returns_false(self):
        curator = EpisodeCurator()
        with patch.dict(sys.modules, {"app.matcher.episode_identification": None}):
            ok = curator._ensure_initialized("Show")
        assert ok is False
