"""Regression tests for the README download-stats generator.

The badge totals are cumulative across releases, but they must only count the
actual per-OS app binaries. Two real-world traps motivated these tests:

1. The ``subtitle-cache-*`` releases ship ``engram-subtitle-cache.tar.gz`` — a
   rolling data pack that is overwritten on every rebuild. Overwriting an asset
   resets GitHub's per-asset ``download_count`` to 0, so counting it made the
   Linux total crater by hundreds and climb back (the "fluctuates down" report).
2. ``engram-macos-*.tar.gz`` also ends in ``.tar.gz`` and was silently folded
   into the Linux total.

Matching by exact asset name (prefix + suffix) instead of bare extension fixes
both. These tests lock that in.
"""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "update_download_stats.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("update_download_stats", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


download_stats = _load_module()


def _asset(name: str, dl: int) -> dict:
    return {"name": name, "download_count": dl}


def _release(tag: str, assets: list[dict]) -> dict:
    return {"tag_name": tag, "assets": assets}


def test_totals_count_only_named_app_binaries():
    releases = [
        _release(
            "v1.0.0",
            [
                _asset("engram-windows-x64.zip", 10),
                _asset("engram-linux-x64.tar.gz", 5),
                _asset("engram-macos-arm64.tar.gz", 3),
                # Non-binary assets must never be counted.
                _asset("engram-windows-x64.manifest.sha256", 99),
                _asset("sha256sums.txt", 99),
            ],
        ),
    ]
    totals, _ = download_stats.compute_stats(releases)
    assert totals["windows"] == 10
    assert totals["linux"] == 5
    assert totals["macos"] == 3


def test_subtitle_cache_pack_is_excluded():
    """A rolling data-pack .tar.gz must not inflate the Linux total."""
    releases = [
        _release("v1.0.0", [_asset("engram-linux-x64.tar.gz", 4)]),
        _release(
            "subtitle-cache-latest",
            [
                _asset("engram-subtitle-cache.tar.gz", 300),
                _asset("manifest.json", 300),
            ],
        ),
    ]
    totals, per_release = download_stats.compute_stats(releases)
    assert totals["linux"] == 4  # not 304
    assert totals["windows"] == 0
    assert totals["macos"] == 0
    # The data-pack release ships no app binary, so it is dropped from the chart.
    assert [row[0] for row in per_release] == ["v1.0.0"]


def test_macos_not_counted_as_linux():
    releases = [
        _release(
            "v1.0.0",
            [
                _asset("engram-linux-x64.tar.gz", 2),
                _asset("engram-macos-arm64.tar.gz", 7),
                _asset("engram-macos-x64.tar.gz", 1),  # legacy Intel naming
            ],
        ),
    ]
    totals, _ = download_stats.compute_stats(releases)
    assert totals["linux"] == 2
    assert totals["macos"] == 8


def test_totals_are_cumulative_across_releases():
    releases = [
        _release("v1.1.0", [_asset("engram-windows-x64.zip", 3)]),
        _release("v1.0.0", [_asset("engram-windows-x64.zip", 4)]),
    ]
    totals, per_release = download_stats.compute_stats(releases)
    assert totals["windows"] == 7
    assert [row[0] for row in per_release] == ["v1.1.0", "v1.0.0"]
