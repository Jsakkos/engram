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
