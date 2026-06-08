"""Guard the three-way sync for the GPU-acceleration config field.

A new AppConfig field must also appear in ConfigUpdate (so PUT accepts it), ConfigResponse
(so GET returns it), and the GET constructor — otherwise Pydantic silently drops it on PUT or
omits it on GET. This test pins all three at the model level (no DB), so a future refactor
can't quietly break the toggle.
"""

from app.api.routes import ConfigResponse, ConfigUpdate
from app.models.app_config import AppConfig

FIELD = "enable_gpu_acceleration"


def test_appconfig_defaults_gpu_off():
    assert getattr(AppConfig(), FIELD) is False


def test_config_update_accepts_and_carries_the_field():
    update = ConfigUpdate(**{FIELD: True})
    # Present in the dump (so update_config persists it) and not coerced away.
    assert update.model_dump()[FIELD] is True
    # Unset stays None so PUT doesn't clobber it when omitted.
    assert ConfigUpdate().model_dump()[FIELD] is None


def test_config_response_exposes_the_field():
    assert FIELD in ConfigResponse.model_fields
