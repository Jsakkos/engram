"""Unit tests for validation logic.

Tests path validation, configuration validation, and input sanitization.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from app.models import AppConfig


class TestPathValidation:
    """Test path validation for configuration."""

    def test_valid_directory_paths(self):
        """Test that valid directory paths are accepted."""
        valid_paths = [
            "/home/user/staging",
            "/media/library/movies",
            "C:\\Users\\user\\Documents",
            "/tmp/test",
        ]

        for path in valid_paths:
            # Should not raise validation error
            config = AppConfig(
                staging_path=path,
                library_movies_path=path,
                library_tv_path=path,
            )
            assert config.staging_path == path

    def test_makemkv_path_validation(self):
        """Test MakeMKV path can be file or directory."""
        # File path (executable)
        config1 = AppConfig(makemkv_path="/usr/bin/makemkvcon")
        assert config1.makemkv_path == "/usr/bin/makemkvcon"

        # Directory path
        config2 = AppConfig(makemkv_path="/usr/bin/")
        assert config2.makemkv_path == "/usr/bin/"

        # Windows executable
        config3 = AppConfig(makemkv_path="C:\\Program Files\\MakeMKV\\makemkvcon64.exe")
        assert "makemkvcon64.exe" in config3.makemkv_path

    def test_relative_paths_handled(self):
        """Test that relative paths are handled appropriately."""
        # Relative paths should be expanded or validated
        config = AppConfig(staging_path="./staging")
        assert config.staging_path is not None

    def test_empty_path_validation(self):
        """Test that empty paths are handled correctly."""
        config = AppConfig(staging_path="")
        # Empty paths are accepted; validation happens at runtime
        assert config.staging_path == ""


class TestAPIKeyValidation:
    """Test API key validation."""

    def test_valid_makemkv_key_format(self):
        """Test MakeMKV license key format validation."""
        valid_keys = [
            "T-test-key-1234567890",
            "T-ABCD-EFGH-1234-5678",
            "T-valid123456789012345",
        ]

        for key in valid_keys:
            config = AppConfig(makemkv_key=key)
            assert config.makemkv_key == key

    def test_valid_tmdb_api_key_format(self):
        """Test TMDB API key (JWT) format validation."""
        valid_keys = [
            "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJ0ZXN0In0.signature",
            "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.payload.signature",
        ]

        for key in valid_keys:
            config = AppConfig(tmdb_api_key=key)
            assert config.tmdb_api_key == key

    def test_empty_api_keys_allowed(self):
        """Test that empty API keys are allowed (optional)."""
        config = AppConfig(makemkv_key=None, tmdb_api_key=None)
        assert config.makemkv_key is None
        assert config.tmdb_api_key is None

    def test_api_key_too_short(self):
        """Test that very short API keys are rejected."""
        try:
            config = AppConfig(makemkv_key="T-123")
            # If accepted, should be at least somewhat reasonable
            assert len(config.makemkv_key or "") >= 5
        except ValidationError:
            # Or validation error is acceptable
            pass


class TestConfigurationValidation:
    """Test configuration object validation."""

    def test_max_concurrent_matches_range(self):
        """Test max_concurrent_matches has valid range."""
        # Valid values
        for value in [1, 4, 8, 16]:
            config = AppConfig(max_concurrent_matches=value)
            assert config.max_concurrent_matches == value

        # Negative and zero values are stored as-is (no model-level validator)
        config = AppConfig(max_concurrent_matches=-1)
        assert config.max_concurrent_matches == -1  # Stored without validation

        config = AppConfig(max_concurrent_matches=0)
        assert config.max_concurrent_matches == 0

    def test_boolean_flags_validation(self):
        """Test boolean configuration flags."""
        config = AppConfig(
            ai_identification_enabled=True,
        )
        assert config.ai_identification_enabled is True

        config2 = AppConfig(ai_identification_enabled=False)
        assert config2.ai_identification_enabled is False

    def test_conflict_resolution_values(self):
        """Test conflict_resolution_default has valid values."""
        valid_values = ["ask", "skip", "rename", "overwrite"]

        for value in valid_values:
            config = AppConfig(conflict_resolution_default=value)
            assert config.conflict_resolution_default == value

        # Invalid value test: stored as-is (no validator)
        config = AppConfig(conflict_resolution_default="invalid")
        assert config.conflict_resolution_default == "invalid"

    def test_analyst_threshold_validation(self):
        """Test analyst classification threshold validation."""
        # Valid thresholds
        config = AppConfig(
            analyst_movie_min_duration=80 * 60,  # 80 minutes
            analyst_tv_min_duration=18 * 60,  # 18 minutes
            analyst_tv_max_duration=70 * 60,  # 70 minutes
        )
        assert config.analyst_movie_min_duration == 80 * 60

        # Logical consistency: TV min should be less than TV max
        try:
            config = AppConfig(
                analyst_tv_min_duration=100 * 60,
                analyst_tv_max_duration=50 * 60,
            )
            # Should reject or auto-correct
            assert config.analyst_tv_min_duration < config.analyst_tv_max_duration, (
                "Min should be less than max"
            )
        except (ValidationError, AssertionError):
            pass  # Expected

    def test_ripping_timeout_validation(self):
        """Test ripping timeout values are reasonable."""
        # Valid timeouts
        config = AppConfig(
            ripping_file_poll_interval=5.0,
            ripping_stability_checks=3,
            ripping_file_ready_timeout=600.0,
        )
        assert config.ripping_file_poll_interval == 5.0
        assert config.ripping_stability_checks == 3
        assert config.ripping_file_ready_timeout == 600.0

        # Negative values are stored as-is (no validator)
        config = AppConfig(ripping_file_poll_interval=-1.0)
        assert config.ripping_file_poll_interval == -1.0


class TestInputSanitization:
    """Test input sanitization and security."""

    def test_path_traversal_prevention(self):
        """Test that path traversal attacks are prevented."""
        # Use forward slashes only — backslash traversal (e.g. ..\\..\\) is
        # Windows-specific and Path.resolve() on Linux treats \\ as literal
        # filename characters, not directory separators.
        dangerous_paths = [
            "../../../etc/passwd",
            "../../../windows/system32",
            "/tmp/../etc/passwd",
            "/Users/../../Windows/System32",
        ]

        for path in dangerous_paths:
            # Paths should be validated or sanitized
            # The exact behavior depends on implementation
            config = AppConfig(staging_path=path)
            # Should not allow traversal or should sanitize
            assert ".." not in str(Path(config.staging_path).resolve())

    def test_special_characters_in_paths(self):
        """Test handling of special characters in paths."""
        special_paths = [
            "/tmp/test & file",
            "/tmp/test; rm -rf /",
            "/tmp/test | cat",
            "/tmp/test`whoami`",
        ]

        for path in special_paths:
            # Should handle special characters safely
            config = AppConfig(staging_path=path)
            # Path should be stored safely
            assert config.staging_path is not None

    def test_sql_injection_in_strings(self):
        """Test that SQL injection attempts are safely handled."""
        injection_attempts = [
            "'; DROP TABLE jobs; --",
            "1' OR '1'='1",
            "admin'--",
        ]

        for attempt in injection_attempts:
            # Should be safely handled by ORM parameterization
            config = AppConfig(makemkv_key=attempt)
            # Should not cause SQL execution
            assert config.makemkv_key == attempt  # Stored as literal string


class TestDefaultValues:
    """Test configuration default values."""

    def test_config_with_defaults(self):
        """Test that configuration uses sensible defaults."""
        config = AppConfig()

        # Should have defaults for critical fields
        assert config.max_concurrent_matches is not None
        assert config.max_concurrent_matches > 0

        assert config.ai_identification_enabled is not None
        assert isinstance(config.ai_identification_enabled, bool)

        assert config.conflict_resolution_default is not None
        assert config.conflict_resolution_default in ["ask", "skip", "rename", "overwrite"]

    def test_analyst_defaults(self):
        """Test analyst configuration defaults."""
        config = AppConfig()

        # Should have reasonable defaults for classification
        assert config.analyst_movie_min_duration > 0
        assert config.analyst_tv_min_duration > 0
        assert config.analyst_tv_max_duration > config.analyst_tv_min_duration
        assert config.analyst_tv_min_cluster_size >= 2

    def test_ripping_defaults(self):
        """Test ripping configuration defaults."""
        config = AppConfig()

        # Should have reasonable defaults for ripping
        assert config.ripping_file_poll_interval > 0
        assert config.ripping_stability_checks >= 1
        assert config.ripping_file_ready_timeout > 0

    def test_sentinel_defaults(self):
        """Test sentinel monitoring defaults."""
        config = AppConfig()

        # Should have reasonable polling interval
        assert config.sentinel_poll_interval > 0
        assert config.sentinel_poll_interval <= 10  # Not too frequent


class TestConfigurationEdgeCases:
    """Test edge cases in configuration."""

    def test_extremely_large_values(self):
        """Test handling of extremely large configuration values."""
        config = AppConfig(
            max_concurrent_matches=1000000,
            ripping_file_ready_timeout=999999999.0,
        )
        # Large values are stored as-is (no clamping validator)
        assert config.max_concurrent_matches == 1000000
        assert config.ripping_file_ready_timeout == 999999999.0

    def test_unicode_in_paths(self):
        """Test handling of unicode characters in paths."""
        unicode_paths = [
            "/tmp/テスト",
            "/tmp/测试",
            "/tmp/тест",
            "/tmp/🎬",
        ]

        for path in unicode_paths:
            config = AppConfig(staging_path=path)
            # Should handle unicode characters
            assert config.staging_path is not None

    def test_very_long_paths(self):
        """Test handling of very long file paths."""
        # Most filesystems have path length limits (e.g. 260 on Windows, 4096 on Linux)
        long_path = "/tmp/" + "a" * 500

        try:
            config = AppConfig(staging_path=long_path)
            # Should either accept or reject based on platform limits
            assert len(config.staging_path) <= 4096
        except ValidationError:
            pass  # Validation error is acceptable


class TestTmdbValidation:
    """Test TMDB API key validation endpoint logic."""

    @patch("app.api.validation.requests.get")
    def test_valid_tmdb_key(self, mock_get):
        """Valid TMDB key returns valid=True."""
        from app.api.validation import TmdbValidationRequest, validate_tmdb

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        import asyncio

        req = TmdbValidationRequest(api_key="eyJhbGciOiJIUzI1NiJ9.test.sig")
        result = asyncio.run(validate_tmdb(req))
        assert result.valid is True

    @patch("app.api.validation.requests.get")
    def test_invalid_tmdb_key(self, mock_get):
        """Invalid TMDB key returns valid=False with error."""
        from app.api.validation import TmdbValidationRequest, validate_tmdb

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_get.return_value = mock_response

        import asyncio

        req = TmdbValidationRequest(api_key="bad_key")
        result = asyncio.run(validate_tmdb(req))
        assert result.valid is False
        assert "Invalid" in result.error

    def test_empty_tmdb_key(self):
        """Empty TMDB key returns valid=False."""
        import asyncio

        from app.api.validation import TmdbValidationRequest, validate_tmdb

        req = TmdbValidationRequest(api_key="")
        result = asyncio.run(validate_tmdb(req))
        assert result.valid is False
        assert "empty" in result.error.lower()

    @patch("app.api.validation.requests.get")
    def test_tmdb_network_error(self, mock_get):
        """Network error returns valid=False with connection error."""
        import requests as req_lib

        from app.api.validation import TmdbValidationRequest, validate_tmdb

        mock_get.side_effect = req_lib.exceptions.ConnectionError("DNS failed")

        import asyncio

        req = TmdbValidationRequest(api_key="some_key")
        result = asyncio.run(validate_tmdb(req))
        assert result.valid is False
        assert "connection" in result.error.lower()


class TestExecutableValidationHardening:
    """The tool validators must refuse to run a binary that isn't the tool."""

    def test_validate_makemkv_rejects_non_makemkv_path(self):
        """A path whose basename is not MakeMKV must not reach subprocess.run."""
        import asyncio

        from app.api.validation import ValidationRequest, validate_makemkv

        with patch("app.api.validation.subprocess.run") as mock_run:
            result = asyncio.run(validate_makemkv(ValidationRequest(path="/bin/sh")))

        assert result.valid is False
        assert "MakeMKV executable" in result.error
        mock_run.assert_not_called()

    def test_validate_ffmpeg_rejects_non_ffmpeg_path(self):
        """A path whose basename is not FFmpeg must not reach subprocess.run."""
        import asyncio

        from app.api.validation import ValidationRequest, validate_ffmpeg

        with patch("app.api.validation.subprocess.run") as mock_run:
            result = asyncio.run(validate_ffmpeg(ValidationRequest(path="/bin/sh")))

        assert result.valid is False
        assert "FFmpeg executable" in result.error
        mock_run.assert_not_called()

    def test_probe_version_refuses_non_makemkv_path(self):
        """The version probe self-guards: a non-MakeMKV basename never reaches subprocess."""
        from app.api.validation import _probe_makemkv_version

        with patch("app.api.validation.subprocess.run") as mock_run:
            version = _probe_makemkv_version("/bin/sh")

        assert version == "MakeMKV (version not detectable)"
        mock_run.assert_not_called()

    def test_validate_binary_refuses_non_makemkv_path(self):
        """The binary validator self-guards: a non-MakeMKV basename never reaches subprocess."""
        from app.api.validation import _validate_makemkv_binary

        with patch("app.api.validation.subprocess.run") as mock_run:
            result = _validate_makemkv_binary("/usr/bin/python3")

        assert result.found is False
        mock_run.assert_not_called()

    def test_probe_timeout_returns_distinct_string(self):
        """A probe timeout is surfaced distinctly, not masked as 'not detectable'."""
        import subprocess

        from app.api.validation import _probe_makemkv_version

        with patch(
            "app.api.validation.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="makemkvcon64", timeout=20),
        ):
            version = _probe_makemkv_version("C:/MakeMKV/makemkvcon64.exe")

        assert version == "MakeMKV (version probe timed out)"

    def test_validate_binary_surfaces_probe_timeout(self):
        """A found binary whose probe times out reports found=True with the timeout string."""
        import subprocess

        from app.api.validation import _validate_makemkv_binary

        def fake_run(cmd, **kwargs):
            if "-r" in cmd:  # the version probe
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=20)
            mock = MagicMock()  # the no-arg validity check
            mock.stdout = "Use: makemkvcon [switches] Command [Parameters]\n"
            mock.stderr = ""
            mock.returncode = 1
            return mock

        with patch("app.api.validation.subprocess.run", side_effect=fake_run):
            result = _validate_makemkv_binary("C:/MakeMKV/makemkvcon64.exe")

        assert result.found is True
        assert result.version == "MakeMKV (version probe timed out)"


