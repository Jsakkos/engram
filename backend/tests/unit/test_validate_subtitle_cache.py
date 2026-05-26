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
    """Build a synthetic release-assets dir at ``assets_dir``.

    When the manifest declares show/season coverage, the helper drops a
    matching ``S{nn}.npz`` + ``S{nn}.index.json`` under the show's
    ``sanitize_filename`` slug so the manifest-↔-tarball consistency gate
    in `validate()` sees the files it expects. Tests that exercise the
    missing-files failure path opt out by passing ``tarball_members``
    explicitly (no per-show files written) or by overriding shows with
    an entry whose seasons list excludes those files.
    """
    from app.matcher.subtitle_utils import sanitize_filename

    assets_dir.mkdir(parents=True, exist_ok=True)
    build_dir = assets_dir / "_build" / "precomputed"
    build_dir.mkdir(parents=True)
    (build_dir / "idf.npy").write_bytes(b"fake-idf")
    (build_dir / "manifest.json").write_text(
        "{}", encoding="utf-8"
    )  # in-tar manifest; validator only checks members

    # Default manifest used to drive vector-file placement; tests can override.
    default_shows = {"Some Show": {"tmdb_id": 1, "seasons": [1], "episode_counts": {"1": 3}}}
    effective_shows = (manifest_overrides or {}).get("shows", default_shows) or {}
    if tarball_members is None:
        for show_name, entry in effective_shows.items():
            if not isinstance(entry, dict):
                continue
            show_dir = build_dir / sanitize_filename(show_name)
            show_dir.mkdir(parents=True, exist_ok=True)
            for season in entry.get("seasons", []):
                (show_dir / f"S{season:02d}.npz").write_bytes(b"fake-npz")
                (show_dir / f"S{season:02d}.index.json").write_text('["S01E01"]', encoding="utf-8")

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
    (assets_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


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

    def test_manifest_lists_season_without_vector_files_detected(self, vsc, tmp_path):
        """The manifest claims coverage for Show S07, but the tarball never
        shipped S07.npz / S07.index.json for it. This is the exact state the
        TNG-S7-D2 rip hit: matcher warns + falls back to scraping for every
        title. The publish gate must reject the build instead of letting it
        roll out to end-users.
        """
        # Tarball with only the top-level required entries, no per-show files.
        _make_assets(
            vsc,
            tmp_path,
            tarball_members=[
                "precomputed",
                "precomputed/idf.npy",
                "precomputed/manifest.json",
            ],
            manifest_overrides={
                "shows": {
                    "Star Trek: The Next Generation": {
                        "tmdb_id": 655,
                        "seasons": [7],
                        "episode_counts": {"7": 26},
                    }
                }
            },
        )
        failures = vsc.validate(tmp_path).failures
        assert any("Star Trek: The Next Generation" in f and "S07" in f for f in failures), (
            f"Expected per-show/season files-missing failure, got: {failures}"
        )

    def test_missing_manifest_reports_clean_failure(self, vsc, tmp_path):
        # No assets at all — what happens when `gh release download` produces
        # nothing useful. Should report a single clean failure, not traceback.
        result = vsc.validate(tmp_path)
        assert len(result.failures) == 1
        assert "manifest.json not found" in result.failures[0]

    def test_missing_tarball_reports_clean_failure(self, vsc, tmp_path):
        (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
        result = vsc.validate(tmp_path)
        assert len(result.failures) == 1
        assert "engram-subtitle-cache.tar.gz not found" in result.failures[0]

    def test_malformed_manifest_reports_clean_failure(self, vsc, tmp_path):
        (tmp_path / "manifest.json").write_text("{not valid json", encoding="utf-8")
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
            ),
            encoding="utf-8",
        )
        (tmp_path / "engram-subtitle-cache.tar.gz").write_bytes(b"not a tarball")
        result = vsc.validate(tmp_path)
        assert any("tarball could not be read" in f for f in result.failures)
        # The pre-existing SHA-mismatch failure must still be reported — the
        # whole point of the try/except is to preserve already-accumulated
        # failures when tarfile.open throws.
        assert any("tarball_sha256 mismatch" in f for f in result.failures)
        # On the unreadable-tarball branch the summary's tarball_size_bytes is
        # None — validate() must never call stat() after tarfile.open already
        # signalled the file wasn't safely readable. Regression guard against
        # someone later "tidying up" the conditional in main() into a TypeError.
        assert result.summary["tarball_size_bytes"] is None

    def test_sha_read_failure_does_not_raise(self, vsc, tmp_path, monkeypatch):
        """validate() promises to return a ValidationResult, never raise.
        Simulate a permission flip between tarball.exists() and the sha
        read — _sha256_of_file's open() would otherwise propagate OSError
        and escape the function unhandled.
        """
        _make_assets(vsc, tmp_path)

        def raise_oserror(_path):
            raise PermissionError("simulated TOCTOU on tarball read")

        monkeypatch.setattr(vsc, "_sha256_of_file", raise_oserror)

        result = vsc.validate(tmp_path)  # must not raise
        assert any("could not read tarball for hashing" in f for f in result.failures)
        # The sha-mismatch check should NOT fire when actual_sha is None —
        # the "could not read" failure already tells the operator what happened.
        assert not any("tarball_sha256 mismatch" in f for f in result.failures)
        # actual_sha lands in summary as None.
        assert result.summary["tarball_sha256"] is None

    def test_stat_failure_yields_none_size(self, vsc, tmp_path, monkeypatch):
        """The symmetric TOCTOU on the tarball_readable=True branch: tarfile.open
        succeeds, but tarball.stat() fails before the summary populate. validate()
        must still return cleanly with tarball_size_bytes=None.

        Arming strategy: signature-based discrimination is non-portable
        (Path.exists() on Linux 3.11 calls self.stat() with no args, same
        shape as the summary call — making `not args and not kwargs` collide
        with the existence check). Instead, only arm the stat-raiser AFTER
        tarfile.open completes; that places the failure exactly at the code
        path the new try/except OSError exists to protect.
        """
        _make_assets(vsc, tmp_path)

        armed = {"on": False}
        original_stat = Path.stat
        original_tarfile_open = tarfile.open

        def stat_maybe_raises(self, *args, **kwargs):
            if armed["on"] and self.name == "engram-subtitle-cache.tar.gz":
                raise PermissionError("simulated TOCTOU on stat")
            return original_stat(self, *args, **kwargs)

        def tarfile_open_arms(*args, **kwargs):
            result = original_tarfile_open(*args, **kwargs)
            armed["on"] = True
            return result

        monkeypatch.setattr(Path, "stat", stat_maybe_raises)
        monkeypatch.setattr(tarfile, "open", tarfile_open_arms)

        result = vsc.validate(tmp_path)  # must not raise
        assert result.summary["tarball_size_bytes"] is None
        # tarfile.open did succeed, so no "could not be read" failure here.
        assert not any("could not be read" in f for f in result.failures)


