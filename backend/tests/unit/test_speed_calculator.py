"""Unit tests for SpeedCalculator.

Tests speed/ETA calculation and debounce behavior.
"""

from unittest.mock import patch

from app.services.job_manager import SpeedCalculator


class TestSpeedCalculator:
    """Test speed and ETA calculation."""

    def test_initial_speed_zero(self):
        """No updates → speed is 0."""
        calc = SpeedCalculator(total_bytes=1_000_000_000)
        assert "0.0" in calc.speed_str
        assert calc.eta_seconds == 0

    def test_speed_after_updates(self):
        """Progressive byte updates → calculated speed > 0."""
        calc = SpeedCalculator(total_bytes=1_000_000_000)

        # Simulate two updates with time gap
        with patch("time.time") as mock_time:
            mock_time.return_value = 1000.0
            calc.update(0)

            mock_time.return_value = 1001.0
            calc.update(10_000_000)  # 10MB in 1 second

        assert calc._current_speed > 0
        assert "0.0" not in calc.speed_str

    def test_eta_calculation(self):
        """Remaining bytes / speed = ETA."""
        total = 1_000_000_000  # 1GB
        calc = SpeedCalculator(total_bytes=total)

        with patch("time.time") as mock_time:
            mock_time.return_value = 1000.0
            calc.update(0)

            mock_time.return_value = 1001.0
            calc.update(100_000_000)  # 100MB in 1 second → 100 MB/s

        # 900MB remaining at 100MB/s → ~9 seconds
        assert calc.eta_seconds > 0
        assert calc.eta_seconds <= 15  # Allow some tolerance

    def test_debounce_rapid_updates(self):
        """Updates <0.5s apart → ignored."""
        calc = SpeedCalculator(total_bytes=1_000_000_000)

        with patch("time.time") as mock_time:
            mock_time.return_value = 1000.0
            calc.update(0)

            # Rapid updates within 0.5s — should be debounced
            mock_time.return_value = 1000.1
            calc.update(1_000_000)
            mock_time.return_value = 1000.2
            calc.update(2_000_000)
            mock_time.return_value = 1000.3
            calc.update(3_000_000)

        # Only the first update (0 bytes) should be in history
        assert len(calc._bytes_history) == 1

    def test_speed_str_format(self):
        """Speed string should contain 'x' and 'M/s'."""
        calc = SpeedCalculator(total_bytes=1_000_000_000)

        with patch("time.time") as mock_time:
            mock_time.return_value = 1000.0
            calc.update(0)

            mock_time.return_value = 1002.0
            calc.update(50_000_000)

        speed = calc.speed_str
        assert "x" in speed
        assert "M/s" in speed

    def test_eta_zero_when_no_progress(self):
        """No progress data → ETA is 0."""
        calc = SpeedCalculator(total_bytes=1_000_000_000)
        assert calc.eta_seconds == 0
