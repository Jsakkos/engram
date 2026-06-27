"""Guard the three-way sync for the background pre-transcription config fields.

A new AppConfig field must also appear in ConfigUpdate (so PUT accepts it), ConfigResponse
(so GET returns it), and the GET constructor — otherwise Pydantic silently drops it on PUT or
omits it on GET. This test pins all three at the model level (no DB), so a future refactor
can't quietly break the toggles.
"""

import pytest

from app.api.routes import ConfigResponse, ConfigUpdate
from app.models.app_config import AppConfig

FIELDS_AND_DEFAULTS = [
    ("enable_background_pretranscription", True),
    ("pretranscribe_full_file", False),
    ("auto_eject_enabled", True),
]


@pytest.mark.parametrize(("field", "default"), FIELDS_AND_DEFAULTS)
def test_appconfig_defaults(field, default):
    assert getattr(AppConfig(), field) is default


@pytest.mark.parametrize(("field", "default"), FIELDS_AND_DEFAULTS)
def test_config_update_accepts_and_carries_the_field(field, default):
    update = ConfigUpdate(**{field: not default})
    # Present in the dump (so update_config persists it) and not coerced away.
    assert update.model_dump()[field] is (not default)
    # Unset stays None so PUT doesn't clobber it when omitted.
    assert ConfigUpdate().model_dump()[field] is None


@pytest.mark.parametrize(("field", "_default"), FIELDS_AND_DEFAULTS)
def test_config_response_exposes_the_field(field, _default):
    assert field in ConfigResponse.model_fields
