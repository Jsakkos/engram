"""Unit tests for TMDB client with focus on variation generation bug fix."""

from unittest.mock import Mock, patch

import pytest
import requests

from app.matcher import tmdb_client
from app.matcher.tmdb_client import (
    _fetch_show_id_cached,
    clear_caches,
    fetch_movie_details,
    fetch_season_details,
    fetch_show_details,
    fetch_show_id,
)
from tests.fixtures.tmdb_responses import (
    TMDB_SEARCH_ARRESTED_DEVELOPMENT,
    TMDB_SEARCH_BREAKING_BAD,
    TMDB_SEARCH_EMPTY,
    TMDB_SEARCH_OFFICE,
    TMDB_SEARCH_STAR_TREK,
    TMDB_SEASON_DETAILS_S01_3EP,
)


@pytest.fixture(autouse=True)
def _clear_tmdb_caches():
    """Reset the in-process TMDB lru_cache between tests.

    Tests patch ``requests.get`` with different mocks but the cached fetch
    functions persist across the test module — without this fixture, a later
    test calling ``fetch_show_id("Arrested Development")`` would receive a
    None cached by an earlier test's mock instead of hitting the new mock.
    """
    clear_caches()
    yield


@pytest.mark.unit
class TestLruCache:
    """The build script calls these fetches once per show during selection AND
    again per season during download. The persistent SQLite layer dedupes
    across runs; the LRU dedupes the rare case where SQLite is cleared
    mid-run. These tests guard against future refactors that accidentally
    remove either layer or change argument types."""

    @patch("app.matcher.tmdb_client.requests.get")
    def test_repeat_call_with_same_arg_hits_at_most_once(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": [{"id": "4589", "name": "X"}]}
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        with patch("app.services.config_service.get_config_sync") as cfg:
            cfg.return_value.tmdb_api_key = "test"
            a = fetch_show_id("X")
            b = fetch_show_id("X")

        assert a == b
        # The second call must NOT have hit the network — it should have been
        # satisfied by either the SQLite layer (preferred) or the inner LRU.
        # The exact dedup layer is an implementation detail; what matters is
        # that one network round-trip was sufficient to satisfy two callers.
        assert mock_get.call_count == 1

    def test_no_api_key_does_not_poison_cache(self):
        """First-boot regression guard.

        Without the inner/outer split, ``fetch_show_id("X")`` called before
        the user runs ConfigWizard would cache ``None`` keyed on ``"X"``.
        After the key is set, subsequent calls for the same show would keep
        returning the cached None until process restart — silently
        disabling TMDB lookups for those shows. With the split, the no-key
        path returns without consulting the cache, so a later call with a
        valid key hits the network normally.
        """
        # No key → public wrapper short-circuits; cache must stay empty.
        with patch("app.services.config_service.get_config_sync") as cfg:
            cfg.return_value.tmdb_api_key = ""
            assert fetch_show_id("Show With No Key") is None
        assert _fetch_show_id_cached.cache_info().currsize == 0

        # Key configured → cached path runs and stores the result.
        with patch("app.matcher.tmdb_client.requests.get") as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200  # branches in the impl gate on this
            mock_response.json.return_value = {"results": [{"id": "42", "name": "X"}]}
            mock_response.raise_for_status = Mock()
            mock_get.return_value = mock_response
            with patch("app.services.config_service.get_config_sync") as cfg:
                cfg.return_value.tmdb_api_key = "test"
                assert fetch_show_id("Show With No Key") == "42"
        assert _fetch_show_id_cached.cache_info().currsize == 1

    def test_clear_caches_resets_all_three(self):
        """``clear_caches()`` is the contract test fixtures rely on; if it
        stops working every test that mutates config between calls breaks
        silently with the wrong cached value."""
        with patch("app.matcher.tmdb_client.requests.get") as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"results": [{"id": "42", "name": "X"}]}
            mock_response.raise_for_status = Mock()
            mock_get.return_value = mock_response
            with patch("app.services.config_service.get_config_sync") as cfg:
                cfg.return_value.tmdb_api_key = "test"
                fetch_show_id("X")
            assert _fetch_show_id_cached.cache_info().currsize >= 1
            clear_caches()
            assert _fetch_show_id_cached.cache_info().currsize == 0

    def test_clear_caches_clears_persistent_layer(self):
        """``clear_caches()`` must flush the SQLite layer too, otherwise a
        TMDB API key rotation leaves results from the revoked key pinned
        for up to 90 days."""
        from app.matcher import tmdb_persistent_cache

        tmdb_persistent_cache.put("show_id:Rotated", "5", ttl_seconds=3600)
        assert tmdb_persistent_cache.get("show_id:Rotated") == "5"
        clear_caches()
        assert tmdb_persistent_cache.get("show_id:Rotated") is None

    def test_persistent_cache_short_circuits_network(self):
        """A warm SQLite row must satisfy fetch_show_details without any
        network round-trip — the whole point of the disk layer."""
        from app.matcher import tmdb_persistent_cache

        tmdb_persistent_cache.put(
            "show_details:1396",
            {"name": "Breaking Bad", "number_of_seasons": 5},
            ttl_seconds=3600,
        )
        with patch("app.matcher.tmdb_client.requests.get") as mock_get:
            mock_get.side_effect = AssertionError("must not hit the network")
            with patch("app.services.config_service.get_config_sync") as cfg:
                cfg.return_value.tmdb_api_key = "test"
                result = fetch_show_details(1396)
        assert result == {"name": "Breaking Bad", "number_of_seasons": 5}
        assert mock_get.call_count == 0

    def test_cached_inner_raises_when_called_without_key(self):
        """Misuse guard: calling ``_fetch_show_id_cached`` directly (bypassing
        the public wrapper) with no API key must raise loudly, not cache
        ``None``. This is what the inner/outer split is for — silently
        caching None here is the exact failure mode we're guarding against.
        """
        with patch("app.services.config_service.get_config_sync") as cfg:
            cfg.return_value.tmdb_api_key = ""
            with pytest.raises(RuntimeError, match="without a TMDB API key"):
                _fetch_show_id_cached("Show")
        # Confirm the exception didn't leak into the cache.
        assert _fetch_show_id_cached.cache_info().currsize == 0

    def test_fetch_show_details_returns_independent_dict_per_call(self):
        """Cache-poisoning guard: a caller mutating the returned dict
        (including nested lists) must not corrupt the cached entry for
        subsequent callers. The public wrapper deep-copies the cached
        value on every call, so each caller gets its own object graph.
        """
        with patch("app.matcher.tmdb_client.requests.get") as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.raise_for_status = Mock()
            mock_response.json.return_value = {
                "name": "Breaking Bad",
                "number_of_seasons": 5,
                "genres": [{"id": 18, "name": "Drama"}],
            }
            mock_get.return_value = mock_response
            with patch("app.services.config_service.get_config_sync") as cfg:
                cfg.return_value.tmdb_api_key = "test"
                first = fetch_show_details(81189)
                # Caller scribbles on both a top-level field AND a nested list.
                first["name"] = "MUTATED"
                first["genres"].append({"id": 999, "name": "POISON"})

                second = fetch_show_details(81189)

        # Network was only hit once — second call came from the cache.
        assert mock_get.call_count == 1
        # Mutation on the first object did NOT leak into the second.
        assert second["name"] == "Breaking Bad"
        assert second["genres"] == [{"id": 18, "name": "Drama"}]
        # The two return values are distinct objects (deepcopy contract).
        assert first is not second
        assert first["genres"] is not second["genres"]

    def test_fetch_movie_details_no_key_returns_none_without_caching(self):
        """Mirrors the show-details no-key short-circuit: without a key the
        public wrapper returns None and never populates the inner LRU."""
        from app.matcher.tmdb_client import _fetch_movie_details_cached

        with patch("app.services.config_service.get_config_sync") as cfg:
            cfg.return_value.tmdb_api_key = ""
            assert fetch_movie_details(27205) is None
        assert _fetch_movie_details_cached.cache_info().currsize == 0

    def test_fetch_movie_details_returns_title_and_caches(self):
        """A configured key fetches /movie/{id}; the title is read from the
        ``title`` field and a second call is served from cache (one round-trip).
        Mirrors ``test_fetch_show_details_returns_independent_dict_per_call``."""
        with patch("app.matcher.tmdb_client.requests.get") as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.raise_for_status = Mock()
            mock_response.json.return_value = {"title": "Inception", "runtime": 148}
            mock_get.return_value = mock_response
            with patch("app.services.config_service.get_config_sync") as cfg:
                cfg.return_value.tmdb_api_key = "test"
                first = fetch_movie_details(27205)
                # Caller mutates the returned dict.
                first["title"] = "MUTATED"
                second = fetch_movie_details(27205)

        assert mock_get.call_count == 1
        # Mutation on the first object did NOT leak into the cached second.
        assert second["title"] == "Inception"
        assert first is not second


