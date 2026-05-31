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
        monkeypatch.setattr(curator, "_ensure_initialized", lambda show, tmdb_id=None: False)

        result = await curator.match_single_file(f, "Show", 1)
        assert result.needs_review is True
        assert result.episode_code == "S01E04"  # recovered from filename

    async def test_unknown_season_with_no_candidate_seasons_falls_back(self, tmp_path, monkeypatch):
        # Season unknown + no seasons have references → filename fallback (needs review).
        curator = EpisodeCurator()
        curator._matcher = Mock()
        f = tmp_path / "Show.S01E04.mkv"
        f.write_text("")
        monkeypatch.setattr(curator, "_ensure_initialized", lambda show, tmdb_id=None: True)
        monkeypatch.setattr(curator, "_candidate_seasons", lambda show: [])

        result = await curator.match_single_file(f, "Show", None)
        assert result.needs_review is True
        assert result.episode_code == "S01E04"  # recovered from filename

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
        monkeypatch.setattr(curator, "_ensure_initialized", lambda show, tmdb_id=None: True)

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
        monkeypatch.setattr(curator, "_ensure_initialized", lambda show, tmdb_id=None: True)

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
        monkeypatch.setattr(curator, "_ensure_initialized", lambda show, tmdb_id=None: True)

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
        monkeypatch.setattr(curator, "_ensure_initialized", lambda show, tmdb_id=None: True)

        result = await curator.match_single_file(f, "Show", 3)
        assert result.needs_review is True
        assert result.episode_code == "S03E07"  # filename fallback


@pytest.mark.unit
class TestMatchAcrossSeasons:
    """Unknown-season import: search every candidate season, keep the best match."""

    async def test_dispatches_when_season_unknown(self, tmp_path, monkeypatch):
        curator = EpisodeCurator()
        curator._matcher = Mock()
        f = tmp_path / "ep.mkv"
        f.write_text("")
        monkeypatch.setattr(curator, "_ensure_initialized", lambda show, tmdb_id=None: True)
        sentinel = MatchResult(f, "S04E02", None, 0.8, False)

        async def fake_across(fp, series, *a, **k):
            return sentinel

        monkeypatch.setattr(curator, "_match_across_seasons", fake_across)
        result = await curator.match_single_file(f, "Show", None)
        assert result is sentinel

    async def test_known_season_does_not_dispatch(self, tmp_path, monkeypatch):
        curator = EpisodeCurator()
        curator._cache_dir = tmp_path
        curator._matcher = Mock()
        f = tmp_path / "ep.mkv"
        f.write_text("")
        monkeypatch.setattr(curator, "_ensure_initialized", lambda show, tmdb_id=None: True)

        async def boom(*a, **k):
            raise AssertionError("should not search all seasons when season is known")

        monkeypatch.setattr(curator, "_match_across_seasons", boom)

        async def fake_prepass(**k):
            return None

        monkeypatch.setattr(curator, "_chromaprint_prepass", fake_prepass)
        sentinel = MatchResult(f, "S02E01", None, 0.9, False)

        async def fake_asr(*a, **k):
            return sentinel

        monkeypatch.setattr(curator, "_run_asr_identify", fake_asr)
        result = await curator.match_single_file(f, "Show", 2)
        assert result is sentinel

    async def test_picks_highest_confidence_across_seasons(self, tmp_path, monkeypatch):
        curator = EpisodeCurator()
        curator._matcher = Mock()
        f = tmp_path / "ep.mkv"
        f.write_text("")
        monkeypatch.setattr(curator, "_candidate_seasons", lambda show: [1, 2, 3])

        canned = {
            1: MatchResult(f, None, None, 0.0, True),
            2: MatchResult(f, "S02E05", None, 0.92, False),
            3: MatchResult(f, "S03E01", None, 0.6, True),
        }

        async def fake_single(fp, series, season, *a, **k):
            return canned[season]

        monkeypatch.setattr(curator, "match_single_file", fake_single)
        result = await curator._match_across_seasons(f, "Show")
        assert result.episode_code == "S02E05"
        assert result.confidence == 0.92

    async def test_no_match_in_any_season_falls_back(self, tmp_path, monkeypatch):
        curator = EpisodeCurator()
        curator._matcher = Mock()
        f = tmp_path / "Show.mkv"
        f.write_text("")
        monkeypatch.setattr(curator, "_candidate_seasons", lambda show: [1, 2])

        async def fake_single(fp, series, season, *a, **k):
            return MatchResult(fp, None, None, 0.0, True)

        monkeypatch.setattr(curator, "match_single_file", fake_single)
        result = await curator._match_across_seasons(f, "Show")
        assert result.episode_code is None
        assert result.needs_review is True

    async def test_no_candidate_seasons_falls_back(self, tmp_path, monkeypatch):
        curator = EpisodeCurator()
        curator._matcher = Mock()
        f = tmp_path / "Show.S01E04.mkv"
        f.write_text("")
        monkeypatch.setattr(curator, "_candidate_seasons", lambda show: [])
        result = await curator._match_across_seasons(f, "Show")
        assert result.needs_review is True
        assert result.episode_code == "S01E04"  # filename fallback


