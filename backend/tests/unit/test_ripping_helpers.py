"""Unit tests for staging-file location."""

from types import SimpleNamespace

import pytest

from app.services.ripping_helpers import find_staging_file


@pytest.mark.unit
def test_find_staging_file_prefers_output_filename(tmp_path):
    f = tmp_path / "Show_t07.mkv"
    f.write_bytes(b"x")
    job = SimpleNamespace(staging_path=str(tmp_path))
    title = SimpleNamespace(output_filename=str(f), title_index=7, organized_to=None)
    assert find_staging_file(job, title) == f


@pytest.mark.unit
def test_find_staging_file_falls_back_to_organized_to(tmp_path):
    """When the staging file is gone, re-match can read the organized library file."""
    organized = tmp_path / "library" / "Show - S01E14.mkv"
    organized.parent.mkdir()
    organized.write_bytes(b"x")
    job = SimpleNamespace(staging_path=str(tmp_path / "empty_staging"))
    title = SimpleNamespace(
        output_filename=str(tmp_path / "gone.mkv"),  # no longer exists
        title_index=7,
        organized_to=str(organized),
    )
    assert find_staging_file(job, title) == organized


@pytest.mark.unit
def test_find_staging_file_returns_none_when_nothing_found(tmp_path):
    job = SimpleNamespace(staging_path=str(tmp_path / "empty"))
    title = SimpleNamespace(output_filename=None, title_index=3, organized_to=None)
    assert find_staging_file(job, title) is None