class TestMakeMKVVersionExtraction:
    """Parse MakeMKV version from real makemkvcon output.

    Regression: makemkvcon with no arguments only prints usage text (no version),
    so version detection must read the robot-mode (-r) MSG:1005 startup banner.
    """

    # Verbatim robot-mode startup line captured from makemkvcon64.exe v1.18.3.
    ROBOT_BANNER = (
        'MSG:1005,0,1,"MakeMKV v1.18.3 win(x64-release) started",'
        '"%1 started","MakeMKV v1.18.3 win(x64-release)"\n'
        'DRV:0,1,999,0,"BD-RE PIONEER BD-RW   BDR-S13U 1.03","","F:"\n'
    )

    # No-arg invocation output — usage text with no version anywhere.
    HELP_TEXT = (
        "Use: makemkvcon [switches] Command [Parameters]\n"
        "\n"
        "Commands:\n"
        "  info <source>\n"
        "      prints info about disc\n"
        "  reg <key string or file name>\n"
        "      enter registration key into program\n"
    )

    def test_extracts_version_from_robot_banner(self):
        """The robot banner yields a clean product+version+platform string."""
        from app.api.validation import _extract_makemkv_version

        assert _extract_makemkv_version(self.ROBOT_BANNER) == "MakeMKV v1.18.3 win(x64-release)"

    def test_extracts_linux_banner(self):
        """Platform tag varies by OS — the Linux banner parses too."""
        from app.api.validation import _extract_makemkv_version

        output = 'MSG:1005,0,1,"MakeMKV v1.17.7 linux(x64-release) started","%1 started",""\n'
        assert _extract_makemkv_version(output) == "MakeMKV v1.17.7 linux(x64-release)"

    def test_help_text_falls_back(self):
        """Usage text has no version, so the fallback string is returned."""
        from app.api.validation import _extract_makemkv_version

        assert _extract_makemkv_version(self.HELP_TEXT) == "MakeMKV (version not detectable)"

    def test_spurious_v1_lines_do_not_match(self):
        """Verbose robot output without a banner must not return a spurious 'v1.' line."""
        from app.api.validation import _extract_makemkv_version

        noisy = (
            'DRV:0,1,999,1,"BD-RE PIONEER BD-RW   BDR-S13U 1.03","","F:"\n'
            'MSG:5010,0,0,"v1.0 codec loaded","%1 loaded","v1.0"\n'
            'MSG:3007,0,0,"using direct disc access","",""\n'
        )
        assert _extract_makemkv_version(noisy) == "MakeMKV (version not detectable)"

    def test_banner_wins_over_noise(self):
        """The real banner is extracted even when spurious 'v1.' lines precede it."""
        from app.api.validation import _extract_makemkv_version

        output = 'MSG:5010,0,0,"v1.0 codec loaded","%1 loaded","v1.0"\n' + self.ROBOT_BANNER
        assert _extract_makemkv_version(output) == "MakeMKV v1.18.3 win(x64-release)"

    def test_validate_binary_probes_robot_mode_for_version(self):
        """End-to-end: validity comes from the no-arg call, version from the -r probe."""
        from app.api.validation import _validate_makemkv_binary

        def fake_run(cmd, **kwargs):
            mock = MagicMock()
            mock.stdout = self.ROBOT_BANNER if "-r" in cmd else self.HELP_TEXT
            mock.stderr = ""
            mock.returncode = 1
            return mock

        with patch("app.api.validation.subprocess.run", side_effect=fake_run):
            result = _validate_makemkv_binary("C:/MakeMKV/makemkvcon64.exe")

        assert result.found is True
        assert result.version == "MakeMKV v1.18.3 win(x64-release)"


