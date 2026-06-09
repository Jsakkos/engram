"""Unit tests for single-track re-rip (Feature C)."""

from app.models.disc_job import DiscTitle


def test_disc_title_has_rerip_attempts_default_zero():
    t = DiscTitle(job_id=1, title_index=0, duration_seconds=100)
    assert t.rerip_attempts == 0
