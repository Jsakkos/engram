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
from dataclasses import dataclass, field
from pathlib import Path

# Idempotent — repeated importlib loads (e.g. one fixture per test file) would
# otherwise accumulate duplicate entries in sys.path on every exec_module call.
_backend_dir = str(Path(__file__).parent.parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from app.matcher.vectorizer_config import (
    CACHE_FORMAT_VERSION,
    HASHING_N_FEATURES,
    vectorizer_config_hash,
)

REQUIRED_TARBALL_ENTRIES = frozenset(
    {"precomputed", "precomputed/idf.npy", "precomputed/manifest.json"}
)
_SHA_CHUNK_SIZE = 1 << 16  # 64 KiB; tarballs grow with show count, stream rather than load


def _sha256_of_file(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_SHA_CHUNK_SIZE), b""):
            sha.update(chunk)
    return sha.hexdigest()


@dataclass
class ValidationResult:
    """Pure result object. ``failures`` is the list smoke-test asserts on;
    ``summary`` is the diagnostic snapshot main() prints to the CI log."""

    failures: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def validate(assets_dir: Path) -> ValidationResult:
    """Return a ValidationResult; empty .failures means cache is healthy."""
    tarball = assets_dir / "engram-subtitle-cache.tar.gz"
    manifest_path = assets_dir / "manifest.json"

    # Treat missing or malformed inputs as validation failures, not tracebacks:
    # this is exactly the kind of corruption the smoke test is supposed to catch.
    if not manifest_path.exists():
        return ValidationResult(failures=[f"manifest.json not found in {assets_dir}"])
    if not tarball.exists():
        return ValidationResult(
            failures=[f"engram-subtitle-cache.tar.gz not found in {assets_dir}"]
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return ValidationResult(failures=[f"manifest.json is not valid JSON: {exc}"])

    failures: list[str] = []
    expected_hash = vectorizer_config_hash()

    # tarball.exists() above doesn't guarantee open() will succeed — a
    # permission flip or transient I/O error between the existence check
    # and the read would otherwise escape validate() unhandled.
    try:
        actual_sha: str | None = _sha256_of_file(tarball)
    except OSError as exc:
        failures.append(f"could not read tarball for hashing: {exc}")
        actual_sha = None

    # Only run the sha comparison if we actually computed one — when the
    # read failed above, "could not read tarball for hashing" already
    # tells the operator everything they need.
    if actual_sha is not None and manifest.get("tarball_sha256") != actual_sha:
        # Distinguish "key absent" from "key is the wrong hex string" — a
        # manifest=None in the CI log otherwise looks like the manifest itself
        # is None, hiding the real cause.
        failures.append(
            f"tarball_sha256 mismatch: "
            f"manifest={manifest.get('tarball_sha256', '<key missing>')!r}, "
            f"actual={actual_sha!r}"
        )

    if manifest.get("cache_format_version") != CACHE_FORMAT_VERSION:
        failures.append(
            f"cache_format_version mismatch: "
            f"manifest={manifest.get('cache_format_version')!r}, "
            f"current main={CACHE_FORMAT_VERSION!r}"
        )

    if manifest.get("vectorizer_config_hash") != expected_hash:
        failures.append(
            f"vectorizer_config_hash mismatch: "
            f"manifest={manifest.get('vectorizer_config_hash')!r}, "
            f"current main={expected_hash!r}"
        )

    if manifest.get("n_features") != HASHING_N_FEATURES:
        failures.append(
            f"n_features mismatch: "
            f"manifest={manifest.get('n_features')!r}, current main={HASHING_N_FEATURES!r}"
        )

    # Catch tarfile.TarError (parent of ReadError, CompressionError, etc.) for
    # corrupt archives, plus OSError for filesystem-level failures (permission
    # denied, transient I/O error) so neither path blows away the failures
    # already collected above.
    tarball_readable = True
    try:
        with tarfile.open(tarball, "r:gz") as tar:
            members = set(tar.getnames())
    except (tarfile.TarError, OSError) as exc:
        failures.append(f"tarball could not be read: {exc}")
        members = set()
        tarball_readable = False
    # Skip the membership check on unreadable tarballs — `REQUIRED_TARBALL_ENTRIES - members`
    # would just echo the required set and add noise to the failure list.
    missing = REQUIRED_TARBALL_ENTRIES - members
    if missing and tarball_readable:
        failures.append(f"tarball missing required entries: {sorted(missing)}")

    # `get("shows", {})` falls back to {} only when the key is *absent* —
    # `"shows": null` would return None and len(None) raises TypeError,
    # bypassing the accumulated failures list. `or {}` handles both.
    n_shows = len(manifest.get("shows") or {})
    if n_shows == 0:
        failures.append("shows dict in manifest is empty — cache is unusable")

    # tarball.stat() can re-raise OSError on its own — a permission flip
    # between tarfile.open succeeding and reaching this line would otherwise
    # escape unhandled. Catch both that and the "tarfile.open already saw
    # the file wasn't readable" case via the same `None` fallback.
    try:
        tarball_size: int | None = tarball.stat().st_size if tarball_readable else None
    except OSError:
        tarball_size = None

    summary = {
        "cache_format_version": manifest.get("cache_format_version"),
        "vectorizer_config_hash": manifest.get("vectorizer_config_hash"),
        "n_features": manifest.get("n_features"),
        "n_shows": n_shows,
        "tarball_size_bytes": tarball_size,
        "tarball_sha256": actual_sha,
    }
    return ValidationResult(failures=failures, summary=summary)


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <assets-dir>", file=sys.stderr)
        return 2
    assets_dir = Path(sys.argv[1])
    result = validate(assets_dir)

    # Diagnostic snapshot first so CI logs show what was inspected even on failure.
    if result.summary:
        for key, value in result.summary.items():
            if key == "tarball_size_bytes":
                # `value` is None when the tarball wasn't safely readable;
                # the validate() comment explains the TOCTOU rationale.
                print(
                    f"tarball size: {value:,} bytes"
                    if value is not None
                    else "tarball size: unreadable"
                )
            else:
                # Summary values come from manifest.json (already validated as
                # JSON-safe) plus our own hex digest — no repr quoting needed.
                print(f"{key}: {value}")
    else:
        # Early-return path (missing/malformed inputs) — list what's on disk so
        # the operator can tell apart "gh release download wrote nothing" from
        # "the wrong file landed there".
        print(f"assets dir: {assets_dir}")
        if assets_dir.exists():
            for entry in sorted(assets_dir.iterdir()):
                size = entry.stat().st_size if entry.is_file() else "-"
                print(f"  {entry.name}: {size} bytes")
        else:
            print("  (does not exist)")

    if result.failures:
        print("VALIDATION FAILURES:")
        for f in result.failures:
            print(f"  - {f}")
        return 1
    print("OK — live release is consistent with current main.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