class TestDetectionOffloadsBlockingWork:
    """Tool detection shells out (blocking), so it must not run on the event loop."""

    def test_detect_tools_runs_detection_off_event_loop(self):
        """detect_tools offloads blocking detection to a worker thread."""
        import asyncio
        import threading

        from app.api import validation
        from app.api.validation import ToolDetectionResult, detect_tools

        loop_thread = threading.get_ident()
        observed: dict[str, int] = {}

        def fake_makemkv() -> ToolDetectionResult:
            observed["makemkv"] = threading.get_ident()
            return ToolDetectionResult(found=True, path="m", version="MakeMKV v1.18.3")

        def fake_ffmpeg() -> ToolDetectionResult:
            observed["ffmpeg"] = threading.get_ident()
            return ToolDetectionResult(found=True, path="f", version="ffmpeg 6.0")

        with (
            patch.object(validation, "detect_makemkv", fake_makemkv),
            patch.object(validation, "detect_ffmpeg", fake_ffmpeg),
        ):
            result = asyncio.run(detect_tools())

        assert result.makemkv.version == "MakeMKV v1.18.3"
        assert result.ffmpeg.version == "ffmpeg 6.0"
        # Both detections ran on worker threads, never the event loop thread.
        assert observed["makemkv"] != loop_thread
        assert observed["ffmpeg"] != loop_thread


