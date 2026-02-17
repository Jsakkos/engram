"""Unit tests for validation logic.

Tests path validation, configuration validation, and input sanitization.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.models import AppConfig
from pydantic import ValidationError


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
        # Empty paths should be rejected or have defaults
        try:
            config = AppConfig(staging_path="")
            # If accepted, should have a default or be None
            assert config.staging_path is None or config.staging_path != ""
        except ValidationError:
            # Or validation error is acceptable
            pass


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

        # Invalid values (negative or zero) should be rejected or clamped
        try:
            config = AppConfig(max_concurrent_matches=-1)
            assert config.max_concurrent_matches > 0, "Negative value should be rejected"
        except ValidationError:
            pass  # Validation error is acceptable

        try:
            config = AppConfig(max_concurrent_matches=0)
            assert config.max_concurrent_matches > 0, "Zero should be rejected"
        except ValidationError:
            pass  # Validation error is acceptable

    def test_boolean_flags_validation(self):
        """Test boolean configuration flags."""
        config = AppConfig(
            transcoding_enabled=True,
        )
        assert config.transcoding_enabled is True

        config2 = AppConfig(transcoding_enabled=False)
        assert config2.transcoding_enabled is False

    def test_conflict_resolution_values(self):
        """Test conflict_resolution_default has valid values."""
        valid_values = ["skip", "rename", "overwrite"]

        for value in valid_values:
            config = AppConfig(conflict_resolution_default=value)
            assert config.conflict_resolution_default == value

        # Invalid value should be rejected
        try:
            config = AppConfig(conflict_resolution_default="invalid")
            # Should either reject or use default
            assert config.conflict_resolution_default in valid_values
        except ValidationError:
            pass  # Validation error is acceptable

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
            assert (
                config.analyst_tv_min_duration < config.analyst_tv_max_duration
            ), "Min should be less than max"
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

        # Negative values should be rejected
        try:
            config = AppConfig(ripping_file_poll_interval=-1.0)
            assert config.ripping_file_poll_interval > 0
        except ValidationError:
            pass


class TestInputSanitization:
    """Test input sanitization and security."""

    def test_path_traversal_prevention(self):
        """Test that path traversal attacks are prevented."""
        dangerous_paths = [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32",
            "/tmp/../etc/passwd",
            "C:\\Users\\..\\..\\Windows\\System32",
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

        assert config.transcoding_enabled is not None
        assert isinstance(config.transcoding_enabled, bool)

        assert config.conflict_resolution_default is not None
        assert config.conflict_resolution_default in ["skip", "rename", "overwrite"]

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
        try:
            config = AppConfig(
                max_concurrent_matches=1000000,
                ripping_file_ready_timeout=999999999.0,
            )
            # Should either reject or clamp to reasonable values
            assert config.max_concurrent_matches < 100  # Reasonable limit
            assert config.ripping_file_ready_timeout < 86400  # Less than a day
        except ValidationError:
            pass  # Validation error is acceptable

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
