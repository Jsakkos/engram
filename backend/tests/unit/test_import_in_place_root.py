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