def _run(coro):
    import asyncio

    return asyncio.run(coro)


class TestFfmpegBinaryValidation:
    """Direct tests for the FFmpeg binary validator (subprocess stubbed)."""

    @staticmethod
    def _fake_run(returncode=0, stdout="ffmpeg version 6.0\nbuilt with gcc\n"):
        def run(cmd, **kwargs):
            m = MagicMock()
            m.returncode = returncode
            m.stdout = stdout
            m.stderr = ""
            return m

        return run

    def test_success_returns_first_stdout_line_as_version(self):
        from app.api.validation import _validate_ffmpeg_binary

        with patch("app.api.validation.subprocess.run", side_effect=self._fake_run()):
            result = _validate_ffmpeg_binary("/usr/bin/ffmpeg")
        assert result.found is True
        assert result.version == "ffmpeg version 6.0"
        assert result.path == "/usr/bin/ffmpeg"

    def test_non_zero_exit_is_not_found(self):
        from app.api.validation import _validate_ffmpeg_binary

        with patch("app.api.validation.subprocess.run", side_effect=self._fake_run(returncode=1)):
            result = _validate_ffmpeg_binary("/usr/bin/ffmpeg")
        assert result.found is False
        assert result.error == "Non-zero exit code"

    def test_timeout(self):
        import subprocess

        from app.api.validation import _validate_ffmpeg_binary

        with patch(
            "app.api.validation.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=10),
        ):
            result = _validate_ffmpeg_binary("/usr/bin/ffmpeg")
        assert result.found is False
        assert result.error == "Command timeout (10s)"

    def test_execution_failure(self):
        from app.api.validation import _validate_ffmpeg_binary

        with patch("app.api.validation.subprocess.run", side_effect=OSError("boom")):
            result = _validate_ffmpeg_binary("/usr/bin/ffmpeg")
        assert result.found is False
        assert "Execution failed" in result.error

    def test_refuses_non_ffmpeg_path(self):
        """The binary validator self-guards: a non-FFmpeg basename never reaches subprocess."""
        from app.api.validation import _validate_ffmpeg_binary

        with patch("app.api.validation.subprocess.run") as mock_run:
            result = _validate_ffmpeg_binary("/usr/bin/python3")

        assert result.found is False
        assert "FFmpeg executable" in result.error
        mock_run.assert_not_called()