@pytest.mark.unit
class TestFetchShowIdVariations:
    """Tests specifically for the variation generation bug fix."""

    @patch("app.matcher.tmdb_client.requests.get")
    def test_clean_name_generates_multiple_variations(self, mock_get):
        """
        BUG FIX TEST: 'Arrested Development' should try multiple variations.
        Before fix: tried 1 variation
        After fix: tried 3+ variations
        """
        # Setup: Capture queries at call time (params dict gets mutated)
        captured_queries = []

        def capture_call(*args, **kwargs):
            if "params" in kwargs and "query" in kwargs["params"]:
                # Make a copy of the query at call time
                captured_queries.append(kwargs["params"]["query"])
            return Mock(status_code=200, text="{}", json=lambda: TMDB_SEARCH_EMPTY)

        mock_get.side_effect = capture_call

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_show_id("Arrested Development")

        assert result is None
        # After fix, should try multiple variations (not just 1)
        assert len(captured_queries) >= 2, (
            f"Should try multiple variations for clean names, "
            f"got {len(captured_queries)}: {captured_queries}"
        )

        # Verify different queries were tried
        assert "Arrested Development" in captured_queries, (
            f"Original name should be tried, got: {captured_queries}"
        )
        assert len(set(captured_queries)) > 1, (
            f"Should try multiple unique variations, got: {set(captured_queries)}"
        )

    @patch("app.matcher.tmdb_client.requests.get")
    def test_the_prefix_variation_tried(self, mock_get):
        """Test that 'The' prefix is removed as variation."""
        # First call fails, second succeeds
        mock_get.side_effect = [
            Mock(status_code=200, json=lambda: TMDB_SEARCH_EMPTY),
            Mock(status_code=200, json=lambda: TMDB_SEARCH_OFFICE),
        ]

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_show_id("The Office")

        assert result == "2316"
        queries = [call[1]["params"]["query"] for call in mock_get.call_args_list]
        assert "The Office" in queries
        assert "Office" in queries, "'The' prefix should be removed as variation"

    @patch("app.matcher.tmdb_client.requests.get")
    def test_punctuation_colon_variations_tried(self, mock_get):
        """Test colon variations are tried (: -> space-dash, : -> empty)."""
        # First attempts fail, then colon variation succeeds
        mock_get.side_effect = [
            Mock(status_code=200, json=lambda: TMDB_SEARCH_EMPTY),
            Mock(status_code=200, json=lambda: TMDB_SEARCH_STAR_TREK),
        ]

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_show_id("Star Trek: TNG")

        assert result == "655"
        assert mock_get.call_count >= 2, "Should try colon variations"

    @patch("app.matcher.tmdb_client.requests.get")
    def test_ampersand_to_and_variation(self, mock_get):
        """Test that & is converted to 'and' as variation."""
        mock_get.side_effect = [
            Mock(status_code=200, json=lambda: TMDB_SEARCH_EMPTY),
            Mock(
                status_code=200,
                json=lambda: {"results": [{"id": 123, "name": "Law and Order"}]},
            ),
        ]

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_show_id("Law & Order")

        assert result == "123"
        queries = [call[1]["params"]["query"] for call in mock_get.call_args_list]
        # Should try converting & to "and"
        assert any("and" in q.lower() for q in queries)

    @patch("app.matcher.tmdb_client.requests.get")
    def test_common_word_removal_variations(self, mock_get):
        """Test that common words like 'Season', 'Complete', 'Series' are removed."""

        # Return empty for first few attempts, then success
        def side_effect(*args, **kwargs):
            query = kwargs.get("params", {}).get("query", "")
            # Return success only for "Breaking Bad" (clean name)
            if query == "Breaking Bad":
                return Mock(status_code=200, text="{}", json=lambda: TMDB_SEARCH_BREAKING_BAD)
            return Mock(status_code=200, text="{}", json=lambda: TMDB_SEARCH_EMPTY)

        mock_get.side_effect = side_effect

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_show_id("Breaking Bad Complete Series")

        assert result == "1396"
        queries = [
            call[1]["params"]["query"]
            for call in mock_get.call_args_list
            if "params" in call[1] and "query" in call[1]["params"]
        ]
        # Should try variations
        assert "Breaking Bad Complete Series" in queries, "Should try original"
        # Should eventually try without both "Complete" and "Series"
        assert any("complete" not in q.lower() and "series" not in q.lower() for q in queries), (
            f"Should remove common words as variation. Tried: {queries}"
        )


