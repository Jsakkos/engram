"""Validate that a downloaded subtitle-cache release matches current main.

Invoked by .github/workflows/subtitle-cache-smoke.yml against the daily-uploaded
`subtitle-cache-latest` release. Checks:

1. sha256 of the tarball matches the value in the sibling manifest.json
2. cache_format_version in manifest matches CACHE_FORMAT_VERSION
3. vectorizer_config_hash in manifest matches vectorizer_config_hash()
4. n_features in manifest matches HASHING_N_FEATURES
5. Tarball untars cleanly and contains required entries
6. shows dict is non-empty (catches smoke-build-uploaded-by-mistake)

Exits 0 on success, 1 on any validation failure (with all failures listed).

Usage:
    uv run python scripts/validate_subtitle_cache.py /path/to/release/assets

The given directory must contain both `engram-subtitle-cache.tar.gz` and
`manifest.json`. Designed to be importable too so the validation logic itself
is unit-testable (see tests/unit/test_validate_subtitle_cache.py).
"""

import hashlib
import json
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.matcher.vectorizer_config import (
    CACHE_FORMAT_VERSION,
    HASHING_N_FEATURES,
    vectorizer_config_hash,
)

REQUIRED_TARBALL_ENTRIES = frozenset(
    {"precomputed", "precomputed/idf.npy", "precomputed/manifest.json"}
)


def validate(assets_dir: Path) -> list[str]:
    """Return a list of failure messages; empty list means cache is healthy."""
    tarball = assets_dir / "engram-subtitle-cache.tar.gz"
    manifest_path = assets_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    failures: list[str] = []

    actual_sha = hashlib.sha256(tarball.read_bytes()).hexdigest()
    if manifest.get("tarball_sha256") != actual_sha:
        failures.append(
            f"tarball_sha256 mismatch: "
            f"manifest={manifest.get('tarball_sha256')!r}, actual={actual_sha!r}"
        )

    if manifest.get("cache_format_version") != CACHE_FORMAT_VERSION:
        failures.append(
            f"cache_format_version mismatch: "
            f"manifest={manifest.get('cache_format_version')!r}, "
            f"current main={CACHE_FORMAT_VERSION!r}"
        )

    if manifest.get("vectorizer_config_hash") != vectorizer_config_hash():
        failures.append(
            f"vectorizer_config_hash mismatch: "
            f"manifest={manifest.get('vectorizer_config_hash')!r}, "
            f"current main={vectorizer_config_hash()!r}"
        )

    if manifest.get("n_features") != HASHING_N_FEATURES:
        failures.append(
            f"n_features mismatch: "
            f"manifest={manifest.get('n_features')!r}, current main={HASHING_N_FEATURES!r}"
        )

    with tarfile.open(tarball, "r:gz") as tar:
        members = set(tar.getnames())
    missing = REQUIRED_TARBALL_ENTRIES - members
    if missing:
        failures.append(f"tarball missing required entries: {sorted(missing)}")

    n_shows = len(manifest.get("shows", {}))
    if n_shows == 0:
        failures.append("shows dict in manifest is empty — cache is unusable")

    print(f"cache_format_version: {manifest.get('cache_format_version')!r}")
    print(f"vectorizer_config_hash: {manifest.get('vectorizer_config_hash')!r}")
    print(f"n_features: {manifest.get('n_features')!r}")
    print(f"n_shows: {n_shows}")
    print(f"tarball size: {tarball.stat().st_size:,} bytes")
    print(f"tarball sha256: {actual_sha}")

    return failures


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <assets-dir>", file=sys.stderr)
        return 2
    failures = validate(Path(sys.argv[1]))
    if failures:
        print("VALIDATION FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK — live release is consistent with current main.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