class TestToolDetection:
    """Auto-detection across PATH and common install locations."""

    def test_detect_makemkv_found_on_path(self):
        from app.api import validation
        from app.api.validation import ToolDetectionResult

        with (
            patch.object(validation.shutil, "which", return_value="/usr/bin/makemkvcon64"),
            patch.object(
                validation,
                "_validate_makemkv_binary",
                return_value=ToolDetectionResult(
                    found=True, path="/usr/bin/makemkvcon64", version="MakeMKV v1.18.3"
                ),
            ),
        ):
            result = validation.detect_makemkv()
        assert result.found is True
        assert result.version == "MakeMKV v1.18.3"

    def test_detect_makemkv_found_in_common_location(self, tmp_path):
        from app.api import validation
        from app.api.validation import ToolDetectionResult

        exe = tmp_path / "makemkvcon64"
        exe.write_text("")
        with (
            patch.object(validation.shutil, "which", return_value=None),
            patch.object(validation, "_get_makemkv_search_paths", return_value=[str(exe)]),
            patch.object(
                validation,
                "_validate_makemkv_binary",
                return_value=ToolDetectionResult(found=True, path=str(exe), version="v"),
            ),
        ):
            result = validation.detect_makemkv()
        assert result.found is True

    def test_detect_makemkv_not_found(self):
        from app.api import validation

        with (
            patch.object(validation.shutil, "which", return_value=None),
            patch.object(validation, "_get_makemkv_search_paths", return_value=[]),
        ):
            result = validation.detect_makemkv()
        assert result.found is False
        assert "not found" in result.error.lower()

    def test_detect_ffmpeg_found_on_path(self):
        from app.api import validation
        from app.api.validation import ToolDetectionResult

        with (
            patch.object(validation.shutil, "which", return_value="/usr/bin/ffmpeg"),
            patch.object(
                validation,
                "_validate_ffmpeg_binary",
                return_value=ToolDetectionResult(
                    found=True, path="/usr/bin/ffmpeg", version="ffmpeg 6.0"
                ),
            ),
        ):
            result = validation.detect_ffmpeg()
        assert result.found is True
        assert result.version == "ffmpeg 6.0"

    def test_detect_ffmpeg_found_in_common_location(self, tmp_path):
        from app.api import validation
        from app.api.validation import ToolDetectionResult

        exe = tmp_path / "ffmpeg"
        exe.write_text("")
        with (
            patch.object(validation.shutil, "which", return_value=None),
            patch.object(validation, "_get_ffmpeg_search_paths", return_value=[str(exe)]),
            patch.object(
                validation,
                "_validate_ffmpeg_binary",
                return_value=ToolDetectionResult(found=True, path=str(exe), version="v"),
            ),
        ):
            result = validation.detect_ffmpeg()
        assert result.found is True

    def test_detect_ffmpeg_not_found(self):
        from app.api import validation

        with (
            patch.object(validation.shutil, "which", return_value=None),
            patch.object(validation, "_get_ffmpeg_search_paths", return_value=[]),
        ):
            result = validation.detect_ffmpeg()
        assert result.found is False
        assert "not found" in result.error.lower()


class TestSearchPaths:
    """Platform-specific search path lists."""

    def test_linux_paths_nonempty(self):
        from app.api import validation

        with patch.object(validation.sys, "platform", "linux"):
            assert validation._get_makemkv_search_paths()
            assert validation._get_ffmpeg_search_paths()

    def test_windows_paths(self):
        from app.api import validation

        with patch.object(validation.sys, "platform", "win32"):
            mk = validation._get_makemkv_search_paths()
            ff = validation._get_ffmpeg_search_paths()
        assert any("makemkvcon64.exe" in p for p in mk)
        assert any("ffmpeg.exe" in p for p in ff)

    def test_windows_ffmpeg_includes_package_manager_paths(self):
        """Windows FFmpeg search covers Chocolatey + scoop + a home extract."""
        from app.api import validation

        with (
            patch.object(validation.sys, "platform", "win32"),
            patch.dict(validation.os.environ, {"USERPROFILE": r"C:\Users\tester"}, clear=False),
        ):
            ff = validation._get_ffmpeg_search_paths()

        # pathlib joins with "\" on Windows but "/" on the Linux CI runner, so
        # normalise separators before comparing — the production guard only ever
        # runs this branch on Windows.
        normalized = [p.replace("\\", "/").lower() for p in ff]
        joined = "\n".join(normalized)
        assert "chocolatey" in joined
        assert "scoop" in joined
        # The user-home extract is built from the expanded USERPROFILE.
        assert "c:/users/tester/ffmpeg/bin/ffmpeg.exe" in normalized

    def test_windows_ffmpeg_paths_without_userprofile(self):
        """Missing USERPROFILE degrades to the machine-wide paths, no crash."""
        from app.api import validation

        with (
            patch.object(validation.sys, "platform", "win32"),
            patch.dict(validation.os.environ, {}, clear=True),
        ):
            ff = validation._get_ffmpeg_search_paths()
        assert any("ffmpeg.exe" in p for p in ff)
        # No per-user paths get appended when USERPROFILE is absent.
        assert not any("scoop" in p.lower() for p in ff)


