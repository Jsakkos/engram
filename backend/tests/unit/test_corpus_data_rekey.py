"""Same-name show isolation for the runtime downloaded-SRT scrape cache.

PR #288 re-keyed the PRECOMPUTED corpus (``<cache>/precomputed/<tmdb_id>/``) by
TMDB id but left the runtime SRT scrape cache (``<cache>/data/<show>/``) keyed by
show name. Two same-named shows (Frasier 1993 #3452 vs the 2023 revival #195241)
would therefore collide into one ``data/Frasier/`` directory and
cross-contaminate references.

These tests pin the runtime ``data/`` cache to ``<cache>/data/<tmdb_id>/`` (with a
fallback to the sanitized name when no id is known) across every reader and
writer, so the keying is all-or-nothing consistent:

- A writer (``download_subtitles``) keyed by a tmdb_id and a reader
  (``LocalSubtitleProvider`` / ``EpisodeMatcher``) keyed by the SAME tmdb_id
  must round-trip to the same directory, while a twin keyed by a different id
  reads a distinct directory.
- A single-name show with no id still resolves under the sanitized-name dir
  (regression: legacy caches and flat imports keep working).
"""

from unittest.mock import Mock, patch

import pytest

from app.matcher.subtitle_provider import LocalSubtitleProvider
from app.matcher.testing_service import download_subtitles

_VALID_SRT = "1\n00:00:00,000 --> 00:00:02,000\n{line}\n"


def _write_srt(path, line):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_VALID_SRT.format(line=line), encoding="utf-8")


@pytest.mark.unit
class TestLocalProviderTmdbKeying:
    """``LocalSubtitleProvider`` reads ``<cache>/data/<tmdb_id>/`` when an id is
    known (isolating two same-named shows) and falls back to the sanitized name
    otherwise."""

    def test_same_name_shows_resolve_to_distinct_dirs(self, tmp_path):
        data = tmp_path / "data"
        _write_srt(data / "3452" / "Frasier - S01E01.srt", "frasier 1993 pilot")
        _write_srt(data / "195241" / "Frasier - S01E01.srt", "frasier 2023 revival")

        provider = LocalSubtitleProvider(cache_dir=tmp_path)

        subs_1993 = provider.get_subtitles("Frasier", 1, tmdb_id=3452)
        subs_2023 = provider.get_subtitles("Frasier", 1, tmdb_id=195241)

        assert [s.path for s in subs_1993] == [data / "3452" / "Frasier - S01E01.srt"]
        assert [s.path for s in subs_2023] == [data / "195241" / "Frasier - S01E01.srt"]
        # The two twins never read each other's references.
        assert subs_1993[0].path != subs_2023[0].path

    def test_name_fallback_when_tmdb_id_unknown(self, tmp_path):
        # Regression: a normal single-name show with no id still finds its subs
        # under the sanitized-name dir (legacy caches, flat imports).
        data = tmp_path / "data"
        _write_srt(data / "Breaking Bad" / "Breaking Bad - S01E01.srt", "say my name")

        provider = LocalSubtitleProvider(cache_dir=tmp_path)
        subs = provider.get_subtitles("Breaking Bad", 1)  # no tmdb_id

        assert [s.path.name for s in subs] == ["Breaking Bad - S01E01.srt"]


