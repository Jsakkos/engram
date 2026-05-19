"""Unit tests for app.core.security — SSRF and command-injection guard helpers.

These cover the CodeQL-flagged sinks:
- is_allowed_image_url: py/full-ssrf in the fetch_cover endpoint
- executable_basename_allowed: py/command-line-injection in validate_makemkv/ffmpeg
"""

from app.core.security import executable_basename_allowed, is_allowed_image_url


class TestIsAllowedImageUrl:
    """SSRF guard for user-supplied cover-image URLs."""

    def test_accepts_amazon_image_cdn(self):
        assert is_allowed_image_url("https://m.media-amazon.com/images/I/abc123.jpg")

    def test_accepts_tmdb_image_host(self):
        assert is_allowed_image_url("https://image.tmdb.org/t/p/w500/poster.jpg")

    def test_accepts_thediscdb_host(self):
        assert is_allowed_image_url("https://thediscdb.com/covers/x.png")

    def test_rejects_localhost(self):
        assert not is_allowed_image_url("http://localhost/cover.jpg")

    def test_rejects_private_ip(self):
        assert not is_allowed_image_url("http://192.168.1.10/cover.jpg")

    def test_rejects_cloud_metadata_endpoint(self):
        assert not is_allowed_image_url("http://169.254.169.254/latest/meta-data/")

    def test_rejects_file_scheme(self):
        assert not is_allowed_image_url("file:///etc/passwd")

    def test_rejects_unlisted_host(self):
        assert not is_allowed_image_url("https://evil.example.com/cover.jpg")

    def test_rejects_lookalike_suffix_host(self):
        # Must not match via bare endswith — requires a dot-delimited suffix.
        assert not is_allowed_image_url("https://evilmedia-amazon.com/cover.jpg")

    def test_rejects_empty_url(self):
        assert not is_allowed_image_url("")


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
