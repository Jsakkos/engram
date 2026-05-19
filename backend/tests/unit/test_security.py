"""Unit tests for app.core.security — SSRF and path-traversal hardening helpers.

These cover the CodeQL-flagged sinks:
- validate_image_url: py/full-ssrf in the fetch_cover endpoint
- safe_static_path: py/path-injection in the SPA catch-all route
- executable_basename_allowed: py/command-line-injection in validate_makemkv/ffmpeg
"""

import os

import pytest

from app.core.security import (
    executable_basename_allowed,
    safe_static_path,
    validate_image_url,
)


class TestValidateImageUrl:
    """SSRF guard for user-supplied cover-image URLs."""

    def test_accepts_amazon_image_cdn(self):
        url = "https://m.media-amazon.com/images/I/abc123.jpg"
        assert validate_image_url(url) == url

    def test_accepts_tmdb_image_host(self):
        url = "https://image.tmdb.org/t/p/w500/poster.jpg"
        assert validate_image_url(url) == url

    def test_accepts_thediscdb_host(self):
        url = "https://thediscdb.com/covers/x.png"
        assert validate_image_url(url) == url

    def test_rejects_localhost(self):
        with pytest.raises(ValueError):
            validate_image_url("http://localhost/cover.jpg")

    def test_rejects_private_ip(self):
        with pytest.raises(ValueError):
            validate_image_url("http://192.168.1.10/cover.jpg")

    def test_rejects_cloud_metadata_endpoint(self):
        with pytest.raises(ValueError):
            validate_image_url("http://169.254.169.254/latest/meta-data/")

    def test_rejects_file_scheme(self):
        with pytest.raises(ValueError):
            validate_image_url("file:///etc/passwd")

    def test_rejects_unlisted_host(self):
        with pytest.raises(ValueError):
            validate_image_url("https://evil.example.com/cover.jpg")

    def test_rejects_lookalike_suffix_host(self):
        # Must not match via bare endswith — requires a dot-delimited suffix.
        with pytest.raises(ValueError):
            validate_image_url("https://evilmedia-amazon.com/cover.jpg")

    def test_rejects_empty_url(self):
        with pytest.raises(ValueError):
            validate_image_url("")


class TestSafeStaticPath:
    """Path-traversal containment for the SPA static-file route."""

    def test_allows_normal_nested_file(self, tmp_path):
        root = str(tmp_path)
        result = safe_static_path(root, "assets/app.js")
        assert result is not None
        assert result == os.path.realpath(os.path.join(root, "assets", "app.js"))

    def test_allows_empty_path_as_root(self, tmp_path):
        root = str(tmp_path)
        assert safe_static_path(root, "") == os.path.realpath(root)

    def test_rejects_dotdot_traversal(self, tmp_path):
        root = str(tmp_path / "static")
        os.makedirs(root, exist_ok=True)
        assert safe_static_path(root, "../../../etc/passwd") is None

    def test_rejects_absolute_path(self, tmp_path):
        root = str(tmp_path / "static")
        os.makedirs(root, exist_ok=True)
        absolute = "C:\\Windows\\win.ini" if os.name == "nt" else "/etc/passwd"
        assert safe_static_path(root, absolute) is None

    def test_rejects_sibling_directory_escape(self, tmp_path):
        root = str(tmp_path / "static")
        os.makedirs(root, exist_ok=True)
        # A prefix-collision sibling (static_evil) must not be treated as inside.
        assert safe_static_path(root, "../static_evil/secret") is None


class TestExecutableBasenameAllowed:
    """Basename allowlist guard for the tool-validation subprocess calls."""

    def test_accepts_makemkv_windows_exe(self):
        assert executable_basename_allowed(
            "C:\\Program Files\\MakeMKV\\makemkvcon64.exe", ["makemkv"]
        )

    def test_accepts_makemkv_linux_binary(self):
        assert executable_basename_allowed("/usr/bin/makemkvcon", ["makemkv"])

    def test_accepts_ffmpeg_binary(self):
        assert executable_basename_allowed("/usr/local/bin/ffmpeg", ["ffmpeg"])

    def test_rejects_arbitrary_shell(self):
        assert not executable_basename_allowed("/bin/sh", ["makemkv"])

    def test_rejects_powershell(self):
        assert not executable_basename_allowed("C:\\Windows\\System32\\cmd.exe", ["ffmpeg"])

    def test_match_is_case_insensitive(self):
        assert executable_basename_allowed("/opt/MakeMKVcon", ["makemkv"])
