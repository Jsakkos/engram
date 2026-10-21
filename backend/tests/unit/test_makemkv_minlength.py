"""MakeMKV's minimum title length is configurable (#771).

MakeMKV hides titles shorter than ``--minlength`` (120 s by default), so short
DVD specials never reached Engram. The setting rides on every scan, rip and
backup invocation. It must be the SAME value for a scan and the rip that
follows: MakeMKV numbers titles after filtering, so a different minimum would
shift every title index between the two.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.extractor import (
    DEFAULT_MIN_TITLE_LENGTH,
    MakeMKVExtractor,
    _build_backup_command,
    _build_rip_commands,
    _build_scan_command,
)


def _assert_minlength_before(cmd: list[str], verb: str, seconds: int) -> None:
    flag = f"--minlength={seconds}"
    assert flag in cmd, f"{verb} argv is missing {flag}: {cmd}"
    assert cmd.index(flag) < cmd.index(verb), f"{flag} must precede {verb!r}: {cmd}"


def test_default_matches_makemkv():
    assert DEFAULT_MIN_TITLE_LENGTH == 120


@pytest.mark.unit
class TestMinlengthOnEveryCall:
    def test_scan(self):
        _assert_minlength_before(_build_scan_command("mmk", "dev:E:", min_length=30), "info", 30)

    def test_rip_all_pass(self):
        [(_, cmd)] = _build_rip_commands("mmk", "dev:E:", "/out", None, min_length=30)
        _assert_minlength_before(cmd, "mkv", 30)

    def test_rip_per_title(self):
        for _, cmd in _build_rip_commands("mmk", "dev:E:", "/out", [1, 3], min_length=0):
            _assert_minlength_before(cmd, "mkv", 0)

    def test_backup(self):
        cmd = _build_backup_command("mmk", "disc:0", "/b/x.partial", min_length=45)
        _assert_minlength_before(cmd, "backup", 45)

    def test_builders_default_to_makemkvs_own_minimum(self):
        _assert_minlength_before(_build_scan_command("mmk", "dev:E:"), "info", 120)


@pytest.mark.unit
class TestExtractorReadsTheSetting:
    def test_explicit_value_wins(self):
        assert MakeMKVExtractor(Path("mmk"), min_title_length=15).min_title_length == 15

    def test_reads_the_configured_value(self):
        with patch(
            "app.services.config_service.get_config_sync",
            return_value=SimpleNamespace(makemkv_min_title_length=42),
        ):
            assert MakeMKVExtractor(Path("mmk")).min_title_length == 42

    def test_unset_config_column_falls_back_to_makemkvs_default(self):
        """An upgraded row read before the reconciler backfills reads as None."""
        with patch(
            "app.services.config_service.get_config_sync",
            return_value=SimpleNamespace(makemkv_min_title_length=None),
        ):
            assert MakeMKVExtractor(Path("mmk")).min_title_length == DEFAULT_MIN_TITLE_LENGTH

    @pytest.mark.asyncio
    async def test_scan_disc_passes_the_setting(self):
        ex = MakeMKVExtractor(makemkv_path=Path("mmk"), min_title_length=10)
        with patch("app.core.extractor.subprocess.Popen") as popen:
            proc = popen.return_value
            proc.communicate.return_value = ("", "")
            proc.returncode = 0
            await ex.scan_disc("dev:E:")
        _assert_minlength_before(popen.call_args.args[0], "info", 10)


@pytest.mark.unit
class TestConfigField:
    """A new AppConfig field must reach ConfigUpdate (PUT accepts it) and
    ConfigResponse (GET returns it), or the setting silently does nothing."""

    FIELD = "makemkv_min_title_length"

    def test_defaults_to_makemkvs_minimum(self):
        from app.models.app_config import AppConfig

        assert getattr(AppConfig(), self.FIELD) == 120

    def test_upgraded_databases_get_the_default(self):
        from app.models.app_config import AppConfig

        column = AppConfig.__table__.c[self.FIELD]
        assert str(column.server_default.arg) == "120"

    def test_config_update_accepts_and_carries_the_field(self):
        from app.api.routes import ConfigUpdate

        assert ConfigUpdate(**{self.FIELD: 30}).model_dump()[self.FIELD] == 30
        assert ConfigUpdate().model_dump()[self.FIELD] is None

    @pytest.mark.parametrize("bad", [-1, 3601])
    def test_config_update_rejects_out_of_range(self, bad):
        from pydantic import ValidationError

        from app.api.routes import ConfigUpdate

        with pytest.raises(ValidationError):
            ConfigUpdate(**{self.FIELD: bad})

    def test_config_response_exposes_the_field(self):
        from app.api.routes import ConfigResponse

        assert self.FIELD in ConfigResponse.model_fields