@pytest.mark.unit
class TestEpisodeMatcherReferenceKeying:
    """``EpisodeMatcher.get_reference_files`` keys ``data/`` by its
    ``expected_tmdb_id`` so two same-named shows never share references."""

    def _matcher(self, cache_dir, tmdb_id):
        from app.matcher.episode_identification import EpisodeMatcher

        return EpisodeMatcher(
            cache_dir=cache_dir,
            show_name="Frasier",
            expected_tmdb_id=tmdb_id,
            model_name="tiny",  # never loaded; we never call ASR
        )

    def test_reference_files_isolated_by_tmdb_id(self, tmp_path):
        data = tmp_path / "data"
        _write_srt(data / "3452" / "Frasier - S01E01.srt", "1993 s01e01")
        _write_srt(data / "195241" / "Frasier - S01E01.srt", "2023 s01e01")
        _write_srt(data / "195241" / "Frasier - S01E02.srt", "2023 s01e02")

        m1993 = self._matcher(tmp_path, 3452)
        m2023 = self._matcher(tmp_path, 195241)

        assert [p.name for p in m1993.get_reference_files(1)] == ["Frasier - S01E01.srt"]
        assert sorted(p.name for p in m2023.get_reference_files(1)) == [
            "Frasier - S01E01.srt",
            "Frasier - S01E02.srt",
        ]

    def test_name_fallback_when_no_expected_id(self, tmp_path):
        data = tmp_path / "data"
        _write_srt(data / "Frasier" / "Frasier - S01E01.srt", "legacy name-keyed cache")

        matcher = self._matcher(tmp_path, None)
        assert [p.name for p in matcher.get_reference_files(1)] == ["Frasier - S01E01.srt"]


@pytest.mark.unit
class TestDownloadSubtitlesTmdbKeying:
    """``download_subtitles`` writes to ``data/<tmdb_id>/`` when a tmdb_id is
    supplied, and the LocalSubtitleProvider reader keyed by the same id finds
    them — proving the writer/reader contract holds end-to-end."""

    @patch("app.matcher.testing_service.TVSubtitlesClient")
    @patch("app.matcher.testing_service.Addic7edClient")
    @patch("app.matcher.testing_service.fetch_show_details")
    @patch("app.matcher.testing_service.fetch_season_details")
    @patch("app.matcher.testing_service.fetch_show_id")
    @patch("app.services.config_service.get_config_sync")
    def test_download_writes_id_dir_and_reader_finds_it(
        self,
        mock_config_sync,
        mock_show_id,
        mock_season,
        mock_details,
        mock_addic7ed,
        mock_tvsub,
        tmp_path,
    ):
        mock_config = Mock()
        mock_config.subtitles_cache_path = str(tmp_path)
        mock_config.opensubtitles_api_key = None  # skip the OpenSubtitles REST path
        mock_config_sync.return_value = mock_config

        mock_details.return_value = {"name": "Frasier"}
        mock_season.return_value = 2  # 2 episodes

        # TVsubtitles fallback stubbed to miss (avoid live HTTP).
        tvsub = Mock()
        tvsub.get_best_subtitle.return_value = None
        tvsub.download_subtitle.return_value = None
        mock_tvsub.return_value = tvsub

        addic7ed = Mock()
        mock_addic7ed.return_value = addic7ed
        addic7ed.get_best_subtitle.return_value = Mock()

        def _download(subtitle, save_path):
            # >= 50 bytes with a real "-->" header so is_valid_srt_file accepts it.
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text(
                "1\n00:00:00,000 --> 00:00:02,000\n"
                f"Subtitle for {save_path.name}\n\n"
                "2\n00:00:02,000 --> 00:00:04,000\nMore dialogue here\n"
            )
            return save_path

        addic7ed.download_subtitle.side_effect = _download

        result = download_subtitles("Frasier", 1, tmdb_id=195241)

        # Writer keyed the dir by the supplied tmdb_id, NOT the show name.
        id_dir = tmp_path / "data" / "195241"
        assert id_dir.is_dir()
        assert not (tmp_path / "data" / "Frasier").exists()
        assert result["cache_dir"] == str(id_dir)

        # Same-name disambiguation must use the id directly, never a name lookup.
        mock_show_id.assert_not_called()

        # The reader keyed by the same id finds them; the twin's dir stays empty.
        provider = LocalSubtitleProvider(cache_dir=tmp_path)
        assert len(provider.get_subtitles("Frasier", 1, tmdb_id=195241)) == 2
        assert provider.get_subtitles("Frasier", 1, tmdb_id=3452) == []