@pytest.mark.unit
class TestCandidateSeasons:
    def test_enumerates_seasons_from_downloaded_srts(self, tmp_path):
        curator = EpisodeCurator()
        curator._cache_dir = tmp_path
        curator._matcher = Mock()
        curator._matcher.show_name = "The Expanse"
        data_dir = tmp_path / "data" / "The Expanse"
        data_dir.mkdir(parents=True)
        (data_dir / "The Expanse - S01E01.srt").write_text("")
        (data_dir / "The Expanse - S01E02.srt").write_text("")
        (data_dir / "The Expanse - S03E05.srt").write_text("")

        assert curator._candidate_seasons("The Expanse") == [1, 3]

    def test_falls_back_to_tmdb_season_count(self, tmp_path, monkeypatch):
        curator = EpisodeCurator()
        curator._cache_dir = tmp_path  # empty: no manifest, no SRTs
        curator._matcher = Mock()
        curator._matcher.show_name = "The Expanse"
        curator._current_show_id = "999"
        monkeypatch.setattr("app.matcher.tmdb_client.get_number_of_seasons", lambda show_id: 6)
        assert curator._candidate_seasons("The Expanse") == [1, 2, 3, 4, 5, 6]


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
        monkeypatch.setattr(curator, "_ensure_initialized", lambda show, tmdb_id=None: False)
        files = [tmp_path / "a.mkv", tmp_path / "b.mkv"]
        seen: list[tuple[int, int]] = []

        results = await curator.match_files(
            files, series_name="Show", progress_callback=lambda c, t: seen.append((c, t))
        )
        assert len(results) == 2
        assert seen == [(1, 2), (2, 2)]

    async def test_success_path_reports_progress(self, tmp_path, monkeypatch):
        curator = EpisodeCurator()
        monkeypatch.setattr(curator, "_ensure_initialized", lambda show, tmdb_id=None: True)

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
        monkeypatch.setattr(curator, "_ensure_initialized", lambda show, tmdb_id=None: True)

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


