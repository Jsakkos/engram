"""Unit tests for scripts/validate_subtitle_cache.py.

The validator runs once a day in subtitle-cache-smoke.yml against the live
release. These tests cover the 6 failure modes (sha mismatch, format-version
mismatch, vectorizer hash mismatch, n_features mismatch, missing tarball
entries, empty shows dict) without touching the network — each builds a
synthetic release-assets dir, mutates one field, and asserts the validator
reports exactly that failure.

The `vsc` fixture (loaded once per pytest session) lives in conftest.py.
"""

import json
import tarfile
from pathlib import Path

import pytest

from app.matcher.vectorizer_config import (
    CACHE_FORMAT_VERSION,
    HASHING_N_FEATURES,
    vectorizer_config_hash,
)


def _make_assets(
    vsc,
    assets_dir: Path,
    *,
    manifest_overrides: dict | None = None,
    tarball_members: list[str] | None = None,
    corrupt_sha: bool = False,
) -> None:
    """Build a synthetic release-assets dir at ``assets_dir``."""
    assets_dir.mkdir(parents=True, exist_ok=True)
    build_dir = assets_dir / "_build" / "precomputed"
    build_dir.mkdir(parents=True)
    (build_dir / "idf.npy").write_bytes(b"fake-idf")
    (build_dir / "manifest.json").write_text("{}")  # in-tar manifest; validator only checks members

    tarball = assets_dir / "engram-subtitle-cache.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        if tarball_members is None:
            tar.add(build_dir.parent / "precomputed", arcname="precomputed")
        else:
            # Synthesize a tarball with exactly the requested member set.
            for member in tarball_members:
                info = tarfile.TarInfo(name=member)
                info.size = 0
                tar.addfile(info)

    # Use the validator's own helper so the test proves it agrees with the
    # build script + validator on the same bytes (matches the round-trip
    # test in test_build_subtitle_cache.py).
    sha = "0" * 64 if corrupt_sha else vsc._sha256_of_file(tarball)
    manifest = {
        "tarball_sha256": sha,
        "cache_format_version": CACHE_FORMAT_VERSION,
        "vectorizer_config_hash": vectorizer_config_hash(),
        "n_features": HASHING_N_FEATURES,
        "shows": {"Some Show": {"tmdb_id": 1, "seasons": [1], "episode_counts": {"1": 3}}},
    }
    manifest.update(manifest_overrides or {})
    (assets_dir / "manifest.json").write_text(json.dumps(manifest))


@pytest.mark.unit
class TestValidate:
    def test_healthy_release_returns_no_failures(self, vsc, tmp_path):
        _make_assets(vsc, tmp_path)
        assert vsc.validate(tmp_path).failures == []

    def test_sha_mismatch_detected(self, vsc, tmp_path):
        _make_assets(vsc, tmp_path, corrupt_sha=True)
        failures = vsc.validate(tmp_path).failures
        assert any("tarball_sha256 mismatch" in f for f in failures)

    def test_format_version_mismatch_detected(self, vsc, tmp_path):
        _make_assets(vsc, tmp_path, manifest_overrides={"cache_format_version": "999"})
        failures = vsc.validate(tmp_path).failures
        assert any("cache_format_version mismatch" in f for f in failures)

    def test_vectorizer_hash_mismatch_detected(self, vsc, tmp_path):
        _make_assets(vsc, tmp_path, manifest_overrides={"vectorizer_config_hash": "deadbeef"})
        failures = vsc.validate(tmp_path).failures
        assert any("vectorizer_config_hash mismatch" in f for f in failures)

    def test_n_features_mismatch_detected(self, vsc, tmp_path):
        _make_assets(vsc, tmp_path, manifest_overrides={"n_features": 1})
        failures = vsc.validate(tmp_path).failures
        assert any("n_features mismatch" in f for f in failures)

    def test_missing_tarball_entries_detected(self, vsc, tmp_path):
        # Tarball that's missing precomputed/idf.npy.
        _make_assets(vsc, tmp_path, tarball_members=["precomputed", "precomputed/manifest.json"])
        failures = vsc.validate(tmp_path).failures
        assert any("missing required entries" in f for f in failures)

    def test_empty_shows_dict_detected(self, vsc, tmp_path):
        _make_assets(vsc, tmp_path, manifest_overrides={"shows": {}})
        failures = vsc.validate(tmp_path).failures
        assert any("shows dict in manifest is empty" in f for f in failures)

    def test_missing_manifest_reports_clean_failure(self, vsc, tmp_path):
        # No assets at all — what happens when `gh release download` produces
        # nothing useful. Should report a single clean failure, not traceback.
        result = vsc.validate(tmp_path)
        assert len(result.failures) == 1
        assert "manifest.json not found" in result.failures[0]

    def test_missing_tarball_reports_clean_failure(self, vsc, tmp_path):
        (tmp_path / "manifest.json").write_text("{}")
        result = vsc.validate(tmp_path)
        assert len(result.failures) == 1
        assert "engram-subtitle-cache.tar.gz not found" in result.failures[0]

    def test_malformed_manifest_reports_clean_failure(self, vsc, tmp_path):
        (tmp_path / "manifest.json").write_text("{not valid json")
        (tmp_path / "engram-subtitle-cache.tar.gz").write_bytes(b"x")
        result = vsc.validate(tmp_path)
        assert len(result.failures) == 1
        assert "not valid JSON" in result.failures[0]

    def test_summary_populated_on_healthy_release(self, vsc, tmp_path):
        _make_assets(vsc, tmp_path)
        result = vsc.validate(tmp_path)
        assert result.summary["n_shows"] == 1
        assert result.summary["cache_format_version"] == CACHE_FORMAT_VERSION
        assert result.summary["n_features"] == HASHING_N_FEATURES
        assert len(result.summary["tarball_sha256"]) == 64

    def test_null_shows_reports_clean_failure(self, vsc, tmp_path):
        """A manifest with `"shows": null` (vs the key absent) used to hit
        `len(None)` and exit with an unhandled TypeError. Now it must
        accumulate the same "shows dict is empty" failure as the absent case.
        """
        _make_assets(vsc, tmp_path, manifest_overrides={"shows": None})
        failures = vsc.validate(tmp_path).failures
        assert any("shows dict in manifest is empty" in f for f in failures)

    def test_corrupt_tarball_reports_clean_failure(self, vsc, tmp_path):
        """Simulates a partial gh-release-download or wrong file uploaded:
        manifest claims a sha that won't match (irrelevant — could be anything),
        and the tarball exists but isn't a gzip. Without the TarError guard
        this raises and loses the earlier SHA-mismatch failure entry.
        """
        (tmp_path / "manifest.json").write_text(
            json.dumps(
                {
                    "tarball_sha256": "a" * 64,
                    "cache_format_version": CACHE_FORMAT_VERSION,
                    "vectorizer_config_hash": vectorizer_config_hash(),
                    "n_features": HASHING_N_FEATURES,
                    "shows": {"Some Show": {}},
                }
            )
        )
        (tmp_path / "engram-subtitle-cache.tar.gz").write_bytes(b"not a tarball")
        result = vsc.validate(tmp_path)
        assert any("not a valid gzip tarball" in f for f in result.failures)
        # The pre-existing SHA-mismatch failure must still be reported — the
        # whole point of the try/except is to preserve already-accumulated
        # failures when tarfile.open throws.
        assert any("tarball_sha256 mismatch" in f for f in result.failures)