@pytest.mark.unit
class TestGetSubtitlesAddic7edTmdbKeying:
    """The Addic7ed scrape function keys its ``data/`` cache dir by the supplied
    tmdb_id and resolves the season by id rather than a name search."""

    def test_keys_data_dir_by_tmdb_id(self, tmp_path, monkeypatch):
        from app.matcher import addic7ed_client as ac
        from app.matcher import tmdb_client as tmdb

        # Client is never used (0 episodes), but must be constructible.
        monkeypatch.setattr(ac, "Addic7edClient", lambda: object())
        monkeypatch.setattr(tmdb, "fetch_season_details", lambda sid, season: 0)

        def _no_name_lookup(name):
            raise AssertionError("tmdb_id supplied; must not resolve show id by name")

        monkeypatch.setattr(tmdb, "fetch_show_id", _no_name_lookup)

        ac.get_subtitles_addic7ed("Frasier", {1}, tmp_path, tmdb_id=195241)

        assert (tmp_path / "data" / "195241").is_dir()
        assert not (tmp_path / "data" / "Frasier").exists()


@pytest.mark.unit
class TestPackResolveCanonicalNumericDir:
    """``pack_subtitle_cache._resolve_canonical`` resolves a numeric (id-keyed)
    ``data/`` dir straight to its tmdb_id instead of searching TMDB by the dir
    name (which would fail for a directory literally named e.g. ``195241``)."""

    def test_numeric_dir_resolves_id_directly(self, psc, monkeypatch):
        monkeypatch.setattr(
            psc,
            "fetch_show_details",
            lambda cid: {"name": "Frasier"} if cid == 195241 else None,
        )

        def _no_name_search(name):
            raise AssertionError("numeric id dir must resolve by id, not a name search")

        monkeypatch.setattr(psc, "fetch_show_id", _no_name_search)

        canonical, tmdb_id, resolved = psc._resolve_canonical("195241", offline=False)
        assert canonical == "Frasier"
        assert tmdb_id == 195241
        assert resolved is True

    def test_nonnumeric_dir_keeps_name_search(self, psc, monkeypatch):
        # Legacy name-keyed dirs still resolve via the TMDB name search.
        monkeypatch.setattr(psc, "fetch_show_id", lambda name: 1396)
        monkeypatch.setattr(psc, "fetch_show_details", lambda cid: {"name": "Breaking Bad"})

        canonical, tmdb_id, resolved = psc._resolve_canonical("Breaking Bad", offline=False)
        assert canonical == "Breaking Bad"
        assert tmdb_id == 1396
        assert resolved is True


@pytest.mark.unit
class TestNormalizeSubtitleCacheTmdbKeying:
    """normalize_subtitle_cache must keep filenames NAME-prefixed even inside an
    id-keyed dir (resolving the id → name via TMDB). Rewriting them to an id
    prefix would make the downloader's name-based cache-hit check miss every
    episode."""

    def test_resolve_prefix_numeric_dir_returns_name(self, nsc, monkeypatch):
        monkeypatch.setattr(
            "app.matcher.tmdb_client.fetch_show_details",
            lambda cid: {"name": "Frasier"} if cid == 195241 else None,
        )
        assert nsc._resolve_prefix("195241") == "Frasier"

    def test_resolve_prefix_legacy_name_dir_is_itself(self, nsc):
        # No TMDB call for a non-numeric (legacy) dir name.
        assert nsc._resolve_prefix("Breaking Bad") == "Breaking Bad"

    def test_id_keyed_dir_gets_name_prefixed_canonical_files(self, nsc, tmp_path, monkeypatch):
        show_dir = tmp_path / "data" / "195241"
        # A non-canonical filename that parses to S01E02 (e.g. a manual drop).
        _write_srt(show_dir / "Frasier 1x02 Title.srt", "dialogue long enough to be valid srt")

        monkeypatch.setattr(
            "app.matcher.tmdb_client.fetch_show_details",
            lambda cid: {"name": "Frasier"} if cid == 195241 else None,
        )
        prefix = nsc._resolve_prefix(show_dir.name)
        tally = nsc.Tally()
        nsc._normalize_show_dir(show_dir, prefix, dry_run=False, tally=tally)

        # Canonicalized to the NAME prefix, never the tmdb_id.
        assert (show_dir / "Frasier - S01E02.srt").exists()
        assert not (show_dir / "195241 - S01E02.srt").exists()