@pytest.mark.unit
class TestLLMFallback:
    @pytest.mark.asyncio
    async def test_disabled_in_config_skips_llm(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock, patch

        from app.core.curator import EpisodeCurator, MatchResult

        curator = EpisodeCurator()
        curator._matcher = MagicMock()
        curator._matcher.identify_episode.return_value = {
            "season": 1,
            "episode": 3,
            "confidence": 0.5,
            "score": 0.5,
            "match_details": {},
            "runner_ups": [],
        }
        curator._cache_dir = tmp_path
        curator._initialized = True
        curator._current_show = "Test"

        fake_config = MagicMock(ai_episode_matching_enabled=False, ai_api_key="k")
        with (
            patch(
                "app.services.config_service.get_config", new=AsyncMock(return_value=fake_config)
            ),
            patch(
                "app.matcher.llm_episode_matcher.match_episode_via_llm", new=AsyncMock()
            ) as mock_llm,
        ):
            result = await curator.match_single_file(tmp_path / "x.mkv", "Test", 1)

        assert isinstance(result, MatchResult)
        mock_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_low_confidence_triggers_llm_and_attaches_suggestion(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock, patch

        from app.core.curator import EpisodeCurator
        from app.matcher.llm_episode_matcher import LLMEpisodeMatch

        curator = EpisodeCurator()
        curator._matcher = MagicMock()
        curator._matcher.identify_episode.return_value = {
            "season": 1,
            "episode": 3,
            "confidence": 0.4,
            "score": 0.4,
            "match_details": {},
            "runner_ups": [],
        }
        curator._matcher.transcribe_full = MagicMock(return_value="x" * 600)
        curator._cache_dir = tmp_path
        curator._initialized = True
        curator._current_show = "Test"

        fake_config = MagicMock(
            ai_episode_matching_enabled=True,
            ai_api_key="k",
            ai_provider="gemini",
            tmdb_api_key="t",
        )

        llm = LLMEpisodeMatch(
            episode=5,
            confidence=0.92,
            reasoning="r",
            runner_up=None,
            model="gemini-2.5-flash-lite",
        )

        with (
            patch(
                "app.services.config_service.get_config", new=AsyncMock(return_value=fake_config)
            ),
            patch("app.matcher.tmdb_client.fetch_show_id", return_value="1234"),
            patch("app.core.curator.match_episode_via_llm", new=AsyncMock(return_value=llm)),
        ):
            result = await curator.match_single_file(tmp_path / "x.mkv", "Test", 1)

        assert result.needs_review is True
        assert result.match_details["llm_suggestion"]["episode"] == 5
        assert result.match_details["llm_suggestion"]["confidence"] == 0.92
        assert result.match_details["llm_suggestion"]["model"] == "gemini-2.5-flash-lite"

    @pytest.mark.asyncio
    async def test_high_confidence_skips_llm(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock, patch

        from app.core.curator import EpisodeCurator

        curator = EpisodeCurator()
        curator._matcher = MagicMock()
        curator._matcher.identify_episode.return_value = {
            "season": 1,
            "episode": 3,
            "confidence": 0.92,
            "score": 0.9,
            "match_details": {},
            "runner_ups": [],
        }
        curator._cache_dir = tmp_path
        curator._initialized = True
        curator._current_show = "Test"

        fake_config = MagicMock(ai_episode_matching_enabled=True, ai_api_key="k")
        with (
            patch(
                "app.services.config_service.get_config", new=AsyncMock(return_value=fake_config)
            ),
            patch("app.core.curator.match_episode_via_llm", new=AsyncMock()) as mock_llm,
        ):
            result = await curator.match_single_file(tmp_path / "x.mkv", "Test", 1)

        assert result.needs_review is False
        mock_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_reuses_existing_transcript_no_double_asr(self, tmp_path):
        """When the primary matcher already produced a transcript (full-file
        fallback), curator should pass it through without re-running Whisper."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from app.core.curator import EpisodeCurator
        from app.matcher.llm_episode_matcher import LLMEpisodeMatch

        curator = EpisodeCurator()
        curator._matcher = MagicMock()
        curator._matcher.identify_episode.return_value = {
            "season": 1,
            "episode": 3,
            "confidence": 0.4,
            "score": 0.4,
            "match_details": {},
            "runner_ups": [],
            "transcript": "primary already transcribed this " * 30,
        }
        # Sentinel — if curator calls transcribe_full, the test fails
        curator._matcher.transcribe_full = MagicMock(
            side_effect=AssertionError("should not re-transcribe")
        )
        curator._cache_dir = tmp_path
        curator._initialized = True
        curator._current_show = "Test"

        fake_config = MagicMock(
            ai_episode_matching_enabled=True,
            ai_api_key="k",
            ai_provider="gemini",
            tmdb_api_key="t",
        )
        llm = LLMEpisodeMatch(
            episode=5, confidence=0.9, reasoning="r", runner_up=None, model="gemini-2.5-flash-lite"
        )
        with (
            patch(
                "app.services.config_service.get_config", new=AsyncMock(return_value=fake_config)
            ),
            patch("app.matcher.tmdb_client.fetch_show_id", return_value="1234"),
            patch(
                "app.core.curator.match_episode_via_llm", new=AsyncMock(return_value=llm)
            ) as mock_llm,
        ):
            result = await curator.match_single_file(tmp_path / "x.mkv", "Test", 1)

        assert result.match_details["llm_suggestion"]["episode"] == 5
        curator._matcher.transcribe_full.assert_not_called()
        # The transcript that reached the LLM should be the one from the primary
        passed_transcript = mock_llm.call_args.kwargs["transcript"]
        assert passed_transcript.startswith("primary already transcribed this")
