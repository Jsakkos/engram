"""Episode-title lookup for the ``{episode_title}`` filename placeholder (#826)."""

from unittest.mock import patch

import pytest

from app.core import episode_titles
from app.core.episode_namespace import current_namespace
from app.core.organizer import organize_tv_episode
from app.models.app_config import AppConfig

TITLED_FORMAT = "{show} - S{season:02d}E{episode:02d} - {episode_title}"


@pytest.fixture(autouse=True)
def _fresh_cache():
    episode_titles.clear_roster_cache()
    yield
    episode_titles.clear_roster_cache()


def _roster(*names: str, start: int = 1) -> list[dict]:
    return [{"episode_number": start + i, "name": n} for i, n in enumerate(names)]


class _Rosters:
    """Stands in for ``season_episodes``, recording what it was asked and under which namespace."""

    def __init__(self, tmdb=None, tvdb=None):
        self.tmdb, self.tvdb = tmdb or {}, tvdb or {}
        self.calls: list[tuple[str, str, int]] = []

    def __call__(self, tmdb_show_id, season, api_key):
        ns = current_namespace()
        self.calls.append((ns, str(tmdb_show_id), season))
        source = self.tvdb if ns == "tvdb" else self.tmdb
        return source.get(season, [])


def _lookup(code, rosters, *, namespace=None, tmdb_id="1618"):
    with patch.object(episode_titles, "season_episodes", rosters):
        return episode_titles.episode_title_for_code(code, tmdb_id, namespace, "key")


class TestEpisodeTitleForCode:
    def test_single_episode(self):
        rosters = _Rosters(tmdb={1: _roster("Pilot", "Second")})
        assert _lookup("S01E02", rosters) == "Second"

    def test_multi_episode_titles_are_joined_with_an_ampersand(self):
        rosters = _Rosters(tmdb={1: _roster("Title 1", "Title 2", "Title 3")})
        assert _lookup("S01E01-E02", rosters) == "Title 1 & Title 2"
        assert _lookup("S01E01-E03", rosters) == "Title 1 & Title 2 & Title 3"

    def test_gapped_run_on_code_joins_only_the_named_episodes(self):
        rosters = _Rosters(tmdb={1: _roster("A", "B", "C")})
        assert _lookup("S01E01E03", rosters) == "A & C"

    def test_exact_duplicate_titles_collapse(self):
        rosters = _Rosters(tmdb={1: _roster("The Pilot", "the  pilot", "Other")})
        assert _lookup("S01E01-E03", rosters) == "The Pilot & Other"

    def test_titles_that_only_look_alike_are_both_kept(self):
        rosters = _Rosters(tmdb={1: _roster("Secret Origins (1)", "Secret Origins (2)")})
        assert _lookup("S01E01-E02", rosters) == "Secret Origins (1) & Secret Origins (2)"

    def test_unnamed_episode_is_skipped_but_the_rest_still_read(self):
        rosters = _Rosters(tmdb={1: _roster("A", "", "C")})
        assert _lookup("S01E01-E03", rosters) == "A & C"

    def test_episode_missing_from_the_roster_gives_empty(self):
        rosters = _Rosters(tmdb={1: _roster("A")})
        assert _lookup("S01E09", rosters) == ""

    def test_roster_unavailable_gives_empty(self):
        assert _lookup("S01E01", _Rosters()) == ""

    @pytest.mark.parametrize("code", ["extra", "skip", "", None, "S01"])
    def test_non_episode_codes_give_empty_without_a_lookup(self, code):
        rosters = _Rosters(tmdb={1: _roster("A")})
        assert _lookup(code, rosters) == ""
        assert rosters.calls == []

    def test_no_show_id_gives_empty_without_a_lookup(self):
        rosters = _Rosters(tmdb={1: _roster("A")})
        assert _lookup("S01E01", rosters, tmdb_id=None) == ""
        assert rosters.calls == []

    def test_tmdb_is_the_default_namespace(self):
        rosters = _Rosters(tmdb={1: _roster("From TMDB")}, tvdb={1: _roster("From TVDB")})
        assert _lookup("S01E01", rosters) == "From TMDB"
        assert rosters.calls == [("tmdb", "1618", 1)]

    def test_tvdb_job_reads_the_tvdb_roster(self):
        # Same code, different catalogues: the number only means something in the
        # namespace the job was matched in.
        rosters = _Rosters(
            tmdb={1: _roster("Secret Origins")},
            tvdb={1: _roster("Secret Origins (1)", "Secret Origins (2)")},
        )
        assert _lookup("S01E02", rosters, namespace="tvdb") == "Secret Origins (2)"
        assert rosters.calls[0][0] == "tvdb"

    def test_the_namespace_does_not_leak_out_of_the_lookup(self):
        rosters = _Rosters(tvdb={1: _roster("A")})
        _lookup("S01E01", rosters, namespace="tvdb")
        assert current_namespace() == "tmdb"

    def test_no_namespace_keeps_the_ambient_one(self):
        from app.core.episode_namespace import namespace_context

        rosters = _Rosters(tvdb={1: _roster("Ambient TVDB")})
        with namespace_context("tvdb"):
            assert _lookup("S01E01", rosters, namespace=None) == "Ambient TVDB"

    def test_multi_season_code_lookups_are_per_season(self):
        rosters = _Rosters(tmdb={2: _roster("S2E1", "S2E2")})
        assert _lookup("S02E01-E02", rosters) == "S2E1 & S2E2"
        assert rosters.calls == [("tmdb", "1618", 2)]