@pytest.mark.unit
class TestFetchShowIdExactMatch:
    """Tests for exact match functionality."""

    @patch("app.matcher.tmdb_client.requests.get")
    def test_exact_match_returns_immediately(self, mock_get):
        """Test that exact match returns without trying variations."""
        mock_get.return_value = Mock(status_code=200, json=lambda: TMDB_SEARCH_ARRESTED_DEVELOPMENT)

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_show_id("Arrested Development")

        assert result == "4589"
        assert mock_get.call_count == 1, "Should not try variations if exact match succeeds"

    @patch("app.matcher.tmdb_client.requests.get")
    def test_returns_first_result_from_multiple_matches(self, mock_get):
        """Test that first result is returned when multiple matches exist."""
        mock_get.return_value = Mock(
            status_code=200,
            json=lambda: {
                "results": [
                    {"id": 111, "name": "Show A"},
                    {"id": 222, "name": "Show B"},
                ]
            },
        )

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_show_id("Test Show")

        assert result == "111", "Should return first result"

    @patch("app.matcher.tmdb_client.requests.get")
    def test_no_api_key_returns_none(self, mock_get):
        """Test that missing API key returns None."""
        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = None
            result = fetch_show_id("Test Show")

            assert result is None
            assert mock_get.call_count == 0, "Should not make API call without key"

    @patch("app.matcher.tmdb_client.requests.get")
    def test_api_error_returns_none(self, mock_get):
        """Test that API error returns None.

        Mock now actually raises HTTPError on raise_for_status() — the
        previous version of this test relied on the function having an
        ``if response.status_code == 200:`` guard that silently fell
        through to return None, which let a 429/500 get permanently
        cached as None by @lru_cache. With raise_for_status() in place,
        a transient HTTPError propagates through retries, exhausts, and
        the public wrapper's try/except returns None — without poisoning
        the cache.
        """
        err_response = Mock(status_code=500)
        err_response.raise_for_status = Mock(
            side_effect=requests.exceptions.HTTPError("500 Server Error")
        )
        mock_get.return_value = err_response

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_show_id("Test Show")

        assert result is None
        # Critical: the post-retry HTTPError must NOT be cached as None.
        # If it were, the next call for "Test Show" would return None
        # from the cache without ever hitting the network again.
        assert _fetch_show_id_cached.cache_info().currsize == 0


