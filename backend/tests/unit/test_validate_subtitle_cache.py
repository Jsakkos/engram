"""Unit tests for scripts/validate_subtitle_cache.py.

The validator runs once a day in subtitle-cache-smoke.yml against the live
release. These tests cover the 6 failure modes (sha mismatch, format-version
mismatch, vectorizer hash mismatch, n_features mismatch, missing tarball
entries, empty shows dict) without touching the network — each builds a
synthetic release-assets dir, mutates one field, and asserts the validator
reports exactly that failure.
"""

import hashlib
import importlib.util
import json
import sys
import tarfile
from pathlib import Path

import pytest

from app.matcher.vectorizer_config import (
    CACHE_FORMAT_VERSION,
    HASHING_N_FEATURES,
    vectorizer_config_hash,
)


def _load_validator():
    backend_root = Path(__file__).parent.parent.parent
    spec = importlib.util.spec_from_file_location(
        "validate_subtitle_cache",
        backend_root / "scripts" / "validate_subtitle_cache.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["validate_subtitle_cache"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def vsc():
    return _load_validator()


def _make_assets(
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

    sha = "0" * 64 if corrupt_sha else hashlib.sha256(tarball.read_bytes()).hexdigest()
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
        _make_assets(tmp_path)
        assert vsc.validate(tmp_path) == []

    def test_sha_mismatch_detected(self, vsc, tmp_path):
        _make_assets(tmp_path, corrupt_sha=True)
        failures = vsc.validate(tmp_path)
        assert any("tarball_sha256 mismatch" in f for f in failures)

    def test_format_version_mismatch_detected(self, vsc, tmp_path):
        _make_assets(tmp_path, manifest_overrides={"cache_format_version": "999"})
        failures = vsc.validate(tmp_path)
        assert any("cache_format_version mismatch" in f for f in failures)

    def test_vectorizer_hash_mismatch_detected(self, vsc, tmp_path):
        _make_assets(tmp_path, manifest_overrides={"vectorizer_config_hash": "deadbeef"})
        failures = vsc.validate(tmp_path)
        assert any("vectorizer_config_hash mismatch" in f for f in failures)

    def test_n_features_mismatch_detected(self, vsc, tmp_path):
        _make_assets(tmp_path, manifest_overrides={"n_features": 1})
        failures = vsc.validate(tmp_path)
        assert any("n_features mismatch" in f for f in failures)

    def test_missing_tarball_entries_detected(self, vsc, tmp_path):
        # Tarball that's missing precomputed/idf.npy.
        _make_assets(tmp_path, tarball_members=["precomputed", "precomputed/manifest.json"])
        failures = vsc.validate(tmp_path)
        assert any("missing required entries" in f for f in failures)

    def test_empty_shows_dict_detected(self, vsc, tmp_path):
        _make_assets(tmp_path, manifest_overrides={"shows": {}})
        failures = vsc.validate(tmp_path)
        assert any("shows dict in manifest is empty" in f for f in failures)
