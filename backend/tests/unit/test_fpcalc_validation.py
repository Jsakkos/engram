"""Tests for fpcalc binary validation."""

import os
from unittest.mock import patch

from app.api.validation import _validate_fpcalc_binary


def test_validate_fpcalc_binary_success():
    """Valid fpcalc binary returns found=True with version."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "fpcalc version 1.5.1 (FFmpeg ...)\n"
        result = _validate_fpcalc_binary("/fake/fpcalc")
        assert result.found is True
        assert "1.5.1" in result.version
        assert result.path == "/fake/fpcalc"


def test_validate_fpcalc_binary_nonzero_exit():
    """Non-zero exit code reports not found with error."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = "bad binary"
        result = _validate_fpcalc_binary("/fake/fpcalc")
        assert result.found is False
        assert "exit" in result.error.lower() or "code" in result.error.lower()


def test_validate_fpcalc_binary_missing_file(tmp_path):
    """A nonexistent path reports found=False without raising."""
    bogus = tmp_path / "nope.exe"
    result = _validate_fpcalc_binary(str(bogus))
    assert result.found is False
    assert result.path == str(bogus)


def test_detect_fpcalc_uses_path_search(monkeypatch):
    """detect_fpcalc consults shutil.which before falling back to common paths."""
    from app.api import validation as v

    # Isolate the PATH-vs-common-locations behavior under test from the higher
    # precedence tiers: no env override and no bundled binary present.
    monkeypatch.delenv(v._DEV_FPCALC_ENV, raising=False)
    monkeypatch.setattr(v, "_bundled_fpcalc_path", lambda: None)

    def fake_which(name):
        return "/usr/local/bin/fpcalc" if name == "fpcalc" else None

    monkeypatch.setattr(v.shutil, "which", fake_which)
    monkeypatch.setattr(
        v,
        "_validate_fpcalc_binary",
        lambda p: v.ToolDetectionResult(found=True, path=p, version="fpcalc version 1.5.1"),
    )
    result = v.detect_fpcalc()
    assert result.found is True
    assert result.path == "/usr/local/bin/fpcalc"


def test_bundled_fpcalc_path_frozen(monkeypatch, tmp_path):
    """In a frozen build, the bundled binary resolves under <_MEIPASS>/bin/."""
    from app.api import validation as v

    name = "fpcalc.exe" if os.name == "nt" else "fpcalc"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / name).write_bytes(b"\x00")
    monkeypatch.setattr(v.sys, "_MEIPASS", str(tmp_path), raising=False)

    assert v._bundled_fpcalc_path() == str(bin_dir / name)


def test_detect_fpcalc_prefers_bundled_over_path(monkeypatch, tmp_path):
    """A valid bundled binary wins over one merely found on PATH.

    End users get the known-good shipped fpcalc by default rather than whatever
    happens to be on PATH.
    """
    from app.api import validation as v

    name = "fpcalc.exe" if os.name == "nt" else "fpcalc"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    bundled = bin_dir / name
    bundled.write_bytes(b"\x00")
    monkeypatch.setattr(v.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.delenv(v._DEV_FPCALC_ENV, raising=False)
    monkeypatch.setattr(v.shutil, "which", lambda n: "/usr/bin/fpcalc")
    monkeypatch.setattr(
        v,
        "_validate_fpcalc_binary",
        lambda p: v.ToolDetectionResult(found=True, path=p, version="fpcalc version 1.5.1"),
    )

    result = v.detect_fpcalc()
    assert result.found is True
    assert result.path == str(bundled)


def test_detect_fpcalc_invalid_bundled_falls_through_to_path(monkeypatch, tmp_path):
    """A bundled binary that fails validation (e.g. wrong arch) falls through to PATH.

    This is the macOS-arm64 safety net: a bundled x86_64 fpcalc that won't run
    without Rosetta must not shadow a native fpcalc the user has on PATH.
    """
    from app.api import validation as v

    name = "fpcalc.exe" if os.name == "nt" else "fpcalc"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    bundled = bin_dir / name
    bundled.write_bytes(b"\x00")
    monkeypatch.setattr(v.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.delenv(v._DEV_FPCALC_ENV, raising=False)
    monkeypatch.setattr(v.shutil, "which", lambda n: "/usr/bin/fpcalc")

    def validate(p):
        if p == str(bundled):
            return v.ToolDetectionResult(found=False, path=p, error="cannot execute")
        return v.ToolDetectionResult(found=True, path=p, version="fpcalc version 1.5.1")

    monkeypatch.setattr(v, "_validate_fpcalc_binary", validate)

    result = v.detect_fpcalc()
    assert result.found is True
    assert result.path == "/usr/bin/fpcalc"
