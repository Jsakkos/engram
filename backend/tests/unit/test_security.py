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

    def test_rejects_ipv6_loopback(self):
        # [::1] is the IPv6 loopback — must be blocked by the IP guard.
        assert not is_allowed_image_url("http://[::1]/cover.jpg")

    def test_rejects_ipv4_mapped_ipv6_loopback(self):
        # ::ffff:127.0.0.1 is the IPv4-mapped form of 127.0.0.1.
        assert not is_allowed_image_url("http://[::ffff:127.0.0.1]/cover.jpg")

    def test_rejects_ipv6_unique_local(self):
        # fc00::/7 is the IPv6 unique-local (private) range.
        assert not is_allowed_image_url("http://[fc00::1]/cover.jpg")

    def test_rejects_public_ip_literal(self):
        # A bare public IP is rejected too — allowlisted CDNs use DNS names.
        assert not is_allowed_image_url("http://8.8.8.8/cover.jpg")

    def test_rejects_malformed_ipv6_url(self):
        # A malformed bracketed literal must yield False, never raise.
        assert not is_allowed_image_url("http://[::1bad]/cover.jpg")
        assert not is_allowed_image_url("http://[:::]/cover.jpg")

    def test_rejects_userinfo_host_confusion(self):
        # urlparse treats the part before "@" as userinfo — the real host is
        # evil.com, which must be rejected despite the allowlisted prefix.
        assert not is_allowed_image_url("https://m.media-amazon.com@evil.com/cover.jpg")


class TestExecutableBasenameAllowed:
    """Exact-basename allowlist guard for the tool-validation subprocess calls."""

    _MAKEMKV = [
        "makemkvcon",
        "makemkvcon.exe",
        "makemkvcon64",
        "makemkvcon64.exe",
        "com.makemkv.MakeMKV",
    ]
    _FFMPEG = ["ffmpeg", "ffmpeg.exe"]

    def test_accepts_makemkv_windows_exe(self):
        assert executable_basename_allowed(
            "C:\\Program Files\\MakeMKV\\makemkvcon64.exe", self._MAKEMKV
        )

    def test_accepts_makemkv_linux_binary(self):
        assert executable_basename_allowed("/usr/bin/makemkvcon", self._MAKEMKV)

    def test_accepts_makemkv_macos_bundle(self):
        assert executable_basename_allowed(
            "/var/lib/flatpak/exports/bin/com.makemkv.MakeMKV", self._MAKEMKV
        )

    def test_accepts_ffmpeg_binary(self):
        assert executable_basename_allowed("/usr/local/bin/ffmpeg", self._FFMPEG)

    def test_rejects_arbitrary_shell(self):
        assert not executable_basename_allowed("/bin/sh", self._MAKEMKV)

    def test_rejects_powershell(self):
        assert not executable_basename_allowed("C:\\Windows\\System32\\cmd.exe", self._FFMPEG)

    def test_rejects_substring_lookalike_script(self):
        # A substring check would let this through — exact match must reject it.
        assert not executable_basename_allowed("/tmp/makemkv-exploit.sh", self._MAKEMKV)

    def test_match_is_case_insensitive(self):
        assert executable_basename_allowed("/opt/MakeMKVcon", self._MAKEMKV)
