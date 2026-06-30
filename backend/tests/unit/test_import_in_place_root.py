"""In-place imports organize under the per-job manifest root."""

import json
from pathlib import Path

from app.services.finalization_coordinator import _library_path_for_job


class _Job:
    def __init__(self, destination_mode, import_manifest_json=None, jid=1):
        self.destination_mode = destination_mode
        self.import_manifest_json = import_manifest_json
        self.id = jid


def test_library_mode_returns_none():
    job = _Job("library", json.dumps({"root": "/x", "files": []}))
    assert _library_path_for_job(job, "tv") is None


def test_in_place_uses_manifest_root_tv():
    job = _Job("in_place", json.dumps({"root": "/media/rips", "files": []}))
    assert _library_path_for_job(job, "tv") == Path("/media/rips") / "TV"


def test_in_place_uses_manifest_root_movie():
    job = _Job("in_place", json.dumps({"root": "/media/rips", "files": []}))
    assert _library_path_for_job(job, "movie") == Path("/media/rips") / "Movies"


def test_in_place_library_pick_keeps_genre_split():
    # picked_is_show False (a parent-of-shows / library root) keeps TV|Movies.
    job = _Job(
        "in_place", json.dumps({"root": "/media/rips", "files": [], "picked_is_show": False})
    )
    assert _library_path_for_job(job, "tv") == Path("/media/rips") / "TV"


def test_in_place_single_show_organizes_beside_picked_folder_tv():
    # picked_is_show True (the picked folder IS the show): organize under the
    # parent so the canonical Show/Season lands in/beside the picked folder, with
    # no spurious TV/ subdir nested inside it.
    job = _Job(
        "in_place",
        json.dumps({"root": "/media/rips/Seinfeld", "files": [], "picked_is_show": True}),
    )
    assert _library_path_for_job(job, "tv") == Path("/media/rips")


def test_in_place_single_title_movie_organizes_beside_picked_folder():
    job = _Job(
        "in_place",
        json.dumps({"root": "/media/rips/Inception (2010)", "files": [], "picked_is_show": True}),
    )
    assert _library_path_for_job(job, "movie") == Path("/media/rips")


def test_in_place_picked_season_organizes_under_show_parent():
    # picked_is_season True (the picked folder IS a "Season NN" folder): organize
    # under the grandparent so the canonical Show (Year)/Season XX lands beside the
    # original show folder, not nested inside the picked season folder. picked_is_show
    # is also True here (the season folder holds media directly) but must not win.
    job = _Job(
        "in_place",
        json.dumps(
            {
                "root": "/media/rips/Seinfeld/Season 4",
                "files": [],
                "picked_is_show": True,
                "picked_is_season": True,
            }
        ),
    )
    assert _library_path_for_job(job, "tv") == Path("/media/rips")