@pytest.mark.unit
class TestFetchSeasonDetails:
    """Tests for season details fetching."""

    @patch("app.matcher.tmdb_client.requests.get")
    def test_fetch_season_details_success(self, mock_get):
        """Test successful season details fetch."""
        mock_get.return_value = Mock(status_code=200, json=lambda: TMDB_SEASON_DETAILS_S01_3EP)

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_season_details("4589", 1)

        assert result == 3, "Should return episode count"
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "4589" in call_args[0][0]
        assert "season/1" in call_args[0][0]

    @patch("app.matcher.tmdb_client.requests.get")
    def test_fetch_season_details_no_api_key(self, mock_get):
        """Test that missing API key returns 0 without making API calls."""
        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = None
            result = fetch_season_details("4589", 1)

            # Returns 0 early when API key is missing
            assert result == 0
            assert mock_get.call_count == 0, "Should not make API call without key"

    @patch("app.matcher.tmdb_client.requests.get")
    def test_fetch_season_details_api_error(self, mock_get):
        """Test that API error returns 0."""
        mock_response = Mock(status_code=404)
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("404 Not Found")
        mock_get.return_value = mock_response

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_season_details("4589", 1)

        # Returns 0 on API error
        assert result == 0

    @patch("app.matcher.tmdb_client.requests.get")
    def test_fetch_season_details_empty_episodes(self, mock_get):
        """Test that season with no episodes returns 0."""
        mock_get.return_value = Mock(status_code=200, json=lambda: {"episodes": []})

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_season_details("4589", 1)

        assert result == 0


