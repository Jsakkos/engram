"""Fixtures for live provider tests.

These tests make real HTTP calls to third-party subtitle sites
(currently TVsubtitles.net). They validate our parsers against
the actual deployed endpoint shape — the unit tests only validate
parsers against captured HTML and won't catch upstream layout drift.

Opted into via env var so a) they don't burn rate-limit budget on
every CI run and b) a temporary site outage doesn't fail the entire
suite. Run locally with:

    ENGRAM_LIVE_PROVIDER_TESTS=1 uv run pytest tests/live/ -v -m live

Tests will SKIP if:
- ``ENGRAM_LIVE_PROVIDER_TESTS`` is not set / falsy
- The site is unreachable (network error, DNS failure)

Tests will FAIL if the site is reachable but our parser can't find
the expected structures — that's the signal that an endpoint shape
has drifted and the client needs adjusting.
"""

import os

import pytest


def _is_opted_in() -> bool:
    return os.environ.get("ENGRAM_LIVE_PROVIDER_TESTS", "").lower() in {"1", "true", "yes"}


@pytest.fixture(autouse=True)
def _skip_unless_opted_in():
    """Auto-skip every live test unless the env var is set.

    Implemented as an autouse fixture (not a collection-level hook) so
    a developer running ``pytest tests/live/test_x.py::test_y`` gets a
    clean ``SKIPPED`` line with the reason, rather than the test being
    silently absent from the collection.
    """
    if not _is_opted_in():
        pytest.skip("Live provider tests are opt-in; set ENGRAM_LIVE_PROVIDER_TESTS=1 to enable")