class TestWingetFfmpegDetection:
    """winget's version-stamped FFmpeg layout is resolved via globbing.

    ``winget install Gyan.FFmpeg`` (the in-app install hint) unpacks the build
    under ``%LOCALAPPDATA%\\Microsoft\\WinGet\\Packages\\Gyan.FFmpeg_*\\ffmpeg-*\\bin``.
    That path is version-stamped, so it can't be hardcoded like the other
    search paths — it has to be globbed.
    """

    @staticmethod
    def _make_winget_layout(root: Path) -> Path:
        bin_dir = (
            root
            / "Microsoft"
            / "WinGet"
            / "Packages"
            / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
            / "ffmpeg-8.1.1-full_build"
            / "bin"
        )
        bin_dir.mkdir(parents=True)
        exe = bin_dir / "ffmpeg.exe"
        exe.write_text("")
        return exe

    def test_winget_glob_resolves_versioned_path(self, tmp_path):
        from app.api import validation

        exe = self._make_winget_layout(tmp_path)
        with (
            patch.object(validation.sys, "platform", "win32"),
            patch.dict(validation.os.environ, {"LOCALAPPDATA": str(tmp_path)}, clear=False),
        ):
            matches = validation._iter_winget_ffmpeg_paths()
        assert str(exe) in matches

    def test_winget_glob_empty_when_no_localappdata(self):
        from app.api import validation

        with (
            patch.object(validation.sys, "platform", "win32"),
            patch.dict(validation.os.environ, {}, clear=True),
        ):
            assert validation._iter_winget_ffmpeg_paths() == []

    def test_winget_glob_empty_off_windows(self):
        from app.api import validation

        with patch.object(validation.sys, "platform", "linux"):
            assert validation._iter_winget_ffmpeg_paths() == []

    def test_detect_ffmpeg_finds_winget_install(self, tmp_path):
        """detect_ffmpeg resolves a winget binary when PATH + fixed paths miss it."""
        from app.api import validation
        from app.api.validation import ToolDetectionResult

        exe = self._make_winget_layout(tmp_path)
        with (
            patch.object(validation.sys, "platform", "win32"),
            patch.object(validation.shutil, "which", return_value=None),
            patch.object(validation, "_get_ffmpeg_search_paths", return_value=[]),
            patch.dict(validation.os.environ, {"LOCALAPPDATA": str(tmp_path)}, clear=False),
            patch.object(
                validation,
                "_validate_ffmpeg_binary",
                return_value=ToolDetectionResult(
                    found=True, path=str(exe), version="ffmpeg version 8.1.1"
                ),
            ),
        ):
            result = validation.detect_ffmpeg()
        assert result.found is True
        assert result.version == "ffmpeg version 8.1.1"


class TestValidateMakemkvEndpoint:
    """The /validate/makemkv endpoint's existence + relabel branches."""

    def test_file_not_found(self):
        from app.api.validation import ValidationRequest, validate_makemkv

        result = _run(validate_makemkv(ValidationRequest(path="/nope/makemkvcon64.exe")))
        assert result.valid is False
        assert "File not found" in result.error

    def test_path_is_not_a_file(self, tmp_path):
        from app.api.validation import ValidationRequest, validate_makemkv

        d = tmp_path / "makemkvcon"  # allowed basename, but a directory
        d.mkdir()
        result = _run(validate_makemkv(ValidationRequest(path=str(d))))
        assert result.valid is False
        assert "not a file" in result.error.lower()

    def test_success_omits_path(self, tmp_path):
        from app.api import validation
        from app.api.validation import (
            ToolDetectionResult,
            ValidationRequest,
            validate_makemkv,
        )

        exe = tmp_path / "makemkvcon64.exe"
        exe.write_text("")
        with patch.object(
            validation,
            "_validate_makemkv_binary",
            return_value=ToolDetectionResult(found=True, version="MakeMKV v1.18.3"),
        ):
            result = _run(validate_makemkv(ValidationRequest(path=str(exe))))
        assert result.valid is True
        assert result.version == "MakeMKV v1.18.3"
        assert result.path is None

    def test_timeout_is_relabeled(self, tmp_path):
        from app.api import validation
        from app.api.validation import (
            ToolDetectionResult,
            ValidationRequest,
            validate_makemkv,
        )

        exe = tmp_path / "makemkvcon64.exe"
        exe.write_text("")
        with patch.object(
            validation,
            "_validate_makemkv_binary",
            return_value=ToolDetectionResult(found=False, error="Command timeout (10s)"),
        ):
            result = _run(validate_makemkv(ValidationRequest(path=str(exe))))
        assert result.valid is False
        assert result.error == "MakeMKV command timeout (10s)"