@pytest.mark.unit
class TestMain:
    """`main()` is the entry point the CI smoke workflow actually calls;
    exit codes drive whether GitHub Actions reports a green or red job.
    Cover the three return values plus the dir-listing else branch.
    """

    def test_exits_0_on_healthy_release(self, vsc, tmp_path, monkeypatch, capsys):
        _make_assets(vsc, tmp_path)
        monkeypatch.setattr("sys.argv", ["validate_subtitle_cache.py", str(tmp_path)])
        assert vsc.main() == 0
        assert "OK" in capsys.readouterr().out

    def test_exits_1_on_validation_failure(self, vsc, tmp_path, monkeypatch, capsys):
        _make_assets(vsc, tmp_path, corrupt_sha=True)
        monkeypatch.setattr("sys.argv", ["validate_subtitle_cache.py", str(tmp_path)])
        assert vsc.main() == 1
        assert "VALIDATION FAILURES" in capsys.readouterr().out

    def test_exits_2_on_wrong_argv(self, vsc, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["validate_subtitle_cache.py"])  # missing arg
        assert vsc.main() == 2
        assert "usage:" in capsys.readouterr().err

    def test_lists_dir_on_missing_inputs(self, vsc, tmp_path, monkeypatch, capsys):
        """The early-return path produces an empty .summary, so main() takes
        the else branch and lists the dir contents to help the operator
        distinguish "gh release download wrote nothing" from "the wrong file
        landed there". This branch was previously untested.
        """
        # Stage an unrelated file so the listing branch has something to print.
        (tmp_path / "unrelated.txt").write_text("hi", encoding="utf-8")
        monkeypatch.setattr("sys.argv", ["validate_subtitle_cache.py", str(tmp_path)])
        assert vsc.main() == 1  # missing manifest is a validation failure

        captured = capsys.readouterr().out
        assert f"assets dir: {tmp_path}" in captured
        assert "unrelated.txt" in captured  # the dir-listing line itself
        assert "manifest.json not found" in captured