@pytest.mark.unit
class TestFetchSeasonEpisodeRuntimes:
    """``fetch_season_episode_runtimes`` feeds the disc-identification
    classifier one runtime list per TV disc. A multi-disc box-set rip of the
    same season would otherwise re-fetch identical runtime data once per disc;
    the @lru_cache inner (mirroring fetch_season_details) collapses those
    repeats to a single TMDB round-trip for the lifetime of the process."""

    @patch("app.matcher.tmdb_client.requests.get")
    def test_repeat_call_same_args_hits_network_once(self, mock_get):
        mock_get.return_value = Mock(
            status_code=200,
            json=lambda: {"episodes": [{"runtime": 42}, {"runtime": 41}]},
            raise_for_status=Mock(),
        )
        with patch("app.services.config_service.get_config_sync") as cfg:
            cfg.return_value.tmdb_api_key = "test_key"
            first = tmdb_client.fetch_season_episode_runtimes("4589", 1)
            second = tmdb_client.fetch_season_episode_runtimes("4589", 1)

        assert first == [42, 41]
        assert first == second
        # The second call for the same (show_id, season_number) must be served
        # from the process-lifetime LRU — only one TMDB round-trip total.
        assert mock_get.call_count == 1

    @patch("app.matcher.tmdb_client.requests.get")
    def test_clear_caches_flushes_runtimes(self, mock_get):
        """``clear_caches()`` must flush this LRU too, otherwise a TMDB key
        rotation would leave runtimes fetched with the revoked key pinned for
        the rest of the process."""
        mock_get.return_value = Mock(
            status_code=200,
            json=lambda: {"episodes": [{"runtime": 42}]},
            raise_for_status=Mock(),
        )
        with patch("app.services.config_service.get_config_sync") as cfg:
            cfg.return_value.tmdb_api_key = "test_key"
            tmdb_client.fetch_season_episode_runtimes("4589", 1)
            clear_caches()
            tmdb_client.fetch_season_episode_runtimes("4589", 1)
        assert mock_get.call_count == 2


@pytest.mark.unit
class TestVariationEdgeCases:
    """Tests for edge cases in variation generation."""

    @patch("app.matcher.tmdb_client.requests.get")
    def test_empty_string_returns_none(self, mock_get):
        """Test that empty string returns None gracefully."""
        mock_get.return_value = Mock(status_code=200, text="{}", json=lambda: TMDB_SEARCH_EMPTY)

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_show_id("")

        assert result is None

    @patch("app.matcher.tmdb_client.requests.get")
    def test_show_name_with_year_removes_year(self, mock_get):
        """Test that year in parentheses is removed as variation."""

        def side_effect(*args, **kwargs):
            query = kwargs.get("params", {}).get("query", "")
            if query == "Test Show":
                return Mock(
                    status_code=200,
                    json=lambda: {"results": [{"id": 999, "name": "Test Show"}]},
                )
            return Mock(status_code=200, json=lambda: TMDB_SEARCH_EMPTY)

        mock_get.side_effect = side_effect

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_show_id("Test Show (2020)")

        assert result == "999"
        queries = [call[1]["params"]["query"] for call in mock_get.call_args_list]
        assert "Test Show (2020)" in queries
        # Should try without year
        assert any("2020" not in q for q in queries)

    @patch("app.matcher.tmdb_client.requests.get")
    def test_show_name_with_underscores(self, mock_get):
        """Test that underscores are normalized to spaces."""
        mock_get.side_effect = [
            Mock(status_code=200, json=lambda: TMDB_SEARCH_EMPTY),
            Mock(status_code=200, json=lambda: TMDB_SEARCH_BREAKING_BAD),
        ]

        with patch("app.services.config_service.get_config_sync") as mock_conf:
            mock_conf.return_value.tmdb_api_key = "test_key"
            result = fetch_show_id("Breaking_Bad")

        assert result == "1396"
        queries = [call[1]["params"]["query"] for call in mock_get.call_args_list]
        # Should try with spaces instead of underscores
        assert any(" " in q and "_" not in q for q in queries)