class TestRosterCache:
    def test_a_roster_is_fetched_once_per_season(self):
        rosters = _Rosters(tmdb={1: _roster("A", "B", "C")})
        for code in ("S01E01", "S01E02", "S01E03"):
            _lookup(code, rosters)
        assert len(rosters.calls) == 1

    def test_namespaces_do_not_share_a_cache_entry(self):
        rosters = _Rosters(tmdb={1: _roster("A")}, tvdb={1: _roster("B")})
        assert _lookup("S01E01", rosters) == "A"
        assert _lookup("S01E01", rosters, namespace="tvdb") == "B"

    def test_a_miss_is_remembered_so_an_outage_costs_one_timeout_not_one_per_file(self):
        rosters = _Rosters()
        for code in ("S01E01", "S01E02", "S01E03"):
            assert _lookup(code, rosters) == ""
        assert len(rosters.calls) == 1

    def test_a_miss_expires_so_a_recovered_service_is_picked_up(self):
        rosters = _Rosters()
        with patch.object(episode_titles, "_now", return_value=0.0):
            _lookup("S01E01", rosters)
        rosters.tmdb = {1: _roster("Back")}
        with patch.object(episode_titles, "_now", return_value=1_000.0):
            assert _lookup("S01E01", rosters) == "Back"


class TestOrganizeWithTitles:
    """``organize_tv_episode`` end to end through a real library directory."""

    @staticmethod
    def _organize(tmp_path, code, rosters, *, fmt=TITLED_FORMAT, namespace=None, name="x.mkv"):
        src = tmp_path / name
        src.write_bytes(b"x")
        cfg = AppConfig(naming_episode_format=fmt, tmdb_api_key="key")
        with (
            patch("app.services.config_service.get_config_sync", return_value=cfg),
            patch.object(episode_titles, "season_episodes", rosters),
        ):
            return organize_tv_episode(
                src,
                "Show",
                code,
                library_path=tmp_path / "lib",
                conflict_resolution="skip",
                tmdb_id="1618",
                episode_namespace=namespace,
            )

    def test_title_lands_in_the_filename(self, tmp_path):
        rosters = _Rosters(tmdb={1: _roster("Pilot")})
        result = self._organize(tmp_path, "S01E01", rosters)
        assert result["success"], result
        assert result["final_path"].name == "Show - S01E01 - Pilot.mkv"

    def test_combined_track_names_both_episodes_and_both_titles(self, tmp_path):
        rosters = _Rosters(tmdb={1: _roster("Title 1", "Title 2")})
        result = self._organize(tmp_path, "S01E01-E02", rosters)
        assert result["final_path"].name == "Show - S01E01-E02 - Title 1 & Title 2.mkv"

    def test_unknown_title_drops_cleanly(self, tmp_path):
        result = self._organize(tmp_path, "S01E01", _Rosters())
        assert result["final_path"].name == "Show - S01E01.mkv"

    def test_tvdb_job_gets_the_tvdb_title_for_its_tvdb_number(self, tmp_path):
        rosters = _Rosters(
            tmdb={1: _roster("Wrong TMDB title")},
            tvdb={1: _roster("Right TVDB title")},
        )
        result = self._organize(tmp_path, "S01E01", rosters, namespace="tvdb")
        assert result["final_path"].name == "Show - S01E01 - Right TVDB title.mkv"

    def test_default_format_never_looks_a_title_up(self, tmp_path):
        rosters = _Rosters(tmdb={1: _roster("Pilot")})
        result = self._organize(
            tmp_path, "S01E01", rosters, fmt="{show} - S{season:02d}E{episode:02d}"
        )
        assert result["final_path"].name == "Show - S01E01.mkv"
        assert rosters.calls == []

    def test_title_survives_an_episode_group_projection(self, tmp_path):
        """The projected number changes the filename; the title is the canonical episode's."""
        rosters = _Rosters(tmdb={1: _roster("Canonical One")})
        src = tmp_path / "x.mkv"
        src.write_bytes(b"x")
        cfg = AppConfig(naming_episode_format=TITLED_FORMAT, tmdb_api_key="key")
        with (
            patch("app.services.config_service.get_config_sync", return_value=cfg),
            patch.object(episode_titles, "season_episodes", rosters),
            patch("app.core.episode_ordering.project_episode", return_value=(1, 5)),
        ):
            result = organize_tv_episode(
                src,
                "Show",
                "S01E01",
                library_path=tmp_path / "lib",
                tmdb_id="1618",
                ordering="dvd",
            )
        assert result["final_path"].name == "Show - S01E05 - Canonical One.mkv"
