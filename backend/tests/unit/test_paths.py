"""Configured-path normalization and checking, including network shares.

Library and staging paths are the settings most likely to be silently wrong, and
a wrong one only surfaces as an organize failure after a full rip. These tests
cover the two halves of preventing that: storing what the user meant, and
reporting honestly whether Engram can actually use it.
"""

from unittest.mock import patch

import pytest

from app.core import paths as paths_mod
from app.core.paths import describe_path, is_unc_path, normalize_user_path


@pytest.mark.unit
class TestIsUncPath:
    @pytest.mark.parametrize(
        "value", [r"\\server\share", "//server/share", r"\\server\share\Media", "//server/share/"]
    )
    def test_network_paths(self, value):
        assert is_unc_path(value) is True

    @pytest.mark.parametrize("value", [r"C:\Media", "/mnt/media", "media", "", "/"])
    def test_local_paths(self, value):
        assert is_unc_path(value) is False


@pytest.mark.unit
class TestNormalizeUserPath:
    def test_strips_the_quotes_windows_copy_as_path_adds(self):
        assert normalize_user_path('"/mnt/media"') == normalize_user_path("/mnt/media")

    def test_strips_surrounding_whitespace(self):
        assert normalize_user_path("  /mnt/media  ") == normalize_user_path("/mnt/media")

    def test_blank_input_is_blank_output(self):
        assert normalize_user_path(None) == ""
        assert normalize_user_path("   ") == ""

    def test_unc_spellings_converge_on_windows(self):
        # "//server/share" (what a shell or address bar gives) and the typed
        # backslash form are ONE setting, not two that happen to behave alike.
        with patch.object(paths_mod, "IS_WINDOWS", True):
            assert normalize_user_path("//server/share/Media") == r"\\server\share\Media"
            assert normalize_user_path(r"\\server\share\Media") == r"\\server\share\Media"

    def test_unc_keeps_its_leading_pair(self):
        with patch.object(paths_mod, "IS_WINDOWS", True):
            assert normalize_user_path(r"\\server\share").startswith("\\\\")

    def test_unc_trailing_separator_is_trimmed(self):
        with patch.object(paths_mod, "IS_WINDOWS", True):
            assert normalize_user_path("//server/share/") == r"\\server\share"

    def test_unc_is_left_alone_off_windows(self):
        # Rewriting it would disguise a real mistake; describe_path names it.
        with patch.object(paths_mod, "IS_WINDOWS", False):
            assert normalize_user_path(r"\\server\share") == r"\\server\share"

    def test_home_is_expanded(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        assert "~" not in normalize_user_path("~/Media")


@pytest.mark.unit
class TestDescribePath:
    def test_a_writable_folder_is_ok(self, tmp_path):
        status = describe_path(str(tmp_path))
        assert status.ok is True
        assert status.exists is True
        assert status.writable is True
        assert status.reason is None

    def test_the_write_probe_leaves_nothing_behind(self, tmp_path):
        describe_path(str(tmp_path))
        assert list(tmp_path.iterdir()) == []

    def test_a_missing_folder_is_reported_but_not_fatal(self, tmp_path):
        status = describe_path(str(tmp_path / "not-yet"))
        assert status.exists is False
        assert status.ok is False
        assert "doesn't exist" in status.reason

    def test_a_relative_path_is_rejected(self):
        # It would resolve against wherever Engram happened to be started.
        status = describe_path("media/tv")
        assert status.ok is False
        assert "full path" in status.reason

    def test_a_file_is_not_a_folder(self, tmp_path):
        target = tmp_path / "a-file.mkv"
        target.write_text("x")
        status = describe_path(str(target))
        assert status.ok is False
        assert "not a folder" in status.reason

    def test_an_empty_setting_says_so(self):
        status = describe_path("")
        assert status.ok is False
        assert status.reason == "No path set."

    def test_an_unwritable_folder_is_reported(self, tmp_path):
        # The permission bits on a share or container mount routinely disagree
        # with reality, so the check writes rather than reasoning about modes.
        with patch("pathlib.Path.touch", side_effect=PermissionError(13, "Permission denied")):
            status = describe_path(str(tmp_path))
        assert status.ok is False
        assert status.writable is False
        assert "can't write" in status.reason

    def test_probe_write_false_skips_the_touch(self, tmp_path):
        with patch("pathlib.Path.touch", side_effect=AssertionError("must not probe")):
            status = describe_path(str(tmp_path), probe_write=False)
        assert status.ok is True

    def test_reports_free_space_for_an_existing_folder(self, tmp_path):
        assert describe_path(str(tmp_path)).free_bytes > 0


@pytest.mark.unit
class TestNetworkShares:
    def test_an_offline_share_is_a_warning_not_an_error(self, tmp_path):
        with patch.object(paths_mod, "IS_WINDOWS", True):
            status = describe_path(r"\\server\share")
        assert status.is_network is True
        # Unreachable, but still worth saving: the share may simply be off right
        # now, and refusing to save it would be worse than flagging it.
        assert status.unreachable is True
        assert "reachable" in status.reason

    def test_a_dead_share_that_errors_on_exists_is_handled(self, tmp_path):
        with (
            patch.object(paths_mod, "IS_WINDOWS", True),
            patch("pathlib.Path.exists", side_effect=OSError(64, "The specified network name...")),
        ):
            status = describe_path(r"\\server\share")
        assert status.unreachable is True
        assert status.ok is False

    def test_a_windows_share_path_on_another_platform_is_explained(self):
        with patch.object(paths_mod, "IS_WINDOWS", False):
            status = describe_path(r"\\server\share")
        assert status.ok is False
        assert "mount" in status.reason.lower()