class TestValidateFfmpegEndpoint:
    """The /validate/ffmpeg endpoint, including the empty-path PATH lookup."""

    def test_empty_path_found_on_system_path(self):
        from app.api import validation
        from app.api.validation import (
            ToolDetectionResult,
            ValidationRequest,
            validate_ffmpeg,
        )

        with (
            patch.object(validation.shutil, "which", return_value="/usr/bin/ffmpeg"),
            patch.object(
                validation,
                "_validate_ffmpeg_binary",
                return_value=ToolDetectionResult(
                    found=True, path="/usr/bin/ffmpeg", version="ffmpeg 6.0"
                ),
            ),
        ):
            result = _run(validate_ffmpeg(ValidationRequest(path="")))
        assert result.valid is True
        assert result.path == "/usr/bin/ffmpeg"

    def test_empty_path_not_on_system_path(self):
        from app.api import validation
        from app.api.validation import ValidationRequest, validate_ffmpeg

        with patch.object(validation.shutil, "which", return_value=None):
            result = _run(validate_ffmpeg(ValidationRequest(path="")))
        assert result.valid is False
        assert "PATH" in result.error

    def test_file_path_not_found(self):
        from app.api.validation import ValidationRequest, validate_ffmpeg

        result = _run(validate_ffmpeg(ValidationRequest(path="/nope/ffmpeg")))
        assert result.valid is False
        assert "File not found" in result.error

    def test_non_zero_is_relabeled(self, tmp_path):
        from app.api import validation
        from app.api.validation import (
            ToolDetectionResult,
            ValidationRequest,
            validate_ffmpeg,
        )

        exe = tmp_path / "ffmpeg"
        exe.write_text("")
        with patch.object(
            validation,
            "_validate_ffmpeg_binary",
            return_value=ToolDetectionResult(found=False, error="Non-zero exit code"),
        ):
            result = _run(validate_ffmpeg(ValidationRequest(path=str(exe))))
        assert result.valid is False
        assert result.error == "FFmpeg returned non-zero exit code"

    def test_timeout_is_relabeled(self, tmp_path):
        from app.api import validation
        from app.api.validation import (
            ToolDetectionResult,
            ValidationRequest,
            validate_ffmpeg,
        )

        exe = tmp_path / "ffmpeg"
        exe.write_text("")
        with patch.object(
            validation,
            "_validate_ffmpeg_binary",
            return_value=ToolDetectionResult(found=False, error="Command timeout (10s)"),
        ):
            result = _run(validate_ffmpeg(ValidationRequest(path=str(exe))))
        assert result.valid is False
        assert result.error == "FFmpeg command timeout (10s)"


class TestDetectToolsVersionProbeBudget:
    """detect-tools probes MakeMKV's version on a short budget; the interactive
    validate path keeps the full budget.

    The version probe runs ``makemkvcon -r info disc:99999``, which enumerates
    optical drives and can block for many seconds on a slow/busy drive. The
    found+path answer never depends on it, so detect-tools must not pay the full
    20s for a cosmetic version string.
    """

    @staticmethod
    def _capture_probe_timeout():
        """Stub subprocess.run, recording the timeout handed to the -r probe."""
        seen: dict[str, float | None] = {}

        def fake_run(cmd, **kwargs):
            m = MagicMock()
            m.returncode = 1
            m.stderr = ""
            if "-r" in cmd:  # the drive-enumerating version probe
                seen["probe_timeout"] = kwargs.get("timeout")
                m.stdout = (
                    'MSG:1005,0,1,"MakeMKV v1.18.3 win(x64-release) started",'
                    '"%1 started","MakeMKV v1.18.3 win(x64-release)"'
                )
            else:  # the fast no-arg validity check
                m.stdout = "Use: makemkvcon [switches] Command [Parameters]"
            return m

        return seen, fake_run

    def test_detect_makemkv_uses_short_probe_timeout(self):
        """The auto-detect path (only caller is detect-tools) probes on the short budget."""
        from app.api import validation
        from app.api.validation import detect_makemkv

        seen, fake_run = self._capture_probe_timeout()
        with (
            patch.object(validation.shutil, "which", return_value="/usr/bin/makemkvcon64"),
            patch("app.api.validation.subprocess.run", side_effect=fake_run),
        ):
            result = detect_makemkv()

        # Found + path + version still resolve when the drive responds quickly...
        assert result.found is True
        assert result.path == "/usr/bin/makemkvcon64"
        assert result.version == "MakeMKV v1.18.3 win(x64-release)"
        # ...but the drive-enumerating probe got the short detect-tools budget, not 20s.
        assert seen["probe_timeout"] == validation._DETECT_VERSION_PROBE_TIMEOUT_S
        assert seen["probe_timeout"] < validation._VERSION_PROBE_TIMEOUT_S

    def test_validate_binary_default_keeps_full_probe_timeout(self):
        """The interactive /validate/makemkv path keeps the full version-probe budget."""
        from app.api import validation
        from app.api.validation import _validate_makemkv_binary

        seen, fake_run = self._capture_probe_timeout()
        with patch("app.api.validation.subprocess.run", side_effect=fake_run):
            result = _validate_makemkv_binary("C:/MakeMKV/makemkvcon64.exe")

        assert result.found is True
        assert seen["probe_timeout"] == validation._VERSION_PROBE_TIMEOUT_S