@pytest.mark.unit
class TestFetchMovieRuntime:
    """fetch_movie_runtime supplies the canonical runtime used to pick a movie's
    main feature apart from its long bonus tracks."""

    @patch("app.matcher.tmdb_client.requests.get")
    def test_returns_runtime_minutes(self, mock_get):
        mock_get.return_value = Mock(
            status_code=200, json=lambda: {"runtime": 149}, raise_for_status=Mock()
        )
        assert tmdb_client.fetch_movie_runtime("1317288", "test_key") == 149
        assert "/movie/1317288" in mock_get.call_args[0][0]

    @patch("app.matcher.tmdb_client.requests.get")
    def test_zero_runtime_returns_none(self, mock_get):
        """TMDB returns 0/None runtime when unknown — treat as no signal."""
        mock_get.return_value = Mock(
            status_code=200, json=lambda: {"runtime": 0}, raise_for_status=Mock()
        )
        assert tmdb_client.fetch_movie_runtime("123", "test_key") is None

    @patch("app.matcher.tmdb_client.requests.get")
    def test_no_api_key_returns_none(self, mock_get):
        assert tmdb_client.fetch_movie_runtime("123", "") is None
        assert mock_get.call_count == 0

    @patch("app.matcher.tmdb_client.requests.get")
    def test_api_error_returns_none(self, mock_get):
        err = Mock(status_code=500)
        err.raise_for_status = Mock(side_effect=requests.exceptions.HTTPError("500 Server Error"))
        mock_get.return_value = err
        assert tmdb_client.fetch_movie_runtime("123", "test_key") is None


class TestFetchSeasonEpisodesOverview:
    def test_includes_overview(self):
        from unittest.mock import patch

        from app.matcher.tmdb_client import fetch_season_episodes

        fake = {
            "episodes": [
                {"episode_number": 1, "name": "Pilot", "runtime": 42, "overview": "A new dawn."},
                {"episode_number": 2, "name": "Cargo", "runtime": 41, "overview": ""},
            ]
        }
        with patch("app.matcher.tmdb_client._tmdb_get_json", return_value=fake):
            eps = fetch_season_episodes("1234", 1, "fake-key")

        assert len(eps) == 2
        assert eps[0]["overview"] == "A new dawn."
        assert eps[1]["overview"] == ""

    def test_overview_missing_defaults_to_empty(self):
        from unittest.mock import patch

        from app.matcher.tmdb_client import fetch_season_episodes

        fake = {"episodes": [{"episode_number": 1, "name": "Pilot", "runtime": 42}]}
        with patch("app.matcher.tmdb_client._tmdb_get_json", return_value=fake):
            eps = fetch_season_episodes("1234", 1, "fake-key")

        assert eps[0]["overview"] == ""


