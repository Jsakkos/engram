"""Unit tests for app.core.security — SSRF and command-injection guard helpers.

These cover the CodeQL-flagged sinks:
- is_allowed_image_url: py/full-ssrf in the fetch_cover endpoint
- executable_basename_allowed: py/command-line-injection in validate_makemkv/ffmpeg
- sanitize_log_value: py/log-injection in disc-event logging
"""

import pytest

from app.core.security import (
    executable_basename_allowed,
    is_allowed_image_url,
    is_within_configured_roots,
    sanitize_log_value,
    sanitize_playlist_field,
)


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


class TestSanitizeLogValue:
    """Log-injection guard for disc/user-controlled values written to logs."""

    def test_strips_crlf_forged_entry(self):
        # A crafted volume label must not be able to inject a second log line.
        forged = "DISC\r\n2026-01-01 00:00:00 | INFO | forged: admin login"
        assert "\n" not in sanitize_log_value(forged)
        assert "\r" not in sanitize_log_value(forged)

    def test_strips_lone_newline(self):
        assert sanitize_log_value("a\nb") == "ab"

    def test_strips_other_control_chars(self):
        # ESC (terminal escape) and NUL removed; surrounding text preserved.
        assert sanitize_log_value("a\x1b[31mb\x00c") == "a[31mbc"

    def test_keeps_tab_and_unicode(self):
        assert sanitize_log_value("col1\tcol2 café") == "col1\tcol2 café"

    def test_coerces_non_str(self):
        assert sanitize_log_value(123) == "123"


class TestSanitizePlaylistField:
    """Playlist-injection guard for disc-derived text embedded in an .m3u."""

    def test_strips_lf(self):
        assert "\n" not in sanitize_playlist_field("a\nb")

    def test_strips_cr(self):
        assert "\r" not in sanitize_playlist_field("a\rb")

    def test_strips_crlf(self):
        result = sanitize_playlist_field("a\r\nb")
        assert "\r" not in result
        assert "\n" not in result

    def test_strips_control_chars(self):
        assert sanitize_playlist_field("a\x1b[31mb\x00c") == "a[31mbc"

    def test_empty_input_returns_empty_string(self):
        assert sanitize_playlist_field("") == ""

    def test_leaves_ordinary_label_unchanged(self):
        assert sanitize_playlist_field("Arrested Development S1D1") == "Arrested Development S1D1"

    def test_crlf_collapses_to_a_single_space(self):
        # One boundary, one space. Replacing CR and LF independently would
        # leave two, which is cosmetic here but signals the pair was not
        # recognised as a unit.
        assert sanitize_playlist_field("a\r\nb") == "a b"

    @pytest.mark.parametrize(
        ("name", "codepoint"),
        [("NEL", 0x85), ("LINE SEPARATOR", 0x2028), ("PARAGRAPH SEPARATOR", 0x2029)],
    )
    def test_strips_exotic_unicode_line_boundaries(self, name, codepoint):
        """Python treats these as line breaks even though .m3u parsers do not.

        No mainstream demuxer splits on them, so they are not exploitable
        against VLC or MPV, but the function claims to strip line breaks and
        that claim should hold against Python's own definition rather than
        against whichever parser happens to read the output. Written with
        chr() so the source file never contains a literal separator, which
        would break the line it sits on.
        """
        result = sanitize_playlist_field(f"a{chr(codepoint)}b")
        assert result == "a b", name
        assert len(result.splitlines()) == 1, name


class TestIsWithinConfiguredRoots:
    """Containment guard for media paths read out of the database."""

    def test_accepts_file_directly_in_root(self, tmp_path):
        root = tmp_path / "staging"
        root.mkdir()
        target = root / "title_01.mkv"
        target.touch()
        assert is_within_configured_roots(target, [str(root)])

    def test_accepts_file_in_nested_subdirectory(self, tmp_path):
        root = tmp_path / "library"
        nested = root / "Show" / "Season 01"
        nested.mkdir(parents=True)
        target = nested / "Show - S01E01.mkv"
        target.touch()
        assert is_within_configured_roots(target, [str(root)])

    def test_accepts_when_any_one_root_matches(self, tmp_path):
        staging = tmp_path / "staging"
        library = tmp_path / "library"
        staging.mkdir()
        library.mkdir()
        target = library / "movie.mkv"
        target.touch()
        assert is_within_configured_roots(target, [str(staging), str(library)])

    def test_rejects_file_outside_every_root(self, tmp_path):
        root = tmp_path / "staging"
        root.mkdir()
        outside = tmp_path / "elsewhere.mkv"
        outside.touch()
        assert not is_within_configured_roots(outside, [str(root)])

    def test_rejects_parent_traversal(self, tmp_path):
        root = tmp_path / "staging"
        root.mkdir()
        secret = tmp_path / "secret.mkv"
        secret.touch()
        traversal = root / ".." / "secret.mkv"
        assert not is_within_configured_roots(traversal, [str(root)])

    def test_rejects_sibling_root_with_shared_prefix(self, tmp_path):
        # "staging-old" must not pass a "staging" root via a bare string prefix
        # comparison. commonpath compares path components, not characters.
        root = tmp_path / "staging"
        sibling = tmp_path / "staging-old"
        root.mkdir()
        sibling.mkdir()
        target = sibling / "leak.mkv"
        target.touch()
        assert not is_within_configured_roots(target, [str(root)])

    def test_rejects_empty_root_list(self, tmp_path):
        target = tmp_path / "x.mkv"
        target.touch()
        assert not is_within_configured_roots(target, [])

    def test_ignores_unset_roots(self, tmp_path):
        # Unconfigured paths arrive as "" from AppConfig defaults. An empty
        # string must never be treated as a root, or it would resolve to the
        # process CWD and silently authorise it.
        root = tmp_path / "staging"
        root.mkdir()
        target = root / "ok.mkv"
        target.touch()
        assert is_within_configured_roots(target, ["", str(root), ""])
        outside = tmp_path / "nope.mkv"
        outside.touch()
        assert not is_within_configured_roots(outside, ["", ""])

    def test_rejects_none_path(self, tmp_path):
        # output_filename/organized_to are both `str | None`. A guard that
        # raises on the normal unset case is not a barrier guard.
        root = tmp_path / "staging"
        root.mkdir()
        assert not is_within_configured_roots(None, [str(root)])

    def test_rejects_empty_path(self, tmp_path):
        root = tmp_path / "staging"
        root.mkdir()
        assert not is_within_configured_roots("", [str(root)])

    def test_rejects_symlink_escaping_root(self, tmp_path):
        root = tmp_path / "staging"
        root.mkdir()
        secret = tmp_path / "secret.mkv"
        secret.write_bytes(b"x")
        link = root / "innocent.mkv"
        try:
            link.symlink_to(secret)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable (Windows without developer mode)")
        assert not is_within_configured_roots(link, [str(root)])
