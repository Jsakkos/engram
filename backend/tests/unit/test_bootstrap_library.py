"""Tests for the bootstrap-library CLI utility."""

from app.scripts.bootstrap_library import parse_episode_filename, walk_library


def test_parse_episode_filename_standard():
    assert parse_episode_filename("Arrested Development - S01E07.mkv") == (
        "Arrested Development",
        1,
        7,
    )
    assert parse_episode_filename("The Gilded Age - S03E08.mkv") == ("The Gilded Age", 3, 8)
    assert parse_episode_filename("Star Trek The Next Generation - S07E09.mkv") == (
        "Star Trek The Next Generation",
        7,
        9,
    )


def test_parse_episode_filename_rejects_garbage():
    assert parse_episode_filename("movie.mkv") is None
    assert parse_episode_filename("Show.mkv") is None
    assert parse_episode_filename("Show - 1x07.mkv") is None  # only the canonical SxxExx form


def test_walk_library_skips_extras(tmp_path):
    show = tmp_path / "Foo"
    season = show / "Season 1"
    season.mkdir(parents=True)
    extras = season / "Extras"
    extras.mkdir()
    (season / "Foo - S01E01.mkv").touch()
    (season / "Foo - S01E02.mkv").touch()
    (extras / "Foo Extra t00.mkv").touch()  # should be ignored

    found = list(walk_library(tmp_path))
    names = sorted(p.name for p, _ in found)
    assert names == ["Foo - S01E01.mkv", "Foo - S01E02.mkv"]
