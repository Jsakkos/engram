"""Fixtures for real-data tests.

These tests require actual ripped MKV files on disk and are
skipped in CI. Run locally with:
    uv run pytest tests/real_data/ -v -m real_data
"""

import json
from pathlib import Path

import pytest

EXPECTED_DIR = Path(__file__).parent / "expected"


@pytest.fixture
def real_staging_path(request):
    """Skip if the requested staging path doesn't exist on this machine.

    Usage:
        @pytest.mark.parametrize("real_staging_path", ["C:/Video/SHOW_S1D1"], indirect=True)
        def test_something(real_staging_path):
            ...
    """
    path = Path(request.param)
    if not path.exists():
        pytest.skip(f"Real data not available: {path}")
    return path


@pytest.fixture
def expected_matches(request):
    """Load expected matches from JSON fixture file.

    Usage:
        @pytest.mark.parametrize("expected_matches", ["arrested_development_s1d1"], indirect=True)
        def test_something(expected_matches):
            ...
    """
    json_name = request.param
    json_path = EXPECTED_DIR / f"{json_name}.json"
    if not json_path.exists():
        pytest.skip(f"Expected data file not found: {json_path}")
    return json.loads(json_path.read_text())
