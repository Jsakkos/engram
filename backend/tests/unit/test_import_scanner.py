"""Unit tests for the recursive manual-import scanner."""

from pathlib import Path

from app.core import import_scanner


def _mkv(p: Path, size: int = 1024) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"0" * size)


def test_show_season_disc_layout_groups_per_season(tmp_path: Path):
    # The King of Queens case: Show / Season N / Disc N / *.mkv
    show = tmp_path / "The King of Queens (1998)"
    _mkv(show / "Season 1" / "Disc 1" / "t00.mkv")
    _mkv(show / "Season 1" / "Disc 2" / "t01.mkv")
    _mkv(show / "Season 2" / "Disc 1" / "t02.mkv")

    scan = import_scanner.scan(show)

    assert scan.total_files == 3
    by_season = {u.season: u for u in scan.units}
    assert set(by_season) == {1, 2}
    assert len(by_season[1].files) == 2  # both disc folders rolled into season 1
    assert len(by_season[2].files) == 1
    assert all(u.show_name == "The King of Queens (1998)" for u in scan.units)


def test_disc_only_layout_no_season_is_flat(tmp_path: Path):
    show = tmp_path / "Show Title"
    _mkv(show / "Disc 1" / "a.mkv")
    _mkv(show / "Disc 2" / "b.mkv")

    scan = import_scanner.scan(show)

    assert scan.total_files == 2
    assert len(scan.units) == 1
    assert scan.units[0].season is None
    assert scan.units[0].show_name == "Show Title"
    assert len(scan.units[0].files) == 2


def test_flat_loose_files(tmp_path: Path):
    show = tmp_path / "Seinfeld"
    _mkv(show / "e1.mkv")
    _mkv(show / "e2.mkv")

    scan = import_scanner.scan(show)

    assert len(scan.units) == 1
    assert scan.units[0].season is None
    assert scan.total_files == 2


def test_loose_files_beside_season_folders_are_reported_not_merged(tmp_path: Path):
    show = tmp_path / "Mixed"
    _mkv(show / "Season 1" / "ep.mkv")
    _mkv(show / "stray.mkv")

    scan = import_scanner.scan(show)

    seasons = [u.season for u in scan.units]
    assert seasons == [1]
    assert [p.name for p in scan.loose_files] == ["stray.mkv"]
    assert scan.total_files == 2  # totals still count the loose file


def test_multiple_shows_under_picked_root(tmp_path: Path):
    _mkv(tmp_path / "King of Queens" / "Season 1" / "a.mkv")
    _mkv(tmp_path / "Seinfeld" / "Season 1" / "b.mkv")

    scan = import_scanner.scan(tmp_path)

    shows = {u.show_name for u in scan.units}
    assert shows == {"King of Queens", "Seinfeld"}


def test_single_file_target(tmp_path: Path):
    f = tmp_path / "Some Folder" / "movie.mkv"
    _mkv(f)

    scan = import_scanner.scan(f)

    assert len(scan.units) == 1
    assert scan.units[0].season is None
    assert scan.units[0].files == [f]
    assert scan.units[0].show_name == "Some Folder"


def test_season_inferred_from_nearest_ancestor(tmp_path: Path):
    f = tmp_path / "Show" / "Season 03" / "Disc 2" / "x.mkv"
    _mkv(f)

    scan = import_scanner.scan(tmp_path / "Show")

    assert scan.units[0].season == 3


def test_underscore_show_name_is_cleaned(tmp_path: Path):
    show = tmp_path / "KING_OF_QUEENS"
    _mkv(show / "Season 1" / "a.mkv")

    scan = import_scanner.scan(show)

    assert scan.units[0].show_name == "KING OF QUEENS"
