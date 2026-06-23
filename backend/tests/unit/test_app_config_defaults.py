import pytest

from app.models.app_config import AppConfig


@pytest.mark.unit
def test_fingerprint_identification_defaults_on():
    """Disc-hash identification is enabled by default for new installs."""
    cfg = AppConfig()
    assert cfg.enable_fingerprint_identification is True


@pytest.mark.unit
def test_fingerprint_contributions_still_default_on():
    """Guard: flipping identification must not disturb the contributions default."""
    cfg = AppConfig()
    assert cfg.enable_fingerprint_contributions is True