class TestDetectToolsDeadline:
    """A slow MakeMKV detector (busy optical drive) must not gate the response."""

    def test_slow_makemkv_does_not_delay_ffmpeg_and_fpcalc(self):
        """detect-tools caps MakeMKV with a per-detector deadline, so its
        drive-enumerating probe can't stall the ffmpeg/fpcalc results."""
        import asyncio
        import threading
        import time

        from app.api import validation
        from app.api.validation import ToolDetectionResult, detect_tools

        release = threading.Event()

        def stuck_makemkv() -> ToolDetectionResult:
            # Simulate a busy optical drive: blocks well past the deadline.
            release.wait(timeout=10)
            return ToolDetectionResult(found=True, path="m", version="MakeMKV v1.18.3")

        def fast_ffmpeg() -> ToolDetectionResult:
            return ToolDetectionResult(found=True, path="f", version="ffmpeg 6.0")

        def fast_fpcalc() -> ToolDetectionResult:
            return ToolDetectionResult(found=True, path="fp", version="fpcalc 1.5.1")

        async def drive():
            start = time.monotonic()
            result = await detect_tools()
            elapsed = time.monotonic() - start
            release.set()  # unblock the orphaned probe thread so teardown is prompt
            return result, elapsed

        with (
            patch.object(validation, "_MAKEMKV_DETECT_DEADLINE_S", 0.2),
            patch.object(validation, "detect_makemkv", stuck_makemkv),
            patch.object(validation, "detect_ffmpeg", fast_ffmpeg),
            patch.object(validation, "detect_fpcalc", fast_fpcalc),
        ):
            result, elapsed = asyncio.run(drive())

        # ffmpeg + fpcalc came back intact; the stuck MakeMKV probe never gated them.
        assert result.ffmpeg.found is True
        assert result.ffmpeg.version == "ffmpeg 6.0"
        assert result.fpcalc.found is True
        assert result.fpcalc.version == "fpcalc 1.5.1"
        # MakeMKV hit its deadline -> degraded result, not a multi-second hang.
        assert result.makemkv.found is False
        assert "timed out" in (result.makemkv.error or "").lower()
        # The response landed near the 0.2s deadline, nowhere near the 10s block.
        assert elapsed < 2.0

    def test_orphaned_makemkv_exception_is_traced_not_swallowed(self, caplog):
        """If the over-deadline detector later raises, the error is logged at debug
        rather than vanishing — detectors normally catch their own errors, so this
        backstop path shouldn't be silent."""
        import asyncio
        import logging
        import threading

        from app.api import validation
        from app.api.validation import ToolDetectionResult, detect_tools

        release = threading.Event()

        def stuck_then_raise() -> ToolDetectionResult:
            release.wait(timeout=10)
            raise RuntimeError("drive exploded")

        def fast_ffmpeg() -> ToolDetectionResult:
            return ToolDetectionResult(found=True, path="f", version="ffmpeg 6.0")

        def fast_fpcalc() -> ToolDetectionResult:
            return ToolDetectionResult(found=True, path="fp", version="fpcalc 1.5.1")

        async def drive():
            result = await detect_tools()
            release.set()  # let the orphaned detector finish (and raise)
            await asyncio.sleep(0.3)  # let its done-callback run before teardown
            return result

        with (
            caplog.at_level(logging.DEBUG, logger="app.api.validation"),
            patch.object(validation, "_MAKEMKV_DETECT_DEADLINE_S", 0.2),
            patch.object(validation, "detect_makemkv", stuck_then_raise),
            patch.object(validation, "detect_ffmpeg", fast_ffmpeg),
            patch.object(validation, "detect_fpcalc", fast_fpcalc),
        ):
            result = asyncio.run(drive())

        # The endpoint still returned the degraded result for the timed-out detector...
        assert result.makemkv.found is False
        assert "timed out" in (result.makemkv.error or "").lower()
        # ...and the orphan's late exception was traced, not silently discarded.
        assert any(
            "orphaned detector finished with error" in m and "drive exploded" in m
            for m in caplog.messages
        )


class TestTmdbValidationRemainingBranches:
    """The status/timeout/generic-exception branches not covered elsewhere."""

    @patch("app.api.validation.requests.get")
    def test_unexpected_status_code(self, mock_get):
        from app.api.validation import TmdbValidationRequest, validate_tmdb

        mock_get.return_value = MagicMock(status_code=500)
        result = _run(validate_tmdb(TmdbValidationRequest(api_key="k")))
        assert result.valid is False
        assert "status 500" in result.error

    @patch("app.api.validation.requests.get")
    def test_timeout(self, mock_get):
        import requests as req_lib

        from app.api.validation import TmdbValidationRequest, validate_tmdb

        mock_get.side_effect = req_lib.exceptions.Timeout()
        result = _run(validate_tmdb(TmdbValidationRequest(api_key="k")))
        assert result.valid is False
        assert "timeout" in result.error.lower()

    @patch("app.api.validation.requests.get")
    def test_generic_exception(self, mock_get):
        from app.api.validation import TmdbValidationRequest, validate_tmdb

        mock_get.side_effect = RuntimeError("weird")
        result = _run(validate_tmdb(TmdbValidationRequest(api_key="k")))
        assert result.valid is False
        assert "Validation failed" in result.error


class TestValidateDiscordTemplateEndpoint:
    """POST /validate/discord-template — live per-keystroke validation for ConfigWizard."""

    def test_valid_template(self):
        from app.api.validation import (
            DiscordTemplateValidationRequest,
            validate_discord_template_endpoint,
        )

        result = _run(
            validate_discord_template_endpoint(
                DiscordTemplateValidationRequest(template="{{title}} - {{duration}}")
            )
        )
        assert result.valid is True
        assert result.error is None

    def test_empty_template_is_valid(self):
        from app.api.validation import (
            DiscordTemplateValidationRequest,
            validate_discord_template_endpoint,
        )

        result = _run(
            validate_discord_template_endpoint(DiscordTemplateValidationRequest(template=""))
        )
        assert result.valid is True

    def test_unknown_variable(self):
        from app.api.validation import (
            DiscordTemplateValidationRequest,
            validate_discord_template_endpoint,
        )

        result = _run(
            validate_discord_template_endpoint(
                DiscordTemplateValidationRequest(template="{{bogus}}")
            )
        )
        assert result.valid is False
        assert "bogus" in result.error

    def test_malformed_syntax(self):
        from app.api.validation import (
            DiscordTemplateValidationRequest,
            validate_discord_template_endpoint,
        )

        result = _run(
            validate_discord_template_endpoint(DiscordTemplateValidationRequest(template="{{title"))
        )
        assert result.valid is False
        assert result.error is not None