@pytest.mark.unit
class TestFetchEpisodeGroups:
    """``fetch_episode_groups`` lists the alternative orderings (DVD, digital,
    absolute, ...) TMDB has for a show. It backs the episode-ordering feature's
    canonical->output projection. Caller supplies the key (matcher-layer
    isolation, same as fetch_season_episodes/fetch_movie_runtime)."""

    @patch("app.matcher.tmdb_client.requests.get")
    def test_returns_results_list(self, mock_get):
        payload = {
            "results": [
                {"id": "grp_dvd", "name": "DVD Order", "type": 3, "group_count": 1},
                {"id": "grp_air", "name": "Aired Order", "type": 1, "group_count": 1},
            ]
        }
        mock_get.return_value = Mock(status_code=200, json=lambda: payload, raise_for_status=Mock())
        groups = tmdb_client.fetch_episode_groups("1437", "test_key")
        assert [g["id"] for g in groups] == ["grp_dvd", "grp_air"]
        assert "/tv/1437/episode_groups" in mock_get.call_args[0][0]

    @patch("app.matcher.tmdb_client.requests.get")
    def test_no_api_key_returns_empty(self, mock_get):
        assert tmdb_client.fetch_episode_groups("1437", "") == []
        assert mock_get.call_count == 0

    @patch("app.matcher.tmdb_client.requests.get")
    def test_api_error_returns_empty(self, mock_get):
        err = Mock(status_code=500)
        err.raise_for_status = Mock(side_effect=requests.exceptions.HTTPError("500"))
        mock_get.return_value = err
        assert tmdb_client.fetch_episode_groups("1437", "test_key") == []

    @patch("app.matcher.tmdb_client.requests.get")
    def test_missing_results_returns_empty(self, mock_get):
        mock_get.return_value = Mock(status_code=200, json=lambda: {}, raise_for_status=Mock())
        assert tmdb_client.fetch_episode_groups("1437", "test_key") == []

    @patch("app.matcher.tmdb_client.requests.get")
    def test_non_numeric_show_id_rejected_without_request(self, mock_get):
        # SSRF guard: a non-numeric show id must never reach the request URL.
        assert tmdb_client.fetch_episode_groups("../../evil", "test_key") == []
        assert mock_get.call_count == 0

    @patch("app.matcher.tmdb_client.requests.get")
    def test_second_call_is_served_from_cache(self, mock_get):
        payload = {"results": [{"id": "grp_dvd", "name": "DVD Order", "type": 3}]}
        mock_get.return_value = Mock(status_code=200, json=lambda: payload, raise_for_status=Mock())
        first = tmdb_client.fetch_episode_groups("1437", "test_key")
        second = tmdb_client.fetch_episode_groups("1437", "test_key")
        assert first == second
        assert mock_get.call_count == 1

    @patch("app.matcher.tmdb_client.requests.get")
    def test_clear_caches_flushes_groups(self, mock_get):
        payload = {"results": [{"id": "grp_dvd", "name": "DVD Order", "type": 3}]}
        mock_get.return_value = Mock(status_code=200, json=lambda: payload, raise_for_status=Mock())
        tmdb_client.fetch_episode_groups("1437", "test_key")
        clear_caches()
        tmdb_client.fetch_episode_groups("1437", "test_key")
        assert mock_get.call_count == 2


@pytest.mark.unit
class TestFetchEpisodeGroup:
    """``fetch_episode_group`` returns a single ordering's full mapping:
    groups[] (each a 'season' with an order), each episode carrying its
    canonical season_number/episode_number plus its position (order) in the
    group. Returns None on failure so the projection falls back to canonical."""

    @patch("app.matcher.tmdb_client.requests.get")
    def test_returns_group_detail(self, mock_get):
        detail = {
            "id": "grp_dvd",
            "name": "DVD Order",
            "type": 3,
            "groups": [
                {
                    "order": 0,
                    "name": "Season 1",
                    "episodes": [
                        {"season_number": 1, "episode_number": 1, "order": 0},
                        {"season_number": 1, "episode_number": 3, "order": 1},
                    ],
                }
            ],
        }
        mock_get.return_value = Mock(status_code=200, json=lambda: detail, raise_for_status=Mock())
        result = tmdb_client.fetch_episode_group("grp_dvd", "test_key")
        assert result["id"] == "grp_dvd"
        assert result["groups"][0]["episodes"][1]["episode_number"] == 3
        assert "/tv/episode_group/grp_dvd" in mock_get.call_args[0][0]

    @patch("app.matcher.tmdb_client.requests.get")
    def test_no_api_key_returns_none(self, mock_get):
        assert tmdb_client.fetch_episode_group("grp_dvd", "") is None
        assert mock_get.call_count == 0

    @patch("app.matcher.tmdb_client.requests.get")
    def test_api_error_returns_none(self, mock_get):
        err = Mock(status_code=404)
        err.raise_for_status = Mock(side_effect=requests.exceptions.HTTPError("404"))
        mock_get.return_value = err
        assert tmdb_client.fetch_episode_group("missing", "test_key") is None

    @patch("app.matcher.tmdb_client.requests.get")
    def test_second_call_is_served_from_cache(self, mock_get):
        detail = {"id": "grp_dvd", "groups": []}
        mock_get.return_value = Mock(status_code=200, json=lambda: detail, raise_for_status=Mock())
        tmdb_client.fetch_episode_group("grp_dvd", "test_key")
        tmdb_client.fetch_episode_group("grp_dvd", "test_key")
        assert mock_get.call_count == 1

    @patch("app.matcher.tmdb_client.requests.get")
    def test_path_injecting_group_id_rejected_without_request(self, mock_get):
        # SSRF guard: a group id with path separators must never reach the URL.
        assert tmdb_client.fetch_episode_group("../../tv/popular", "test_key") is None
        assert mock_get.call_count == 0
