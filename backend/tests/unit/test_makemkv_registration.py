"""Unit tests for MakeMKV settings.conf registration.

makemkvcon reads its license from MakeMKV's own settings.conf (separate from
Engram's config DB). These tests cover the upsert helper that bridges Engram's
stored ``makemkv_key`` into that file without clobbering unrelated settings.
"""

from unittest.mock import MagicMock

from app.core.makemkv_registration import makemkv_settings_path, write_makemkv_settings


class TestWriteMakemkvSettings:
    def test_creates_file_and_parent_dir(self, tmp_path):
        target = tmp_path / "MakeMKV" / "settings.conf"

        written = write_makemkv_settings("T-newkey123", settings_path=target)

        assert written is True
        assert target.exists()
        assert 'app_Key = "T-newkey123"' in target.read_text()

    def test_preserves_unrelated_settings(self, tmp_path):
        target = tmp_path / "settings.conf"
        target.write_text(
            'app_DefaultSelectionString = "-sel:all"\n'
            'app_Key = "T-oldkey"\n'
            'app_DestinationDir = "/media"\n'
        )

        write_makemkv_settings("T-rotated", settings_path=target)

        contents = target.read_text()
        assert 'app_DefaultSelectionString = "-sel:all"' in contents
        assert 'app_DestinationDir = "/media"' in contents
        # Old key replaced, exactly one app_Key line remains.
        assert 'app_Key = "T-rotated"' in contents
        assert 'app_Key = "T-oldkey"' not in contents
        assert contents.count("app_Key") == 1

    def test_appends_key_when_absent(self, tmp_path):
        target = tmp_path / "settings.conf"
        target.write_text('app_DefaultLanguage = "eng"\n')

        write_makemkv_settings("T-appended", settings_path=target)

        contents = target.read_text()
        assert 'app_DefaultLanguage = "eng"' in contents
        assert 'app_Key = "T-appended"' in contents

    def test_blank_key_is_noop(self, tmp_path):
        target = tmp_path / "settings.conf"

        assert write_makemkv_settings("", settings_path=target) is False
        assert write_makemkv_settings("   ", settings_path=target) is False
        assert not target.exists()

    def test_idempotent_when_key_unchanged(self, tmp_path):
        target = tmp_path / "settings.conf"

        assert write_makemkv_settings("T-same", settings_path=target) is True
        # Second call with the same key should not rewrite the file.
        assert write_makemkv_settings("T-same", settings_path=target) is False


class TestSettingsPathResolution:
    def test_unix_path_uses_home_dot_makemkv(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        path = makemkv_settings_path()

        assert path == tmp_path / ".MakeMKV" / "settings.conf"

    def test_windows_path_uses_appdata(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))

        path = makemkv_settings_path()

        assert path == tmp_path / "AppData" / "Roaming" / "MakeMKV" / "settings.conf"


class TestConfigServiceWiring:
    """update_config must register a changed MakeMKV key with makemkvcon."""

    async def test_update_config_registers_makemkv_key(self, monkeypatch):
        from app.services import config_service

        # Override the conftest no-op with a recording mock (lazy import in
        # update_config re-reads this attribute at call time).
        mock_write = MagicMock(return_value=True)
        monkeypatch.setattr("app.core.makemkv_registration.write_makemkv_settings", mock_write)

        await config_service.update_config(makemkv_key="T-wired-key")

        mock_write.assert_called_once_with("T-wired-key")

    async def test_update_config_skips_registration_without_key(self, monkeypatch):
        from app.services import config_service

        mock_write = MagicMock(return_value=True)
        monkeypatch.setattr("app.core.makemkv_registration.write_makemkv_settings", mock_write)

        await config_service.update_config(staging_path="/tmp/x")

        mock_write.assert_not_called()
