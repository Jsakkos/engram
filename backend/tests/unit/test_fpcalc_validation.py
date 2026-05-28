"""Tests for fpcalc binary validation."""

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
